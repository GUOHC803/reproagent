"""run_tests: run pytest (or another test command) in the sandbox and parse the outcome."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..core.failures import classify_exec
from .base import Tool, ToolContext, ToolResult

_SUMMARY = re.compile(r"=+ (.*?) in [\d.]+s")
_COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|warnings?)")
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
_NO_TESTS = re.compile(r"no tests ran|collected 0 items")


class RunTestsParams(BaseModel):
    command: str = Field("python -m pytest -q -x --no-header -p no:cacheprovider",
                         description="Test command. Default runs pytest quietly, stopping at first failure.")
    path: str = Field(".", description="Directory to run in, relative to workdir.")
    timeout_s: int | None = Field(None, ge=5, le=1800, description="Override the default timeout.")


class RunTestsTool(Tool):
    name = "run_tests"
    description = (
        "Run the test suite (pytest by default) inside the sandbox. Returns pass/fail counts, the names of failing "
        "tests and the tail of the traceback. Use it after every code change; use `-k name` in the command to run "
        "a single test."
    )
    Params = RunTestsParams

    def run(self, p: RunTestsParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        cwd = ctx.resolve(p.path)
        res = ctx.sandbox.run(p.command, cwd=cwd, timeout_s=p.timeout_s or ctx.default_timeout_s)
        text = res.stdout + "\n" + res.stderr
        counts = {k: int(v) for v, k in _COUNT.findall(text)}
        failed = _FAILED_LINE.findall(text)
        summary = _SUMMARY.findall(text)
        data = {
            "exit_code": res.exit_code,
            "passed": counts.get("passed", 0),
            "failed": counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0),
            "failing_tests": failed,
            "summary": summary[-1] if summary else "",
            "duration_s": round(res.duration_s, 2),
        }
        if res.blocked_reason:
            return ToolResult(status="blocked", stderr=res.stderr, data=data, next_hint="Adjust the command.")
        if res.timed_out:
            return ToolResult(status="timeout", stdout=_tail(res.stdout), stderr=_tail(res.stderr), data=data,
                              next_hint="Tests hung. Look for infinite loops or run a single test with -k.")
        if res.exit_code == 0 and not _NO_TESTS.search(text):
            return ToolResult(status="ok", stdout=_tail(res.stdout, 1500), data=data,
                              next_hint=f"All tests passed ({data['passed']} passed).")
        if _NO_TESTS.search(text):
            return ToolResult(status="partial", stdout=_tail(res.stdout), stderr=_tail(res.stderr), data=data,
                              next_hint="No tests were collected. Check the path or the test command.")
        diag = classify_exec(res)
        hint = "Tests failed."
        if failed:
            hint = f"{len(failed)} failing: {', '.join(failed[:5])}. Read the traceback and fix the implementation."
        elif diag:
            hint = f"{diag.kind.value}: {diag.evidence}"
        return ToolResult(status="error", stdout=_tail(res.stdout), stderr=_tail(res.stderr), data=data, next_hint=hint)


def _tail(s: str, n: int = 4000) -> str:
    return s if len(s) <= n else "...\n" + s[-n:]
