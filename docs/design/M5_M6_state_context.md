# M5 · 状态持久化与恢复 / M6 · 上下文管理

> 对应代码：`reproagent/store/trace.py`、`reproagent/core/context.py`、`reproagent/core/state.py` 的 `StageSummaries`、`reproagent/core/runtime.py` 的 `tool_loop`。
> 面试十问对应：Q5 为什么需要 artifact 和 trace、如何支持任务恢复；Q6 上下文过长如何压缩而不丢关键信息。

## M5 · Trace 与恢复

### 1. 五张表

| 表 | 一行是什么 | 用来回答 |
|---|---|---|
| runs | 一次运行：任务、配置、状态、汇总 | 跑了什么、结果如何 |
| steps | 一个节点的执行：结果 + **完整 TaskState 快照** | 当时在哪一步、恢复点 |
| tool_calls | 一次工具调用：参数、status、结果、耗时 | 模型都干了什么、哪些工具老失败 |
| llm_calls | 一次模型调用：token、成本、延迟、**完整 messages** | 成本/延迟统计、prompt 回放调试 |
| artifacts | 一个产物文件 | 报告引用 |

SQLite、无 ORM、一个文件。选它的理由：评测要跑几十上百次 run，需要跨 run 聚合（`run_metrics` 一条 SQL），JSONL 做不到；Postgres 是为多机部署准备的，第一版没有这个需求。

### 2. Trace 的三个用途

1. **回放调试**：`reproagent replay <run_id> --node repair` 打印那个节点的完整对话。prompt 调优全靠这个——没有它你只能猜模型为什么那样做。
2. **恢复**：见 M1；`latest_state` 取最后一个 step 的 `state_json`。
3. **评测指标**：`run_metrics` 从 llm_calls/tool_calls/steps 聚合出 token、成本、工具失败数、修复轮次。**简历上的所有数字都从这里来**，不是手填的。

### 3. Artifact

`save_artifact` 把文件复制到 `runs/<run_id>/artifacts/`，并在表里登记；REPORT 节点生成 `report.md`（人读）和 `result.json`（机器读，评测用）。为什么要单独一个目录而不是留在工作目录？因为工作目录是任务的副本，随时可能被清理；产物要独立于工作目录活着。

### 4. 恢复的边界

- 恢复点是**节点粒度**：节点内部（比如工具循环跑到一半）的进度不保存。理由：节点是幂等的单位——重跑一次 IMPLEMENT 最多浪费几次调用，而把工具循环做成可恢复要序列化 messages，复杂度翻倍。
- 工作目录必须还在（`runs/<run_id>/work`）；如果磁盘上的代码已经不是检查点那一刻的样子，恢复结果不保证正确。这是已知局限。

## M6 · 上下文管理

### 1. 两个机制

**① 阶段摘要（跨节点）**——`StageSummaries` 里每个节点写一段 ≤1500 字的摘要，后面的节点**只读摘要和结构化 state**，不读前面节点的原始对话。RETRIEVE 读了 40k 字的 PDF，IMPLEMENT 看到的只是"summary + extracted + relevant_files"。这不是"压缩"，是**设计上不让上下文积累**。

**② 节点内折叠（`ContextManager.maybe_compact`）**——工具循环里 token 估算超过阈值（默认 24k）时，把**较早的 `role=tool` 消息内容**换成一行存根：

```
[status=ok stdout: ...] output folded (5120 chars). Re-run the tool if you need it again.
```

最近 N 条（默认 6）保持原样。消息**不删只替换内容**，所以 `tool_call_id` 配对不会断（OpenAI 协议要求每个 tool_call 都有对应的 tool 消息）。

### 2. 为什么折叠工具输出而不是让模型写摘要

- 工具输出是上下文膨胀的主要来源（模型自己的话通常很短）。
- 折叠成本为零；让模型写摘要要多一次调用，还可能把关键数字改错。
- 存根里保留了"我调过这个工具、结果 status 是什么"，模型不会重复调用；真需要内容可以重新调（工具是幂等的）。

### 3. 单个工具输出的截断

`ToolResult.to_model_text(max_chars)` 与 `ContextManager.clip_tool_output` 做头 2/3 + 尾 1/3 的截断，中间标注省略了多少字符。为什么保尾巴？因为错误信息、测试汇总、JSON 输出行都在最后。

### 4. 与 Claude Code 体感的对照（面试可讲）

Claude Code 长对话会"summarize 之前的上下文再继续"，本质是①的手动版；工具输出被截断显示 `... [N lines omitted]` 是③。本项目的差别在于把①做成结构（`StageSummaries` 是 Pydantic 字段，不是一段自由文本），后续节点按字段取用。

### 5. 没做的事

- 没做检索式记忆（把历史放向量库按需取）：任务时长几分钟、上下文几万 token，检索的复杂度不值。
- `no_compaction` 配置保留着但没进默认消融——当前任务规模下很少触发阈值，跑了也是无差异，说了等于没说。

---

## 拷打题

1. 五张表各存什么？`run_metrics` 从哪几张表聚合出哪些指标？
2. "为什么需要 trace"——给三个用途，每个对应一条 CLI 命令或一个评测指标。
3. `resume` 的恢复粒度是什么？为什么不做到工具循环内部？前提条件是什么？
4. artifact 为什么不直接留在工作目录？
5. 跨节点的上下文是怎么控制的？IMPLEMENT 节点能看到 RETRIEVE 读过的 PDF 原文吗？
6. 节点内折叠的触发条件、折叠对象、保留规则各是什么？为什么消息只换内容不删除？
7. 工具输出截断为什么保头 2/3 尾 1/3 而不是只保头？
8. 为什么不让模型来写压缩摘要？
9. 如果 RETRIEVE 抽出的某个数字在折叠后被"丢"了，系统靠什么保证它不丢？（答案：数字在 `finish` 的 `extracted` 里，进了 state，不依赖 messages）
10. 简历上的 token/成本数字是怎么来的？如果换了模型价格表，哪里改？
