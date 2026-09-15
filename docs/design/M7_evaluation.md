# M7 · 评测体系

> 对应代码：`evals/tasks/*.yaml`（17 个任务）、`evals/fixtures/`（素材）、`reproagent/evals/checkers.py`（判定）、`runner.py`（批跑）、`report.py`（汇总）、`configs.py`（消融配置）。
> 面试十问对应：Q7 如何设计离线评测、避免只看主观 Demo；Q9 成本与延迟。
> **最终数字见 `evals/results/<最新>/summary.md` 和 README，本文只讲设计。**

## 1. 任务集：17 个，四类

| 类别 | 数 | 素材 | 判定方式 |
|---|---|---|---|
| paper_extraction | 4 | Attention / LoRA / ResNet / π0.5 四篇 arXiv PDF | `fields`：每个字段与标准答案容错比对 |
| repo_locate | 4 | mini_mlp、textstats、pi05_snapshot（我自己的 OpenPI 复现仓库快照）| `fields` |
| repo_run | 3 | 训练 mini_mlp；从 loss CSV 算最小值；跑 textstats CLI | `reference_json`/`reference_script`：**在干净固件上重算标准答案**再比对 |
| bug_fix | 6 | textstats 注入 6 种 bug（off-by-one、符号反、None 守卫、排序方向、NameError、SyntaxError）| `tests_pass`：pytest 退出 0 **且** tests/ 的哈希未变 |

设计原则：

- **标准答案不靠模型**：抽取任务的答案是我读 PDF 手核的；运行任务的答案由检查器在干净固件上重新算（固件改了答案自动跟着变）；bug 任务的答案就是测试。
- **bug 是声明式注入的**：任务 YAML 里写 `inject: {file, old, new}`，运行时在固件副本上替换。任何人都能看到 bug 是什么，不存在"藏起来的 bug"。
- **素材有一部分是真实项目**：pi05_snapshot 是我 8 月复现 π0.5 时的仓库，任务问的是"消融用了多少 trials、LoRA 显存多少、哪个环境变量传 episode 子集"——这些是我当时真正踩过的点。
- **防作弊**：bug 任务检查 tests/ 和 conftest.py 的 SHA-256；工具层也拦改测试。

## 2. 判定器的容错规则（`match_value`）

- 数值：从答案里抽第一个数字（支持 `100,000`、`1.28 million`），相对误差 1%（可配）。
- 字符串：归一化（小写、去空格标点）后子串匹配。`"35MB"` 能匹配 `"35 MB"`，不会匹配 `"350GB"`。
- 列表：每个期望项都要出现在答案文本里。
- 布尔：`false`/`no`/`0` 等价。

宽松的目的是不因为格式差异误判，但**数值本身错就是错**。

## 3. 指标

每个 run 一行（`rows.jsonl`），字段：

- `success`（检查器判定，**不是** agent 自己说的）、`agent_claimed_success`（agent 说的）→ 两者差 = `claimed_but_wrong`
- `repairs`、`tool_calls`、`tool_calls_failed`、`llm_calls`、`prompt/completion tokens`、`cost_usd`、`wall_time_s`
- `last_failure`（最终失败类别）、`failures_seen`（过程中遇到过的所有类别，含修好的）、`node_path`

汇总（`summary.md`）：按配置 × 类别的成功率；平均修复轮次/工具调用/模型调用/token/成本/耗时；首轮成功 vs 修复后成功；假成功数；失败类别分布；任务 × 配置矩阵。

## 4. 消融：每一组回答一个问题

| 配置 | 改了什么 | 回答的问题 |
|---|---|---|
| full | — | 基线 |
| no_repair | `max_repairs=0` | REPAIR 节点值多少？ |
| single_call | 一次调用、无工具、无状态机、无修复；把材料（PDF 全文 / 仓库文件）直接塞进 prompt | 整个状态机 + 工具体系相对"一个大 prompt"值多少？ |
| free_text_tools | 工具改自由文本协议，无 schema、无 status/next_hint | 结构化工具协议本身值多少？ |
| no_verify | 只做确定性检查，无模型裁判 | 模型裁判抓出多少假成功？ |

`single_call` 的公平性：它拿到同样的任务描述和成功标准，patch 以整文件形式应用，命令照样在沙箱里执行，判定用同一套检查器。它输的不是"没机会跑代码"，而是"没有反馈回路"。

## 5. 成本与延迟怎么估（Q9）

- 单任务成本 = Σ llm_calls 的 token × 单价。trace 里每次调用都有 token，`run_metrics` 聚合。
- P95 延迟：从 `rows.jsonl` 的 `wall_time_s` 取分位数（`summary.md` 给的是均值，P95 要自己算，评测脚本里加一行就行）。
- 上界可以**先验**给出：`max_llm_calls × 单次上限 + max_tool_calls × tool_timeout` —— 这就是预算存在的另一个理由。

## 5b. 最终数字（2026-09-15，`evals/results/final/`，必须背下来）

模型 deepseek-v4.1-flash，22 任务 × 4 配置 = 88 次运行，每任务 1 次。

| 配置 | 成功率 | repo_run | repo_locate | 均修复轮次 | 均 token | 均耗时 | p95 耗时 | 自称成功但错 |
|---|---|---|---|---|---|---|---|---|
| full | **100%** (22/22) | 7/7 | 4/4 | 0.23 | 61.8k | 95s | 235s | 0 |
| free_text_tools | 95.5% (21/22) | 7/7 | 3/4 | 0.55 | 61.9k | 151s | 394s | 1 |
| no_repair | 90.9% (20/22) | 6/7 | 3/4 | 0 | 51.2k | 75s | 234s | 1 |
| single_call | 81.8% (18/22) | 3/7 | 4/4 | 0 | 11.2k | 15s | 31s | 4 |

三句话结论：① single_call 只输在必须执行才能得到数字的任务，4 次"编数字"全是它；② 拿掉 REPAIR 掉 2 个任务，full 有 3 个任务靠修复救回，代价是 token 多 20%；③ 自由文本协议只少 1 个任务，但修复轮次 2.4×、p95 耗时 1.7×，还多出 no_progress/budget_exhausted/node_timeout 这些结构化协议下没有的失败。bug_fix 七个任务四种配置全过 → 注入的 bug 对这个模型偏简单，这是评测的已知短板。失败统计统一按"失败的节点步骤"计（`scripts/failure_stats.py`）：88 次运行共 34 个失败步骤，落在 20 次运行里。按类别 7 种：nonzero_exit 17（50%）、verify_mismatch 5（15%）、import_error 4（12%）、no_progress 4（12%）、budget_exhausted 2、node_timeout 1、file_not_found 1。按节点：execute 20（59%）、repair 6（18%）、verify 5（15%）、implement 3（9%）。full 配置自己只有 7 个失败步骤（3 次运行）：execute 5、repair 2；类别 import_error 4、nonzero_exit 3。

**第二个模型 deepseek-v4-pro**（同一中转，只改模型名；`evals/results/final-deepseek-v4-pro/`，44 次）：full 95.5%（21/22，唯一失败是 episode 统计没填字段）、single_call 81.8%（18/22，输的还是那几个必须执行的 repo_run）；full 均 55.2k token、183s（pro 每次调用更慢）。其中 full 的 tfidf 任务第一次跑被中转站卡了 752s 零 token 返回（node_timeout），按基础设施故障重跑一次后通过，用 `scripts/rescore.py --override` 合并，两批原始记录都保留在 `evals/results/`。面试话术：两个模型下 single_call 都是 81.8%，说明"状态机的价值在执行反馈"这个结论不依赖模型。

## 6. 评测的诚实边界（面试主动说）

- 17 个任务是小规模，成功率差 1 个任务就是 6 个百分点；结论看趋势，不看小数点。
- 固件是自己写的，bug 是自己注入的——难度可控但不代表真实仓库。pi05_snapshot 是真实的，但只有定位/统计类任务，没有真训练。
- 单次运行有随机性（temperature=0 但服务端不保证确定性）；`--repeats` 可以多跑取平均。

---

## 拷打题

1. 17 个任务分哪四类、各多少、判定方式各是什么？
2. 标准答案为什么不能来自模型？三类任务的标准答案分别从哪来？
3. `reference_json` 检查器是怎么工作的？固件改了会怎样？
4. bug 任务怎么防作弊？两道防线分别在哪一层？
5. `success` 和 `agent_claimed_success` 的差是什么指标？它说明什么？
6. 五组消融各回答什么问题？`single_call` 怎么保证公平？
7. 背出最终数字：full / no_repair / single_call 的总成功率；bug_fix 类的三者差；平均修复轮次；单任务平均 token。
8. 哪一组消融差异最大？哪一组最小？为什么（结合任务类型解释）？
9. 单任务成本和 P95 延迟怎么估？先验上界怎么算？
10. 评测的三个诚实边界是什么？如果要把评测做扎实，下一步加什么？
