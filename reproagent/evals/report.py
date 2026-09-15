"""Aggregate evaluation rows into summary.json / summary.md."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def _rate(rows: list[dict[str, Any]]) -> float:
    return sum(1 for r in rows if r.get("success")) / len(rows) if rows else 0.0


def _avg(rows: list[dict[str, Any]], key: str) -> float:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return mean(vals) if vals else 0.0


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_cfg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_cfg[r["config"]].append(r)
    categories = sorted({r.get("category") or "custom" for r in rows})
    out: dict[str, Any] = {"configs": {}, "categories": categories, "n_rows": len(rows)}
    for cfg, rs in by_cfg.items():
        entry = {
            "n": len(rs),
            "success_rate": _rate(rs),
            "by_category": {c: {"n": len([r for r in rs if r.get("category") == c]),
                                "success_rate": _rate([r for r in rs if r.get("category") == c])} for c in categories},
            "avg_repairs": _avg(rs, "repairs"),
            "avg_tool_calls": _avg(rs, "tool_calls"),
            "avg_tool_calls_failed": _avg(rs, "tool_calls_failed"),
            "avg_llm_calls": _avg(rs, "llm_calls"),
            "avg_tokens": _avg(rs, "tokens"),
            "avg_cost_usd": _avg(rs, "cost_usd"),
            "avg_wall_time_s": _avg(rs, "wall_time_s"),
            "success_after_repair": len([r for r in rs if r.get("success") and (r.get("repairs") or 0) > 0]),
            "success_first_try": len([r for r in rs if r.get("success") and not r.get("repairs")]),
            "claimed_but_wrong": len([r for r in rs if r.get("agent_claimed_success") and not r.get("success")]),
            "failure_kinds": dict(Counter(r.get("last_failure") for r in rs if not r.get("success") and r.get("last_failure"))),
            "failures_seen": dict(Counter(k for r in rs for k in (r.get("failures_seen") or []))),
        }
        out["configs"][cfg] = entry
    return out


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    cfgs = list(summary["configs"])
    L = [f"# ReproAgent evaluation - {meta.get('timestamp', '')}", "",
         f"model: `{meta.get('model')}` | tasks: {len(meta.get('tasks', []))} | repeats: {meta.get('repeats', 1)} | rows: {summary['n_rows']}", "",
         "## Success rate by configuration", "",
         "| config | n | success | " + " | ".join(summary["categories"]) + " | avg repairs | avg tool calls | avg model calls | avg tokens | avg cost $ | avg s |",
         "|---|---|---|" + "---|" * len(summary["categories"]) + "---|---|---|---|---|---|"]
    for c in cfgs:
        e = summary["configs"][c]
        cats = " | ".join(f"{e['by_category'][k]['success_rate']:.0%} ({e['by_category'][k]['n']})" for k in summary["categories"])
        L.append(f"| {c} | {e['n']} | **{e['success_rate']:.1%}** | {cats} | {e['avg_repairs']:.2f} | {e['avg_tool_calls']:.1f} | "
                 f"{e['avg_llm_calls']:.1f} | {e['avg_tokens']:.0f} | {e['avg_cost_usd']:.4f} | {e['avg_wall_time_s']:.0f} |")
    L += ["", "## Repair and verification", "", "| config | solved first try | solved after repair | claimed success but wrong |", "|---|---|---|---|"]
    for c in cfgs:
        e = summary["configs"][c]
        L.append(f"| {c} | {e['success_first_try']} | {e['success_after_repair']} | {e['claimed_but_wrong']} |")
    L += ["", "## Failure kinds (final, unsuccessful runs)", ""]
    for c in cfgs:
        e = summary["configs"][c]
        fk = ", ".join(f"{k}: {v}" for k, v in sorted(e["failure_kinds"].items(), key=lambda kv: -kv[1])) or "none"
        seen = ", ".join(f"{k}: {v}" for k, v in sorted(e["failures_seen"].items(), key=lambda kv: -kv[1])) or "none"
        L.append(f"- **{c}** - final: {fk}; encountered during runs (incl. repaired): {seen}")
    L += ["", "## Per-task results", "", "| task | category | " + " | ".join(cfgs) + " |", "|---|---|" + "---|" * len(cfgs)]
    tasks = sorted({(r["task_id"], r.get("category") or "") for r in rows}, key=lambda x: (x[1], x[0]))
    for tid, cat in tasks:
        cells = []
        for c in cfgs:
            rs = [r for r in rows if r["task_id"] == tid and r["config"] == c]
            if not rs:
                cells.append("-")
                continue
            ok = sum(1 for r in rs if r.get("success"))
            rep = "/".join(str(r.get("repairs", "?")) for r in rs)
            cells.append(f"{'✅' if ok == len(rs) else ('⚠️' if ok else '❌')} {ok}/{len(rs)} (rep {rep})")
        L.append(f"| {tid} | {cat} | " + " | ".join(cells) + " |")
    L += ["", "Legend: ✅ all repeats passed, ⚠️ some, ❌ none; `rep` = repair rounds per repeat.", ""]
    return "\n".join(L)


def write_summary(rows: list[dict[str, Any]], out_dir: Path, meta: dict[str, Any]) -> None:
    s = summarize(rows)
    s["meta"] = meta
    (out_dir / "summary.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_markdown(s, rows, meta), encoding="utf-8")


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
