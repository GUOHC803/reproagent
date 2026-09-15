"""SQLite trace store.

One file, five tables, no ORM.  The store answers three questions:

* **What happened?**  every node step, tool call and model call is a row with timing/cost.
* **Where was I?**    each step persists the full serialized ``TaskState``; ``resume``
  loads the latest one and the orchestrator continues from ``state.current``.
* **What did it produce?** artifacts are indexed by run so a report can cite them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  task_id TEXT,
  task_json TEXT NOT NULL,
  config_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  summary_json TEXT
);
CREATE TABLE IF NOT EXISTS steps (
  run_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  node TEXT NOT NULL,
  status TEXT NOT NULL,
  failure TEXT,
  result_json TEXT NOT NULL,
  state_json TEXT NOT NULL,
  duration_s REAL,
  ts REAL NOT NULL,
  PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  step_idx INTEGER NOT NULL,
  node TEXT NOT NULL,
  name TEXT NOT NULL,
  args_json TEXT NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT NOT NULL,
  duration_s REAL,
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  step_idx INTEGER NOT NULL,
  node TEXT NOT NULL,
  model TEXT,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  cost_usd REAL,
  latency_s REAL,
  n_tool_calls INTEGER,
  messages_json TEXT,
  response_json TEXT,
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  step_idx INTEGER,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  kind TEXT,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, idx);
CREATE INDEX IF NOT EXISTS idx_tool_run ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_llm_run ON llm_calls(run_id);
"""


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class TraceStore:
    def __init__(self, path: Path | str, *, record_messages: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.record_messages = record_messages
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    # ---- runs -----------------------------------------------------------
    def create_run(self, run_id: str, task: dict[str, Any], config: dict[str, Any]) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs(run_id, task_id, task_json, config_json, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, task.get("task_id"), _dump(task), _dump(config), "running", now, now),
            )

    def set_run_status(self, run_id: str, status: str, summary: dict[str, Any] | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET status=?, updated_at=?, summary_json=COALESCE(?, summary_json) WHERE run_id=?",
                (status, time.time(), _dump(summary) if summary is not None else None, run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_run(r) for r in rows]

    # ---- steps ----------------------------------------------------------
    def save_step(self, run_id: str, idx: int, node: str, result: dict[str, Any], state: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO steps(run_id, idx, node, status, failure, result_json, state_json, duration_s, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    idx,
                    node,
                    result.get("status", ""),
                    result.get("failure"),
                    _dump(result),
                    _dump(state),
                    result.get("duration_s"),
                    time.time(),
                ),
            )
            self._conn.execute("UPDATE runs SET updated_at=? WHERE run_id=?", (time.time(), run_id))

    def latest_state(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT state_json FROM steps WHERE run_id=? ORDER BY idx DESC LIMIT 1", (run_id,)
        ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def steps(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM steps WHERE run_id=? ORDER BY idx", (run_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["result"] = json.loads(d.pop("result_json"))
            d.pop("state_json", None)
            out.append(d)
        return out

    # ---- tool / llm calls ----------------------------------------------
    def log_tool_call(
        self, run_id: str, step_idx: int, node: str, name: str, args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tool_calls(run_id, step_idx, node, name, args_json, status, result_json, duration_s, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, step_idx, node, name, _dump(args), result.get("status", ""), _dump(result),
                 result.get("duration_s"), time.time()),
            )

    def log_llm_call(
        self,
        run_id: str,
        step_idx: int,
        node: str,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_s: float,
        n_tool_calls: int,
        messages: list[dict[str, Any]] | None,
        response: dict[str, Any] | None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO llm_calls(run_id, step_idx, node, model, prompt_tokens, completion_tokens, cost_usd, "
                "latency_s, n_tool_calls, messages_json, response_json, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, step_idx, node, model, prompt_tokens, completion_tokens, cost_usd, latency_s, n_tool_calls,
                    _dump(messages) if (self.record_messages and messages is not None) else None,
                    _dump(response) if response is not None else None,
                    time.time(),
                ),
            )

    def tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM tool_calls WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["args"] = json.loads(d.pop("args_json"))
            d["result"] = json.loads(d.pop("result_json"))
            out.append(d)
        return out

    def llm_calls(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, run_id, step_idx, node, model, prompt_tokens, completion_tokens, cost_usd, latency_s, "
            "n_tool_calls, ts FROM llm_calls WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def llm_messages(self, run_id: str) -> Iterator[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT step_idx, node, messages_json, response_json FROM llm_calls WHERE run_id=? ORDER BY id", (run_id,)
        )
        for r in rows:
            yield {
                "step_idx": r["step_idx"],
                "node": r["node"],
                "messages": json.loads(r["messages_json"]) if r["messages_json"] else None,
                "response": json.loads(r["response_json"]) if r["response_json"] else None,
            }

    # ---- artifacts --------------------------------------------------------
    def add_artifact(self, run_id: str, step_idx: int | None, name: str, path: str, kind: str = "file") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO artifacts(run_id, step_idx, name, path, kind, ts) VALUES (?,?,?,?,?,?)",
                (run_id, step_idx, name, path, kind, time.time()),
            )

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- aggregates -----------------------------------------------------
    def run_metrics(self, run_id: str) -> dict[str, Any]:
        llm = self._conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct, "
            "COALESCE(SUM(cost_usd),0) cost, COALESCE(SUM(latency_s),0) lat FROM llm_calls WHERE run_id=?",
            (run_id,),
        ).fetchone()
        tools = self._conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) n_bad FROM tool_calls WHERE run_id=?",
            (run_id,),
        ).fetchone()
        steps = self._conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN node='repair' THEN 1 ELSE 0 END) n_repair, "
            "COALESCE(SUM(duration_s),0) dur FROM steps WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return {
            "llm_calls": llm["n"],
            "prompt_tokens": llm["pt"],
            "completion_tokens": llm["ct"],
            "total_tokens": llm["pt"] + llm["ct"],
            "cost_usd": llm["cost"],
            "llm_latency_s": llm["lat"],
            "tool_calls": tools["n"],
            "tool_calls_failed": tools["n_bad"] or 0,
            "steps": steps["n"],
            "repairs": steps["n_repair"] or 0,
            "wall_time_s": steps["dur"],
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["task"] = json.loads(d.pop("task_json"))
    d["config"] = json.loads(d.pop("config_json"))
    d["summary"] = json.loads(d["summary_json"]) if d.get("summary_json") else None
    d.pop("summary_json", None)
    return d
