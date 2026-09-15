# 设计说明索引

每篇：解决什么问题 → 怎么做的（对应代码位置）→ 备选方案为什么不选 → 已知局限 → 拷打题。

| 模块 | 文件 | 代码 |
|---|---|---|
| M1 状态机 / Orchestrator | [M1_orchestrator.md](M1_orchestrator.md) | `core/orchestrator.py`, `core/state.py`, `core/nodes.py` |
| M2 工具协议 | [M2_tool_protocol.md](M2_tool_protocol.md) | `tools/base.py`, `tools/*.py`, `core/runtime.py` |
| M3 沙箱与安全 | [M3_sandbox.md](M3_sandbox.md) | `sandbox/policy.py`, `sandbox/local.py`, `sandbox/docker.py` |
| M4 验证与修复 | [M4_verify_repair.md](M4_verify_repair.md) | `core/failures.py`, `core/nodes.py` (verify/repair) |
| M5 状态持久化 / M6 上下文 | [M5_M6_state_context.md](M5_M6_state_context.md) | `store/trace.py`, `core/context.py` |
| M7 评测 | [M7_evaluation.md](M7_evaluation.md) | `evals/`, `reproagent/evals/` |
| M8 部署与接口 | [M8_deploy.md](M8_deploy.md) | `cli.py`, `api.py`, `runner.py`, `llm/client.py`, Docker |
