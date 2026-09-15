# Sandbox image: what agent-generated commands run inside (DockerSandbox).
# No network at runtime (--network none), read-only root fs, non-root user, only /work is writable.
FROM python:3.12-slim
RUN pip install --no-cache-dir numpy pyyaml pytest && useradd -m -u 1000 runner
USER runner
WORKDIR /work
