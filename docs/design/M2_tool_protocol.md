# M2 · 工具协议

> 对应代码：`reproagent/tools/base.py`（协议）、`reproagent/tools/*.py`（六个工具）、`reproagent/core/runtime.py`（工具循环，含 `finish` 伪工具和自由文本消融）。
> 面试十问对应：Q3 工具调用失败/超时/部分成功；Q10 换模型时哪些模块不变。

## 1. 一个工具 = 三样东西

```python
class Tool:
    name: str                    # 模型看到的函数名
    description: str             # 模型看到的说明（写给模型的，不是写给人的）
    Params: type[BaseModel]      # Pydantic 参数模型 → 自动导出 JSON schema
    def run(self, params, ctx) -> ToolResult
```

`schema()` 直接用 `Params.model_json_schema()` 生成 OpenAI function-calling 格式。**模型看到的契约和代码校验的契约是同一个对象**，不会出现"文档说要 path，代码其实读 file"的漂移。

## 2. 统一返回 `ToolResult`

```python
status:    "ok" | "error" | "timeout" | "blocked" | "partial"
stdout:    str            # 主要内容
stderr:    str            # 错误信息
artifacts: list[str]      # 产物路径
next_hint: str | None     # 工具给模型的下一步建议
data:      dict           # 结构化附加信息（测试计数、diff、退出码……）
```

五种 status 的含义要背下来：

| status | 含义 | 例子 |
|---|---|---|
| ok | 做完了 | 测试全过 |
| error | 工具正常运行，但结果是失败 | 测试有失败；文件不存在 |
| timeout | 沙箱超时把命令杀了 | 训练脚本跑太久 |
| blocked | 策略层拒绝执行 | `curl`、改测试文件、路径逃逸 |
| partial | 做了一部分，信息不完整 | grep 命中数被截断；PDF 没找到指定章节 |

**为什么不让工具抛异常？** 因为异常会把整个节点带崩，而"文件不存在"对 agent 来说是**信息**不是**事故**——它应该看到这个结果然后换个路径。`Tool.__call__` 把参数校验错误、`PermissionError`、任意异常全部转成 `ToolResult`，节点永远拿到一个可读的结果。

## 3. `next_hint`：工具比模型更知道下一步

- `run_tests` 失败 → `"2 failing: tests/test_stats.py::test_ngrams_counts, ... Read the traceback and fix the implementation."`
- `patch_file` 的 `old` 没匹配上 → `"old text not found in stats.py. Closest line: 'return [tuple(...)]'"`
- `read_pdf` 输出被截断 → `"Output truncated at 12000 chars (48000 available). Use pages/section/grep to narrow."`

这些提示对工具来说是几行代码就能算出来的，对模型来说却是一整轮推理（而且可能推错）。每省一轮就是省一次模型调用——在评测里体现为平均模型调用次数下降。

## 4. 六个工具与节点级白名单

| 工具 | 读/写 | 允许它的节点 |
|---|---|---|
| read_pdf | 读 | RETRIEVE, IMPLEMENT, REPAIR |
| inspect_repo（tree/grep/read） | 读 | RETRIEVE, IMPLEMENT, REPAIR |
| patch_file（replace/create） | 写 | IMPLEMENT, REPAIR |
| run_tests | 执行 | EXECUTE（由代码调用，不给模型） |
| run_experiment | 执行 | EXECUTE（同上） |
| save_artifact | 写产物 | 任意（当前主要由 REPORT 逻辑使用） |

节点白名单是第二层控制：RETRIEVE 阶段模型**根本看不到** `patch_file`，就不可能"顺手改代码"。这比在 prompt 里写"请不要修改文件"可靠得多。

`patch_file` 默认保护 `tests/`、`test_*.py`、`conftest.py`——bug 修复任务里模型最常见的作弊方式就是改测试，所以直接在工具层拦掉（`blocked`）。

## 5. 工具循环（ReAct 的最小实现）

`NodeRuntime.tool_loop`：

```
messages = [system, user]
loop:
    预算检查；上下文压缩
    resp = 模型(messages, tools=白名单schema + finish)
    若 resp 没有 tool_calls：尝试从文本解析最终答案；三次都不行 → NO_PROGRESS
    对每个 tool_call：
        finish → 用 final_schema 校验参数 → 返回
        其他   → 执行 → ToolResult.to_model_text() 截断后追加为 role=tool 消息
        同样调用连续 3 次 → NO_PROGRESS
```

**`finish` 伪工具**是关键设计：节点的"最终答案"也是一个带 schema 的工具调用，所以 Findings / ImplementResult / RepairResult 都经过 Pydantic 校验，校验失败会把错误信息回给模型让它重填。

## 6. 消融：自由文本协议（`free_text_tools`）

同一个状态机，但工具改成"prompt 里描述 + 模型写 ```tool 代码块 + 返回纯 stdout/stderr 文本"（没有 status/next_hint/data）。这组消融回答的问题是：**结构化协议本身值多少**。预期差异来自三处：参数拼错（无 schema 提示）、看不到 status 导致误判成功、没有 next_hint 多绕几轮。

## 7. 换模型时什么不变（Q10）

工具协议、状态机、沙箱、评测——全部不变。变的只有 `AgentConfig.model` 这一个字符串和 `llm/client.py` 里 LiteLLM 的适配。前提是新模型支持 function calling；如果不支持，`structured_tools=False` 的自由文本路径就是降级方案（这也是保留这条消融的第二个理由）。

---

## 拷打题

1. 闭卷写出 `patch_file` 的完整 schema（参数名、类型、必填、约束），以及它返回的 `ToolResult` 每个字段填什么。
2. `ToolResult.status` 五种取值分别是什么？"测试有失败"是 error 还是 partial？为什么？
3. 工具为什么不抛异常？`Tool.__call__` 里拦了哪三类异常，各转成什么 status？
4. `next_hint` 的设计理由是什么？举两个具体例子说明它省掉了模型的一轮推理。
5. 节点级工具白名单和 prompt 里写"不要改文件"相比，可靠性差在哪？
6. `finish` 为什么做成伪工具而不是"让模型最后输出一段 JSON"？（答案：走同一条 schema 校验路径；模型在 function-calling 模式下混输文本和 JSON 不稳定）
7. 自由文本协议消融拿掉了哪三样东西？你预期每一样各影响什么指标？
8. 模型调工具时参数不是合法 JSON（`__raw__`）会发生什么？
9. `run_tests` 怎么从 pytest 输出里解析出通过/失败计数和失败用例名？用了什么正则？
10. 如果要新增一个 `list_datasets` 工具，需要写哪几样东西？要不要改状态机？
