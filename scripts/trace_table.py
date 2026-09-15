"""Print the step table of one run from a trace DB (plain text, for READMEs / docs).

    python scripts/trace_table.py evals/results/final/trace-<stamp>.sqlite3 <run_id prefix>
"""

from __future__ import annotations

import json
import sqlite3
import sys


def main() -> None:
    db, prefix = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    for run in c.execute("select run_id, status from runs where run_id like ? order by run_id", (prefix + "%",)):
        rid = run["run_id"]
        print(f"== {rid}  status={run['status']}")
        print(f"{'#':<3}{'node':<11}{'status':<7}{'failure':<16}{'tools':>5}{'llm':>4}{'tokens':>8}{'s':>6}  message")
        for s in c.execute("select idx, node, status, failure, result_json from steps where run_id=? order by idx", (rid,)):
            r = json.loads(s["result_json"])
            msg = (r.get("message") or "").replace("\n", " ")[:90]
            print(f"{s['idx']:<3}{s['node']:<11}{s['status']:<7}{(s['failure'] or ''):<16}{r['tool_calls']:>5}{r['llm_calls']:>4}"
                  f"{r['tokens']:>8}{r['duration_s']:>6.0f}  {msg}")


if __name__ == "__main__":
    main()
