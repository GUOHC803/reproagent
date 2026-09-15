"""Offline evaluation runner: tasks x configs -> rows.jsonl + summary.md."""

from __future__ import annotations

import concurrent.futures as cf
import json
import shutil
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ..config import AgentConfig, Settings
from ..core.state import TaskSpec
from ..runner import run_task
from ..store.trace import TraceStore
from .checkers import run_checks
from .configs import get_config
from .report import write_summary


def load_tasks(tasks_dir: Path, only: list[str] | None = None) -> list[dict[str, Any]]:
    tasks = []
    for p in sorted(Path(tasks_dir).glob("*.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        raw["_path"] = p
        raw["_fixture"] = (p.parent / raw["source_dir"]).resolve()
        if only and raw["task_id"] not in only:
            continue
        tasks.append(raw)
    return tasks


def prepare_source(task: dict[str, Any]) -> Path:
    """Copy the fixture and apply the task's bug injection (if any)."""
    fixture: Path = task["_fixture"]
    tmp = Path(tempfile.mkdtemp(prefix=f"src-{task['task_id']}-"))
    dst = tmp / "src"
    shutil.copytree(fixture, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc"))
    inj = task.get("inject")
    for one in (inj if isinstance(inj, list) else [inj]) if inj else []:
        f = dst / one["file"]
        text = f.read_text(encoding="utf-8")
        if one["old"] not in text:
            raise RuntimeError(f"{task['task_id']}: injection anchor not found in {one['file']}")
        f.write_text(text.replace(one["old"], one["new"], 1), encoding="utf-8")
    return dst


def run_one(task: dict[str, Any], cfg: AgentConfig, settings: Settings, repeat: int, log: Callable[[str], None]) -> dict[str, Any]:
    tid = task["task_id"]
    spec = TaskSpec.model_validate({k: v for k, v in task.items() if k in TaskSpec.model_fields})
    spec.task_id = f"{tid}"
    src = prepare_source(task)
    t0 = time.time()
    row: dict[str, Any] = {"task_id": tid, "category": task.get("category"), "config": cfg.name, "repeat": repeat, "model": cfg.model}
    try:
        h = run_task(spec, cfg=cfg, settings=settings, source_dir=src, copy_source=True,
                     run_id=f"{cfg.name}-{tid}-r{repeat}-{int(t0)}", log=lambda s: log(f"[{cfg.name}/{tid}] {s}"))
        st = h.state
        chk = run_checks(task, workdir=h.workdir, fixture_dir=task["_fixture"], answer=st.final_answer or {})
        store = TraceStore(settings.resolved_db_path)
        m = store.run_metrics(st.run_id)
        store.close()
        row.update({
            "run_id": st.run_id, "success": chk.passed, "agent_claimed_success": st.status == "done",
            "check_details": chk.details, "answer": st.final_answer,
            "steps": st.step_idx, "repairs": st.repairs_used, "tool_calls": st.tool_calls_total,
            "tool_calls_failed": m.get("tool_calls_failed", 0), "llm_calls": st.llm_calls_total,
            "prompt_tokens": m.get("prompt_tokens", 0), "completion_tokens": m.get("completion_tokens", 0),
            "tokens": st.tokens_total, "cost_usd": round(st.cost_usd, 5),
            "last_failure": st.last_failure.value if (st.last_failure and st.status != "done") else None,
            "failures_seen": sorted({h.failure.value for h in st.history if h.failure}),
            "node_path": [h.node.value + ("" if h.status == "ok" else f"!{h.status}") for h in st.history],
            "wall_time_s": round(time.time() - t0, 1), "report": st.report_path,
        })
    except Exception as e:  # noqa: BLE001
        row.update({"success": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-2000:],
                    "wall_time_s": round(time.time() - t0, 1), "last_failure": "runner_error"})
    finally:
        shutil.rmtree(src.parent, ignore_errors=True)
    log(f"[{cfg.name}/{tid}] {'PASS' if row.get('success') else 'FAIL'} in {row['wall_time_s']}s"
        + (f" (repairs={row.get('repairs')}, tokens={row.get('tokens')})" if 'repairs' in row else f" {row.get('error', '')}"))
    return row


def run_eval(*, tasks_dir: Path, config_names: list[str], model: str | None, only: list[str] | None, repeats: int,
             out_dir: Path, sandbox: str | None, workers: int, log: Callable[[str], None]) -> Path:
    tasks = load_tasks(tasks_dir, only)
    if not tasks:
        raise SystemExit("no tasks found")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(out_dir) / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=run_dir / "runs", db_path=run_dir / "trace.sqlite3")
    rows_path = run_dir / "rows.jsonl"
    jobs = []
    for name in config_names:
        cfg = get_config(name)
        if model:
            cfg = cfg.with_updates(model=model)
        if sandbox:
            cfg = cfg.with_updates(sandbox=cfg.sandbox.model_copy(update={"backend": sandbox}))
        for task in tasks:
            for r in range(repeats):
                jobs.append((task, cfg, r))
    log(f"{len(jobs)} runs ({len(tasks)} tasks x {config_names} x {repeats}) -> {run_dir}")
    rows: list[dict[str, Any]] = []

    def _do(job):
        task, cfg, r = job
        return run_one(task, cfg, settings, r, log)

    with rows_path.open("a", encoding="utf-8") as f:
        if workers > 1:
            with cf.ThreadPoolExecutor(max_workers=workers) as ex:
                for row in ex.map(_do, jobs):
                    rows.append(row)
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    f.flush()
        else:
            for job in jobs:
                row = _do(job)
                rows.append(row)
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                f.flush()
    write_summary(rows, run_dir, meta={"model": model or get_config(config_names[0]).model, "configs": config_names,
                                       "tasks": [t["task_id"] for t in tasks], "repeats": repeats, "timestamp": stamp})
    log(f"summary: {run_dir / 'summary.md'}")
    return run_dir
