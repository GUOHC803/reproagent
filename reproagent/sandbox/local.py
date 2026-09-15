"""Local subprocess sandbox.

Used for development, CI and the evaluation set (the fixtures are pure-Python
and need no image).  It applies: policy check -> env scrub -> cwd jail ->
rlimits (address space, CPU seconds, process count) -> wall-clock timeout with
process-group kill.  It does *not* isolate the network; use ``DockerSandbox``
for untrusted repositories.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .base import ExecResult
from .policy import CommandPolicy, scrub_env

_MAX_CAPTURE = 200_000  # chars kept per stream


def _parse_mem(limit: str) -> int:
    s = limit.strip().lower()
    mult = {"k": 1 << 10, "m": 1 << 20, "g": 1 << 30}
    if s[-1] in mult:
        return int(float(s[:-1]) * mult[s[-1]])
    return int(s)


class LocalSandbox:
    name = "local"

    def __init__(
        self,
        root: Path,
        *,
        policy: CommandPolicy | None = None,
        memory_limit: str = "2g",
        cpu_seconds: int = 600,
        max_procs: int = 128,
        python: str | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.policy = policy or CommandPolicy()
        self.memory_bytes = _parse_mem(memory_limit)
        self.cpu_seconds = cpu_seconds
        self.max_procs = max_procs
        # The interpreter the sandboxed code runs with (our venv by default so
        # fixtures can import numpy/pytest without a network install step).
        self.python = python or sys.executable

    def _preexec(self):
        def fn() -> None:
            os.setsid()  # own process group -> we can kill children on timeout
            try:
                import resource

                if self.memory_bytes:
                    resource.setrlimit(resource.RLIMIT_AS, (self.memory_bytes, self.memory_bytes))
                resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_seconds, self.cpu_seconds + 5))
                resource.setrlimit(resource.RLIMIT_NPROC, (self.max_procs, self.max_procs))
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            except (ImportError, ValueError, OSError):
                pass

        return fn

    def run(self, command: str, *, cwd: Path, timeout_s: float, env: dict[str, str] | None = None) -> ExecResult:
        cwd = Path(cwd).resolve()
        if cwd != self.root and self.root not in cwd.parents:
            return ExecResult(126, "", f"cwd {cwd} is outside sandbox root", 0.0, blocked_reason="cwd escape", command=command)
        decision = self.policy.check(command)
        if not decision.allowed:
            return ExecResult(126, "", decision.reason or "blocked", 0.0, blocked_reason=decision.reason, command=command)

        run_env = scrub_env(dict(os.environ), env)
        # Put our interpreter first so `python` resolves to the venv.
        py_dir = str(Path(self.python).parent)
        run_env["PATH"] = py_dir + os.pathsep + run_env.get("PATH", "/usr/bin:/bin")
        run_env["REPROAGENT_SANDBOX"] = "local"

        t0 = time.time()
        proc = subprocess.Popen(
            ["/bin/bash", "-o", "pipefail", "-c", command],
            cwd=str(cwd),
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            preexec_fn=self._preexec(),
            text=True,
            errors="replace",
        )
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = proc.communicate()
        dur = time.time() - t0
        code = proc.returncode if not timed_out else 124
        if len(out) > _MAX_CAPTURE:
            out = out[:_MAX_CAPTURE] + f"\n...[truncated {len(out) - _MAX_CAPTURE} chars]"
        if len(err) > _MAX_CAPTURE:
            err = err[-_MAX_CAPTURE:]
        if timed_out:
            err += f"\n[sandbox] command killed after {timeout_s}s timeout"
        return ExecResult(code, out, err, dur, timed_out=timed_out, command=command)
