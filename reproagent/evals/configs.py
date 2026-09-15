"""Named agent configurations = the ablation arms.

* full            - the complete system.
* no_repair       - VERIFY still runs, but a failure ends the run (max_repairs=0).
* single_call     - one model call with a material dump; no tools, no state machine.
* free_text_tools - same state machine, but tools are called through free-text blocks
                    with raw-text results (no schema validation, no status/next_hint).
* no_verify       - VERIFY is replaced by exit-code checks only (no model judge).
"""

from __future__ import annotations

from ..config import AgentConfig, Budget

_BASE = AgentConfig()


def get_config(name: str) -> AgentConfig:
    if name == "full":
        return _BASE.with_updates(name="full")
    if name == "no_repair":
        return _BASE.with_updates(name="no_repair", budget=_BASE.budget.model_copy(update={"max_repairs": 0}))
    if name == "single_call":
        return _BASE.with_updates(name="single_call", mode="single_call")
    if name == "free_text_tools":
        return _BASE.with_updates(name="free_text_tools", structured_tools=False)
    if name == "no_verify":
        return _BASE.with_updates(name="no_verify", enable_verify=False)
    if name == "no_compaction":
        return _BASE.with_updates(name="no_compaction", enable_context_compaction=False)
    raise KeyError(f"unknown config {name!r}; choose from full, no_repair, single_call, free_text_tools, no_verify")


ALL_CONFIGS = ["full", "no_repair", "single_call", "free_text_tools", "no_verify"]
__all__ = ["ALL_CONFIGS", "Budget", "get_config"]
