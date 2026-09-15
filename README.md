# ReproAgent：面向科研复现任务的可控 Agent

[English](README_en.md)

ReproAgent 接收一篇论文、一个代码仓库或一个研究问题，跑完「计划 → 检索 → 实现 → 沙箱执行 → 验证 → 有限修复 → 报告」的闭环，并把每一步的工具调用、模型调用、代码 diff 和产物落盘，可追溯、可恢复、可离线评测。

四件工程上的事：

- **状态机编排**而不是一个大 Prompt 循环：七个节点、显式转移规则、预算上限、节点超时、逐节点检查点（`reproagent/core/orchestrator.py`，约 150 行）。
- **显式 schema 的工具协议**：六个工具，参数用 Pydantic 定义并导出为 function-calling schema，统一返回 `status / stdout / stderr / artifacts / next_hint`，节点级工具白名单。
- **三层沙箱**：命令策略黑名单 → 资源限制（ulimit / cgroup）→ 隔离（目录锁定、环境变量白名单、Docker 无网络只读根）。
- **失败分类 + 有限修复 + 离线评测**：17 种失败类型的规则分类器；REPAIR 节点带类别修复、上限可配；17 个固定任务、确定性判定、五组消融。

项目的来历：2026 年 8 月我手动复现 π₀.₅（[pi05-libero-reproduction](https://github.com/GUOHC803/pi05-libero-reproduction)），三天里踩了 13 个坑——找入口、改配置、缺依赖、显存爆、检查点写满磁盘。ReproAgent 把这些痛点做成了工具和失败分类；评测集里的 `pi05_snapshot` 就是那个仓库的快照。

## 一次完整运行

```bash
pip install -e ".[dev]" numpy
cp .env.example .env            # 填一个模型 key（LiteLLM 命名，默认 openai 兼容）
python evals/fixtures/papers/fetch.py   # 下载评测用的四篇 arXiv PDF（可选）

# 修一个注入了 bug 的仓库
reproagent run-task evals/tasks/bugfix_nameerror_freq.yaml

# 从论文抽训练超参
reproagent run-task evals/tasks/paper_resnet_training.yaml

# 即席任务
reproagent run "Train for 50 epochs with lr 0.3 and report the final loss" --dir evals/fixtures/mini_mlp --category repo_run

reproagent runs                    # 历史
reproagent show <run_id>           # 步骤、失败类别、token、耗时
reproagent replay <run_id> --node repair   # 回放某节点的完整对话
reproagent resume <run_id>         # 从检查点恢复
```

一次 bug 修复运行的步骤轨迹（真实输出，`reproagent show`）：

```
#  node       status  failure        tools  llm  tokens
0  plan       ok                     0      1    1.9k
1  retrieve   ok                     3      2    9.8k     ← 先跑测试复现，再读 traceback 里的文件
2  implement  ok                     2      2    6.1k     ← patch_file 返回 diff
3  execute    fail    test_failure   1      0    0        ← 不调模型；分类器给出 kind/file/line
4  repair     ok                     2      2    5.4k
5  execute    ok                     1      0    0
6  verify     ok                     0      1    1.2k     ← 确定性检查 AND 模型裁判
7  report     ok                     0      1    0.9k
```

## 架构

```
                 ┌──────────────────────── Orchestrator（状态机 + 预算 + 检查点）────────────────────────┐
  TaskSpec ──▶   PLAN ─▶ RETRIEVE ─▶ IMPLEMENT ─▶ EXECUTE ─▶ VERIFY ─▶ REPORT ─▶ report.md / result.json
                                          ▲            │fail       │fail
                                          └── REPAIR ◀─┴───────────┘   （REPAIRABLE 且 repairs < max_repairs）
                 └──────────────────────────────────────────────────────────────────────────────────────┘
        节点内：NodeRuntime.tool_loop（function calling + `finish` 伪工具 + 上下文折叠 + NO_PROGRESS 检测）
        工具：read_pdf · inspect_repo · patch_file · run_tests · run_experiment · save_artifact
        沙箱：CommandPolicy → LocalSandbox(ulimit) | DockerSandbox(--network none, --read-only, cap-drop)
        存储：SQLite trace（runs / steps / tool_calls / llm_calls / artifacts），每节点后保存完整 TaskState
        模型：LiteLLM（换模型只改一个字符串）；Replay / Mock 客户端让 33 个测试不需要 key
```

每个模块的设计取舍、备选方案和局限写在 `docs/design/`（M1 状态机、M2 工具协议、M3 沙箱、M4 验证与修复、M5/M6 状态与上下文、M7 评测、M8 部署）。

### 失败分类表

| 类别 | 例子 | 进 REPAIR？ |
|---|---|---|
| syntax_error / import_error / file_not_found / runtime_error | traceback 规则匹配，带 `file:line` 定位 | ✅ |
| test_failure / assertion_error | pytest 计数与失败用例名解析 | ✅ |
| exec_timeout / nonzero_exit | 沙箱超时；无 traceback 的非零退出 | ✅ |
| resource_limit / policy_blocked | OOM/被杀；命令被策略拒绝 | ❌ 直接报告 |
| model_output_invalid / tool_args_invalid / no_progress | JSON 校验三次失败；参数错；同一调用重复 3 次 | ✅ |
| verify_mismatch / missing_artifact | 跑通但不满足成功标准；期望产物缺失 | ✅ |
| budget_exhausted / node_timeout / llm_error | 预算与基建 | ❌ |

## 评测

17 个固定任务（`evals/tasks/`）、四类、全部确定性判定（不用模型打分）：

| 类别 | 数 | 素材 | 判定 |
|---|---|---|---|
| paper_extraction | 4 | Attention / LoRA / ResNet / π₀.₅ 四篇 arXiv PDF | 字段与人工核对的标准答案容错比对 |
| repo_locate | 4 | mini_mlp、textstats、pi05_snapshot（真实仓库快照） | 同上 |
| repo_run | 3 | 训练小模型、CSV 统计、跑 CLI | 检查器在干净固件上**重算**标准答案再比对 |
| bug_fix | 6 | textstats 注入 6 种 bug（YAML 里声明） | pytest 退出 0 **且** 测试文件哈希未变 |

五组配置（`reproagent/evals/configs.py`）：`full`、`no_repair`（max_repairs=0）、`single_call`（一次调用、无工具、无状态机，材料直接塞 prompt）、`free_text_tools`（工具改自由文本协议，无 schema / status / next_hint）、`no_verify`（无模型裁判）。

```bash
reproagent eval --configs full,no_repair,single_call,free_text_tools --workers 3
# → evals/results/<时间戳>/{rows.jsonl, summary.json, summary.md, trace.sqlite3, runs/}
```

### 结果

模型 `deepseek-v4.1-flash`（OpenAI 兼容中转，LiteLLM 接入），22 任务 × 4 配置 = 88 次运行，每任务 1 次。怎么读这张表：

- **单次调用基线只输在"必须执行才能得到数字"的任务上**（repo_run 3/7）：读得到的它都答得出，算不出的它会编——4 次"自称成功但错了"全部来自它。状态机的价值不在"多聪明"，在于**执行反馈**和**不让编造的数字进报告**。
- **拿掉 REPAIR 掉 2 个任务**（90.9%）；full 里 3 个任务是靠修复救回的，代价是平均 token 多 20%，其中从进入修复起的开销占总量 12.9%。
- **自由文本工具协议**成功率低 1 个任务，修复轮次 2.4 倍、p95 耗时 1.7 倍，并且出现了结构化协议下从未出现的 `no_progress`×4、`budget_exhausted`×2、`node_timeout`；唯一的失败是输出格式漂移（把整数字段答成了 `{value, evidence}` 对象）。**schema 买到的是稳定性和成本。**
- 失败集中在 EXECUTE 节点（34 次节点失败中 20 次），失败类别 7 种，最大一类 `nonzero_exit` 占 42%。

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

## 部署

```bash
reproagent serve                      # FastAPI：POST /tasks, GET /tasks/{id}, GET /tasks/{id}/report
docker compose up                     # 服务镜像
make sandbox-image                    # 沙箱镜像；然后 --sandbox docker
```

`sandbox.backend=local` 用 `ulimit` 限内存/CPU/进程数、锁定工作目录、白名单环境变量；`docker` 后端在此之上加 `--network none`、只读根文件系统、非 root、`cap-drop ALL`，用于不可信仓库。


## 仓库结构

```
reproagent/
├─ core/        state.py（TaskState/FailureKind）orchestrator.py  nodes.py  runtime.py  failures.py  context.py  single_call.py
├─ tools/       base.py（协议）+ 六个工具
├─ sandbox/     policy.py  local.py  docker.py
├─ store/       trace.py（SQLite）
├─ llm/         client.py（LiteLLM / Replay / Mock）structured.py
├─ evals/       configs.py  checkers.py  runner.py  report.py
├─ cli.py  api.py  runner.py  config.py
evals/          tasks/*.yaml  fixtures/  results/
tests/          33 个测试，不需要 API key
docs/design/    M1–M8 设计说明
```

## 扩展一个工具

1. 在 `reproagent/tools/` 写一个 `Tool` 子类：`name`、`description`、`Params`（Pydantic）、`run()` 返回 `ToolResult`。
2. 在 `tools/__init__.py` 的 `default_registry()` 注册。
3. 在需要它的节点的 `allowed` 列表里加上名字（`core/nodes.py`）。
状态机不用改。
