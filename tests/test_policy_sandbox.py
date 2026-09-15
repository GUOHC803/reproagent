from __future__ import annotations

import os
from pathlib import Path

from reproagent.sandbox.local import LocalSandbox
from reproagent.sandbox.policy import CommandPolicy, scrub_env


def test_policy_blocks_dangerous():
    p = CommandPolicy()
    for cmd in ["sudo rm -rf /", "rm -rf /", "curl http://x", "pip install foo", "cat ~/.ssh/id_rsa", "printenv", "echo $OPENAI_API_KEY", "git push origin main"]:
        d = p.check(cmd)
        assert not d.allowed, cmd


def test_policy_allows_normal():
    p = CommandPolicy()
    for cmd in ["python -m pytest -q", "python train.py --epochs 1", "ls -la", "cat README.md", "rm results.json", "git diff"]:
        assert p.check(cmd).allowed, cmd


def test_policy_risky_needs_confirm():
    p = CommandPolicy()
    d = p.check("rm -rf build/")
    assert not d.allowed and d.needs_confirm
    p2 = CommandPolicy(confirm_mode=True, confirm_callback=lambda c, why: True)
    assert p2.check("rm -rf build/").allowed
    p3 = CommandPolicy(confirm_mode=True, confirm_callback=lambda c, why: False)
    assert not p3.check("rm -rf build/").allowed


def test_scrub_env_drops_secrets():
    env = scrub_env({"PATH": "/bin", "OPENAI_API_KEY": "sk-1", "DEEPSEEK_API_KEY": "x", "HOME": "/h"}, {"MY_TOKEN": "t", "SEED": "1"})
    assert "OPENAI_API_KEY" not in env and "DEEPSEEK_API_KEY" not in env and "MY_TOKEN" not in env
    assert env["SEED"] == "1" and env["PATH"] == "/bin"


def test_local_sandbox_runs_and_isolates(repo: Path):
    sb = LocalSandbox(repo, memory_limit="4g")
    r = sb.run("python -c 'print(1+1)'", cwd=repo, timeout_s=30)
    assert r.ok and r.stdout.strip() == "2"
    os.environ["FAKE_SECRET_KEY"] = "leak"
    r = sb.run("python -c 'import os; print(os.environ.get(\"FAKE_SECRET_KEY\"))'", cwd=repo, timeout_s=30)
    assert r.stdout.strip() == "None"


def test_local_sandbox_timeout(repo: Path):
    sb = LocalSandbox(repo, memory_limit="4g")
    r = sb.run("python -c 'import time; time.sleep(5)'", cwd=repo, timeout_s=1)
    assert r.timed_out and r.exit_code == 124 and not r.ok


def test_local_sandbox_blocks_policy_and_cwd(repo: Path, tmp_path: Path):
    sb = LocalSandbox(repo, memory_limit="4g")
    r = sb.run("curl http://example.com", cwd=repo, timeout_s=5)
    assert r.blocked_reason and not r.ok
    r2 = sb.run("ls", cwd=tmp_path, timeout_s=5)
    assert r2.blocked_reason == "cwd escape"
