"""Failure statistics on one basis: every failed node step across the given trace DBs.

    python scripts/failure_stats.py evals/results/final/trace-*.sqlite3 [--prefix full-]
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter


def main() -> None:
    dbs = [a for a in sys.argv[1:] if not a.startswith("--")]
    prefix = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--prefix=")), "")
    kind: Counter[str] = Counter()
    node: Counter[str] = Counter()
    pair: Counter[tuple[str, str]] = Counter()
    runs = set()
    for db in dbs:
        c = sqlite3.connect(db)
        for rid, n, f in c.execute("select run_id, node, failure from steps where status='fail'"):
            if not rid.startswith(prefix):
                continue
            runs.add(rid)
            kind[f or "unknown"] += 1
            node[n] += 1
            pair[(n, f or "unknown")] += 1
    t = sum(kind.values())
    print(f"failed steps: {t} (in {len(runs)} runs){' prefix=' + prefix if prefix else ''}")
    print("by kind:")
    for k, v in kind.most_common():
        print(f"  {k:18s} {v:3d}  {v / t:5.0%}")
    print("by node:")
    for k, v in node.most_common():
        print(f"  {k:18s} {v:3d}  {v / t:5.0%}")
    print("node x kind:")
    for (n, k), v in pair.most_common():
        print(f"  {n:10s} {k:18s} {v}")


if __name__ == "__main__":
    main()
