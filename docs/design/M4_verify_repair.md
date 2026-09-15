# M4 · 验证与修复

> 对应代码：`reproagent/core/failures.py`（失败分类器）、`reproagent/core/nodes.py` 的 `verify_node` / `repair_node` / `_deterministic_checks`、`reproagent/core/state.py` 的 `FailureKind` / `REPAIRABLE`。
> 面试十问对应：Q2 如何判断 Agent 生成的结果可信；Q3 失败处理；Q7 离线评测（一半）。

## 1. 失败分类表（必须默写）

| 类别 | FailureKind | 触发规则（`failures.py`） | 可修复？ |
|---|---|---|---|
| 执行侧 | SYNTAX_ERROR | `SyntaxError|IndentationError|TabError` | ✅ |
| | IMPORT_ERROR | `ModuleNotFoundError|ImportError` | ✅（缺包时模型应报 fixable=false）|
| | FILE_NOT_FOUND | `FileNotFoundError|No such file` | ✅ |
| | RUNTIME_ERROR | `Traceback` / `XxxError:` | ✅ |
| | TEST_FAILURE | `N failed` / `FAILED test::name` | ✅ |
| | ASSERTION_ERROR | pytest 之外的 AssertionError | ✅ |
| | EXEC_TIMEOUT | 沙箱 `timed_out` | ✅ |
| | RESOURCE_LIMIT | `MemoryError|Killed|exit 137` | ❌ |
| | POLICY_BLOCKED | 沙箱 `blocked_reason` | ❌ |
| | NONZERO_EXIT | 退出码≠0 但无可识别 traceback | ✅ |
| 模型侧 | MODEL_OUTPUT_INVALID | JSON/schema 校验三次失败 | ✅ |
| | TOOL_ARGS_INVALID / TOOL_ERROR | 参数校验失败 / 工具自身异常 | ✅ |
| | NO_PROGRESS | 同样调用连续 3 次 / 3 轮不调工具 | ✅ |
| 验证侧 | VERIFY_MISMATCH | 跑通了但不满足成功条件 | ✅ |
| | MISSING_ARTIFACT | 期望产物不存在 | ✅ |
| 预算/基建 | BUDGET_EXHAUSTED / NODE_TIMEOUT / LLM_ERROR | 见 M1 | ❌ |

"可修复"= 在 `REPAIRABLE` 集合里，`next_node` 只对这些失败进 REPAIR。RESOURCE_LIMIT、POLICY_BLOCKED、预算类失败再修也没用，直接出报告——这就是"有限重试"的第一层含义：**不是所有失败都值得重试**。

## 2. 分类器的实现

`classify_exec(ExecResult) -> Diagnosis | None`：

1. `blocked_reason` → POLICY_BLOCKED；`timed_out` → EXEC_TIMEOUT；`exit_code == 0` → None。
2. 退出码 137/-9 或输出含 `Killed` → RESOURCE_LIMIT。
3. 按规则表顺序正则匹配 stderr+stdout，第一条命中即为类别；记录命中行作为 `evidence`。
4. 从 traceback 里抽最后一个**不在 site-packages** 的 `File "x", line N` 作为定位（`file`/`line`），抽最后一个 `XxxError:` 作为异常类型，抽 `FAILED test::name` 作为失败用例列表。

规则顺序有讲究：SYNTAX 在 IMPORT 前（语法错的文件被 import 时两个词都会出现），TEST_FAILURE 在 RUNTIME 前（pytest 输出里也有 Traceback）。

**为什么先用规则不用模型？** 规则免费、确定、可测（`test_classify_exec` 覆盖 8 种）；模型分类会把"分类错了"和"修复错了"两种错误混在一起，评测时说不清。模型的作用是在**知道类别之后**做诊断和修复。

## 3. VERIFY：两层判断

```
确定性检查（不调模型）                         模型裁判（Verdict）
- expected_outputs 文件存在？                  - 给它：目标、成功标准、plan.success_check、
- 上次执行每条命令 status==ok？                   确定性检查结果、执行输出尾巴、findings
- run_tests 的 passed/failed 计数              - 要它：passed / reason / missing / answer
- 抽取任务：output_schema 字段都非空？
                    ↓
          passed = 确定性全过 AND 模型说过
```

关键是 **AND**：模型说"过了"但文件不存在，仍然不过。反过来确定性检查过了但模型认为"结果和任务要求不符"（比如跑了 10 epoch 而任务要 100），也不过。模型裁判有一个明确的提示："a run that finishes is not the same as a correct result"。

`answer` 字段是最终给用户的结构化答案：抽取任务里是各字段的值；运行任务里是从输出解析的指标。评测用的就是这个 `answer`。

`enable_verify=False`（`no_verify` 消融）时只做确定性检查——用来量化"模型裁判"这一层抓出了多少假成功（评测里的 `claimed_but_wrong` 列）。

## 4. REPAIR：带类别的针对性修复

REPAIR 的 prompt 里塞进去的不是"出错了请修"，而是：

- 失败类别（`kind`）和证据行（`evidence`）
- 定位（`file:line`）和失败用例名
- 错误输出的**尾巴**（`error_tail`，stderr 尾 3000 字 + stdout 尾 1500 字，因为关键信息总在最后）
- **之前每一轮 REPAIR 的诊断摘要**（防止重复同一个错误的修法）
- 规则：先读失败代码、修根因不修症状、不改测试；修不了（缺包等）就 `fixable=false`

REPAIR 结束后回到 EXECUTE 重跑，而不是直接 VERIFY——修复必须被执行验证。

"有限重试"的第二层含义：`max_repairs`（默认 3）。为什么要有限？三个理由：① 成本线性增长而成功概率边际递减（评测里能看到大多数成功在第 1 轮修复，见 `success_after_repair`）；② 无限重试会掩盖系统性问题（任务本身不可能完成、沙箱缺依赖），报告出来比硬撑有用；③ 可预测的上界才能估 P95 延迟和成本（Q9）。

REPAIR 自己也可能失败：`fixable=false` → 报告；没改任何文件也没给新命令 → NO_PROGRESS → 报告。

## 5. 与消融的关系

- `no_repair`（max_repairs=0）：量化 REPAIR 节点的贡献 = full 与 no_repair 的成功率差，主要来自 bug_fix 和 repo_run 类任务。
- `no_verify`：量化模型裁判抓出的假成功。

---

## 拷打题

1. 默写失败分类表：至少 12 个类别、每个的触发规则、是否可修复。
2. `classify_exec` 的规则顺序为什么是 SYNTAX → IMPORT → FILE_NOT_FOUND → RESOURCE → TEST → ASSERTION → RUNTIME？交换其中两条会出什么错？
3. 为什么失败分类用规则不用模型？模型在修复流程里的角色是什么？
4. VERIFY 的两层判断是怎么合成最终结论的？举一个"确定性检查过了但模型判失败"的例子，和一个反过来的例子。
5. "如何判断 Agent 生成的结果可信"——用本项目的机制回答，至少四点（确定性检查、模型裁判、evidence 引用、评测的 `claimed_but_wrong`）。
6. 为什么重试要有限？给三个理由。`max_repairs=3` 是怎么定的？
7. REPAIR 的 prompt 里放了哪六类信息？为什么要放之前的修复历史？
8. REPAIR 之后为什么回 EXECUTE 而不是直接 VERIFY？
9. 哪些失败类别不进 REPAIR？为什么？
10. `no_verify` 消融和 `no_repair` 消融分别量化什么？预期哪类任务差异最大？
