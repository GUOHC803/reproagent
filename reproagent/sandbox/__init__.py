from pathlib import Path

from ..config import SandboxConfig
from .base import ExecResult, Sandbox
from .docker import DockerSandbox
from .local import LocalSandbox
from .policy import CommandPolicy, PolicyDecision, scrub_env


def build_sandbox(cfg: SandboxConfig, root: Path, *, confirm_callback=None) -> Sandbox:
    policy = CommandPolicy(confirm_mode=cfg.confirm_mode, confirm_callback=confirm_callback)
    if cfg.backend == "docker":
        if not DockerSandbox.available():
            raise RuntimeError("sandbox.backend=docker but the docker daemon is not reachable")
        return DockerSandbox(
            root,
            image=cfg.docker_image,
            policy=policy,
            memory_limit=cfg.memory_limit,
            cpu_limit=cfg.cpu_limit,
            pids_limit=cfg.pids_limit,
            network=cfg.network,
        )
    return LocalSandbox(root, policy=policy, memory_limit=cfg.memory_limit)


__all__ = [
    "ExecResult",
    "Sandbox",
    "LocalSandbox",
    "DockerSandbox",
    "CommandPolicy",
    "PolicyDecision",
    "scrub_env",
    "build_sandbox",
]
