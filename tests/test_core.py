from __future__ import annotations

from reproagent.config import AgentConfig
from reproagent.core.context import ContextManager, estimate_tokens
from reproagent.core.failures import classify_exec
from reproagent.core.orchestrator import next_node
from reproagent.core.state import FailureKind, Node, NodeResult, Plan, TaskSpec, TaskState
from reproagent.llm.client import MockLLMClient
from reproagent.llm.structured import StructuredOutputError, ask_structured, extract_json
from reproagent.sandbox.base import ExecResult
from pydantic import BaseModel


def _ex(code=1, out="", err="", to=False, blocked=None):
    return ExecResult(code, out, err, 0.1, timed_out=to, blocked_reason=blocked)


def test_classify_exec():
    assert classify_exec(_ex(0)) is None
    assert classify_exec(_ex(to=True)).kind == FailureKind.EXEC_TIMEOUT
    assert classify_exec(_ex(blocked="policy")).kind == FailureKind.POLICY_BLOCKED
    d = classify_exec(_ex(err='Traceback (most recent call last):\n  File "train.py", line 12, in <module>\n    import foo\nModuleNotFoundError: No module named \'foo\''))
    assert d.kind == FailureKind.IMPORT_ERROR and d.file == "train.py" and d.line == 12 and d.exception_type == "ModuleNotFoundError"
    d = classify_exec(_ex(err='  File "a.py", line 3\n    def f(:\nSyntaxError: invalid syntax'))
    assert d.kind == FailureKind.SYNTAX_ERROR
    d = classify_exec(_ex(out="FAILED tests/test_x.py::test_a - AssertionError\n1 failed, 2 passed in 0.1s"))
    assert d.kind == FailureKind.TEST_FAILURE and d.failing_tests == ["tests/test_x.py::test_a"]
    assert classify_exec(_ex(137)).kind == FailureKind.RESOURCE_LIMIT
    assert classify_exec(_ex(2, err="usage: bad args")).kind == FailureKind.NONZERO_EXIT


def _state(plan: Plan | None = None, repairs=0) -> TaskState:
    s = TaskState(task=TaskSpec(goal="g"))
    s.plan = plan
    s.repairs_used = repairs
    return s


def _plan(code=True, run=True) -> Plan:
    return Plan(task_type="bug_fix", needs_code_change=code, needs_execution=run, steps=[])


def test_transitions():
    cfg = AgentConfig()
    ok = lambda n: NodeResult(node=n, status="ok")  # noqa: E731
    fail = lambda n, k: NodeResult(node=n, status="fail", failure=k)  # noqa: E731
    assert next_node(_state(), ok(Node.PLAN), cfg) == Node.RETRIEVE
    assert next_node(_state(), fail(Node.PLAN, FailureKind.MODEL_OUTPUT_INVALID), cfg) == Node.FAILED
    assert next_node(_state(_plan()), ok(Node.RETRIEVE), cfg) == Node.IMPLEMENT
    assert next_node(_state(_plan(code=False)), ok(Node.RETRIEVE), cfg) == Node.EXECUTE
    assert next_node(_state(_plan(code=False, run=False)), ok(Node.RETRIEVE), cfg) == Node.VERIFY
    assert next_node(_state(_plan()), ok(Node.IMPLEMENT), cfg) == Node.EXECUTE
    assert next_node(_state(_plan()), fail(Node.EXECUTE, FailureKind.TEST_FAILURE), cfg) == Node.REPAIR
    # repair budget exhausted -> report, not another repair
    assert next_node(_state(_plan(), repairs=3), fail(Node.EXECUTE, FailureKind.TEST_FAILURE), cfg) == Node.REPORT
    # non-repairable failure -> report
    assert next_node(_state(_plan()), fail(Node.EXECUTE, FailureKind.POLICY_BLOCKED), cfg) == Node.REPORT
    assert next_node(_state(_plan()), ok(Node.VERIFY), cfg) == Node.REPORT
    assert next_node(_state(_plan()), fail(Node.VERIFY, FailureKind.VERIFY_MISMATCH), cfg) == Node.REPAIR
    assert next_node(_state(_plan()), ok(Node.REPAIR), cfg) == Node.EXECUTE
    assert next_node(_state(_plan()), fail(Node.REPAIR, FailureKind.NO_PROGRESS), cfg) == Node.REPORT
    assert next_node(_state(), ok(Node.REPORT), cfg) == Node.DONE
    no_repair = AgentConfig(budget={"max_repairs": 0})
    assert next_node(_state(_plan()), fail(Node.EXECUTE, FailureKind.TEST_FAILURE), no_repair) == Node.REPORT


def test_state_roundtrip():
    s = _state(_plan())
    s.record(NodeResult(node=Node.PLAN, status="ok", tool_calls=2, llm_calls=1, tokens=100))
    s.record(NodeResult(node=Node.EXECUTE, status="fail", failure=FailureKind.TEST_FAILURE, message="boom"))
    raw = s.model_dump(mode="json")
    s2 = TaskState.model_validate(raw)
    assert s2.step_idx == 2 and s2.tool_calls_total == 2 and s2.last_failure == FailureKind.TEST_FAILURE and s2.plan.task_type == "bug_fix"


class Out(BaseModel):
    x: int
    y: str


def test_ask_structured_repairs_invalid_output():
    m = MockLLMClient(["not json at all", '{"x": "notint", "y": "a"}', '```json\n{"x": 3, "y": "ok"}\n```'])
    out = ask_structured(m, [{"role": "user", "content": "go"}], Out, max_attempts=3)
    assert out.x == 3 and len(m.calls) == 3
    assert "not valid" in m.calls[2]["messages"][-1]["content"]
    m2 = MockLLMClient(["nope", "nope", "nope"])
    try:
        ask_structured(m2, [{"role": "user", "content": "go"}], Out, max_attempts=3)
        raise AssertionError("expected failure")
    except StructuredOutputError as e:
        assert "3 attempts" in str(e)


def test_extract_json_variants():
    assert extract_json('prefix {"a": 1} suffix') == {"a": 1}
    assert extract_json('```json\n{"a": [1,2]}\n```') == {"a": [1, 2]}


def test_context_compaction_folds_old_tool_outputs():
    cm = ContextManager(AgentConfig().context.model_copy(update={"compaction_threshold_tokens": 100, "keep_last_messages": 2}))
    msgs = [{"role": "system", "content": "s"}]
    for i in range(6):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i)}]})
        msgs.append({"role": "tool", "tool_call_id": str(i), "content": f"[status=ok] result {i} " + "x" * 300})
    before = estimate_tokens(msgs)
    out = cm.maybe_compact(msgs)
    after = estimate_tokens(out)
    assert after < before and cm.compactions == 1
    assert out[-1]["content"].endswith("x" * 10)  # last kept verbatim
    assert "folded" in out[2]["content"] and out[2]["tool_call_id"] == "0"  # pairing preserved
    assert all(not k.startswith("_") for m in ContextManager.strip_private(out) for k in m)
