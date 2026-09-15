"""Print a task x config matrix and failure details from an evaluation's rows.jsonl.

    python scripts/eval_matrix.py [evals/results/<stamp>]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) > 1:
        res = Path(sys.argv[1])
    else:
        res = sorted(p for p in (ROOT / "evals" / "results").iterdir() if (p / "rows.jsonl").exists())[-1]
    rows = [json.loads(line) for line in (res / "rows.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    cfgs = list(dict.fromkeys(r["config"] for r in rows))
    by: dict[str, dict[str, dict]] = defaultdict(dict)
    cat: dict[str, str] = {}
    for r in rows:
        by[r["task_id"]][r["config"]] = r
        cat[r["task_id"]] = r.get("category") or ""
    print(f"{'task':28s}{'cat':18s}" + "".join(f"{c:20s}" for c in cfgs))
    for t in sorted(by, key=lambda t: (cat[t], t)):
        cells = []
        for c in cfgs:
            r = by[t].get(c)
            if not r:
                cells.append("-")
            else:
                cells.append(("PASS" if r.get("success") else "FAIL") + f" r{r.get('repairs', 0)} {(r.get('tokens') or 0) // 1000}k {r.get('wall_time_s', 0):.0f}s")
        print(f"{t:28s}{cat[t]:18s}" + "".join(f"{x:20s}" for x in cells))
    print()
    for r in rows:
        if not r.get("success"):
            det = r.get("check_details") or [r.get("error", "")]
            bad = [d for d in det if "BAD" in d] or det[:1]
            print(f"FAIL {r['config']:16s} {r['task_id']:26s} {str(r.get('last_failure')):18s} | {bad[0][:110] if bad else ''}")


if __name__ == "__main__":
    main()
