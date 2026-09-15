# ReproAgent evaluation - 20260915-182054

model: `openai/deepseek-v4-pro` | tasks: 22 | repeats: 1 | rows: 44

## Success rate by configuration

| config | n | success | bug_fix | paper_extraction | repo_locate | repo_run | avg repairs | avg tool calls | avg model calls | avg tokens | p95 tokens | avg s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 22 | **90.9%** | 100% (7) | 100% (4) | 100% (4) | 71% (7) | 0.23 | 14.5 | 11.5 | 52710 | 105147 | 188 | 551 |
| single_call | 22 | **81.8%** | 100% (7) | 100% (4) | 100% (4) | 43% (7) | 0.00 | 1.3 | 1.0 | 11527 | 21273 | 109 | 597 |

## Repair and verification

| config | solved first try | solved after repair | claimed success but wrong |
|---|---|---|---|
| full | 17 | 3 | 1 |
| single_call | 18 | 0 | 3 |

## Failure kinds (final, unsuccessful runs)

- **full** - final: node_timeout: 1; encountered during runs (incl. repaired): verify_mismatch: 2, no_progress: 1, nonzero_exit: 1, node_timeout: 1
- **single_call** - final: missing_artifact: 1; encountered during runs (incl. repaired): missing_artifact: 1

## Per-task results

| task | category | full | single_call |
|---|---|---|---|
| bugfix_nameerror_freq | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_ngram_offbyone | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_syntax_error | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_tfidf_sign | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_tokenize_none | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_topk_order | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| bugfix_two_bugs | bug_fix | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| paper_attention_training | paper_extraction | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| paper_lora_setup | paper_extraction | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| paper_pi05_overview | paper_extraction | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| paper_resnet_training | paper_extraction | ✅ 1/1 (rep 1) | ✅ 1/1 (rep 0) |
| locate_mini_mlp | repo_locate | ✅ 1/1 (rep 3) | ✅ 1/1 (rep 0) |
| locate_pi05_code | repo_locate | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| locate_pi05_readme | repo_locate | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| locate_textstats | repo_locate | ✅ 1/1 (rep 1) | ✅ 1/1 (rep 0) |
| run_mini_mlp | repo_run | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) |
| run_mini_mlp_seed7 | repo_run | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) |
| run_pi05_episode_gaps | repo_run | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_pi05_loss_mean | repo_run | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) |
| run_pi05_loss_min | repo_run | ✅ 1/1 (rep 0) | ❌ 0/1 (rep 0) |
| run_textstats_cli | repo_run | ✅ 1/1 (rep 0) | ✅ 1/1 (rep 0) |
| run_textstats_tfidf | repo_run | ❌ 0/1 (rep 0) | ✅ 1/1 (rep 0) |

Legend: ✅ all repeats passed, ⚠️ some, ❌ none; `rep` = repair rounds per repeat.
