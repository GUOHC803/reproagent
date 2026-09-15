"""run_experiment: run an arbitrary command in the sandbox and report exit code / output / new files."""

from __future__ import annotations

import os
import time
from pathlib import Path

from pydantic import BaseModel, Field

from ..core.failures import classify_exec
from .base import Tool, ToolContext, ToolResult


class RunExperimentParams(BaseModel):
    command: str = Field(..., description="Shell command, e.g. 'python train.py --epochs 2 --out results.json'.")
    path: str = Field(".", description="Directory to run in, relative to workdir.")
    timeout_s: int | None = Field(None, ge=5, le=3600, description="Override the default timeout.")


class RunExperimentTool(Tool):
    name = "run_experiment"
    description = (
        "Execute a shell command inside the sandbox (no network, resource-limited, workdir only). Returns exit code, "
        "stdout/stderr tails and the list of files the command created or modified. Prefer small/fast settings "
        "(few epochs, small subsets) - the goal is a verified result, not a full training run."
    )
    Params = RunExperimentParams

    def run(self, p: RunExperimentParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        cwd = ctx.resolve(p.path)
        before = _snapshot(ctx.workdir)
        t0 = time.time()
        res = ctx.sandbox.run(p.command, cwd=cwd, timeout_s=p.timeout_s or ctx.default_timeout_s)
        after = _snapshot(ctx.workdir)
        new_files = sorted(f for f, m in after.items() if m > t0 - 1 and (f not in before or before[f] != m))
        data = {"exit_code": res.exit_code, "duration_s": round(res.duration_s, 2), "new_files": new_files[:50]}
        if res.blocked_reason:
            return ToolResult(status="blocked", stderr=res.stderr, data=data,
                              next_hint="This command is not allowed in the sandbox; achieve the goal another way.")
        if res.timed_out:
            return ToolResult(status="timeout", stdout=_tail(res.stdout), stderr=_tail(res.stderr), data=data,
                              next_hint="Reduce the workload (fewer epochs/samples) or raise timeout_s.")
        diag = classify_exec(res)
        if diag is None:
            hint = "Command succeeded." + (f" New/modified files: {', '.join(new_files[:5])}" if new_files else "")
            return ToolResult(status="ok", stdout=_tail(res.stdout), stderr=_tail(res.stderr, 1000), data=data,
                              artifacts=new_files[:20], next_hint=hint)
        data["failure_kind"] = diag.kind.value
        loc = f" at {diag.file}:{diag.line}" if diag.file else ""
        return ToolResult(status="error", stdout=_tail(res.stdout), stderr=_tail(res.stderr), data=data,
                          next_hint=f"{diag.kind.value}{loc}: {diag.evidence}")


def _snapshot(root: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".venv", ".pytest_cache", ".reproagent"}]
        for f in filenames:
            if f.endswith((".pyc",)):
                continue
            fp = Path(dirpath) / f
            try:
                out[fp.relative_to(root).as_posix()] = fp.stat().st_mtime
            except OSError:
                pass
        if len(out) > 5000:
            break
    return out


def _tail(s: str, n: int = 4000) -> str:
    return s if len(s) <= n else "...\n" + s[-n:]
