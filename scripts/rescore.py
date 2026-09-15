"""Re-apply the deterministic checkers to finished evaluation rows and (optionally) merge result dirs.

Use when a checker rule changed after a batch ran: the agent's stored answer and its work directory
are re-checked, nothing is re-run through the model.

    python scripts/rescore.py evals/results/<stampA> [evals/results/<stampB> ...] -o evals/results/final
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from reproagent.evals.checkers import run_checks
from reproagent.evals.report import write_summary
from reproagent.evals.runner import load_tasks

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, default=ROOT / "evals" / "tasks")
    ap.add_argument("--override", action="store_true",
                    help="later dirs replace earlier rows with the same (task, config, repeat) - for re-runs")
    args = ap.parse_args()
    tasks = {t["task_id"]: t for t in load_tasks(args.tasks)}
    rows, changed = [], 0
    for d in args.dirs:
        for line in (d / "rows.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            t = tasks.get(r["task_id"])
            work = d / "runs" / r.get("run_id", "") / "work"
            if t and r.get("run_id") and work.is_dir():
                chk = run_checks(t, workdir=work, fixture_dir=t["_fixture"], answer=r.get("answer") or {})
                if chk.passed != bool(r.get("success")):
                    changed += 1
                    print(f"  {r['config']}/{r['task_id']}: {r.get('success')} -> {chk.passed}")
                r["success"], r["check_details"], r["rescored_from"] = chk.passed, chk.details, str(d.name)
            key = (r["task_id"], r["config"], r.get("repeat", 0))
            if args.override:
                old = next((i for i, x in enumerate(rows) if (x["task_id"], x["config"], x.get("repeat", 0)) == key), None)
                if old is not None:
                    print(f"  override {key} from {rows[old].get('rescored_from')} with {d.name}")
                    rows[old] = r
                    continue
            rows.append(r)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "rows.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    metas = [json.loads((d / "summary.json").read_text(encoding="utf-8")).get("meta", {}) for d in args.dirs if (d / "summary.json").exists()]
    meta = {"model": metas[0].get("model") if metas else None, "configs": sorted({r["config"] for r in rows}),
            "tasks": sorted({r["task_id"] for r in rows}), "repeats": max([m.get("repeats", 1) for m in metas] or [1]),
            "timestamp": time.strftime("%Y%m%d-%H%M%S"), "merged_from": [d.name for d in args.dirs]}
    write_summary(rows, args.out, meta)
    for d in args.dirs:  # keep the trace DBs reachable from the merged dir
        if (d / "trace.sqlite3").exists():
            shutil.copyfile(d / "trace.sqlite3", args.out / f"trace-{d.name}.sqlite3")
    print(f"{len(rows)} rows, {changed} verdicts changed -> {args.out / 'summary.md'}")


if __name__ == "__main__":
    main()
