"""Sandbox protocol: the *only* way agent-generated commands get executed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    blocked_reason: str | None = None
    command: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.blocked_reason is None


class Sandbox(Protocol):
    name: str

    def run(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_s: float,
        env: dict[str, str] | None = None,
    ) -> ExecResult: ...
