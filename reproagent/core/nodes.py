"""The seven nodes.  Each is ``(state, rt) -> NodeResult`` and touches only ``state``.

PLAN -> RETRIEVE -> [IMPLEMENT -> EXECUTE -> VERIFY -> (REPAIR -> EXECUTE ...)] -> REPORT
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..core import prompts as P
from ..core.failures import Diagnosis, classify_exec, error_tail
from ..sandbox.base import ExecResult
from ..tools.base import ToolResult
from .runtime import BudgetExceeded, NodeRuntime, StructuredOutputError
from .state import FailureKind, FileChange, Node, NodeResult, Plan, TaskState

# ---------------------------------------------------------------- schemas


class Findings(BaseModel):
    summary: str
    key_facts: dict[str, Any] = Field(default_factory=dict)
    extracted: dict[str, Any] = Field(default_factory=dict)
    relevant_files: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class ImplementResult(BaseModel):
    summary: str
    files_changed: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)


class RepairResult(BaseModel):
    diagnosis: str
    fix_summary: str
    files_changed: list[str] = Field(default_factory=list)
    fixable: bool = True
    commands: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    passed: bool
    reason: str
    missing: list[str] = Field(default_factory=list)
    answer: dict[str, Any] = Field(default_factory=dict)


class Summary(BaseModel):
    summary: str


# ---------------------------------------------------------------- helpers


def _timed(fn):
    def wrapper(state: TaskState, rt: NodeRuntime) -> NodeResult:
        t0 = time.time()
        try:
            res = fn(state, rt)
        except BudgetExceeded as e:
            res = NodeResult(node=rt.node, status="fail", failure=FailureKind.BUDGET_EXHAUSTED, message=str(e))
        except StructuredOutputError as e:
            res = NodeResult(node=rt.node, status="fail", failure=FailureKind.MODEL_OUTPUT_INVALID, message=str(e))
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            kind = FailureKind.LLM_ERROR if "LLM" in name else FailureKind.UNKNOWN
            res = NodeResult(node=rt.node, status="fail", failure=kind, message=f"{name}: {e}"[:1000])
        res.duration_s = time.time() - t0
        res.tool_calls = rt.tool_calls
        res.llm_calls = rt.llm_calls
        res.tokens = rt.tokens
        state.cost_usd += rt.cost
        return res

    wrapper.__name__ = fn.__name__
    return wrapper


def _tree(root: Path, depth: int = 2, max_lines: int = 80) -> str:
    lines: list[str] = []
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in {".git", "__pycache__", ".venv", ".reproagent", ".pytest_cache"})
        d = len(Path(dirpath).parts) - base
        if d >= depth:
            dirnames[:] = []
        rel = Path(dirpath).relative_to(root).as_posix()
        lines.append(("  " * d) + (rel if rel != "." else ".") + "/")
        for f in sorted(filenames):
            lines.append(("  " * d) + "  " + f)
        if len(lines) > max_lines:
            lines.append("  ...")
            break
    return "\n".join(lines)


def _inputs(state: TaskState) -> str:
    return json.dumps(state.task.inputs, ensure_ascii=False)


def _fields(state: TaskState) -> str:
    fields = (state.plan.output_schema if state.plan else None) or state.task.inputs.get("fields") or {}
    return json.dumps(fields, ensure_ascii=False) if fields else "none"


def _apply_loop_failure(rt: NodeRuntime, outcome, default_msg: str) -> NodeResult:
    return NodeResult(node=rt.node, status="fail", failure=outcome.failure or FailureKind.UNKNOWN,
                      message=outcome.message or default_msg)


def _record_changes(state: TaskState, outcome) -> list[str]:
    changed: list[str] = []
    for name, args, res in outcome.tool_results:
        if name == "patch_file" and res.status == "ok":
            state.changes.append(FileChange(path=res.data.get("path", args.get("path", "?")),
                                            action=res.data.get("action", "modify"), diff=res.data.get("diff", "")))
            changed.append(res.data.get("path", args.get("path", "?")))
    return changed


# ---------------------------------------------------------------- nodes


@_timed
def plan_node(state: TaskState, rt: NodeRuntime) -> NodeResult:
    t = state.task
    user = P.PLAN_INSTRUCTIONS.format(
        tree=_tree(rt.workdir), goal=t.goal, criteria=t.success_criteria or "(none given)", inputs=_inputs(state),
        hints="; ".join(t.hints) or "none",
    )
    plan = rt.structured([{"role": "system", "content": P.SYSTEM_BASE}, {"role": "user", "content": user}], Plan)
    if t.category != "custom" and plan.task_type != t.category:
        plan.task_type = t.category  # the task author knows better than the planner
    if t.inputs.get("fields") and not plan.output_schema:
        plan.output_schema = {k: str(v) for k, v in t.inputs["fields"].items()}
    if t.inputs.get("command") and not plan.commands:
        plan.commands = [t.inputs["command"]]
    state.plan = plan
    steps = "; ".join(f"{s.id}.{s.node}: {s.description}" for s in plan.steps)
    state.summaries.plan = rt.ctx.summarize(
        f"type={plan.task_type}, code_change={plan.needs_code_change}, execution={plan.needs_execution}; "
        f"steps: {steps}; commands: {plan.commands}; check: {plan.success_check}"
    )
    return NodeResult(node=Node.PLAN, status="ok", message=state.summaries.plan[:300],
                      output={"plan": plan.model_dump()})


@_timed
def retrieve_node(state: TaskState, rt: NodeRuntime) -> NodeResult:
    t = state.task
    user = P.RETRIEVE_INSTRUCTIONS.format(
        plan_summary=state.summaries.plan, goal=t.goal, criteria=t.success_criteria or "(none)", inputs=_inputs(state),
        fields=_fields(state),
    )
    allowed = [n for n in ("read_pdf", "inspect_repo") if n in rt.tools.names()]
    outcome = rt.tool_loop(system=P.SYSTEM_BASE, user=user, allowed_tools=allowed, final_schema=Findings)
    if outcome.final is None:
        return _apply_loop_failure(rt, outcome, "retrieve did not finish")
    f: Findings = outcome.final  # type: ignore[assignment]
    state.findings = f.model_dump()
    state.summaries.retrieve = rt.ctx.summarize(
        f.summary + (f" | extracted: {json.dumps(f.extracted, ensure_ascii=False)}" if f.extracted else "")
        + (f" | files: {', '.join(f.relevant_files[:8])}" if f.relevant_files else "")
    )
    return NodeResult(node=Node.RETRIEVE, status="ok", message=f.summary[:300], output={"findings": state.findings})


@_timed
def implement_node(state: TaskState, rt: NodeRuntime) -> NodeResult:
    t = state.task
    plan = state.plan
    user = P.IMPLEMENT_INSTRUCTIONS.format(
        plan_summary=state.summaries.plan, findings_summary=state.summaries.retrieve or "(none)", goal=t.goal,
        criteria=t.success_criteria or "(none)", targets=plan.target_files if plan else [],
        commands=plan.commands if plan else [],
    )
    allowed = [n for n in ("inspect_repo", "patch_file", "read_pdf") if n in rt.tools.names()]
    outcome = rt.tool_loop(system=P.SYSTEM_BASE, user=user, allowed_tools=allowed, final_schema=ImplementResult,
                           tool_extra={"allow_test_edits": bool(t.inputs.get("allow_test_edits"))})
    changed = _record_changes(state, outcome)
    if outcome.final is None:
        return _apply_loop_failure(rt, outcome, "implement did not finish")
    r: ImplementResult = outcome.final  # type: ignore[assignment]
    if r.commands and plan is not None:
        plan.commands = r.commands
    state.summaries.implement = rt.ctx.summarize(f"{r.summary} | changed: {', '.join(changed) or 'nothing'}")
    return NodeResult(node=Node.IMPLEMENT, status="ok", message=r.summary[:300],
                      output={"files_changed": changed, "commands": plan.commands if plan else []})


@_timed
def execute_node(state: TaskState, rt: NodeRuntime) -> NodeResult:
    """No model call: run the planned commands through the tools and classify."""
    plan = state.plan
    commands = list(plan.commands) if plan and plan.commands else []
    if not commands and state.task.category == "bug_fix":
        commands = ["python -m pytest -q --no-header -p no:cacheprovider"]
    if not commands:
        return NodeResult(node=Node.EXECUTE, status="skip", message="nothing to execute")
    results: list[dict[str, Any]] = []
    failure: Diagnosis | None = None
    for cmd in commands:
        is_test = "pytest" in cmd or "unittest" in cmd
        tool = "run_tests" if is_test else "run_experiment"
        res: ToolResult = rt.call_tool(tool, {"command": cmd})
        results.append({"command": cmd, "tool": tool, "status": res.status, "exit_code": res.data.get("exit_code"),
                        "stdout": res.stdout[-4000:], "stderr": res.stderr[-4000:], "data": {k: v for k, v in res.data.items() if k != "diff"}})
        if res.status != "ok":
            ex = ExecResult(exit_code=res.data.get("exit_code", 1) if res.status != "timeout" else 124,
                            stdout=res.stdout, stderr=res.stderr, duration_s=res.duration_s,
                            timed_out=res.status == "timeout",
                            blocked_reason=res.stderr if res.status == "blocked" else None, command=cmd)
            failure = classify_exec(ex) or Diagnosis(FailureKind.NONZERO_EXIT, res.next_hint or "command failed")
            if res.data.get("failing_tests"):
                failure.failing_tests = res.data["failing_tests"]
                failure.kind = FailureKind.TEST_FAILURE
            break
    state.exec_results.append({"attempt": len(state.exec_results) + 1, "results": results})
    tail = "; ".join(f"{r['command']!r} -> {r['status']}" for r in results)
    state.summaries.execute = rt.ctx.summarize(tail)
    if failure is not None:
        detail = {"kind": failure.kind.value, "evidence": failure.evidence, "file": failure.file, "line": failure.line,
                  "failing_tests": failure.failing_tests, "exception": failure.exception_type}
        return NodeResult(node=Node.EXECUTE, status="fail", failure=failure.kind,
                          message=f"{failure.kind.value}: {failure.evidence}"[:500],
                          output={"results": results, "diagnosis": detail})
    return NodeResult(node=Node.EXECUTE, status="ok", message=tail[:300], output={"results": results})


def _deterministic_checks(state: TaskState, rt: NodeRuntime) -> tuple[bool, list[str], FailureKind | None]:
    """Things we can check without a model.  Returns (all_ok, notes, failure)."""
    notes: list[str] = []
    ok = True
    kind: FailureKind | None = None
    for name in state.task.expected_outputs:
        p = rt.workdir / name
        a = rt.artifacts_dir / name
        if p.exists() or a.exists():
            notes.append(f"expected output {name}: present")
        else:
            notes.append(f"expected output {name}: MISSING")
            ok, kind = False, FailureKind.MISSING_ARTIFACT
    if state.exec_results:
        last = state.exec_results[-1]["results"]
        for r in last:
            if r["status"] != "ok":
                notes.append(f"command {r['command']!r} status={r['status']}")
                ok = False
                kind = kind or FailureKind.NONZERO_EXIT
            elif r["tool"] == "run_tests":
                notes.append(f"tests: {r['data'].get('passed', 0)} passed, {r['data'].get('failed', 0)} failed")
    if state.plan and state.plan.output_schema:
        extracted = (state.findings or {}).get("extracted") or {}
        missing = [k for k in state.plan.output_schema if extracted.get(k) in (None, "", [], {})]
        if missing:
            notes.append(f"extracted fields missing/null: {missing}")
        else:
            notes.append("all requested fields extracted")
    return ok, notes, kind


@_timed
def verify_node(state: TaskState, rt: NodeRuntime) -> NodeResult:
    t = state.task
    det_ok, notes, kind = _deterministic_checks(state, rt)
    exec_tail = ""
    if state.exec_results:
        for r in state.exec_results[-1]["results"]:
            exec_tail += f"\n$ {r['command']} -> {r['status']}\n{r['stdout'][-1500:]}\n{r['stderr'][-800:]}"
    findings = json.dumps({k: v for k, v in (state.findings or {}).items() if k != "evidence"}, ensure_ascii=False)[:6000]
    if not rt.cfg.enable_verify:
        answer = (state.findings or {}).get("extracted") or {}
        state.verify_result = {"passed": det_ok, "reason": "deterministic checks only", "checks": notes, "answer": answer}
        state.final_answer = answer
        return NodeResult(node=Node.VERIFY, status="ok" if det_ok else "fail", failure=None if det_ok else (kind or FailureKind.VERIFY_MISMATCH),
                          message="; ".join(notes)[:500], output=state.verify_result)
    user = P.VERIFY_INSTRUCTIONS.format(
        goal=t.goal, criteria=t.success_criteria or "(none)", success_check=state.plan.success_check if state.plan else "",
        det_checks="\n".join(notes) or "none", exec_tail=exec_tail or "(nothing executed)", findings=findings,
    )
    v = rt.structured([{"role": "system", "content": P.SYSTEM_BASE}, {"role": "user", "content": user}], Verdict)
    passed = v.passed and det_ok
    answer = v.answer or (state.findings or {}).get("extracted") or {}
    state.verify_result = {"passed": passed, "reason": v.reason, "missing": v.missing, "checks": notes, "answer": answer}
    state.final_answer = answer
    state.summaries.verify = rt.ctx.summarize(f"passed={passed}: {v.reason}" + (f" missing={v.missing}" if v.missing else ""))
    if passed:
        return NodeResult(node=Node.VERIFY, status="ok", message=v.reason[:300], output=state.verify_result)
    return NodeResult(node=Node.VERIFY, status="fail", failure=kind or FailureKind.VERIFY_MISMATCH,
                      message=(v.reason + (" | missing: " + ", ".join(v.missing) if v.missing else ""))[:500],
                      output=state.verify_result)


@_timed
def repair_node(state: TaskState, rt: NodeRuntime) -> NodeResult:
    t = state.task
    last_exec = next((h for h in reversed(state.history) if h.node in (Node.EXECUTE, Node.VERIFY, Node.IMPLEMENT) and h.status == "fail"), None)
    diag = (last_exec.output.get("diagnosis") if last_exec else None) or {}
    tail = ""
    if state.exec_results:
        for r in state.exec_results[-1]["results"]:
            if r["status"] != "ok":
                tail += f"$ {r['command']}\n{error_tail(ExecResult(r.get('exit_code') or 1, r['stdout'], r['stderr'], 0.0))}\n"
    if last_exec and last_exec.node == Node.VERIFY:
        tail += f"\nVERIFY said: {last_exec.message}\nchecks: {state.verify_result.get('checks')}"
    previous = [h.message for h in state.history if h.node == Node.REPAIR]
    user = P.REPAIR_INSTRUCTIONS.format(
        goal=t.goal, criteria=t.success_criteria or "(none)", commands=state.plan.commands if state.plan else [],
        kind=(state.last_failure.value if state.last_failure else "unknown"), evidence=diag.get("evidence") or state.last_failure_detail,
        location=f"{diag.get('file')}:{diag.get('line')}" if diag.get("file") else "unknown",
        failing_tests=diag.get("failing_tests") or "n/a", error_tail=tail[-5000:] or "(no output captured)",
        previous="\n".join(f"- {p}" for p in previous) or "none",
    )
    allowed = [n for n in ("inspect_repo", "patch_file", "read_pdf") if n in rt.tools.names()]
    outcome = rt.tool_loop(system=P.SYSTEM_BASE, user=user, allowed_tools=allowed, final_schema=RepairResult,
                           tool_extra={"allow_test_edits": bool(t.inputs.get("allow_test_edits"))})
    changed = _record_changes(state, outcome)
    state.repairs_used += 1
    if outcome.final is None:
        return _apply_loop_failure(rt, outcome, "repair did not finish")
    r: RepairResult = outcome.final  # type: ignore[assignment]
    if r.commands and state.plan is not None:
        state.plan.commands = r.commands
    state.summaries.repair = rt.ctx.summarize(f"#{state.repairs_used} {r.diagnosis} -> {r.fix_summary} (changed: {', '.join(changed) or 'nothing'})")
    if not r.fixable:
        return NodeResult(node=Node.REPAIR, status="fail", failure=state.last_failure or FailureKind.UNKNOWN,
                          message=f"not fixable in sandbox: {r.diagnosis}"[:500], output={"repair": r.model_dump(), "files_changed": changed})
    if not changed and not r.commands:
        return NodeResult(node=Node.REPAIR, status="fail", failure=FailureKind.NO_PROGRESS,
                          message="repair made no change", output={"repair": r.model_dump()})
    return NodeResult(node=Node.REPAIR, status="ok", message=f"{r.diagnosis} -> {r.fix_summary}"[:300],
                      output={"repair": r.model_dump(), "files_changed": changed})


@_timed
def report_node(state: TaskState, rt: NodeRuntime) -> NodeResult:
    rt.artifacts_dir.mkdir(parents=True, exist_ok=True)
    succeeded = bool(state.verify_result.get("passed")) if state.verify_result else (
        state.plan is not None and not state.plan.needs_execution and not state.plan.needs_code_change and bool(state.findings)
    )
    if not state.final_answer:
        state.final_answer = (state.findings or {}).get("extracted") or {}
    summary_text = ""
    try:
        s = rt.structured([{"role": "user", "content": P.REPORT_SUMMARY_INSTRUCTIONS.format(
            goal=state.task.goal, status="success" if succeeded else "failed", plan=state.summaries.plan,
            findings=state.summaries.retrieve, changes=state.summaries.implement + " " + state.summaries.repair,
            execution=state.summaries.execute, verdict=state.summaries.verify)}], Summary, max_attempts=2)
        summary_text = s.summary
    except Exception as e:  # noqa: BLE001 - summary is optional
        summary_text = f"(summary unavailable: {type(e).__name__})"
    md = _render_report(state, succeeded, summary_text)
    report = rt.artifacts_dir / "report.md"
    report.write_text(md, encoding="utf-8")
    result = {
        "run_id": state.run_id, "task_id": state.task.task_id, "success": succeeded, "answer": state.final_answer,
        "verify": state.verify_result, "repairs": state.repairs_used, "changes": [c.path for c in state.changes],
        "last_failure": state.last_failure.value if state.last_failure and not succeeded else None,
        "steps": [h.node.value + ":" + h.status for h in state.history],
    }
    (rt.artifacts_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    state.report_path = str(report)
    state.artifacts.extend([str(report), str(rt.artifacts_dir / "result.json")])
    if rt.store:
        rt.store.add_artifact(state.run_id, state.step_idx, "report.md", str(report), "report")
        rt.store.add_artifact(state.run_id, state.step_idx, "result.json", str(rt.artifacts_dir / "result.json"), "result")
    return NodeResult(node=Node.REPORT, status="ok", message=("success" if succeeded else "failed") + ": " + summary_text[:200],
                      output={"success": succeeded, "report": str(report)})


def _render_report(state: TaskState, succeeded: bool, summary: str) -> str:
    t = state.task
    L: list[str] = [f"# ReproAgent report - {t.task_id}", "", f"**Status:** {'SUCCESS' if succeeded else 'FAILED'}  ",
                    f"**Run:** `{state.run_id}`  ", f"**Goal:** {t.goal}", "", "## Summary", "", summary, ""]
    if state.final_answer:
        L += ["## Answer", "", "```json", json.dumps(state.final_answer, ensure_ascii=False, indent=2, default=str), "```", ""]
    if state.plan:
        L += ["## Plan", "", f"- type: {state.plan.task_type}; code change: {state.plan.needs_code_change}; execution: {state.plan.needs_execution}"]
        L += [f"- {s.id}. **{s.node}** - {s.description}" for s in state.plan.steps]
        if state.plan.commands:
            L += ["- commands:"] + [f"  - `{c}`" for c in state.plan.commands]
        L.append("")
    if state.findings:
        L += ["## Findings", "", state.findings.get("summary", "")]
        if state.findings.get("evidence"):
            L += ["", "Evidence:"] + [f"- {e}" for e in state.findings["evidence"][:12]]
        L.append("")
    if state.changes:
        L += ["## Code changes", ""]
        for c in state.changes:
            L += [f"### {c.action} `{c.path}`", "", "```diff", c.diff[:6000], "```", ""]
    if state.exec_results:
        L += ["## Execution", ""]
        for att in state.exec_results:
            L.append(f"### Attempt {att['attempt']}")
            for r in att["results"]:
                L += ["", f"`$ {r['command']}` -> **{r['status']}** (exit {r.get('exit_code')})", "", "```", (r['stdout'] or '')[-1500:], (r['stderr'] or '')[-800:], "```"]
        L.append("")
    if state.verify_result:
        L += ["## Verification", "", f"- passed: {state.verify_result.get('passed')}", f"- reason: {state.verify_result.get('reason')}"]
        for c in state.verify_result.get("checks", []):
            L.append(f"- check: {c}")
        L.append("")
    L += ["## Trace", "", "| # | node | status | failure | tools | llm | tokens | s |", "|---|---|---|---|---|---|---|---|"]
    for i, h in enumerate(state.history):
        L.append(f"| {i} | {h.node.value} | {h.status} | {h.failure.value if h.failure else ''} | {h.tool_calls} | {h.llm_calls} | {h.tokens} | {h.duration_s:.1f} |")
    L += ["", f"Totals: {state.llm_calls_total} model calls, {state.tool_calls_total} tool calls, {state.tokens_total} tokens, "
              f"{state.repairs_used} repair round(s), cost ${state.cost_usd:.4f}.", ""]
    return "\n".join(L)


HANDLERS = {
    Node.PLAN: plan_node,
    Node.RETRIEVE: retrieve_node,
    Node.IMPLEMENT: implement_node,
    Node.EXECUTE: execute_node,
    Node.VERIFY: verify_node,
    Node.REPAIR: repair_node,
    Node.REPORT: report_node,
}
