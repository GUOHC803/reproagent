# ReproAgent: a controllable agent for research-code reproduction tasks

[中文](README.md)

ReproAgent takes a paper, a code repository or a research question and runs the loop
plan → retrieve → implement → sandboxed execute → verify → bounded repair → report,
persisting every tool call, model call, code diff and artifact so a run is traceable,
resumable and evaluable offline.

It is not a chatbot and not a RAG demo. Four engineering points carry the project:

- **A state machine instead of one big prompt loop**: seven nodes, an explicit transition
  function, budgets, per-node timeouts, a checkpoint after every node
  (`reproagent/core/orchestrator.py`, ~150 lines).
- **Schema-first tools**: six tools whose parameters are Pydantic models exported as
  function-calling schemas; a uniform `status / stdout / stderr / artifacts / next_hint`
  result; per-node tool allow-lists.
- **Three-layer sandbox**: command policy deny-list → resource limits (ulimit / cgroups)
  → isolation (workdir jail, environment allow-list, Docker with no network and a read-only root).
- **Failure taxonomy + bounded repair + offline evaluation**: a rule-based classifier for 17
  failure kinds; a REPAIR node conditioned on the kind with a configurable cap; 17 fixed tasks,
  deterministic checkers, five ablation arms.

Where it comes from: in August 2026 I reproduced π₀.₅ by hand
([pi05-libero-reproduction](https://github.com/GUOHC803/pi05-libero-reproduction)) and hit 13
distinct problems in three days - finding entry points, missing configs, missing dependencies,
OOM, checkpoints filling the disk. ReproAgent turns those into tools and failure kinds; the
`pi05_snapshot` fixture in the evaluation set is a snapshot of that repository.

## Quick start

```bash
pip install -e ".[dev]" numpy
cp .env.example .env                    # one model key (LiteLLM naming; OpenAI-compatible by default)
python evals/fixtures/papers/fetch.py   # optional: the four arXiv PDFs used by the extraction tasks

reproagent run-task evals/tasks/bugfix_nameerror_freq.yaml     # fix an injected bug
reproagent run-task evals/tasks/paper_resnet_training.yaml     # extract a training recipe from a PDF
reproagent run "Train for 50 epochs with lr 0.3 and report the final loss" \
    --dir evals/fixtures/mini_mlp --category repo_run

reproagent runs / show <run_id> / replay <run_id> --node repair / resume <run_id>
```

## Architecture

```
                 ┌──────────────── Orchestrator (state machine + budgets + checkpoints) ────────────────┐
  TaskSpec ──▶   PLAN ─▶ RETRIEVE ─▶ IMPLEMENT ─▶ EXECUTE ─▶ VERIFY ─▶ REPORT ─▶ report.md / result.json
                                          ▲            │fail       │fail
                                          └── REPAIR ◀─┴───────────┘   (REPAIRABLE kind and repairs < max_repairs)
                 └───────────────────────────────────────────────────────────────────────────────────────┘
  inside a node : NodeRuntime.tool_loop (function calling + `finish` pseudo-tool + context folding + no-progress detection)
  tools         : read_pdf · inspect_repo · patch_file · run_tests · run_experiment · save_artifact
  sandbox       : CommandPolicy → LocalSandbox (ulimit) | DockerSandbox (--network none, --read-only, cap-drop ALL)
  store         : SQLite trace (runs / steps / tool_calls / llm_calls / artifacts); full TaskState after every node
  model         : LiteLLM (swap models by changing one string); Replay / Mock clients keep the 33 tests key-free
```

Design notes, alternatives and limitations per module live in `docs/design/` (Chinese).

### Failure taxonomy

| kind | how it is detected | goes to REPAIR? |
|---|---|---|
| syntax_error / import_error / file_not_found / runtime_error | traceback rules, with `file:line` | yes |
| test_failure / assertion_error | pytest counts and failing test names | yes |
| exec_timeout / nonzero_exit | sandbox timeout; non-zero exit without a traceback | yes |
| resource_limit / policy_blocked | OOM / killed; command denied by policy | no - report |
| model_output_invalid / tool_args_invalid / no_progress | JSON validation failed 3x; bad args; same call 3x | yes |
| verify_mismatch / missing_artifact | ran, but does not meet the criteria; expected output missing | yes |
| budget_exhausted / node_timeout / llm_error | budgets and infrastructure | no |

## Evaluation

17 fixed tasks (`evals/tasks/`), four categories, all judged deterministically (no model-as-judge):

| category | n | material | checker |
|---|---|---|---|
| paper_extraction | 4 | Attention / LoRA / ResNet / π₀.₅ arXiv PDFs | fields vs hand-verified ground truth, tolerant matching |
| repo_locate | 4 | mini_mlp, textstats, pi05_snapshot (a real repository) | same |
| repo_run | 3 | train a small model, CSV statistics, run a CLI | the checker **recomputes** ground truth on a pristine fixture |
| bug_fix | 6 | textstats with six injected bugs (declared in the task YAML) | pytest exits 0 **and** test files are hash-identical |

Five configurations (`reproagent/evals/configs.py`): `full`, `no_repair` (max_repairs=0),
`single_call` (one model call, no tools, no state machine; the material is pasted into the prompt),
`free_text_tools` (tools via free-text blocks, no schema / status / next_hint), `no_verify` (no model judge).

```bash
reproagent eval --configs full,no_repair,single_call,free_text_tools --workers 3
# → evals/results/<timestamp>/{rows.jsonl, summary.json, summary.md, trace.sqlite3, runs/}
```

### Results

<!-- RESULTS:BEGIN -->
Not yet run (this section is generated from `evals/results/<latest>/summary.md`).
<!-- RESULTS:END -->

## Deployment

```bash
reproagent serve            # FastAPI: POST /tasks, GET /tasks/{id}, GET /tasks/{id}/report
docker compose up           # service image
make sandbox-image          # sandbox image; then run with --sandbox docker
```

The `local` sandbox limits memory / CPU / processes with `ulimit`, jails the working directory and
allow-lists environment variables but does **not** isolate the network; use the `docker` backend for
untrusted repositories. The evaluation fixtures are hand-written pure Python and run on the local backend.

## Limitations and non-goals

- No model training, no front-end, no multi-agent: VERIFY already is a second perspective with a different prompt.
- The evaluation is small (17 tasks): read trends, not decimals; fixtures are controlled, not large real repositories.
- Resume granularity is a node; progress inside a node's tool loop is not persisted.
- The local sandbox has no network isolation and no disk quota.

## Adding a tool

1. Subclass `Tool` in `reproagent/tools/`: `name`, `description`, `Params` (Pydantic), `run()` returning a `ToolResult`.
2. Register it in `default_registry()` (`tools/__init__.py`).
3. Add its name to the `allowed` list of the nodes that may use it (`core/nodes.py`). The state machine is unchanged.
