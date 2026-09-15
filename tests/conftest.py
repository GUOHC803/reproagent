from __future__ import annotations

import json
from pathlib import Path

import pytest

from reproagent.config import AgentConfig
from reproagent.llm.client import LLMResponse, MockLLMClient, ToolCall
from reproagent.sandbox.local import LocalSandbox
from reproagent.store.trace import TraceStore
from reproagent.tools import default_registry
from reproagent.tools.base import ToolContext

BUGGY_CALC = '''"""Tiny calculator with a deliberate bug."""


def add(a, b):
    return a - b  # BUG: should be a + b


def mul(a, b):
    return a * b
'''

TEST_CALC = '''from calc import add, mul


def test_add():
    assert add(2, 3) == 5


def test_mul():
    assert mul(2, 3) == 6
'''


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    (work / "calc.py").write_text(BUGGY_CALC)
    (work / "tests" / "test_calc.py").write_text(TEST_CALC)
    (work / "conftest.py").write_text("import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n")
    (work / "README.md").write_text("# calc\nRun `python -m pytest -q`.\n")
    return work


@pytest.fixture
def sandbox(repo: Path) -> LocalSandbox:
    return LocalSandbox(repo, memory_limit="4g")


@pytest.fixture
def tool_ctx(repo: Path, sandbox: LocalSandbox, tmp_path: Path) -> ToolContext:
    return ToolContext(workdir=repo, artifacts_dir=tmp_path / "artifacts", sandbox=sandbox, run_id="t", default_timeout_s=60)


@pytest.fixture
def registry():
    return default_registry()


@pytest.fixture
def store(tmp_path: Path) -> TraceStore:
    return TraceStore(tmp_path / "trace.sqlite3")


@pytest.fixture
def cfg() -> AgentConfig:
    return AgentConfig(sandbox={"memory_limit": "4g"}, budget={"node_timeout_s": 60, "tool_timeout_s": 60})


# ---- helpers to script the mock model -----------------------------------


def tc(name: str, **args) -> LLMResponse:
    return LLMResponse(content="", tool_calls=[ToolCall(id=f"c-{name}", name=name, arguments=args)], prompt_tokens=10, completion_tokens=5, model="mock")


def finish(**args) -> LLMResponse:
    return tc("finish", **args)


def js(obj) -> str:
    return json.dumps(obj)


PLAN_BUGFIX = js({
    "task_type": "bug_fix", "needs_code_change": True, "needs_execution": True,
    "steps": [{"id": 1, "node": "retrieve", "description": "read calc.py"},
              {"id": 2, "node": "implement", "description": "fix add"},
              {"id": 3, "node": "execute", "description": "run tests"},
              {"id": 4, "node": "verify", "description": "check tests pass"}],
    "commands": ["python -m pytest -q --no-header -p no:cacheprovider"], "target_files": ["calc.py"],
    "success_check": "pytest exits 0", "output_schema": {},
})

VERDICT_OK = js({"passed": True, "reason": "all tests pass", "missing": [], "answer": {"tests_passed": 2}})
VERDICT_BAD = js({"passed": False, "reason": "tests fail", "missing": ["test_add"], "answer": {}})
SUMMARY = js({"summary": "Fixed add() and tests pass."})


def make_mock(*responses) -> MockLLMClient:
    return MockLLMClient(list(responses))
