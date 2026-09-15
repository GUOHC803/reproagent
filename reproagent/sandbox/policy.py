"""Command policy: the first line of defence, applied *before* anything runs.

Three layers protect the host (see docs/design/M3_sandbox.md):

1. **Policy** (this file)  - static pattern checks on the command string.
   Cheap, deterministic, and logs a ``blocked`` ToolResult the model can react to.
2. **Process limits**      - rlimits / Docker cgroups: CPU, memory, pids, wall time.
3. **Isolation**           - working-directory jail, scrubbed environment (no API keys),
   and ``--network none`` in Docker.

The deny-list is intentionally conservative: an agent that needs ``sudo`` is a
bug in the task, not a capability gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# Patterns that are never allowed.
DENY_PATTERNS: list[tuple[str, str]] = [
    (r"\bsudo\b", "privilege escalation"),
    (r"\bsu\s+-?\w*", "privilege escalation"),
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?(/|~|\$HOME|\.\.)(\s|$)", "recursive delete outside workdir"),
    (r"\bmkfs\b|\bdd\s+if=", "disk destruction"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b", "system power"),
    (r"\bkill\s+-9\s+-1\b|\bkillall\b|\bpkill\b", "killing other processes"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"\bchmod\s+[0-7]*777\s+/", "world-writable root"),
    (r"(^|[\s;&|])(curl|wget|nc|ncat|netcat|telnet|ssh|scp|sftp|rsync)\b", "network access"),
    (r"\bpip3?\s+install\b|\buv\s+pip\s+install\b|\bconda\s+install\b|\bapt(-get)?\s+", "package installation"),
    (r"\bgit\s+(clone|push|fetch|pull)\b", "git network operation"),
    (r"\b(env|printenv)\b(\s|$)|\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD)\w*\}?", "credential exposure"),
    (r"~/\.ssh|/etc/(passwd|shadow)|\.aws/credentials|\.netrc", "credential files"),
    (r"\bcrontab\b|\bsystemctl\b|\bservice\s+\w+\s+(start|stop)", "system services"),
    (r"\bdocker\b|\bnsenter\b|\bchroot\b", "container escape"),
]

# Patterns that need human confirmation when ``confirm_mode`` is on, and are
# blocked otherwise.
RISKY_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+-[a-zA-Z]*r", "recursive delete"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--)", "destructive git"),
    (r">\s*/dev/sd", "raw device write"),
    (r"\bfind\b.*-delete", "bulk delete"),
]

_DENY = [(re.compile(p, re.IGNORECASE), why) for p, why in DENY_PATTERNS]
_RISKY = [(re.compile(p, re.IGNORECASE), why) for p, why in RISKY_PATTERNS]


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str | None = None
    needs_confirm: bool = False


class CommandPolicy:
    def __init__(
        self,
        *,
        confirm_mode: bool = False,
        confirm_callback: Callable[[str, str], bool] | None = None,
        extra_deny: list[tuple[str, str]] | None = None,
    ) -> None:
        self.confirm_mode = confirm_mode
        self.confirm_callback = confirm_callback
        self._extra = [(re.compile(p, re.IGNORECASE), why) for p, why in (extra_deny or [])]

    def check(self, command: str) -> PolicyDecision:
        for rx, why in _DENY + self._extra:
            if rx.search(command):
                return PolicyDecision(False, f"blocked by policy ({why}): pattern {rx.pattern!r}")
        for rx, why in _RISKY:
            if rx.search(command):
                if self.confirm_mode and self.confirm_callback is not None:
                    if self.confirm_callback(command, why):
                        return PolicyDecision(True, None, needs_confirm=True)
                    return PolicyDecision(False, f"human declined risky command ({why})", True)
                return PolicyDecision(False, f"risky command requires confirm_mode ({why})", True)
        return PolicyDecision(True)


SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "PYTHONPATH", "PYTHONUNBUFFERED", "TMPDIR")


def scrub_env(base: dict[str, str], extra: dict[str, str] | None = None) -> dict[str, str]:
    """Only whitelisted variables reach the sandboxed process - API keys never do."""
    env = {k: v for k, v in base.items() if k in SAFE_ENV_KEYS}
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("LANG", "C.UTF-8")
    if extra:
        for k, v in extra.items():
            if not re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.IGNORECASE):
                env[k] = v
    return env
