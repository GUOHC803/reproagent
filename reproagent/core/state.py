"""Task state: the single object that flows through the state machine.

It is a plain Pydantic model so it can be serialized after *every* node
(checkpoint) and reloaded for ``resume``.  Nothing in the orchestrator or the
nodes keeps hidden state outside of it - that is what makes replay and
resume possible.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Node(str, Enum):
    PLAN = "plan"
    RETRIEVE = "retrieve"
    IMPLEMENT = "implement"
    EXECUTE = "execute"
    VERIFY = "verify"
    REPAIR = "repair"
    REPORT = "report"
    DONE = "done"
    FAILED = "failed"


TERMINAL = {Node.DONE, Node.FAILED}


class FailureKind(str, Enum):
    """Failure taxonomy.  Every non-ok node result carries exactly one of these.

    The taxonomy is what the REPAIR node conditions on, what the evaluation
    reports as a distribution, and what a human reads first when a run fails.
    """

    # --- execution-side (things the sandbox reported) ---
    SYNTAX_ERROR = "syntax_error"            # SyntaxError / IndentationError
    IMPORT_ERROR = "import_error"            # ModuleNotFoundError / ImportError
    FILE_NOT_FOUND = "file_not_found"        # FileNotFoundError / No such file
    RUNTIME_ERROR = "runtime_error"          # any other uncaught exception
    TEST_FAILURE = "test_failure"            # tests ran, some failed
    ASSERTION_ERROR = "assertion_error"      # AssertionError outside pytest
    EXEC_TIMEOUT = "exec_timeout"            # sandbox wall-clock timeout
    RESOURCE_LIMIT = "resource_limit"        # MemoryError / killed / rlimit
    POLICY_BLOCKED = "policy_blocked"        # command denied by sandbox policy
    NONZERO_EXIT = "nonzero_exit"            # exit != 0 without recognisable traceback
    # --- model-side ---
    MODEL_OUTPUT_INVALID = "model_output_invalid"  # JSON / schema validation failed
    TOOL_ARGS_INVALID = "tool_args_invalid"        # model called a tool with bad args
    TOOL_ERROR = "tool_error"                      # tool raised / unknown tool
    NO_PROGRESS = "no_progress"                    # model looped without new actions
    # --- verification-side ---
    VERIFY_MISMATCH = "verify_mismatch"      # ran fine, but output does not meet criteria
    MISSING_ARTIFACT = "missing_artifact"    # expected output file not produced
    # --- budget / infra ---
    BUDGET_EXHAUSTED = "budget_exhausted"
    NODE_TIMEOUT = "node_timeout"
    LLM_ERROR = "llm_error"
    UNKNOWN = "unknown"


REPAIRABLE = {
    FailureKind.SYNTAX_ERROR,
    FailureKind.IMPORT_ERROR,
    FailureKind.FILE_NOT_FOUND,
    FailureKind.RUNTIME_ERROR,
    FailureKind.TEST_FAILURE,
    FailureKind.ASSERTION_ERROR,
    FailureKind.EXEC_TIMEOUT,
    FailureKind.NONZERO_EXIT,
    FailureKind.VERIFY_MISMATCH,
    FailureKind.MISSING_ARTIFACT,
    FailureKind.TOOL_ARGS_INVALID,
    FailureKind.TOOL_ERROR,
    FailureKind.NO_PROGRESS,
    FailureKind.MODEL_OUTPUT_INVALID,
}


class TaskSpec(BaseModel):
    task_id: str = Field(default_factory=lambda: "task-" + uuid.uuid4().hex[:8])
    goal: str = Field(..., description="Natural-language objective.")
    category: Literal["paper_extraction", "repo_locate", "repo_run", "bug_fix", "custom"] = "custom"
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Task inputs: pdf (relative path), repo (relative dir), command, etc.",
    )
    success_criteria: str = Field("", description="What VERIFY should check; also shown to the model.")
    expected_outputs: list[str] = Field(default_factory=list, description="Artifact names the task must produce.")
    allowed_tools: list[str] | None = Field(None, description="Restrict tool set (None = all).")
    hints: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    id: int
    node: Literal["retrieve", "implement", "execute", "verify"]
    description: str


class Plan(BaseModel):
    task_type: Literal["paper_extraction", "repo_locate", "repo_run", "bug_fix", "custom"]
    needs_code_change: bool
    needs_execution: bool
    steps: list[PlanStep]
    commands: list[str] = Field(default_factory=list, description="Shell commands EXECUTE will run in order.")
    target_files: list[str] = Field(default_factory=list)
    success_check: str = Field("", description="Concrete, checkable condition for VERIFY.")
    output_schema: dict[str, str] = Field(
        default_factory=dict, description="For extraction tasks: field -> description of expected value."
    )


class FileChange(BaseModel):
    path: str
    action: Literal["create", "modify", "delete"]
    diff: str = ""


class NodeResult(BaseModel):
    node: Node
    status: Literal["ok", "fail", "skip"]
    failure: FailureKind | None = None
    message: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    duration_s: float = 0.0
    tool_calls: int = 0
    llm_calls: int = 0
    tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class StageSummaries(BaseModel):
    """Cross-node memory.  Each node writes a short summary; later nodes read
    *only these*, never the raw message history of earlier nodes."""

    plan: str = ""
    retrieve: str = ""
    implement: str = ""
    execute: str = ""
    verify: str = ""
    repair: str = ""


class TaskState(BaseModel):
    run_id: str = Field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    task: TaskSpec
    current: Node = Node.PLAN
    status: Literal["running", "done", "failed", "paused"] = "running"
    step_idx: int = 0
    history: list[NodeResult] = Field(default_factory=list)
    plan: Plan | None = None
    findings: dict[str, Any] = Field(default_factory=dict, description="RETRIEVE output.")
    changes: list[FileChange] = Field(default_factory=list)
    exec_results: list[dict[str, Any]] = Field(default_factory=list)
    last_failure: FailureKind | None = None
    last_failure_detail: str = ""
    verify_result: dict[str, Any] = Field(default_factory=dict)
    repairs_used: int = 0
    tool_calls_total: int = 0
    llm_calls_total: int = 0
    tokens_total: int = 0
    cost_usd: float = 0.0
    artifacts: list[str] = Field(default_factory=list)
    summaries: StageSummaries = Field(default_factory=StageSummaries)
    final_answer: dict[str, Any] = Field(default_factory=dict)
    report_path: str | None = None
    started_at: float = Field(default_factory=time.time)
    finished_at: float | None = None

    def record(self, res: NodeResult) -> None:
        self.history.append(res)
        self.step_idx += 1
        self.tool_calls_total += res.tool_calls
        self.llm_calls_total += res.llm_calls
        self.tokens_total += res.tokens
        if res.status == "fail":
            self.last_failure = res.failure or FailureKind.UNKNOWN
            self.last_failure_detail = res.message
