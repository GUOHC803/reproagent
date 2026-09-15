"""Glue: build the pieces for one run and execute it.  Used by the CLI, the API and the evaluator."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from .config import AgentConfig, Settings, load_settings
from .core.orchestrator import Orchestrator
from .core.runtime import NodeRuntime
from .core.single_call import run_single_call
from .core.state import Node, TaskSpec, TaskState
from .llm.client import LLMClient, build_client
from .sandbox import build_sandbox
from .store.trace import TraceStore
from .tools import default_registry


@dataclass
class RunHandle:
    state: TaskState
    workdir: Path
    artifacts_dir: Path
    store_path: Path


def prepare_workdir(source: Path | None, dest: Path, *, copy: bool) -> Path:
    """The agent works on a *copy* of the input directory so a run never mutates the original."""
    dest = Path(dest)
    if source is None:
        dest.mkdir(parents=True, exist_ok=True)
        return dest
    source = Path(source)
    if not copy:
        return source.resolve()
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", ".pytest_cache"))
    return dest.resolve()


def run_task(
    task: TaskSpec,
    *,
    cfg: AgentConfig | None = None,
    settings: Settings | None = None,
    source_dir: Path | None = None,
    copy_source: bool = True,
    llm: LLMClient | None = None,
    run_id: str | None = None,
    log: Callable[[str], None] | None = None,
    confirm_callback=None,
) -> RunHandle:
    load_dotenv()
    cfg = cfg or AgentConfig()
    settings = settings or load_settings()
    data_dir = Path(settings.data_dir)
    state = TaskState(task=task) if run_id is None else TaskState(task=task, run_id=run_id)
    run_dir = data_dir / state.run_id
    workdir = prepare_workdir(source_dir, run_dir / "work", copy=copy_source)
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    store = TraceStore(settings.resolved_db_path, record_messages=settings.record_llm)
    llm = llm or build_client(cfg.model, timeout_s=settings.llm_timeout_s, max_retries=settings.llm_max_retries)
    sandbox = build_sandbox(cfg.sandbox, workdir, confirm_callback=confirm_callback)
    tools = default_registry()
    if task.allowed_tools:
        tools = tools.subset(task.allowed_tools)

    store.create_run(state.run_id, task.model_dump(), cfg.model_dump())
    if cfg.mode == "single_call":
        rt = NodeRuntime(state=state, cfg=cfg, llm=llm, tools=tools, store=store, sandbox=sandbox, workdir=workdir,
                         artifacts_dir=artifacts_dir, node=Node.PLAN, log=log)
        state = run_single_call(state, rt)
    else:
        orch = Orchestrator(cfg=cfg, llm=llm, tools=tools, sandbox=sandbox, store=store, workdir=workdir,
                            artifacts_dir=artifacts_dir, log=log)
        state = orch.run(state)
    store.close()
    return RunHandle(state=state, workdir=workdir, artifacts_dir=artifacts_dir, store_path=settings.resolved_db_path)


def resume_run(run_id: str, *, settings: Settings | None = None, llm: LLMClient | None = None,
               log: Callable[[str], None] | None = None) -> RunHandle:
    load_dotenv()
    settings = settings or load_settings()
    store = TraceStore(settings.resolved_db_path, record_messages=settings.record_llm)
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(f"unknown run {run_id}")
    cfg = AgentConfig.model_validate(run["config"])
    run_dir = Path(settings.data_dir) / run_id
    workdir = run_dir / "work"
    artifacts_dir = run_dir / "artifacts"
    llm = llm or build_client(cfg.model, timeout_s=settings.llm_timeout_s, max_retries=settings.llm_max_retries)
    sandbox = build_sandbox(cfg.sandbox, workdir)
    tools = default_registry()
    orch = Orchestrator(cfg=cfg, llm=llm, tools=tools, sandbox=sandbox, store=store, workdir=workdir,
                        artifacts_dir=artifacts_dir, log=log)
    state = orch.resume(run_id)
    store.close()
    return RunHandle(state=state, workdir=workdir, artifacts_dir=artifacts_dir, store_path=settings.resolved_db_path)


def task_from_dict(d: dict[str, Any]) -> TaskSpec:
    return TaskSpec.model_validate(d)
