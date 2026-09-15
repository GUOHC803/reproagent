"""FastAPI service: submit a task, poll status, download the report.

Runs execute in a background thread pool; the trace DB is the source of truth
for status so the API is stateless across restarts.
"""

from __future__ import annotations

import concurrent.futures as cf
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .config import AgentConfig, load_settings
from .core.state import TaskSpec, TaskState
from .evals.configs import get_config
from .runner import run_task
from .store.trace import TraceStore

app = FastAPI(title="ReproAgent", version="0.1.0")
_pool = cf.ThreadPoolExecutor(max_workers=2)
_settings = load_settings()


class SubmitRequest(BaseModel):
    goal: str
    category: str = "custom"
    inputs: dict[str, Any] = Field(default_factory=dict)
    success_criteria: str = ""
    expected_outputs: list[str] = Field(default_factory=list)
    config: str = "full"
    model: str | None = None
    source_dir: str | None = Field(None, description="Server-side path of the material directory.")


class SubmitResponse(BaseModel):
    run_id: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tasks", response_model=SubmitResponse)
def submit(req: SubmitRequest) -> SubmitResponse:
    cfg: AgentConfig = get_config(req.config)
    if req.model:
        cfg = cfg.with_updates(model=req.model)
    task = TaskSpec(goal=req.goal, category=req.category, inputs=req.inputs, success_criteria=req.success_criteria,  # type: ignore[arg-type]
                    expected_outputs=req.expected_outputs)
    state = TaskState(task=task)
    src = Path(req.source_dir) if req.source_dir else None
    if src and not src.is_dir():
        raise HTTPException(400, f"source_dir not found: {src}")
    # register the run first so /tasks/{id} works immediately
    store = TraceStore(_settings.resolved_db_path)
    store.create_run(state.run_id, task.model_dump(), cfg.model_dump())
    store.set_run_status(state.run_id, "queued")
    store.close()
    _pool.submit(_run, task, cfg, src, state.run_id)
    return SubmitResponse(run_id=state.run_id)


def _run(task: TaskSpec, cfg: AgentConfig, src: Path | None, run_id: str) -> None:
    try:
        run_task(task, cfg=cfg, source_dir=src, run_id=run_id, settings=_settings)
    except Exception as e:  # noqa: BLE001
        store = TraceStore(_settings.resolved_db_path)
        store.set_run_status(run_id, "failed", {"error": f"{type(e).__name__}: {e}"})
        store.close()


@app.post("/tasks/upload", response_model=SubmitResponse)
async def submit_with_upload(goal: str, file: UploadFile, category: str = "paper_extraction", config: str = "full",
                             success_criteria: str = "") -> SubmitResponse:
    """Convenience: upload one PDF and ask a question about it."""
    tmp = Path(tempfile.mkdtemp(prefix="reproagent-upload-"))
    dst = tmp / (file.filename or "input.pdf")
    with dst.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return submit(SubmitRequest(goal=goal, category=category, inputs={"pdf": dst.name}, success_criteria=success_criteria,
                                config=config, source_dir=str(tmp)))


@app.get("/tasks")
def list_tasks(limit: int = 20) -> list[dict[str, Any]]:
    store = TraceStore(_settings.resolved_db_path)
    try:
        return [{k: v for k, v in r.items() if k != "config"} for r in store.list_runs(limit)]
    finally:
        store.close()


@app.get("/tasks/{run_id}")
def get_task(run_id: str) -> dict[str, Any]:
    store = TraceStore(_settings.resolved_db_path)
    try:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "unknown run")
        return {"run_id": run_id, "status": run["status"], "summary": run.get("summary"), "steps": store.steps(run_id),
                "metrics": store.run_metrics(run_id), "artifacts": store.artifacts(run_id)}
    finally:
        store.close()


@app.get("/tasks/{run_id}/report", response_class=PlainTextResponse)
def get_report(run_id: str) -> str:
    p = Path(_settings.data_dir) / run_id / "artifacts" / "report.md"
    if not p.exists():
        raise HTTPException(404, "report not ready")
    return p.read_text(encoding="utf-8")


@app.get("/tasks/{run_id}/artifacts/{name}")
def get_artifact(run_id: str, name: str):
    if "/" in name or name.startswith("."):
        raise HTTPException(400, "bad name")
    p = Path(_settings.data_dir) / run_id / "artifacts" / name
    if not p.exists():
        raise HTTPException(404, "no such artifact")
    return FileResponse(p)
