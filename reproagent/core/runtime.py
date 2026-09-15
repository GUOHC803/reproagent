"""Per-node runtime: model calls, tool loop, budget accounting, trace logging.

A node is a function ``(state, rt) -> NodeResult``.  ``rt`` (this class) is the
only thing a node may use to talk to the outside world, which keeps nodes
short and makes the whole system testable with a mock model + temp dir.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import AgentConfig
from ..llm.client import LLMClient, LLMResponse, ToolCall
from ..llm.structured import StructuredOutputError, ask_structured, extract_json
from ..store.trace import TraceStore
from ..tools.base import ToolContext, ToolRegistry, ToolResult
from .context import ContextManager
from .state import FailureKind, Node, TaskState

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


class NoProgress(RuntimeError):
    pass


@dataclass
class LoopOutcome:
    final: BaseModel | None
    messages: list[dict[str, Any]]
    tool_calls: int
    failure: FailureKind | None = None
    message: str = ""
    tool_results: list[tuple[str, dict[str, Any], ToolResult]] = field(default_factory=list)


class NodeRuntime:
    def __init__(
        self,
        *,
        state: TaskState,
        cfg: AgentConfig,
        llm: LLMClient,
        tools: ToolRegistry,
        store: TraceStore | None,
        sandbox: Any,
        workdir: Path,
        artifacts_dir: Path,
        node: Node,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.state = state
        self.cfg = cfg
        self.llm = llm
        self.tools = tools
        self.store = store
        self.sandbox = sandbox
        self.workdir = Path(workdir)
        self.artifacts_dir = Path(artifacts_dir)
        self.node = node
        self.ctx = ContextManager(cfg.context, enabled=cfg.enable_context_compaction)
        self.log = log or (lambda s: None)
        self.tool_calls = 0
        self.llm_calls = 0
        self.tokens = 0
        self.cost = 0.0

    # ---- tool context ----------------------------------------------------
    def tool_context(self, **extra: Any) -> ToolContext:
        return ToolContext(
            workdir=self.workdir,
            artifacts_dir=self.artifacts_dir,
            sandbox=self.sandbox,
            run_id=self.state.run_id,
            default_timeout_s=self.cfg.budget.tool_timeout_s,
            extra={"store": self.store, "step_idx": self.state.step_idx, **extra},
        )

    def call_tool(self, name: str, args: dict[str, Any], **extra: Any) -> ToolResult:
        self._check_budget(tool=True)
        res = self.tools.call(name, args, self.tool_context(**extra))
        self.tool_calls += 1
        self.log(f"  tool {name}({_short(args)}) -> {res.status} {res.duration_s:.1f}s")
        if self.store:
            self.store.log_tool_call(self.state.run_id, self.state.step_idx, self.node.value, name, args, res.model_dump())
        return res

    # ---- model calls -----------------------------------------------------
    def chat(self, messages: list[dict[str, Any]], *, tools=None, json_mode=False) -> LLMResponse:
        self._check_budget(llm=True)
        resp = self.llm.chat(
            ContextManager.strip_private(messages), tools=tools, json_mode=json_mode, temperature=self.cfg.temperature
        )
        self.llm_calls += 1
        self.tokens += resp.total_tokens
        self.cost += resp.cost_usd
        self.log(f"  llm {resp.model} {resp.prompt_tokens}+{resp.completion_tokens} tok, {len(resp.tool_calls)} tool calls")
        if self.store:
            self.store.log_llm_call(
                self.state.run_id, self.state.step_idx, self.node.value, model=resp.model,
                prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd,
                latency_s=resp.latency_s, n_tool_calls=len(resp.tool_calls),
                messages=ContextManager.strip_private(messages),
                response={"content": resp.content, "tool_calls": [tc.__dict__ for tc in resp.tool_calls]},
            )
        return resp

    def structured(self, messages: list[dict[str, Any]], schema: type[T], *, max_attempts: int = 3) -> T:
        self._check_budget(llm=True)
        proxy = _ClientProxy(self)
        return ask_structured(proxy, messages, schema, max_attempts=max_attempts, temperature=self.cfg.temperature)

    # ---- the tool loop ---------------------------------------------------
    def tool_loop(
        self,
        *,
        system: str,
        user: str,
        allowed_tools: list[str],
        final_schema: type[T],
        max_tool_calls: int | None = None,
        tool_extra: dict[str, Any] | None = None,
    ) -> LoopOutcome:
        """Run a bounded ReAct-style loop until the model calls ``finish``.

        Structured mode (default): native function calling; ``finish`` is a pseudo-tool
        whose schema is ``final_schema``.  Free-text mode (ablation): tools are described
        in prose, the model writes ```tool ... ``` blocks, results come back as raw text.
        """
        max_calls = max_tool_calls or self.cfg.budget.max_tool_calls_per_node
        reg = self.tools.subset(allowed_tools)
        if self.cfg.structured_tools:
            return self._loop_structured(system, user, reg, final_schema, max_calls, tool_extra or {})
        return self._loop_freetext(system, user, reg, final_schema, max_calls, tool_extra or {})

    def _loop_structured(self, system, user, reg, final_schema, max_calls, tool_extra) -> LoopOutcome:
        finish_schema = {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Call this exactly once when you are done. Its arguments are your final structured answer.",
                "parameters": _params_schema(final_schema),
            },
        }
        schemas = reg.schemas() + [finish_schema]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        results: list[tuple[str, dict[str, Any], ToolResult]] = []
        recent: list[str] = []
        n_calls = 0
        idle_turns = 0
        while True:
            if n_calls >= max_calls:
                # Last chance: no more tools, only `finish` with whatever has been learned.
                messages.append({"role": "user", "content": f"Tool budget ({max_calls} calls) exhausted. "
                                 "Call `finish` now with your best answer from what you have seen."})
                resp = self.chat(self.ctx.maybe_compact(messages), tools=[finish_schema])
                for tc in resp.tool_calls:
                    if tc.name == "finish":
                        try:
                            return LoopOutcome(final_schema.model_validate(tc.arguments), messages, n_calls, tool_results=results)
                        except ValidationError:
                            break
                return LoopOutcome(None, messages, n_calls, FailureKind.BUDGET_EXHAUSTED,
                                   f"node tool budget ({max_calls}) exhausted", results)
            messages = self.ctx.maybe_compact(messages)
            resp = self.chat(messages, tools=schemas)
            if not resp.tool_calls:
                # Model answered in prose. Try to read a final answer from it, else nudge once.
                parsed = _try_parse(resp.content, final_schema)
                if parsed is not None:
                    return LoopOutcome(parsed, messages, n_calls, tool_results=results)
                idle_turns += 1
                messages.append({"role": "assistant", "content": resp.content})
                if idle_turns >= 3:
                    return LoopOutcome(None, messages, n_calls, FailureKind.NO_PROGRESS,
                                       "model stopped calling tools without finishing", results)
                messages.append({"role": "user", "content": "Continue: call a tool, or call `finish` with your final answer."})
                continue
            idle_turns = 0
            messages.append({"role": "assistant", "content": resp.content or "", "tool_calls": [_tc_to_msg(tc) for tc in resp.tool_calls]})
            for tc in resp.tool_calls:
                if tc.name == "finish":
                    try:
                        final = final_schema.model_validate(tc.arguments)
                    except ValidationError as e:
                        messages.append(_tool_msg(tc.id, f"[status=error] finish arguments invalid: {e.errors(include_url=False)}\nCall finish again with corrected fields."))
                        continue
                    return LoopOutcome(final, messages, n_calls, tool_results=results)
                sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True, default=str)}"
                recent.append(sig)
                if len(recent) >= 3 and recent[-1] == recent[-2] == recent[-3]:
                    return LoopOutcome(None, messages, n_calls, FailureKind.NO_PROGRESS,
                                       f"same tool call repeated 3x: {tc.name}", results)
                res = self.call_tool(tc.name, tc.arguments, **tool_extra)
                n_calls += 1
                results.append((tc.name, tc.arguments, res))
                messages.append(_tool_msg(tc.id, self.ctx.clip_tool_output(res.to_model_text(self.cfg.context.max_tool_output_chars))))

    def _loop_freetext(self, system, user, reg, final_schema, max_calls, tool_extra) -> LoopOutcome:
        desc = "\n".join(
            f"- {t['function']['name']}: {t['function']['description']}\n  parameters: "
            + ", ".join(t["function"]["parameters"].get("properties", {}).keys())
            for t in reg.schemas()
        )
        proto = (
            "\n\nTOOLS (free-text protocol). To call a tool write exactly one block:\n"
            "```tool\n{\"name\": \"<tool>\", \"args\": {...}}\n```\n"
            "You will get the raw output back. When done, write:\n```final\n{...json matching the answer schema...}\n```\n"
            f"Answer schema: {json.dumps(_params_schema(final_schema))}\nAvailable tools:\n{desc}"
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system + proto}, {"role": "user", "content": user}]
        results: list[tuple[str, dict[str, Any], ToolResult]] = []
        n_calls = 0
        idle = 0
        recent: list[str] = []
        while True:
            if n_calls >= max_calls:
                return LoopOutcome(None, messages, n_calls, FailureKind.BUDGET_EXHAUSTED, f"node tool budget ({max_calls}) exhausted", results)
            messages = self.ctx.maybe_compact(messages)
            resp = self.chat(messages)
            content = resp.content or ""
            messages.append({"role": "assistant", "content": content})
            m_final = re.search(r"```final\s*(.*?)```", content, re.DOTALL)
            if m_final:
                parsed = _try_parse(m_final.group(1), final_schema)
                if parsed is not None:
                    return LoopOutcome(parsed, messages, n_calls, tool_results=results)
                messages.append({"role": "user", "content": "The final block did not match the answer schema. Fix it."})
                continue
            m_tool = re.search(r"```tool\s*(.*?)```", content, re.DOTALL)
            if not m_tool:
                idle += 1
                if idle >= 3:
                    return LoopOutcome(None, messages, n_calls, FailureKind.NO_PROGRESS, "no tool/final block", results)
                messages.append({"role": "user", "content": "Write a ```tool``` block or a ```final``` block."})
                continue
            idle = 0
            try:
                call = json.loads(m_tool.group(1))
                name, args = call["name"], call.get("args", {})
            except Exception as e:  # noqa: BLE001
                messages.append({"role": "user", "content": f"Could not parse tool block: {e}"})
                n_calls += 1
                continue
            sig = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
            recent.append(sig)
            if len(recent) >= 3 and recent[-1] == recent[-2] == recent[-3]:
                return LoopOutcome(None, messages, n_calls, FailureKind.NO_PROGRESS, f"same tool call repeated 3x: {name}", results)
            res = self.call_tool(name, args, **tool_extra)
            n_calls += 1
            results.append((name, args, res))
            raw = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
            messages.append({"role": "user", "content": self.ctx.clip_tool_output(raw) or "(no output)"})

    # ---- budgets ---------------------------------------------------------
    def _check_budget(self, *, tool: bool = False, llm: bool = False) -> None:
        b = self.cfg.budget
        st = self.state
        if tool and st.tool_calls_total + self.tool_calls >= b.max_tool_calls:
            raise BudgetExceeded("max_tool_calls")
        if llm and st.llm_calls_total + self.llm_calls >= b.max_llm_calls:
            raise BudgetExceeded("max_llm_calls")
        if st.tokens_total + self.tokens >= b.max_tokens_total:
            raise BudgetExceeded("max_tokens_total")


class _ClientProxy:
    """Routes ``ask_structured`` calls through the runtime so they are logged and budgeted."""

    def __init__(self, rt: NodeRuntime) -> None:
        self.rt = rt
        self.model = rt.llm.model

    def chat(self, messages, *, tools=None, json_mode=False, temperature=0.0, max_tokens=None):
        return self.rt.chat(messages, tools=tools, json_mode=json_mode)


def _params_schema(model: type[BaseModel]) -> dict[str, Any]:
    s = model.model_json_schema()
    s.pop("title", None)
    return s


def _try_parse(text: str, schema: type[BaseModel]) -> BaseModel | None:
    try:
        return schema.model_validate(extract_json(text))
    except Exception:  # noqa: BLE001
        return None


def _tc_to_msg(tc: ToolCall) -> dict[str, Any]:
    return {"id": tc.id or "call_0", "type": "function",
            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False, default=str)}}


def _tool_msg(call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id or "call_0", "content": content}


def _short(args: dict[str, Any], n: int = 80) -> str:
    s = json.dumps(args, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "..."


__all__ = ["BudgetExceeded", "LoopOutcome", "NoProgress", "NodeRuntime", "StructuredOutputError"]
