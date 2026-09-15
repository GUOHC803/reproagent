"""Deterministic task checkers.

A task lists one or more checks; all must pass.  Checkers never call a model:
success in the evaluation is decided by exact/tolerant comparison against
ground truth that is either written in the task file or *computed* from the
pristine fixture (``reference_json`` / ``reference_script``), so a change in
the fixture cannot silently invalidate the expected values.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    passed: bool
    details: list[str] = field(default_factory=list)


def _norm(s: Any) -> str:
    return re.sub(r"[\s_\-\.,;:'\"`*]+", "", str(s).lower())


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").replace("×", "x")
        m = re.search(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?", s, re.IGNORECASE)
        if m:
            x = float(m.group(0))
            tail = s[m.end():m.end() + 8].lower()
            if tail.startswith("million") or tail.startswith(" million") or tail.startswith("m ") and "mb" not in tail:
                x *= 1e6
            if "^4" in s or "×10^4" in s or "x10^4" in s:
                x *= 1e4
            return x
    return None


def match_value(expected: Any, actual: Any, tol: float = 1e-2) -> bool:
    """Tolerant comparison used by the ``fields`` check.

    * list expected -> every element must be found in the actual value (as text)
    * bool expected -> actual must be the same boolean (or 'true'/'false' text)
    * numeric expected -> actual parsed as number, relative tolerance ``tol``
    * string expected -> normalized substring match either way
    """
    if actual is None:
        return False
    if isinstance(expected, list):
        a_text = _norm(json.dumps(actual, ensure_ascii=False) if not isinstance(actual, str) else actual)
        return all(_norm(e) in a_text for e in expected)
    if isinstance(expected, bool):
        if isinstance(actual, bool):
            return actual == expected
        return _norm(actual) in ({"true", "yes", "1"} if expected else {"false", "no", "0", "none"})
    if isinstance(expected, (int, float)):
        a = _num(actual)
        if a is None:
            return False
        if expected == 0:
            return abs(a) < 1e-9
        return math.isclose(a, float(expected), rel_tol=tol, abs_tol=1e-9)
    e_text, a_text = _norm(expected), _norm(json.dumps(actual, ensure_ascii=False) if not isinstance(actual, str) else actual)
    return e_text in a_text or (a_text and a_text in e_text and len(a_text) >= max(3, len(e_text) // 2))


def check_fields(expected: dict[str, Any], answer: dict[str, Any], tol: float = 1e-2) -> CheckResult:
    answer = answer or {}
    lowered = {str(k).lower(): v for k, v in answer.items()}
    details, ok = [], True
    for k, exp in expected.items():
        act = answer.get(k, lowered.get(k.lower()))
        good = match_value(exp, act, tol)
        ok &= good
        details.append(f"{'ok ' if good else 'BAD'} {k}: expected {exp!r}, got {act!r}")
    return CheckResult(ok, details)


def _hash_tree(root: Path, rel_paths: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in rel_paths:
        p = root / rel
        if p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    out[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def _run(cmd: str, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not re.search(r"KEY|TOKEN|SECRET", k)}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return subprocess.run(["/bin/bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def check_tests_pass(workdir: Path, fixture_dir: Path, command: str, protected: list[str]) -> CheckResult:
    details = []
    before = _hash_tree(fixture_dir, protected)
    after = _hash_tree(workdir, protected)
    changed = [k for k in before if after.get(k) != before[k]] + [k for k in after if k not in before]
    if changed:
        details.append(f"BAD protected files modified: {changed}")
        return CheckResult(False, details)
    details.append("ok protected files untouched")
    r = _run(command, workdir)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout + r.stderr).strip() else ""
    details.append(f"{'ok ' if r.returncode == 0 else 'BAD'} `{command}` exit={r.returncode}: {tail}")
    return CheckResult(r.returncode == 0, details)


def _reference_from_command(fixture_dir: Path, command: str, file: str) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="ref-"))
    try:
        shutil.copytree(fixture_dir, tmp / "ref", dirs_exist_ok=True)
        r = _run(command, tmp / "ref")
        if r.returncode != 0:
            raise RuntimeError(f"reference command failed: {r.stderr[-500:]}")
        return json.loads((tmp / "ref" / file).read_text())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _reference_from_script(fixture_dir: Path, script: str) -> dict[str, Any]:
    r = subprocess.run([sys.executable, "-c", script], cwd=fixture_dir, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"reference script failed: {r.stderr[-500:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _compare_json(expected: Any, actual: Any, tol: float, path: str = "") -> list[str]:
    bad: list[str] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path or '$'}: expected object"]
        for k, v in expected.items():
            if k not in actual:
                bad.append(f"{path}.{k}: missing")
            else:
                bad += _compare_json(v, actual[k], tol, f"{path}.{k}")
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        a = _num(actual)
        if a is None or not math.isclose(a, float(expected), rel_tol=tol, abs_tol=tol):
            bad.append(f"{path}: expected {expected}, got {actual!r}")
    elif expected != actual and _norm(expected) != _norm(actual):
        bad.append(f"{path}: expected {expected!r}, got {actual!r}")
    return bad


def run_checks(task: dict[str, Any], *, workdir: Path, fixture_dir: Path, answer: dict[str, Any]) -> CheckResult:
    all_ok, details = True, []
    for chk in task.get("check", []):
        t = chk["type"]
        try:
            if t == "fields":
                r = check_fields(chk["expected"], answer, chk.get("tolerance", 1e-2))
            elif t == "tests_pass":
                r = check_tests_pass(workdir, fixture_dir, chk["command"], chk.get("protected", ["tests"]))
            elif t in ("reference_json", "reference_script"):
                exp = (_reference_from_command(fixture_dir, chk["command"], chk["file"]) if t == "reference_json"
                       else _reference_from_script(fixture_dir, chk["script"]))
                p = workdir / chk["file"]
                if not p.exists():
                    r = CheckResult(False, [f"BAD {chk['file']} not produced"])
                else:
                    act = json.loads(p.read_text())
                    if chk.get("fields"):
                        exp = {k: exp[k] for k in chk["fields"] if k in exp}
                    bad = _compare_json(exp, act, chk.get("tolerance", 1e-4))
                    r = CheckResult(not bad, [f"{'ok ' if not bad else 'BAD'} {chk['file']} vs reference" + (": " + "; ".join(bad[:6]) if bad else "")])
            elif t in ("fields_from_reference", "fields_from_script"):
                if t == "fields_from_reference":
                    p = workdir / chk["file"]
                    ref = json.loads(p.read_text()) if p.exists() else {}
                    exp = {k: ref.get(src) for k, src in chk["map"].items()}
                else:
                    exp = _reference_from_script(fixture_dir, chk["script"])
                r = check_fields(exp, answer, chk.get("tolerance", 1e-3))
            else:
                r = CheckResult(False, [f"BAD unknown check type {t}"])
        except Exception as e:  # noqa: BLE001
            r = CheckResult(False, [f"BAD check {t} crashed: {type(e).__name__}: {e}"])
        all_ok &= r.passed
        details += [f"[{t}] {d}" for d in r.details]
    return CheckResult(all_ok, details)
