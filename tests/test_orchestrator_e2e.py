"""End-to-end runs of the state machine against the tiny buggy repo with a scripted model."""

from __future__ import annotations

from pathlib import Path

from reproagent.config import AgentConfig
from reproagent.core.orchestrator import Orchestrator
from reproagent.core.state import FailureKind, Node, TaskSpec
from reproagent.llm.client import MockLLMClient
from reproagent.sandbox.local import LocalSandbox
from reproagent.store.trace import TraceStore
from reproagent.tools import default_registry

from .conftest import PLAN_BUGFIX, SUMMARY, VERDICT_OK, finish, js, tc

TASK = TaskSpec(task_id="calc-fix", goal="Make the test suite pass by fixing calc.py", category="bug_fix",
                success_criteria="pytest exits 0")


def _orch(cfg: AgentConfig, llm: MockLLMClient, repo: Path, store: TraceStore, tmp_path: Path) -> Orchestrator:
    return Orchestrator(cfg=cfg, llm=llm, tools=default_registry(), sandbox=LocalSandbox(repo, memory_limit="4g"), store=store,
                        workdir=repo, artifacts_dir=tmp_path / "artifacts")


def _good_patch():
    return tc("patch_file", path="calc.py", mode="replace", old="return a - b", new="return a + b")


def _bad_patch():
    return tc("patch_file", path="calc.py", mode="replace", old="return a - b", new="return a * 0 + b - a")


def test_happy_path_no_repair(cfg, repo, store, tmp_path):
    llm = MockLLMClient([
        PLAN_BUGFIX,                                                      # PLAN
        tc("inspect_repo", action="read", path="calc.py"),                 # RETRIEVE: look
        finish(summary="add subtracts", key_facts={"bug": "a - b"}, extracted={}, relevant_files=["calc.py"], evidence=["calc.py:5"]),
        _good_patch(),                                                    # IMPLEMENT
        finish(summary="fixed add", files_changed=["calc.py"], commands=["python -m pytest -q --no-header -p no:cacheprovider"]),
        VERDICT_OK,                                                       # VERIFY
        SUMMARY,                                                          # REPORT
    ])
    st = _orch(cfg, llm, repo, store, tmp_path).start(TASK)
    assert st.status == "done" and st.repairs_used == 0
    assert [h.node for h in st.history] == [Node.PLAN, Node.RETRIEVE, Node.IMPLEMENT, Node.EXECUTE, Node.VERIFY, Node.REPORT]
    assert st.final_answer == {"tests_passed": 2}
    assert Path(st.report_path).exists() and "SUCCESS" in Path(st.report_path).read_text()
    assert len(st.changes) == 1 and st.changes[0].path == "calc.py"
    # trace store has everything
    m = store.run_metrics(st.run_id)
    assert m["steps"] == 6 and m["tool_calls"] == 3 and m["llm_calls"] == 7
    assert store.get_run(st.run_id)["status"] == "done"


def test_repair_path(cfg, repo, store, tmp_path):
    llm = MockLLMClient([
        PLAN_BUGFIX,
        finish(summary="read", key_facts={}, extracted={}, relevant_files=["calc.py"], evidence=[]),   # RETRIEVE (no tool use)
        _bad_patch(),                                                                                  # IMPLEMENT: wrong fix
        finish(summary="tried", files_changed=["calc.py"], commands=[]),
        # EXECUTE fails -> REPAIR
        tc("inspect_repo", action="read", path="calc.py"),
        tc("patch_file", path="calc.py", mode="replace", old="return a * 0 + b - a", new="return a + b"),
        finish(diagnosis="operator wrong", fix_summary="use +", files_changed=["calc.py"], fixable=True, commands=[]),
        VERDICT_OK,
        SUMMARY,
    ])
    st = _orch(cfg, llm, repo, store, tmp_path).start(TASK)
    assert st.status == "done" and st.repairs_used == 1
    nodes = [h.node for h in st.history]
    assert nodes == [Node.PLAN, Node.RETRIEVE, Node.IMPLEMENT, Node.EXECUTE, Node.REPAIR, Node.EXECUTE, Node.VERIFY, Node.REPORT]
    exec_fail = st.history[3]
    assert exec_fail.status == "fail" and exec_fail.failure == FailureKind.TEST_FAILURE
    assert exec_fail.output["diagnosis"]["failing_tests"] == ["tests/test_calc.py::test_add"]
    assert len(st.exec_results) == 2


def test_no_repair_ablation_reports_failure(repo, store, tmp_path):
    cfg = AgentConfig(budget={"max_repairs": 0, "node_timeout_s": 60}, sandbox={"memory_limit": "4g"})
    llm = MockLLMClient([
        PLAN_BUGFIX,
        finish(summary="read", key_facts={}, extracted={}, relevant_files=[], evidence=[]),
        _bad_patch(),
        finish(summary="tried", files_changed=["calc.py"], commands=[]),
        SUMMARY,  # straight to REPORT
    ])
    st = _orch(cfg, llm, repo, store, tmp_path).start(TASK)
    assert st.status == "failed" and st.repairs_used == 0
    assert [h.node for h in st.history] == [Node.PLAN, Node.RETRIEVE, Node.IMPLEMENT, Node.EXECUTE, Node.REPORT]
    assert st.last_failure == FailureKind.TEST_FAILURE
    assert "FAILED" in Path(st.report_path).read_text()


def test_budget_and_no_progress(cfg, repo, store, tmp_path):
    # model keeps repeating the same tool call in RETRIEVE -> NO_PROGRESS -> FAILED (no findings)
    same = tc("inspect_repo", action="tree")
    llm = MockLLMClient([PLAN_BUGFIX, same, same, same, SUMMARY])
    st = _orch(cfg, llm, repo, store, tmp_path).start(TASK)
    assert st.history[1].failure == FailureKind.NO_PROGRESS
    assert st.status == "failed"


def test_resume_from_checkpoint(cfg, repo, store, tmp_path):
    # First run: model errors out (exhausted) inside IMPLEMENT after PLAN and RETRIEVE checkpointed.
    llm = MockLLMClient([PLAN_BUGFIX, finish(summary="read", key_facts={}, extracted={}, relevant_files=[], evidence=[])])
    o = _orch(cfg, llm, repo, store, tmp_path)
    st = o.start(TASK)
    assert st.history[2].node == Node.IMPLEMENT and st.history[2].status == "fail"
    assert st.history[2].failure == FailureKind.LLM_ERROR
    # run ended (REPAIR of LLM_ERROR is not allowed -> REPORT -> DONE with failed status)
    # Now simulate a crash instead: take the checkpoint after RETRIEVE and resume from there.
    raw = store._conn.execute("SELECT state_json FROM steps WHERE run_id=? AND idx=1", (st.run_id,)).fetchone()[0]
    store._conn.execute("DELETE FROM steps WHERE run_id=? AND idx>1", (st.run_id,))
    store._conn.execute("UPDATE steps SET state_json=? WHERE run_id=? AND idx=1", (raw.replace('"status":"failed"', '"status":"running"').replace('"status": "failed"', '"status": "running"'), st.run_id))
    store._conn.commit()
    llm2 = MockLLMClient([
        _good_patch(), finish(summary="fixed", files_changed=["calc.py"], commands=["python -m pytest -q --no-header -p no:cacheprovider"]),
        VERDICT_OK, SUMMARY,
    ])
    o2 = _orch(cfg, llm2, repo, store, tmp_path)
    st2 = o2.resume(st.run_id)
    assert st2.status == "done" and st2.run_id == st.run_id
    assert [h.node for h in st2.history] == [Node.PLAN, Node.RETRIEVE, Node.IMPLEMENT, Node.EXECUTE, Node.VERIFY, Node.REPORT]


def test_extraction_task_skips_code_nodes(cfg, repo, store, tmp_path):
    (repo / "notes.md").write_text("Dataset: ETH/UCY\nMetric: ADE/FDE\n")
    task = TaskSpec(task_id="ex", goal="Which dataset and metric are named in notes.md?", category="repo_locate",
                    inputs={"fields": {"dataset": "dataset name", "metric": "metric name"}})
    plan = js({"task_type": "repo_locate", "needs_code_change": False, "needs_execution": False,
               "steps": [{"id": 1, "node": "retrieve", "description": "read notes"}], "commands": [], "target_files": ["notes.md"],
               "success_check": "fields filled", "output_schema": {"dataset": "name", "metric": "name"}})
    llm = MockLLMClient([
        plan,
        tc("inspect_repo", action="read", path="notes.md"),
        finish(summary="found", key_facts={}, extracted={"dataset": "ETH/UCY", "metric": "ADE/FDE"}, relevant_files=["notes.md"], evidence=["notes.md:1"]),
        js({"passed": True, "reason": "fields present", "missing": [], "answer": {"dataset": "ETH/UCY", "metric": "ADE/FDE"}}),
        SUMMARY,
    ])
    st = _orch(cfg, llm, repo, store, tmp_path).start(task)
    assert st.status == "done"
    assert [h.node for h in st.history] == [Node.PLAN, Node.RETRIEVE, Node.VERIFY, Node.REPORT]
    assert st.final_answer["dataset"] == "ETH/UCY"


def test_free_text_tool_protocol(repo, store, tmp_path):
    cfg = AgentConfig(structured_tools=False, budget={"node_timeout_s": 60}, sandbox={"memory_limit": "4g"})
    llm = MockLLMClient([
        PLAN_BUGFIX,
        '```tool\n{"name": "inspect_repo", "args": {"action": "read", "path": "calc.py"}}\n```',
        '```final\n{"summary": "read", "key_facts": {}, "extracted": {}, "relevant_files": ["calc.py"], "evidence": []}\n```',
        '```tool\n{"name": "patch_file", "args": {"path": "calc.py", "mode": "replace", "old": "return a - b", "new": "return a + b"}}\n```',
        '```final\n{"summary": "fixed", "files_changed": ["calc.py"], "commands": ["python -m pytest -q --no-header -p no:cacheprovider"]}\n```',
        VERDICT_OK, SUMMARY,
    ])
    st = _orch(cfg, llm, repo, store, tmp_path).start(TASK)
    assert st.status == "done" and st.tool_calls_total == 3
