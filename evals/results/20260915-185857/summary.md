# ReproAgent evaluation - 20260915-185857

model: `openai/deepseek-v4-pro` | tasks: 1 | repeats: 1 | rows: 1

## Success rate by configuration

| config | n | success | repo_run | avg repairs | avg tool calls | avg model calls | avg tokens | p95 tokens | avg s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|
| full | 1 | **100.0%** | 100% (1) | 1.00 | 16.0 | 11.0 | 54676 | 54676 | 637 | 637 |

## Repair and verification

| config | solved first try | solved after repair | claimed success but wrong |
|---|---|---|---|
| full | 0 | 1 | 0 |

## Failure kinds (final, unsuccessful runs)

- **full** - final: none; encountered during runs (incl. repaired): runtime_error: 1

## Per-task results

| task | category | full |
|---|---|---|
| run_textstats_tfidf | repo_run | ✅ 1/1 (rep 1) |

Legend: ✅ all repeats passed, ⚠️ some, ❌ none; `rep` = repair rounds per repeat.
