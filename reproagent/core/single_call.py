"""Ablation baseline: one model call, no tools, no state machine, no repair.

The model gets the same task plus a *dump* of the material (PDF text or repo
files, bounded), must answer / emit whole-file patches / list commands in one
shot.  We then apply the patches, run the commands and verify with the same
deterministic checks the state machine uses, so the comparison is fair.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..core import prompts as P
from ..core.failures import classify_exec
from ..sandbox.base import ExecResult
from .nodes import _deterministic_checks, _render_report, plan_node  # noqa: F401 - reuse helpers
from .runtime import NodeRuntime
from .state import FailureKind, FileChange, Node, NodeResult, Plan, TaskState


class SingleCallAnswer(BaseModel):
    answer: dict[str, Any] = Field(default_factory=dict)
    patches: list[dict[str, str]] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)


_MAX_MATERIAL = 60_000


def _material(state: TaskState, workdir: Path) -> str:
    inputs = state.task.inputs
    parts: list[str] = []
    if inputs.get("pdf"):
        from ..tools.read_pdf import _extract_pages

        p = workdir / inputs["pdf"]
        if p.exists():
            pages = _extract_pages(str(p))
            parts.append(f"=== PDF {inputs['pdf']} ({len(pages)} pages) ===\n" + "\n".join(pages))
    repo = workdir / inputs.get("repo", ".") if inputs.get("repo") else workdir
    if repo.is_dir():
        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(repo):
            dirnames[:] = sorted(d for d in dirnames if d not in {".git", "__pycache__", ".venv", ".reproagent", ".pytest_cache"})
            for f in sorted(filenames):
                fp = Path(dirpath) / f
                if fp.suffix in {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".json", ".cfg", ".sh"} and fp.stat().st_size < 40_000:
                    files.append(fp)
        # small files first so the dump covers as much of the repo as possible
        files.sort(key=lambda p: (0 if p.suffix in {".md", ".yaml", ".yml", ".toml", ".cfg"} else 1, p.stat().st_size))
        for fp in files:
            rel = fp.relative_to(workdir).as_posix()
            parts.append(f"=== FILE {rel} ===\n" + fp.read_text(encoding="utf-8", errors="replace"))
    text = "\n\n".join(parts)
    return text[:_MAX_MATERIAL] + ("\n...[material truncated]" if len(text) > _MAX_MATERIAL else "")


def run_single_call(state: TaskState, rt: NodeRuntime) -> TaskState:
    t0 = time.time()
    t = state.task
    fields = t.inputs.get("fields") or {}
    user = P.SINGLE_CALL_INSTRUCTIONS.format(goal=t.goal, criteria=t.success_criteria or "(none)",
                                             fields=json.dumps(fields, ensure_ascii=False) or "none",
                                             material=_material(state, rt.workdir))
    # A minimal plan object so downstream helpers (deterministic checks, report) work unchanged.
    state.plan = Plan(task_type=t.category if t.category != "custom" else "custom", needs_code_change=False,
                      needs_execution=False, steps=[], output_schema={k: str(v) for k, v in fields.items()},
                      commands=[t.inputs["command"]] if t.inputs.get("command") else [])
    res_node = Node.PLAN
    try:
        ans = rt.structured([{"role": "system", "content": P.SYSTEM_BASE}, {"role": "user", "content": user}],
                            SingleCallAnswer, max_attempts=2)
    except Exception as e:  # noqa: BLE001
        _fail(state, rt, res_node, FailureKind.MODEL_OUTPUT_INVALID, f"{type(e).__name__}: {e}", t0)
        return state
    state.findings = {"summary": "single-call answer", "extracted": ans.answer}
    for p in ans.patches:
        res = rt.call_tool("patch_file", {"path": p.get("path", ""), "mode": "create", "content": p.get("content", "")},
                           allow_test_edits=bool(t.inputs.get("allow_test_edits")))
        if res.status == "ok":
            state.changes.append(FileChange(path=res.data["path"], action=res.data["action"], diff=res.data.get("diff", "")))
    commands = ans.commands or state.plan.commands
    if not commands and t.category == "bug_fix":
        commands = ["python -m pytest -q --no-header -p no:cacheprovider"]
    state.plan.commands = commands
    results: list[dict[str, Any]] = []
    failure = None
    for cmd in commands:
        tool = "run_tests" if "pytest" in cmd else "run_experiment"
        r = rt.call_tool(tool, {"command": cmd})
        results.append({"command": cmd, "tool": tool, "status": r.status, "exit_code": r.data.get("exit_code"),
                        "stdout": r.stdout[-4000:], "stderr": r.stderr[-4000:], "data": {k: v for k, v in r.data.items() if k != "diff"}})
        if r.status != "ok":
            ex = ExecResult(r.data.get("exit_code", 1), r.stdout, r.stderr, r.duration_s, timed_out=r.status == "timeout",
                            blocked_reason=r.stderr if r.status == "blocked" else None, command=cmd)
            d = classify_exec(ex)
            failure = d.kind if d else FailureKind.NONZERO_EXIT
            break
    if commands:
        state.exec_results.append({"attempt": 1, "results": results})
    det_ok, notes, kind = _deterministic_checks(state, rt)
    passed = det_ok and failure is None
    state.final_answer = ans.answer
    state.verify_result = {"passed": passed, "reason": "single-call baseline: deterministic checks only", "checks": notes, "answer": ans.answer}
    nr = NodeResult(node=Node.PLAN, status="ok" if passed else "fail", failure=None if passed else (failure or kind or FailureKind.VERIFY_MISMATCH),
                    message="single_call " + ("passed" if passed else "failed"), duration_s=time.time() - t0,
                    tool_calls=rt.tool_calls, llm_calls=rt.llm_calls, tokens=rt.tokens)
    state.cost_usd += rt.cost
    state.record(nr)
    _finish(state, rt, passed, nr)
    return state


def _fail(state: TaskState, rt: NodeRuntime, node: Node, kind: FailureKind, msg: str, t0: float) -> None:
    nr = NodeResult(node=node, status="fail", failure=kind, message=msg[:500], duration_s=time.time() - t0,
                    llm_calls=rt.llm_calls, tokens=rt.tokens)
    state.record(nr)
    _finish(state, rt, False, nr)


def _finish(state: TaskState, rt: NodeRuntime, passed: bool, nr: NodeResult) -> None:
    rt.artifacts_dir.mkdir(parents=True, exist_ok=True)
    md = _render_report(state, passed, "Single-call baseline (no tools, no repair).")
    (rt.artifacts_dir / "report.md").write_text(md, encoding="utf-8")
    result = {"run_id": state.run_id, "task_id": state.task.task_id, "success": passed, "answer": state.final_answer,
              "verify": state.verify_result, "repairs": 0, "changes": [c.path for c in state.changes],
              "last_failure": nr.failure.value if nr.failure else None, "steps": ["single_call:" + nr.status]}
    (rt.artifacts_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    state.report_path = str(rt.artifacts_dir / "report.md")
    state.current = Node.DONE
    state.status = "done" if passed else "failed"
    state.finished_at = time.time()
    if rt.store:
        rt.store.save_step(state.run_id, 0, "single_call", nr.model_dump(mode="json"), state.model_dump(mode="json"))
        rt.store.set_run_status(state.run_id, state.status, {"success": passed, "steps": 1, "repairs": 0,
                                                             "tool_calls": state.tool_calls_total, "llm_calls": state.llm_calls_total,
                                                             "tokens": state.tokens_total, "cost_usd": round(state.cost_usd, 5),
                                                             "last_failure": None if passed else (nr.failure.value if nr.failure else None),
                                                             "wall_time_s": round(state.finished_at - state.started_at, 1),
                                                             "answer": state.final_answer})
