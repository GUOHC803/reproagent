# Evaluation fixtures

| Directory | What | Used by |
|---|---|---|
| `papers/` | Four arXiv PDFs (downloaded by `papers/fetch.py`, not committed) | `paper_*` extraction tasks |
| `mini_mlp/` | numpy MLP trainer with a config file and CLI flags | `locate_mini_mlp`, `run_mini_mlp` |
| `textstats/` | Small text-statistics library with 12 pytest tests | `locate_textstats`, `run_textstats_cli`, `bugfix_*` (bugs are injected per task) |
| `pi05_snapshot/` | Snapshot (docs, scripts, ablation lists, loss CSV) of the author's real `pi05-libero-reproduction` repository | `locate_pi05_*`, `run_pi05_loss_min` |

Every task runs on a fresh copy of its fixture; the agent never touches these directories.
Bug-fix tasks declare their injected bug in the task YAML (`inject:`), so the "broken" state is
reproducible and reviewable.
