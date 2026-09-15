"""Structured output with validation-driven repair.

``ask_structured`` asks the model for JSON, validates it against a Pydantic
model, and - if validation fails - sends the *validation error* back to the
model and asks again (bounded).  A malformed model output is therefore a
first-class, recoverable failure (``FailureKind.MODEL_OUTPUT_INVALID``) rather
than a crash.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .client import LLMClient, LLMResponse

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    def __init__(self, message: str, last_raw: str = "") -> None:
        super().__init__(message)
        self.last_raw = last_raw


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Best-effort: strip code fences / prose and parse the first JSON object."""
    text = text.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # find outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("no JSON object found", text, 0)


def ask_structured(
    client: LLMClient,
    messages: list[dict[str, Any]],
    schema: type[T],
    *,
    max_attempts: int = 3,
    temperature: float = 0.0,
    on_response: Callable[[LLMResponse], None] | None = None,
) -> T:
    """Call the model until the reply validates as ``schema`` or attempts run out."""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    sys_hint = {
        "role": "system",
        "content": (
            "Reply with a single JSON object and nothing else. It must validate against this "
            f"JSON schema:\n{schema_json}"
        ),
    }
    convo = [sys_hint, *messages]
    last_raw = ""
    for _attempt in range(max_attempts):
        resp = client.chat(convo, json_mode=True, temperature=temperature)
        if on_response:
            on_response(resp)
        last_raw = resp.content
        try:
            data = extract_json(resp.content)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            err = str(e)[:1500]
            convo = convo + [
                {"role": "assistant", "content": resp.content},
                {
                    "role": "user",
                    "content": f"Your reply was not valid. Error:\n{err}\n"
                    "Return only a corrected JSON object.",
                },
            ]
    raise StructuredOutputError(
        f"model output did not validate as {schema.__name__} after {max_attempts} attempts", last_raw
    )
