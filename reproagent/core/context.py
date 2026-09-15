"""Context management.

Two mechanisms, both deliberately simple:

1. **Stage summaries** (cross-node).  A node never sees another node's raw
   message history.  It sees ``state.summaries`` - a few hundred characters per
   earlier stage - plus the structured state (plan, findings, changes).  This
   bounds prompt size by *design* rather than by trimming.

2. **In-node compaction**.  Inside a tool-calling loop, tool outputs are the
   bulk of the context.  When the estimated token count crosses a threshold,
   older tool results are replaced by a one-line stub ("[read_pdf ok, 5.1k
   chars folded]") while the last N messages stay verbatim.  The model keeps
   the *fact that it did something* and the current working set, which is what
   it needs to avoid repeating itself.
"""

from __future__ import annotations

from typing import Any

from ..config import ContextConfig


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Cheap estimate (~4 chars/token for English, ~1.5 for CJK - we use 3 as a middle)."""
    n = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        for tc in m.get("tool_calls") or []:
            n += len(str(tc))
    return n // 3


class ContextManager:
    def __init__(self, cfg: ContextConfig, *, enabled: bool = True) -> None:
        self.cfg = cfg
        self.enabled = enabled
        self.compactions = 0
        self.chars_folded = 0

    def clip_tool_output(self, text: str) -> str:
        m = self.cfg.max_tool_output_chars
        if len(text) <= m:
            return text
        head = m * 2 // 3
        return text[:head] + f"\n... [{len(text) - m} chars omitted] ...\n" + text[-(m - head):]

    def maybe_compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fold older tool outputs when the history grows too large.

        Messages are kept in place (so tool_call_id pairing stays valid); only
        the *content* of old ``role=tool`` messages is replaced with a stub.
        """
        if not self.enabled:
            return messages
        if estimate_tokens(messages) < self.cfg.compaction_threshold_tokens:
            return messages
        keep = self.cfg.keep_last_messages
        cutoff = max(0, len(messages) - keep)
        out: list[dict[str, Any]] = []
        for i, m in enumerate(messages):
            if i < cutoff and m.get("role") == "tool" and not m.get("_folded"):
                content = m.get("content") or ""
                if len(content) > 200:
                    first = content.splitlines()[0][:80] if content else ""
                    stub = f"[{first}] output folded ({len(content)} chars). Re-run the tool if you need it again."
                    self.chars_folded += len(content) - len(stub)
                    m = {**m, "content": stub, "_folded": True}
            out.append(m)
        self.compactions += 1
        return out

    @staticmethod
    def strip_private(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove our bookkeeping keys before sending to the model."""
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]

    def summarize(self, text: str) -> str:
        m = self.cfg.stage_summary_max_chars
        return text if len(text) <= m else text[: m - 20] + " ...[truncated]"
