# ReproAgent: a controllable agent for research-code reproduction tasks

[中文](README.md)

ReproAgent takes a paper, a code repository or a research question and runs the loop
plan → retrieve → implement → sandboxed execute → verify → bounded repair → report,
persisting every tool call, model call, code diff and artifact so a run is traceable,
resumable and evaluable offline.

Four engineering points carry the project:

- **A state machine instead of one big prompt loop**: seven nodes, an explicit transition
  function, budgets, per-node timeouts, a checkpoint after every node
  (`reproagent/core/orchestrator.py`, ~150 lines).
- **Schema-first tools**: six tools whose parameters are Pydantic models exported as
  function-calling schemas; a uniform `status / stdout / stderr / artifacts / next_hint`
  result; per-node tool allow-lists.
- **Three-layer sandbox**: command policy deny-list → resource limits (ulimit / cgroups)
  → isolation (workdir jail, environment allow-list, Docker with no network and a read-only root).
- **Failure taxonomy + bounded repair + offline evaluation**: a rule-based classifier for 17
  failure kinds; a REPAIR node conditioned on the kind with a configurable cap; 22 fixed tasks,
  deterministic checkers, four ablation arms.

The project grew out of reproducing π₀.₅
([pi05-libero-reproduction](https://github.com/GUOHC803/pi05-libero-reproduction)). Most of the time in
reproducing a paper's code goes not into the algorithm but into finding the entry point, matching the config,
filling in dependencies and reading errors - and every step only tells you whether it worked after you run it.
ReproAgent hands that loop to an agent while keeping it bounded, verifiable and traceable. The `pi05_snapshot`
fixture in the evaluation set is a snapshot of that repository.

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

22 fixed tasks (`evals/tasks/`), four categories, all judged deterministically (no model-as-judge):

| category | n | material | checker |
|---|---|---|---|
| paper_extraction | 4 | Attention / LoRA / ResNet / π₀.₅ arXiv PDFs | fields vs hand-verified ground truth, tolerant matching |
| repo_locate | 4 | mini_mlp, textstats, pi05_snapshot (a real repository) | same |
| repo_run | 7 | train a small model, CSV statistics, episode-list statistics, TF-IDF, run a CLI | the checker **recomputes** ground truth on a pristine fixture |
| bug_fix | 7 | textstats with six single-site bugs and one two-bug case (declared in the task YAML) | pytest exits 0 **and** test files are hash-identical |

Four configurations (`reproagent/evals/configs.py`): `full`, `no_repair` (max_repairs=0),
`single_call` (one model call, no tools, no state machine; the material is pasted into the prompt),
`free_text_tools` (tools via free-text blocks, no schema / status / next_hint).

```bash
reproagent eval --configs full,no_repair,single_call,free_text_tools --workers 3
# → evals/results/<timestamp>/{rows.jsonl, summary.json, summary.md, trace.sqlite3, runs/}
```

### Results

Model `deepseek-v4.1-flash` (OpenAI-compatible relay via LiteLLM), 22 tasks x 4 configurations = 88 runs, one repeat each. How to read the table:

- **The single-call baseline loses only where the answer must be computed** (repo_run 3/7). Whatever can be read, it answers; whatever must be run, it makes up - all four "claimed success but wrong" rows are its. The state machine's value is execution feedback and keeping invented numbers out of the report, not raw intelligence.
- **Removing REPAIR costs 2 tasks** (90.9%); in `full`, 3 tasks were rescued by a repair round, at ~20% more tokens on average (12.9% of all tokens are spent from the first repair onward).
- **Free-text tool protocol** is one task worse, with 2.4x the repair rounds, 1.7x the p95 latency, and failure kinds the structured protocol never produced (`no_progress` x4, `budget_exhausted` x2, `node_timeout`); its one failure is output-format drift (an integer field answered as a `{value, evidence}` object). **Schemas buy stability and cost.**
- 88 runs produced 34 failed node steps (in 20 runs), concentrated in EXECUTE (20/34 = 59%); 7 failure kinds were observed, the largest being `nonzero_exit` at 17/34 = 50% (`scripts/failure_stats.py`).

<!-- RESULTS:BEGIN -->
来源 / source: `evals/results/final/summary.md`（rows.jsonl 里有每次运行的明细）

model: `openai/deepseek-v4.1-flash` | tasks: 22 | repeats: 1 | rows: 88

#### Success rate by configuration

| config | n | success | bug_fix | paper_extraction | repo_locate | repo_run | avg repairs | avg tool calls | avg model calls | avg tokens | p95 tokens | avg s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 22 | **100.0%** | 100% (7) | 100% (4) | 100% (4) | 100% (7) | 0.23 | 18.4 | 11.8 | 61808 | 117987 | 95 | 235 |
| no_repair | 22 | **90.9%** | 100% (7) | 100% (4) | 75% (4) | 86% (7) | 0.00 | 16.5 | 10.0 | 51237 | 116696 | 75 | 234 |
| single_call | 22 | **81.8%** | 100% (7) | 100% (4) | 100% (4) | 43% (7) | 0.00 | 2.0 | 1.0 | 11217 | 19838 | 15 | 31 |
| free_text_tools | 22 | **95.5%** | 100% (7) | 100% (4) | 75% (4) | 100% (7) | 0.55 | 14.1 | 17.1 | 61891 | 201636 | 151 | 394 |

#### Repair and verification

| config | solved first try | solved after repair | claimed success but wrong |
|---|---|---|---|
| full | 19 | 3 | 0 |
| no_repair | 20 | 0 | 1 |
| single_call | 18 | 0 | 4 |
| free_text_tools | 14 | 7 | 1 |

#### Failure kinds (final, unsuccessful runs)

- **full** - final: none; encountered during runs (incl. repaired): import_error: 2, nonzero_exit: 2
- **no_repair** - final: verify_mismatch: 1; encountered during runs (incl. repaired): nonzero_exit: 5, verify_mismatch: 2
- **single_call** - final: none; encountered during runs (incl. repaired): none
- **free_text_tools** - final: none; encountered during runs (incl. repaired): no_progress: 4, nonzero_exit: 4, verify_mismatch: 3, budget_exhausted: 2, node_timeout: 1, file_not_found: 1

#### Per-task results

| task | category | full | no_repair | single_call | free_text_tools |
|---|---|---|---|---|---|
| bugfix_nameerror_freq | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_ngram_offbyone | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_syntax_error | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 1) |
| bugfix_tfidf_sign | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_tokenize_none | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_topk_order | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_two_bugs | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| paper_attention_training | paper_extraction | ✅ 1/1 (rep 1) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| paper_lora_setup | paper_extraction | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 3) |
| paper_pi05_overview | paper_extraction | ✅ 1/1 (rep 3) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 1) |
| paper_resnet_training | paper_extraction | ✅ 1/1 (rep 1) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 1) |
| locate_mini_mlp | repo_locate | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| locate_pi05_code | repo_locate | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| locate_pi05_readme | repo_locate | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 3) |
| locate_textstats | repo_locate | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 1) |
| run_mini_mlp | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 1) |
| run_mini_mlp_seed7 | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_pi05_episode_gaps | repo_run | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_pi05_loss_mean | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 1) |
| run_pi05_loss_min | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_textstats_cli | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_textstats_tfidf | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |

Legend: ✅ all repeats passed, ⚠️ some, ❌ none; `rep` = repair rounds per repeat.
<!-- RESULTS:END -->

## Deployment

```bash
reproagent serve            # FastAPI: POST /tasks, GET /tasks/{id}, GET /tasks/{id}/report
docker compose up           # service image
make sandbox-image          # sandbox image; then run with --sandbox docker
```

The `local` sandbox limits memory / CPU / processes with `ulimit`, jails the working directory and
allow-lists environment variables; the `docker` backend adds `--network none`, a read-only root filesystem,
a non-root user and `cap-drop ALL` for untrusted repositories.


## Adding a tool

1. Subclass `Tool` in `reproagent/tools/`: `name`, `description`, `Params` (Pydantic), `run()` returning a `ToolResult`.
2. Register it in `default_registry()` (`tools/__init__.py`).
3. Add its name to the `allowed` list of the nodes that may use it (`core/nodes.py`). The state machine is unchanged.
