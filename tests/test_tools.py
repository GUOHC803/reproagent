from __future__ import annotations

from pathlib import Path

from reproagent.tools.base import ToolContext, ToolRegistry


def test_schema_export(registry: ToolRegistry):
    s = registry.schemas()
    names = {x["function"]["name"] for x in s}
    assert names == {"read_pdf", "inspect_repo", "patch_file", "run_tests", "run_experiment", "save_artifact"}
    pf = next(x for x in s if x["function"]["name"] == "patch_file")
    assert "path" in pf["function"]["parameters"]["properties"]


def test_invalid_args_become_error_result(registry: ToolRegistry, tool_ctx: ToolContext):
    r = registry.call("inspect_repo", {"action": "grep"}, tool_ctx)  # grep without pattern
    assert r.status == "error" and "invalid arguments" in r.stderr and r.next_hint
    r = registry.call("nope", {}, tool_ctx)
    assert r.status == "error" and "unknown tool" in r.stderr


def test_inspect_repo(registry: ToolRegistry, tool_ctx: ToolContext):
    r = registry.call("inspect_repo", {"action": "tree"}, tool_ctx)
    assert r.status == "ok" and "calc.py" in r.stdout and "tests/" in r.stdout
    r = registry.call("inspect_repo", {"action": "grep", "pattern": "def add"}, tool_ctx)
    assert r.status == "ok" and r.stdout.startswith("calc.py:4:")
    r = registry.call("inspect_repo", {"action": "read", "path": "calc.py", "start_line": 4, "end_line": 5}, tool_ctx)
    assert r.status == "ok" and "return a - b" in r.stdout
    r = registry.call("inspect_repo", {"action": "read", "path": "../outside.py"}, tool_ctx)
    assert r.status == "blocked"


def test_patch_file_replace_and_protect(registry: ToolRegistry, tool_ctx: ToolContext, repo: Path):
    r = registry.call("patch_file", {"path": "calc.py", "mode": "replace", "old": "return a - b", "new": "return a + b"}, tool_ctx)
    assert r.status == "ok" and "-    return a - b" in r.data["diff"] and "+    return a + b" in r.data["diff"]
    assert "return a + b" in (repo / "calc.py").read_text()
    r = registry.call("patch_file", {"path": "calc.py", "mode": "replace", "old": "nonexistent", "new": "x"}, tool_ctx)
    assert r.status == "error" and "not found" in r.stderr
    r = registry.call("patch_file", {"path": "tests/test_calc.py", "mode": "create", "content": "x"}, tool_ctx)
    assert r.status == "blocked"
    r = registry.call("patch_file", {"path": "new/mod.py", "mode": "create", "content": "X = 1\n"}, tool_ctx)
    assert r.status == "ok" and (repo / "new" / "mod.py").exists()


def test_run_tests_parses_failures_then_passes(registry: ToolRegistry, tool_ctx: ToolContext):
    r = registry.call("run_tests", {}, tool_ctx)
    assert r.status == "error" and r.data["failed"] == 1 and r.data["failing_tests"] == ["tests/test_calc.py::test_add"]
    assert "test_add" in (r.next_hint or "")
    registry.call("patch_file", {"path": "calc.py", "mode": "replace", "old": "a - b", "new": "a + b"}, tool_ctx)
    r = registry.call("run_tests", {"command": "python -m pytest -q --no-header -p no:cacheprovider"}, tool_ctx)
    assert r.status == "ok" and r.data["passed"] == 2 and r.data["failed"] == 0


def test_run_experiment_and_artifacts(registry: ToolRegistry, tool_ctx: ToolContext, repo: Path):
    r = registry.call("run_experiment", {"command": "python -c \"import json; json.dump({'loss': 0.5}, open('out.json','w'))\""}, tool_ctx)
    assert r.status == "ok" and "out.json" in r.data["new_files"]
    r = registry.call("run_experiment", {"command": "python -c 'import nosuchmod'"}, tool_ctx)
    assert r.status == "error" and r.data["failure_kind"] == "import_error"
    r = registry.call("save_artifact", {"name": "out.json", "src_path": "out.json"}, tool_ctx)
    assert r.status == "ok" and Path(r.data["path"]).exists()
    r = registry.call("save_artifact", {"name": "note.txt", "content": "hi"}, tool_ctx)
    assert r.status == "ok"


def test_read_pdf(registry: ToolRegistry, tool_ctx: ToolContext, repo: Path):
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    with (repo / "blank.pdf").open("wb") as f:
        w.write(f)
    r = registry.call("read_pdf", {"path": "blank.pdf"}, tool_ctx)
    assert r.status == "ok" and r.data["pages"] == 1
    r = registry.call("read_pdf", {"path": "missing.pdf"}, tool_ctx)
    assert r.status == "error"
