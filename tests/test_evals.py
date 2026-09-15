from __future__ import annotations

from pathlib import Path

from reproagent.evals.checkers import check_fields, match_value, run_checks
from reproagent.evals.report import render_markdown, summarize
from reproagent.evals.runner import load_tasks, prepare_source

ROOT = Path(__file__).resolve().parents[1]


def test_match_value():
    assert match_value(8, "8 P100 GPUs")
    assert match_value(100000, "100,000 steps")
    assert match_value(0.1, 0.1) and not match_value(0.1, 0.3)
    assert match_value(1280000, "1.28 million")
    assert match_value("GLUE", "the GLUE benchmark")
    assert match_value("35MB", "35 MB") and not match_value("35MB", "350GB")
    assert match_value(["pre-training", "post-training"], ["Pre-training", "Post-training"])
    assert match_value(False, "false") and match_value(False, False) and not match_value(False, True)
    assert not match_value(28.4, None)


def test_check_fields_details():
    r = check_fields({"a": 1, "b": "x"}, {"a": "1", "B": "xyz"})
    assert r.passed and len(r.details) == 2


def test_tasks_load_and_injections_apply():
    tasks = load_tasks(ROOT / "evals" / "tasks")
    assert len(tasks) >= 15
    cats = {t["category"] for t in tasks}
    assert cats == {"paper_extraction", "repo_locate", "repo_run", "bug_fix"}
    for t in tasks:
        assert t["_fixture"].is_dir(), t["task_id"]
        if t.get("inject"):
            src = prepare_source(t)
            text = (src / t["inject"]["file"]).read_text()
            assert t["inject"]["new"] in text and t["inject"]["old"] not in text


def test_tests_pass_checker_on_fixture(tmp_path):
    tasks = {t["task_id"]: t for t in load_tasks(ROOT / "evals" / "tasks")}
    t = tasks["bugfix_ngram_offbyone"]
    broken = prepare_source(t)
    r = run_checks(t, workdir=broken, fixture_dir=t["_fixture"], answer={})
    assert not r.passed and any("exit=1" in d for d in r.details)
    fixed = prepare_source({**t, "inject": None})
    r = run_checks(t, workdir=fixed, fixture_dir=t["_fixture"], answer={})
    assert r.passed, r.details
    # touching a test file fails the check even if tests pass
    (fixed / "tests" / "test_stats.py").write_text("def test_x():\n    pass\n")
    assert not run_checks(t, workdir=fixed, fixture_dir=t["_fixture"], answer={}).passed


def test_reference_checkers(tmp_path):
    tasks = {t["task_id"]: t for t in load_tasks(ROOT / "evals" / "tasks")}
    t = tasks["run_pi05_loss_min"]
    work = prepare_source(t)
    import csv, json  # noqa: E401

    best = {}
    for r in csv.DictReader((work / "results" / "loss_curves.csv").open()):
        v = float(r["loss"])
        if r["run"] not in best or v < best[r["run"]]["min_loss"]:
            best[r["run"]] = {"min_loss": v, "step": int(r["step"])}
    (work / "summary.json").write_text(json.dumps(best))
    k = min(best, key=lambda x: best[x]["min_loss"])
    r = run_checks(t, workdir=work, fixture_dir=t["_fixture"], answer={"best_run": k, "best_min_loss": best[k]["min_loss"]})
    assert r.passed, r.details
    r = run_checks(t, workdir=work, fixture_dir=t["_fixture"], answer={"best_run": k, "best_min_loss": 9.9})
    assert not r.passed


def test_summary_rendering():
    rows = [
        {"task_id": "a", "category": "bug_fix", "config": "full", "success": True, "repairs": 1, "tool_calls": 5, "llm_calls": 4, "tokens": 100, "cost_usd": 0.01, "wall_time_s": 3, "agent_claimed_success": True, "failures_seen": ["test_failure"]},
        {"task_id": "a", "category": "bug_fix", "config": "no_repair", "success": False, "repairs": 0, "tool_calls": 3, "llm_calls": 3, "tokens": 80, "cost_usd": 0.01, "wall_time_s": 2, "agent_claimed_success": False, "last_failure": "test_failure", "failures_seen": ["test_failure"]},
    ]
    s = summarize(rows)
    assert s["configs"]["full"]["success_rate"] == 1.0 and s["configs"]["no_repair"]["failure_kinds"] == {"test_failure": 1}
    md = render_markdown(s, rows, {"model": "m", "tasks": ["a"], "repeats": 1, "timestamp": "t"})
    assert "| full |" in md and "✅" in md and "❌" in md
