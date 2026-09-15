"""Docker sandbox: the same contract as ``LocalSandbox`` but with real isolation.

* ``--network none``      : no data exfiltration, no surprise installs
* ``--memory/--cpus/--pids-limit`` : cgroup limits instead of rlimits
* ``--read-only`` root fs + a writable bind mount of the task workdir only
* ``--user`` non-root, ``--cap-drop ALL``, ``--security-opt no-new-privileges``
* wall-clock timeout enforced from outside via ``docker kill``
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .base import ExecResult
from .policy import CommandPolicy, scrub_env


class DockerSandbox:
    name = "docker"

    def __init__(
        self,
        root: Path,
        *,
        image: str = "reproagent-sandbox:latest",
        policy: CommandPolicy | None = None,
        memory_limit: str = "2g",
        cpu_limit: float = 2.0,
        pids_limit: int = 256,
        network: str = "none",
        mount_path: str = "/work",
    ) -> None:
        self.root = Path(root).resolve()
        self.image = image
        self.policy = policy or CommandPolicy()
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.network = network
        self.mount_path = mount_path

    @staticmethod
    def available() -> bool:
        if not shutil.which("docker"):
            return False
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
            return r.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    def run(self, command: str, *, cwd: Path, timeout_s: float, env: dict[str, str] | None = None) -> ExecResult:
        cwd = Path(cwd).resolve()
        if cwd != self.root and self.root not in cwd.parents:
            return ExecResult(126, "", f"cwd {cwd} is outside sandbox root", 0.0, blocked_reason="cwd escape", command=command)
        decision = self.policy.check(command)
        if not decision.allowed:
            return ExecResult(126, "", decision.reason or "blocked", 0.0, blocked_reason=decision.reason, command=command)

        rel = cwd.relative_to(self.root).as_posix()
        workdir = self.mount_path if rel == "." else f"{self.mount_path}/{rel}"
        name = f"reproagent-{uuid.uuid4().hex[:10]}"
        run_env = scrub_env({}, env)
        args = [
            "docker", "run", "--rm", "--name", name,
            "--network", self.network,
            "--memory", self.memory_limit,
            "--cpus", str(self.cpu_limit),
            "--pids-limit", str(self.pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:rw,size=512m",
            "-v", f"{self.root}:{self.mount_path}:rw",
            "-w", workdir,
            "--user", f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000",
        ]
        for k, v in run_env.items():
            args += ["-e", f"{k}={v}"]
        args += ["-e", "REPROAGENT_SANDBOX=docker", self.image, "bash", "-o", "pipefail", "-c", command]

        t0 = time.time()
        timed_out = False
        try:
            proc = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout_s)
            out, err, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            subprocess.run(["docker", "kill", name], capture_output=True)
            out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            err += f"\n[sandbox] container killed after {timeout_s}s timeout"
            code = 124
        return ExecResult(code, out, err, time.time() - t0, timed_out=timed_out, command=command, meta={"container": name})
