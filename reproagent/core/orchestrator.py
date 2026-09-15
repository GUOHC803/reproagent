"""The state machine.

Why a state machine and not one big prompt loop?

* every transition is an explicit, testable rule (``next_node`` below);
* budgets (steps, repairs, tool calls, tokens) are enforced *between* nodes,
  so the run always terminates;
* each node's timeout is enforced from outside the node;
* the state is checkpointed after every node -> ``resume`` and trace replay.
"""

from __future__ import annotations

import concurrent.futures as cf
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import AgentConfig
from ..llm.client import LLMClient
from ..store.trace import TraceStore
from ..tools.base import ToolRegistry
from .nodes import HANDLERS
from .runtime import NodeRuntime
from .state import REPAIRABLE, TERMINAL, FailureKind, Node, NodeResult, TaskSpec, TaskState


def next_node(state: TaskState, res: NodeResult, cfg: AgentConfig) -> Node:
    """Pure transition function.  Kept free of side effects so it can be unit-tested."""
    n, ok = res.node, res.status == "ok"
    plan = state.plan
    if n == Node.PLAN:
        return Node.RETRIEVE if ok else Node.FAILED
    if n == Node.RETRIEVE:
        if not ok:
            return Node.REPORT if state.findings else Node.FAILED
        if plan and plan.needs_code_change:
            return Node.IMPLEMENT
        if plan and plan.needs_execution:
            return Node.EXECUTE
        return Node.VERIFY
    if n == Node.IMPLEMENT:
        return Node.EXECUTE if ok else _repair_or_report(state, res, cfg)
    if n == Node.EXECUTE:
        if res.status == "skip":
            return Node.VERIFY
        return Node.VERIFY if ok else _repair_or_report(state, res, cfg)
    if n == Node.VERIFY:
        return Node.REPORT if ok else _repair_or_report(state, res, cfg)
    if n == Node.REPAIR:
        return Node.EXECUTE if ok else Node.REPORT
    if n == Node.REPORT:
        return Node.DONE
    return Node.FAILED


def _repair_or_report(state: TaskState, res: NodeResult, cfg: AgentConfig) -> Node:
    if res.failure in REPAIRABLE and state.repairs_used < cfg.budget.max_repairs:
        return Node.REPAIR
    return Node.REPORT


class Orchestrator:
    def __init__(
        self,
        *,
        cfg: AgentConfig,
        llm: LLMClient,
        tools: ToolRegistry,
        sandbox: Any,
        store: TraceStore | None,
        workdir: Path,
        artifacts_dir: Path,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.llm = llm
        self.tools = tools
        self.sandbox = sandbox
        self.store = store
        self.workdir = Path(workdir)
        self.artifacts_dir = Path(artifacts_dir)
        self.log = log or (lambda s: None)

    # ---- public --------------------------------------------------------
    def start(self, task: TaskSpec, run_id: str | None = None) -> TaskState:
        state = TaskState(task=task) if run_id is None else TaskState(task=task, run_id=run_id)
        if self.store:
            self.store.create_run(state.run_id, task.model_dump(), self.cfg.model_dump())
        return self.run(state)

    def resume(self, run_id: str) -> TaskState:
        if not self.store:
            raise RuntimeError("resume requires a trace store")
        raw = self.store.latest_state(run_id)
        if raw is None:
            raise KeyError(f"no checkpoint for run {run_id}")
        state = TaskState.model_validate(raw)
        if state.current in TERMINAL:
            self.log(f"run {run_id} already finished ({state.status})")
            return state
        self.log(f"resuming {run_id} at {state.current.value} (step {state.step_idx})")
        state.status = "running"
        self.store.set_run_status(run_id, "running")
        return self.run(state)

    def run(self, state: TaskState) -> TaskState:
        b = self.cfg.budget
        while state.current not in TERMINAL:
            if state.step_idx >= b.max_steps:
                self._finish(state, FailureKind.BUDGET_EXHAUSTED, f"max_steps ({b.max_steps}) reached")
                break
            node = state.current
            self.log(f"[{state.run_id}] step {state.step_idx}: {node.value}")
            res = self._run_node(state, node)
            state.record(res)
            nxt = next_node(state, res, self.cfg)
            self.log(f"  -> {res.status}" + (f" ({res.failure.value}: {res.message[:120]})" if res.status == "fail" else "") + f" => {nxt.value}")
            state.current = nxt
            if nxt == Node.DONE:
                state.status = "done" if res.output.get("success") else "failed"
                state.finished_at = time.time()
            elif nxt == Node.FAILED:
                state.status = "failed"
                state.finished_at = time.time()
            self._checkpoint(state, res)
        if self.store:
            self.store.set_run_status(state.run_id, state.status, self.summary(state))
        return state

    # ---- internals -----------------------------------------------------
    def _run_node(self, state: TaskState, node: Node) -> NodeResult:
        rt = NodeRuntime(state=state, cfg=self.cfg, llm=self.llm, tools=self.tools, store=self.store,
                         sandbox=self.sandbox, workdir=self.workdir, artifacts_dir=self.artifacts_dir, node=node, log=self.log)
        handler = HANDLERS[node]
        timeout = self.cfg.budget.node_timeout_s
        with cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"node-{node.value}") as ex:
            fut = ex.submit(handler, state, rt)
            try:
                return fut.result(timeout=timeout)
            except cf.TimeoutError:
                # The worker thread keeps running until its current tool/model call returns; we do not
                # block on it.  Sandboxed commands have their own (shorter) timeouts.
                return NodeResult(node=node, status="fail", failure=FailureKind.NODE_TIMEOUT,
                                  message=f"node exceeded {timeout}s", duration_s=timeout,
                                  tool_calls=rt.tool_calls, llm_calls=rt.llm_calls, tokens=rt.tokens)

    def _finish(self, state: TaskState, kind: FailureKind, msg: str) -> None:
        res = NodeResult(node=state.current, status="fail", failure=kind, message=msg)
        state.record(res)
        state.current = Node.FAILED
        state.status = "failed"
        state.finished_at = time.time()
        self._checkpoint(state, res)

    def _checkpoint(self, state: TaskState, res: NodeResult) -> None:
        if self.store:
            self.store.save_step(state.run_id, state.step_idx - 1, res.node.value, res.model_dump(mode="json"),
                                 state.model_dump(mode="json"))

    @staticmethod
    def summary(state: TaskState) -> dict[str, Any]:
        return {
            "success": state.status == "done",
            "steps": state.step_idx,
            "repairs": state.repairs_used,
            "tool_calls": state.tool_calls_total,
            "llm_calls": state.llm_calls_total,
            "tokens": state.tokens_total,
            "cost_usd": round(state.cost_usd, 5),
            "last_failure": state.last_failure.value if (state.last_failure and state.status != "done") else None,
            "wall_time_s": round((state.finished_at or time.time()) - state.started_at, 1),
            "answer": state.final_answer,
        }
