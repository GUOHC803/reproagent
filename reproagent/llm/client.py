"""Model client abstraction.

Only this module knows about LiteLLM.  Everything above it (nodes, orchestrator,
evaluation) talks to the small ``LLMClient`` protocol, which is the answer to
"if you swap the model, what stays unchanged?": everything except this file
and the model name in ``AgentConfig``.

Three implementations:

* ``LiteLLMClient``  - real calls (DeepSeek / OpenAI / Anthropic / local via LiteLLM).
* ``ReplayLLMClient`` - deterministic replay from a recorded JSONL cassette
  (unit tests and CI run without any API key).
* ``MockLLMClient``  - scripted responses for unit tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    latency_s: float = 0.0
    finish_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient(Protocol):
    model: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Real client
# --------------------------------------------------------------------------- #


class LiteLLMClient:
    """Thin wrapper over ``litellm.completion`` with retry/backoff and cost accounting."""

    def __init__(
        self,
        model: str = "deepseek/deepseek-chat",
        *,
        timeout_s: float = 120.0,
        max_retries: int = 4,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.api_key = api_key
        self.api_base = api_base

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        import litellm  # imported lazily: heavy, and tests should not need it

        litellm.suppress_debug_info = True
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            timeout=self.timeout_s,
        )
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            t0 = time.time()
            try:
                resp = litellm.completion(**kwargs)
                return self._convert(resp, time.time() - t0)
            except Exception as e:
                last_err = e
                if not _is_retryable(e) or attempt == self.max_retries:
                    raise LLMError(f"{type(e).__name__}: {e}") from e
                sleep = min(30.0, (2**attempt) + random.random())
                time.sleep(sleep)
        raise LLMError(str(last_err))

    def _convert(self, resp: Any, latency: float) -> LLMResponse:
        choice = resp.choices[0]
        msg = choice.message
        calls: list[ToolCall] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"__raw__": raw}
            calls.append(ToolCall(id=tc.id or "", name=tc.function.name, arguments=args))
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        cost = 0.0
        try:
            import litellm

            cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:  # noqa: BLE001 - cost is best-effort
            cost = 0.0
        return LLMResponse(
            content=msg.content or "",
            tool_calls=calls,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=cost,
            model=self.model,
            latency_s=latency,
            finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        )


def _is_retryable(e: Exception) -> bool:
    name = type(e).__name__
    retryable = (
        "RateLimitError",
        "Timeout",
        "APIConnectionError",
        "ServiceUnavailableError",
        "InternalServerError",
        "APIError",
    )
    return any(r in name for r in retryable)


# --------------------------------------------------------------------------- #
# Recording / replay
# --------------------------------------------------------------------------- #


def _fingerprint(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, json_mode: bool) -> str:
    payload = json.dumps(
        {"m": messages, "t": [t["function"]["name"] for t in tools] if tools else None, "j": json_mode},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class RecordingLLMClient:
    """Wraps a real client and appends every exchange to a JSONL cassette."""

    def __init__(self, inner: LLMClient, cassette: Path) -> None:
        self.inner = inner
        self.model = inner.model
        self.cassette = Path(cassette)
        self.cassette.parent.mkdir(parents=True, exist_ok=True)

    def chat(self, messages, *, tools=None, json_mode=False, temperature=0.0, max_tokens=None):  # type: ignore[override]
        resp = self.inner.chat(
            messages, tools=tools, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens
        )
        rec = {
            "key": _fingerprint(messages, tools, json_mode),
            "response": {
                "content": resp.content,
                "tool_calls": [tc.__dict__ for tc in resp.tool_calls],
                "prompt_tokens": resp.prompt_tokens,
                "completion_tokens": resp.completion_tokens,
                "cost_usd": resp.cost_usd,
                "model": resp.model,
                "finish_reason": resp.finish_reason,
            },
        }
        with self.cassette.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return resp


class ReplayLLMClient:
    """Replays a cassette.  Matching is by exact prompt fingerprint, falling back to
    sequential order so small prompt drift does not break a test."""

    def __init__(self, cassette: Path, *, strict: bool = False) -> None:
        self.model = "replay"
        self.strict = strict
        self._by_key: dict[str, list[dict[str, Any]]] = {}
        self._seq: list[dict[str, Any]] = []
        self._cursor = 0
        for line in Path(cassette).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            self._by_key.setdefault(rec["key"], []).append(rec["response"])
            self._seq.append(rec["response"])

    def chat(self, messages, *, tools=None, json_mode=False, temperature=0.0, max_tokens=None):  # type: ignore[override]
        key = _fingerprint(messages, tools, json_mode)
        bucket = self._by_key.get(key)
        if bucket:
            raw = bucket.pop(0)
        elif not self.strict and self._cursor < len(self._seq):
            raw = self._seq[self._cursor]
            self._cursor += 1
        else:
            raise LLMError(f"No recorded response for prompt fingerprint {key}")
        return LLMResponse(
            content=raw.get("content", ""),
            tool_calls=[ToolCall(**tc) for tc in raw.get("tool_calls", [])],
            prompt_tokens=raw.get("prompt_tokens", 0),
            completion_tokens=raw.get("completion_tokens", 0),
            cost_usd=raw.get("cost_usd", 0.0),
            model=raw.get("model", "replay"),
            finish_reason=raw.get("finish_reason", "stop"),
        )


class MockLLMClient:
    """Scripted responses for unit tests.  Each entry is either a plain string
    (content) or an ``LLMResponse``."""

    def __init__(self, responses: list[str | LLMResponse] | None = None) -> None:
        self.model = "mock"
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def push(self, *responses: str | LLMResponse) -> None:
        self.responses.extend(responses)

    def chat(self, messages, *, tools=None, json_mode=False, temperature=0.0, max_tokens=None):  # type: ignore[override]
        self.calls.append({"messages": messages, "tools": tools, "json_mode": json_mode})
        if not self.responses:
            raise LLMError("MockLLMClient exhausted")
        r = self.responses.pop(0)
        if isinstance(r, str):
            return LLMResponse(content=r, prompt_tokens=len(json.dumps(messages)) // 4, completion_tokens=len(r) // 4, model="mock")
        return r


def build_client(model: str, *, timeout_s: float = 120.0, max_retries: int = 4) -> LLMClient:
    """Factory used by the CLI.  ``REPROAGENT_CASSETTE`` switches to replay mode."""
    cassette = os.environ.get("REPROAGENT_CASSETTE")
    if cassette:
        return ReplayLLMClient(Path(cassette))
    client: LLMClient = LiteLLMClient(model, timeout_s=timeout_s, max_retries=max_retries)
    record_to = os.environ.get("REPROAGENT_RECORD_CASSETTE")
    if record_to:
        client = RecordingLLMClient(client, Path(record_to))
    return client
