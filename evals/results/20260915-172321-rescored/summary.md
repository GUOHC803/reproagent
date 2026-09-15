# ReproAgent evaluation - 20260915-181000

model: `openai/deepseek-v4.1-flash` | tasks: 17 | repeats: 1 | rows: 68

## Success rate by configuration

| config | n | success | bug_fix | paper_extraction | repo_locate | repo_run | avg repairs | avg tool calls | avg model calls | avg tokens | p95 tokens | avg s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 17 | **100.0%** | 100% (6) | 100% (4) | 100% (4) | 100% (3) | 0.29 | 19.0 | 12.2 | 65536 | 117987 | 101 | 235 |
| no_repair | 17 | **94.1%** | 100% (6) | 100% (4) | 75% (4) | 100% (3) | 0.00 | 16.5 | 10.1 | 51246 | 116696 | 78 | 234 |
| single_call | 17 | **88.2%** | 100% (6) | 100% (4) | 100% (4) | 33% (3) | 0.00 | 1.9 | 1.0 | 9781 | 18822 | 10 | 20 |
| free_text_tools | 17 | **94.1%** | 100% (6) | 100% (4) | 75% (4) | 100% (3) | 0.65 | 13.8 | 16.9 | 58829 | 172626 | 172 | 394 |

## Repair and verification

| config | solved first try | solved after repair | claimed success but wrong |
|---|---|---|---|
| full | 14 | 3 | 0 |
| no_repair | 16 | 0 | 0 |
| single_call | 15 | 0 | 2 |
| free_text_tools | 10 | 6 | 1 |

## Failure kinds (final, unsuccessful runs)

- **full** - final: none; encountered during runs (incl. repaired): import_error: 2, nonzero_exit: 2
- **no_repair** - final: verify_mismatch: 1; encountered during runs (incl. repaired): nonzero_exit: 4, verify_mismatch: 2
- **single_call** - final: none; encountered during runs (incl. repaired): none
- **free_text_tools** - final: none; encountered during runs (incl. repaired): no_progress: 4, nonzero_exit: 4, verify_mismatch: 3, budget_exhausted: 1, node_timeout: 1

## Per-task results

| task | category | full | no_repair | single_call | free_text_tools |
|---|---|---|---|---|---|
| bugfix_nameerror_freq | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_ngram_offbyone | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_syntax_error | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 1) |
| bugfix_tfidf_sign | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_tokenize_none | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_topk_order | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| paper_attention_training | paper_extraction | ✅ 1/1 (rep 1) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| paper_lora_setup | paper_extraction | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 3) |
| paper_pi05_overview | paper_extraction | ✅ 1/1 (rep 3) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 1) |
| paper_resnet_training | paper_extraction | ✅ 1/1 (rep 1) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 1) |
| locate_mini_mlp | repo_locate | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| locate_pi05_code | repo_locate | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| locate_pi05_readme | repo_locate | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 3) |
| locate_textstats | repo_locate | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 1) |
| run_mini_mlp | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 1) |
| run_pi05_loss_min | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_textstats_cli | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |

Legend: ✅ all repeats passed, ⚠️ some, ❌ none; `rep` = repair rounds per repeat.
