"""Tool protocol.

Every tool:

* declares its parameters as a Pydantic model  -> exported as an OpenAI-style
  function schema, so the *model* sees exactly the same contract the *code* enforces;
* returns a ``ToolResult`` with the same five fields regardless of what it does
  (``status / stdout / stderr / artifacts / next_hint``), so the orchestrator and
  the failure classifier never special-case a tool;
* never raises to the caller - exceptions become ``status="error"`` with a hint.

``next_hint`` is the tool telling the model what a sensible next move is
("test X failed at line 12", "path does not exist; try inspect_repo tree").
It is cheap for the tool to compute and saves a whole reasoning turn.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError

ToolStatus = Literal["ok", "error", "timeout", "blocked", "partial"]


class ToolResult(BaseModel):
    status: ToolStatus
    stdout: str = ""
    stderr: str = ""
    artifacts: list[str] = Field(default_factory=list)
    next_hint: str | None = None
    data: dict[str, Any] = Field(default_factory=dict, description="Structured extras (parsed test counts, diff, ...)")
    duration_s: float = 0.0

    def to_model_text(self, max_chars: int = 6000) -> str:
        """Render for the model: compact, bounded, and always shows status + hint."""
        parts = [f"[status={self.status}]"]
        if self.stdout:
            parts.append("stdout:\n" + _clip(self.stdout, max_chars))
        if self.stderr:
            parts.append("stderr:\n" + _clip(self.stderr, max_chars // 2))
        if self.data:
            parts.append("data: " + _clip(json.dumps(self.data, ensure_ascii=False, default=str), max_chars // 2))
        if self.artifacts:
            parts.append("artifacts: " + ", ".join(self.artifacts))
        if self.next_hint:
            parts.append("next_hint: " + self.next_hint)
        return "\n".join(parts)


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    return text[:head] + f"\n... [{len(text) - max_chars} chars omitted] ...\n" + text[-tail:]


@dataclass
class ToolContext:
    """What a tool is allowed to see.  Deliberately small."""

    workdir: Path
    artifacts_dir: Path
    sandbox: Any  # reproagent.sandbox.base.Sandbox
    run_id: str = ""
    default_timeout_s: float = 120.0
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve(self, rel: str) -> Path:
        """Resolve a path *inside* the workdir; raise on escape."""
        p = (self.workdir / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
        root = self.workdir.resolve()
        if p != root and root not in p.parents:
            raise PermissionError(f"path escapes workdir: {rel}")
        return p


class Tool:
    """Base class.  Subclasses set ``name``, ``description``, ``Params`` and implement ``run``."""

    name: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[BaseModel]]
    read_only: ClassVar[bool] = True  # used by node-level allow-lists

    def schema(self) -> dict[str, Any]:
        params = self.Params.model_json_schema()
        params.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }

    def run(self, params: BaseModel, ctx: ToolContext) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        t0 = time.time()
        try:
            params = self.Params.model_validate(args)
        except ValidationError as e:
            return ToolResult(
                status="error",
                stderr=f"invalid arguments for {self.name}: {e.errors(include_url=False)}",
                next_hint="Fix the arguments to match the tool schema and call again.",
                duration_s=time.time() - t0,
            )
        try:
            res = self.run(params, ctx)
        except PermissionError as e:
            res = ToolResult(status="blocked", stderr=str(e), next_hint="Stay inside the task workdir.")
        except Exception as e:  # noqa: BLE001 - tool bugs must not kill the run
            res = ToolResult(
                status="error",
                stderr=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}",
                next_hint="The tool itself failed; try different arguments or another tool.",
            )
        res.duration_s = time.time() - t0
        return res


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def subset(self, names: list[str]) -> "ToolRegistry":
        return ToolRegistry([self._tools[n] for n in names if n in self._tools])

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                status="error",
                stderr=f"unknown tool: {name}",
                next_hint=f"Available tools: {', '.join(self._tools)}",
            )
        if "__raw__" in args:  # model emitted non-JSON arguments
            return ToolResult(
                status="error",
                stderr=f"tool arguments were not valid JSON: {args['__raw__'][:300]}",
                next_hint="Emit arguments as a JSON object.",
            )
        return tool(args, ctx)
