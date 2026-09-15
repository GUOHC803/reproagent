"""Command-line interface.

    reproagent run  "goal" --dir ./repo [--pdf paper.pdf] [--command "pytest -q"] [--config full|no_repair|single_call]
    reproagent run-task task.yaml --dir ./fixtures/...
    reproagent resume <run_id>
    reproagent runs / show <run_id> / replay <run_id>
    reproagent eval  [--tasks evals/tasks] [--configs full,no_repair,single_call]
    reproagent serve --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from .config import AgentConfig, load_settings
from .core.state import TaskSpec
from .store.trace import TraceStore

app = typer.Typer(add_completion=False, help="ReproAgent - a controllable agent for research-code tasks.")
console = Console()


def _log(s: str) -> None:
    console.print(f"[dim]{s}[/dim]")


def _load_config(name: str | None, model: str | None) -> AgentConfig:
    from .evals.configs import get_config

    cfg = get_config(name or "full")
    if model:
        cfg = cfg.with_updates(model=model)
    return cfg


@app.command()
def run(
    goal: str = typer.Argument(..., help="What to do."),
    dir: Path | None = typer.Option(None, "--dir", "-d", help="Repository / material directory (copied)."),
    pdf: str | None = typer.Option(None, help="PDF path relative to --dir."),
    command: str | None = typer.Option(None, help="Command EXECUTE should run."),
    criteria: str = typer.Option("", help="Success criteria for VERIFY."),
    category: str = typer.Option("custom", help="paper_extraction|repo_locate|repo_run|bug_fix|custom"),
    config: str = typer.Option("full", help="full | no_repair | single_call | free_text_tools | no_verify"),
    model: str | None = typer.Option(None, help="LiteLLM model name, e.g. deepseek/deepseek-chat"),
    sandbox: str | None = typer.Option(None, help="local | docker"),
    no_copy: bool = typer.Option(False, help="Work in --dir directly instead of a copy (dangerous)."),
):
    """Run one task."""
    from .runner import run_task

    cfg = _load_config(config, model)
    if sandbox:
        cfg = cfg.with_updates(sandbox=cfg.sandbox.model_copy(update={"backend": sandbox}))
    inputs = {}
    if pdf:
        inputs["pdf"] = pdf
    if command:
        inputs["command"] = command
    task = TaskSpec(goal=goal, category=category, inputs=inputs, success_criteria=criteria)  # type: ignore[arg-type]
    h = run_task(task, cfg=cfg, source_dir=dir, copy_source=not no_copy, log=_log)
    _print_result(h)


@app.command("run-task")
def run_task_cmd(
    task_file: Path = typer.Argument(..., help="YAML/JSON task spec."),
    dir: Path | None = typer.Option(None, "--dir", "-d", help="Material directory (default: task's `source_dir`)."),
    config: str = typer.Option("full"),
    model: str | None = typer.Option(None),
    sandbox: str | None = typer.Option(None),
):
    """Run a task defined in a file (same format as evals/tasks/*.yaml)."""
    from .runner import run_task

    raw = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    src = dir or (task_file.parent / raw["source_dir"] if raw.get("source_dir") else None)
    if raw.get("inject") and src is not None:
        from .evals.runner import prepare_source

        raw["_fixture"] = Path(src).resolve()
        src = prepare_source(raw)  # apply the declared bug injection to a temp copy
        console.print(f"[dim]injected bug(s): {raw.get('bug_note', '')}[/dim]")
    task = TaskSpec.model_validate({k: v for k, v in raw.items() if k in TaskSpec.model_fields})
    cfg = _load_config(config, model)
    if sandbox:
        cfg = cfg.with_updates(sandbox=cfg.sandbox.model_copy(update={"backend": sandbox}))
    h = run_task(task, cfg=cfg, source_dir=src, log=_log)
    _print_result(h)


@app.command()
def resume(run_id: str):
    """Continue an interrupted run from its last checkpoint."""
    from .runner import resume_run

    h = resume_run(run_id, log=_log)
    _print_result(h)


@app.command()
def runs(limit: int = 20):
    """List recent runs."""
    st = load_settings()
    store = TraceStore(st.resolved_db_path)
    t = Table("run_id", "task", "status", "steps", "repairs", "tokens", "cost$", "s")
    for r in store.list_runs(limit):
        s = r.get("summary") or {}
        t.add_row(r["run_id"], (r["task_id"] or "")[:28], r["status"], str(s.get("steps", "")), str(s.get("repairs", "")),
                  str(s.get("tokens", "")), f"{s.get('cost_usd', 0):.4f}" if s else "", str(s.get("wall_time_s", "")))
    console.print(t)


@app.command()
def show(run_id: str):
    """Show the step trace of a run."""
    st = load_settings()
    store = TraceStore(st.resolved_db_path)
    run = store.get_run(run_id)
    if not run:
        raise typer.BadParameter(f"unknown run {run_id}")
    console.print(f"[bold]{run_id}[/bold]  status={run['status']}  task={run['task'].get('goal', '')[:100]}")
    t = Table("#", "node", "status", "failure", "message", "tools", "llm", "tokens", "s")
    for s in store.steps(run_id):
        r = s["result"]
        t.add_row(str(s["idx"]), s["node"], r["status"], r.get("failure") or "", (r.get("message") or "")[:70],
                  str(r.get("tool_calls", 0)), str(r.get("llm_calls", 0)), str(r.get("tokens", 0)), f"{r.get('duration_s', 0):.1f}")
    console.print(t)
    console.print(json.dumps(store.run_metrics(run_id), indent=2))
    arts = store.artifacts(run_id)
    if arts:
        console.print("artifacts: " + ", ".join(a["path"] for a in arts))


@app.command()
def replay(run_id: str, node: str | None = None):
    """Print the model conversation of a run (optionally one node) - for debugging prompts."""
    st = load_settings()
    store = TraceStore(st.resolved_db_path)
    for call in store.llm_messages(run_id):
        if node and call["node"] != node:
            continue
        console.rule(f"step {call['step_idx']} / {call['node']}")
        for m in call["messages"] or []:
            console.print(f"[bold]{m.get('role')}[/bold]: {str(m.get('content'))[:1500]}")
        console.print("[bold green]assistant[/bold green]: " + json.dumps(call["response"], ensure_ascii=False)[:1500])


@app.command()
def eval(
    tasks: Path = typer.Option(Path("evals/tasks"), help="Task directory."),
    configs: str = typer.Option("full", help="Comma-separated config names."),
    model: str | None = typer.Option(None),
    only: str | None = typer.Option(None, help="Comma-separated task ids."),
    repeats: int = typer.Option(1, help="Runs per task (results are aggregated)."),
    out: Path = typer.Option(Path("evals/results"), help="Where to write results."),
    sandbox: str | None = typer.Option(None),
    workers: int = typer.Option(1, help="Parallel tasks."),
):
    """Run the offline evaluation set."""
    from .evals.runner import run_eval

    run_eval(tasks_dir=tasks, config_names=[c.strip() for c in configs.split(",") if c.strip()], model=model,
             only=[t.strip() for t in only.split(",")] if only else None, repeats=repeats, out_dir=out,
             sandbox=sandbox, workers=workers, log=_log)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000):
    """Start the HTTP API."""
    import uvicorn

    uvicorn.run("reproagent.api:app", host=host, port=port)


@app.command()
def tools():
    """Print the tool schemas the model sees."""
    from .tools import default_registry

    console.print_json(json.dumps(default_registry().schemas()))


def _print_result(h) -> None:
    s = h.state
    color = "green" if s.status == "done" else "red"
    console.print(f"[{color}]{s.status.upper()}[/{color}] run={s.run_id} steps={s.step_idx} repairs={s.repairs_used} "
                  f"tools={s.tool_calls_total} llm={s.llm_calls_total} tokens={s.tokens_total} cost=${s.cost_usd:.4f}")
    if s.final_answer:
        console.print_json(json.dumps(s.final_answer, ensure_ascii=False, default=str))
    if s.last_failure and s.status != "done":
        console.print(f"[red]last failure:[/red] {s.last_failure.value}: {s.last_failure_detail[:300]}")
    if s.report_path:
        console.print(f"report: {s.report_path}")


if __name__ == "__main__":
    app()
