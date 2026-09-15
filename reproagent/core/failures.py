"""Rule-based failure classifier.

Given a sandbox result (exit code, stderr, timeout flag) decide *which kind*
of failure it is.  Deterministic rules first because they are free and
explainable; the REPAIR node only asks the model for a diagnosis *after* the
kind is known, which makes the repair prompt much more targeted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..sandbox.base import ExecResult
from .state import FailureKind

_RULES: list[tuple[FailureKind, re.Pattern[str]]] = [
    (FailureKind.SYNTAX_ERROR, re.compile(r"\b(SyntaxError|IndentationError|TabError)\b")),
    (FailureKind.IMPORT_ERROR, re.compile(r"\b(ModuleNotFoundError|ImportError)\b")),
    (FailureKind.FILE_NOT_FOUND, re.compile(r"FileNotFoundError|No such file or directory|cannot find the file|can't open file")),
    (FailureKind.RESOURCE_LIMIT, re.compile(r"\bMemoryError\b|Killed\b|OOM|Cannot allocate memory|Resource temporarily unavailable")),
    (FailureKind.TEST_FAILURE, re.compile(r"(\d+) failed|FAILED |=+ FAILURES =+|AssertionError.*\n.*test_", re.DOTALL)),
    (FailureKind.ASSERTION_ERROR, re.compile(r"\bAssertionError\b")),
    (FailureKind.RUNTIME_ERROR, re.compile(r"Traceback \(most recent call last\)|\w+Error: |\w+Exception: ")),
]


@dataclass
class Diagnosis:
    kind: FailureKind
    evidence: str  # the line(s) that triggered the rule
    exception_type: str | None = None
    file: str | None = None
    line: int | None = None
    failing_tests: list[str] | None = None


_TB_LOC = re.compile(r'File "([^"]+)", line (\d+)')
_EXC = re.compile(r"^(\w+(?:\.\w+)*(?:Error|Exception|Exit|Interrupt|Warning)):", re.MULTILINE)
_PYTEST_FAIL = re.compile(r"^(?:FAILED|ERROR) ([^\s]+)", re.MULTILINE)


def classify_exec(res: ExecResult) -> Diagnosis | None:
    """Return None when the execution is fine."""
    if res.blocked_reason:
        return Diagnosis(FailureKind.POLICY_BLOCKED, res.blocked_reason)
    if res.timed_out:
        return Diagnosis(FailureKind.EXEC_TIMEOUT, f"timed out after {res.duration_s:.0f}s")
    if res.exit_code == 0:
        return None
    text = (res.stderr or "") + "\n" + (res.stdout or "")
    if res.exit_code in (137, -9) or "Killed" in text:
        return Diagnosis(FailureKind.RESOURCE_LIMIT, f"exit code {res.exit_code}")

    kind = FailureKind.NONZERO_EXIT
    evidence = f"exit code {res.exit_code}"
    for k, rx in _RULES:
        m = rx.search(text)
        if m:
            kind = k
            evidence = _context_line(text, m.start())
            break

    exc = None
    m_exc = list(_EXC.finditer(text))
    if m_exc:
        exc = m_exc[-1].group(1)
    file = line = None
    locs = _TB_LOC.findall(text)
    if locs:
        # last frame is usually the culprit; prefer frames inside the workdir (relative paths)
        for f, ln in reversed(locs):
            if "site-packages" not in f and not f.startswith("<"):
                file, line = f, int(ln)
                break
        if file is None:
            file, line = locs[-1][0], int(locs[-1][1])
    tests = _PYTEST_FAIL.findall(text) or None
    if tests:
        kind = FailureKind.TEST_FAILURE
    return Diagnosis(kind, evidence[:500], exc, file, line, tests)


def _context_line(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def error_tail(res: ExecResult, max_chars: int = 3000) -> str:
    """The part of the output a human would read first: tail of stderr, then tail of stdout."""
    err = (res.stderr or "").strip()
    out = (res.stdout or "").strip()
    combined = ""
    if err:
        combined += "stderr (tail):\n" + err[-max_chars:]
    if out:
        combined += "\nstdout (tail):\n" + out[-(max_chars // 2):]
    return combined.strip()
