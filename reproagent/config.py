"""Runtime configuration.

Everything that changes agent behaviour lives here so that an ablation is just a
different ``AgentConfig`` instance (see ``evals/configs``).  Secrets (API keys)
are read from the environment / ``.env`` and never written to traces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Budget(BaseModel):
    """Hard limits that bound a single run.

    The orchestrator refuses to proceed once any budget is exhausted; this is
    what turns an open-ended "agent loop" into a terminating state machine.
    """

    max_steps: int = Field(40, description="Max node executions per run (incl. repairs).")
    max_repairs: int = Field(3, description="Max REPAIR rounds. 0 disables the repair node.")
    max_tool_calls: int = Field(60, description="Max tool invocations per run.")
    max_tool_calls_per_node: int = Field(12, description="Max tool invocations inside one node.")
    max_llm_calls: int = Field(60, description="Max model calls per run.")
    node_timeout_s: float = Field(600.0, description="Wall-clock timeout for one node.")
    tool_timeout_s: float = Field(120.0, description="Default timeout for a sandboxed command.")
    max_tokens_total: int = Field(600_000, description="Prompt+completion token budget per run.")


class SandboxConfig(BaseModel):
    backend: Literal["local", "docker"] = "local"
    docker_image: str = "reproagent-sandbox:latest"
    memory_limit: str = "2g"
    cpu_limit: float = 2.0
    pids_limit: int = 256
    network: Literal["none", "bridge"] = "none"
    confirm_mode: bool = Field(
        False,
        description="If True, commands matched by the RISKY list require an explicit confirm "
        "callback; otherwise they are blocked.",
    )


class ContextConfig(BaseModel):
    max_tool_output_chars: int = Field(6000, description="Head+tail kept from a tool result.")
    compaction_threshold_tokens: int = Field(
        24_000, description="Approx. tokens of in-node history before older tool outputs are folded."
    )
    keep_last_messages: int = Field(6, description="Recent messages never compacted.")
    stage_summary_max_chars: int = 1500


class AgentConfig(BaseModel):
    """Behavioural switches.  Ablations flip these."""

    name: str = "full"
    model: str = "deepseek/deepseek-chat"
    temperature: float = 0.0
    budget: Budget = Field(default_factory=Budget)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    mode: Literal["state_machine", "single_call"] = Field(
        "state_machine", description="single_call = ablation baseline: one model call, no tools."
    )
    structured_tools: bool = Field(
        True, description="False = ablation: tools invoked through free-text tags, no schema validation."
    )
    enable_verify: bool = True
    enable_context_compaction: bool = True

    def with_updates(self, **kw) -> "AgentConfig":
        return self.model_copy(update=kw, deep=True)


class Settings(BaseSettings):
    """Process-level settings (paths, secrets). Loaded from env and .env."""

    model_config = SettingsConfigDict(env_prefix="REPROAGENT_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("runs"), description="Where traces & artifacts are stored.")
    db_path: Path | None = None
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 4
    record_llm: bool = Field(True, description="Record every model call to the trace DB.")

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path or (self.data_dir / "trace.sqlite3")


def load_settings() -> Settings:
    return Settings()
