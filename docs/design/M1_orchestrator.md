# M1 · 任务状态机 / Orchestrator

> 对应代码：`reproagent/core/orchestrator.py`（状态机本体，~150 行）、`reproagent/core/state.py`（状态与失败分类）、`reproagent/core/nodes.py`（七个节点）。
> 面试十问对应：Q1 为什么用状态机而不是一个大 Prompt；Q3 工具调用失败/超时/部分成功怎么处理（一半）；Q9 如何估计成本和 P95 延迟（预算部分）。

## 1. 它解决什么问题

一个"大 Prompt 循环"（while True: 让模型决定下一步）有三个工程上的硬伤：

1. **不保证终止**：模型可以一直调工具、一直改代码，没有任何外部力量让它停。
2. **不可测试**：什么时候修复、什么时候放弃，全在模型的"心情"里，写不出单元测试。
3. **不可恢复**：进程一挂，之前做的事只剩一堆聊天记录，没有"我现在在哪一步"的概念。

状态机把这三件事从模型手里拿回来：**转移规则是代码，预算是代码，检查点是代码**。模型只负责节点内部的"怎么做"，不负责"接下来轮到谁"。

## 2. 七个节点与转移图

```
PLAN ──ok──▶ RETRIEVE ──ok──▶ ┬─ needs_code_change ──▶ IMPLEMENT ──ok──▶ EXECUTE ──ok──▶ VERIFY ──ok──▶ REPORT ──▶ DONE
  │fail        │fail          ├─ needs_execution  ───────────────────────▶ EXECUTE            │fail
  ▼            ▼              └─ 纯阅读任务 ─────────────────────────────────────────▶ VERIFY   │
FAILED    有findings→REPORT                                                                    ▼
          否则→FAILED                     IMPLEMENT/EXECUTE/VERIFY 失败 ──▶ REPAIR ──ok──▶ EXECUTE
                                          （可修复类型 且 repairs_used < max_repairs）  └fail──▶ REPORT
                                          否则 ──▶ REPORT（带失败状态）
```

转移函数 `next_node(state, result, cfg)` 是**纯函数**：输入（当前状态、本节点结果、配置），输出下一个节点，没有副作用。这就是它能被 `tests/test_core.py::test_transitions` 用 15 个断言覆盖的原因。

每个节点的职责（一句话）：

| 节点 | 谁干活 | 输入 | 输出 |
|---|---|---|---|
| PLAN | 模型（结构化输出） | 任务 + 目录树 | `Plan`：task_type、是否改代码/执行、步骤、命令、输出字段 schema |
| RETRIEVE | 模型 + 只读工具 | Plan 摘要 | `Findings`：summary、key_facts、extracted、evidence |
| IMPLEMENT | 模型 + 读写工具 | Plan + Findings 摘要 | 文件 diff 列表 + 要执行的命令 |
| EXECUTE | **不调模型** | Plan.commands | 每条命令的退出码/输出/失败分类 |
| VERIFY | 确定性检查 + 模型裁判 | 执行输出 + findings | `Verdict`：passed、reason、missing、answer |
| REPAIR | 模型 + 读写工具 | 失败分类 + 错误尾巴 + 历史修复 | diff + 是否可修复 |
| REPORT | 模板 + 一次模型摘要 | 全部 state | report.md、result.json |

注意 EXECUTE 不调模型——"运行命令"是确定性的事，让模型来做只会引入噪声。这是"该用代码的地方不用模型"的一个具体例子。

## 3. 预算：让它一定停下来

`Budget`（`config.py`）里有七个上限，作用点各不相同：

| 上限 | 在哪检查 | 超过后 |
|---|---|---|
| `max_steps` | 每次进入节点前（orchestrator 主循环） | 直接 FAILED |
| `max_repairs` | `next_node` 决定是否进 REPAIR | 转 REPORT，带失败 |
| `max_tool_calls` / `max_llm_calls` / `max_tokens_total` | 每次调工具/模型前（`NodeRuntime._check_budget`） | 抛 `BudgetExceeded` → 节点结果 fail(BUDGET_EXHAUSTED) |
| `max_tool_calls_per_node` | 节点内工具循环 | 循环结束，节点 fail |
| `node_timeout_s` | 节点在线程池中执行，`future.result(timeout)` | 节点 fail(NODE_TIMEOUT) |

另外工具循环里有一个 **NO_PROGRESS 检测**：同一个工具带同样参数连续调三次，直接判定卡死。这是从真实观察来的——模型卡住时最常见的表现就是反复 `inspect_repo tree`。

## 4. 检查点与恢复

每个节点结束后 `_checkpoint` 把完整的 `TaskState`（Pydantic → JSON）写进 SQLite `steps` 表。`resume(run_id)` 取最后一行、反序列化、从 `state.current` 继续跑主循环——**主循环不知道自己是第一次跑还是恢复的**，这是设计目标。前提是 `TaskState` 里装了全部需要的东西（plan、findings、changes、exec_results、summaries），节点不能在别处藏状态。

## 5. 备选方案与为什么不选

- **LangGraph**：能做同样的事，但状态机+预算+检查点加起来不到 300 行，自己写可以逐行解释；而且 LangGraph 的 checkpointer/interrupt 语义在面试里讲不清就是负分。
- **DAG / 并行节点**：七个节点几乎是严格顺序依赖（先读再改再跑再验），并行没有收益。
- **多 Agent（Planner/Coder/Reviewer）**：VERIFY 已经是"另一个视角的审查"，用同一个模型换一份 prompt 就够了；拆成多个 Agent 只会增加消息传递和状态同步的成本，见 Q8。

## 6. 已知局限（面试可主动说）

- 节点超时靠线程池的 `future.result(timeout)`，超时后工作线程不会被杀，只是不再等它；真正的硬超时在沙箱这一层（每条命令有自己的 timeout）。
- 转移表是写死的；如果任务类型扩展（比如需要"数据下载"节点），要改 `next_node`。这是有意的：转移规则改动应该经过代码审查，而不是模型即兴决定。

---

## 拷打题（闭卷先答，再对答案）

1. 画出七节点转移图，标出哪些边由 `needs_code_change` / `needs_execution` 决定、哪些边受 `max_repairs` 限制。
2. 手写 orchestrator 主循环的伪代码（10 行以内），必须体现：预算检查、节点超时、检查点、终止条件。
3. "为什么不用一个大 Prompt 循环？"——给出三个工程理由，每个理由对应代码里的一个具体机制。
4. EXECUTE 节点为什么不调模型？如果让模型来决定"跑哪条命令"，会引入什么问题？
5. `next_node` 为什么要写成纯函数？它的单元测试长什么样？
6. 一次运行在 IMPLEMENT 之后进程被杀，`resume` 是怎么接着跑的？哪些信息必须在 `TaskState` 里、哪些可以不在？
7. `max_steps`、`max_repairs`、`node_timeout_s` 三个上限各在哪里生效？超过后分别走到哪个节点？
8. NO_PROGRESS 是怎么检测的？为什么选"同样工具+同样参数连续 3 次"而不是"总调用次数"？
9. 如果要加一个"下载数据集"节点，需要改哪几个文件？（答案：state.Node 枚举、nodes.HANDLERS、orchestrator.next_node、Plan 的 steps 类型）
10. VERIFY 失败和 EXECUTE 失败都会进 REPAIR，REPAIR 怎么知道自己在修什么？（答案：`state.last_failure` + 最近一个失败节点的 `output["diagnosis"]` + `verify_result`）
