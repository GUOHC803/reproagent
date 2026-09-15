# ReproAgent evaluation - 20260915-180949

model: `openai/deepseek-v4.1-flash` | tasks: 5 | repeats: 1 | rows: 20

## Success rate by configuration

| config | n | success | bug_fix | repo_run | avg repairs | avg tool calls | avg model calls | avg tokens | p95 tokens | avg s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 5 | **100.0%** | 100% (1) | 100% (4) | 0.00 | 16.4 | 10.4 | 49133 | 109690 | 72 | 156 |
| no_repair | 5 | **80.0%** | 100% (1) | 75% (4) | 0.00 | 16.4 | 10.0 | 51209 | 111778 | 65 | 169 |
| single_call | 5 | **60.0%** | 100% (1) | 50% (4) | 0.00 | 2.4 | 1.0 | 16097 | 30602 | 32 | 102 |
| free_text_tools | 5 | **100.0%** | 100% (1) | 100% (4) | 0.20 | 15.2 | 18.0 | 72302 | 215804 | 82 | 210 |

## Repair and verification

| config | solved first try | solved after repair | claimed success but wrong |
|---|---|---|---|
| full | 5 | 0 | 0 |
| no_repair | 4 | 0 | 1 |
| single_call | 3 | 0 | 2 |
| free_text_tools | 4 | 1 | 0 |

## Failure kinds (final, unsuccessful runs)

- **full** - final: none; encountered during runs (incl. repaired): none
- **no_repair** - final: none; encountered during runs (incl. repaired): nonzero_exit: 1
- **single_call** - final: none; encountered during runs (incl. repaired): none
- **free_text_tools** - final: none; encountered during runs (incl. repaired): budget_exhausted: 1, file_not_found: 1

## Per-task results

| task | category | full | no_repair | single_call | free_text_tools |
|---|---|---|---|---|---|
| bugfix_two_bugs | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_mini_mlp_seed7 | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_pi05_episode_gaps | repo_run | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_pi05_loss_mean | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 1) |
| run_textstats_tfidf | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |

Legend: ✅ all repeats passed, ⚠️ some, ❌ none; `rep` = repair rounds per repeat.
