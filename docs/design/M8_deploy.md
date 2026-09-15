# M8 · 部署与接口

> 对应代码：`reproagent/cli.py`、`reproagent/api.py`、`reproagent/runner.py`、`Dockerfile`、`docker/sandbox.Dockerfile`、`docker-compose.yml`、`.github/workflows/ci.yml`。
> 面试十问对应：Q10 换模型时哪些模块应保持不变。

## 1. 三个入口，一个 `run_task`

```
CLI (typer)  ─┐
FastAPI      ─┼─▶ runner.run_task(task, cfg, source_dir) ─▶ Orchestrator / single_call
eval runner  ─┘
```

`run_task` 做的事：复制输入目录到 `runs/<run_id>/work`（agent 永远不动原目录）→ 建 TraceStore → 建模型客户端 → 建沙箱 → 建工具表 → 按 `cfg.mode` 走状态机或单次调用。三个入口共用它，所以 CLI 跑出来的和评测跑出来的是同一条路径。

## 2. CLI 命令

| 命令 | 用途 |
|---|---|
| `run "目标" --dir repo [--pdf x.pdf] [--command ...] --config full` | 跑一个即席任务 |
| `run-task task.yaml` | 跑一个 YAML 定义的任务（和评测同格式） |
| `resume <run_id>` | 从检查点恢复 |
| `runs` / `show <run_id>` / `replay <run_id> [--node]` | 看历史 / 看步骤与指标 / 回放对话 |
| `eval --configs full,no_repair,... --workers 3` | 批跑评测 |
| `serve` | 起 HTTP 服务 |
| `tools` | 打印模型看到的工具 schema |

## 3. HTTP API

`POST /tasks`（提交，返回 run_id，后台线程池执行）、`GET /tasks/{id}`（状态、步骤、指标、产物）、`GET /tasks/{id}/report`、`GET /tasks/{id}/artifacts/{name}`、`POST /tasks/upload`（传一个 PDF 直接提问）。状态以 trace DB 为准，服务重启不丢。

没做：鉴权、任务队列（Celery/RQ）、WebSocket 推流。第一版明确不做前端。

## 4. 模型层：LiteLLM 的意义

`llm/client.py` 是唯一 import litellm 的地方。上层只依赖四个字段：`content`、`tool_calls`、`prompt_tokens`、`completion_tokens`。换模型 = 改 `.env` 里的 `REPROAGENT_MODEL`（LiteLLM 命名，如 `openai/deepseek-v4.1-flash`、`anthropic/claude-...`、`ollama/qwen`）。

三个客户端实现：`LiteLLMClient`（真调用，带指数退避重试）、`ReplayLLMClient`（从录制的 JSONL 回放，CI 不需要 key）、`MockLLMClient`（单元测试脚本化）。33 个测试全部不碰网络。

**换模型时不变的**：状态机、工具协议、沙箱、trace、评测、检查器。**会变的**：prompt 可能要微调（不同模型对 function calling 的遵从度不同）、成本表、function calling 不支持时要切 `structured_tools=False`。

## 5. Docker

- `Dockerfile`：服务镜像（CLI + API），`docker compose up` 起服务。
- `docker/sandbox.Dockerfile`：沙箱镜像（python + numpy + pytest，非 root 用户），给 `sandbox.backend=docker` 用。
- 服务镜像里要用 Docker 沙箱需挂载 docker.sock（compose 里注释给出）。

## 6. CI

GitHub Actions：装依赖 → ruff → pytest。评测不进 CI（要 key、要几十分钟）；评测结果作为文件提交在 `evals/results/`。

---

## 拷打题

1. 三个入口为什么要共用 `run_task`？它做了哪六件事？
2. 换模型时哪些模块不变、哪些会变？LiteLLM 层在这里的意义是什么？
3. CI 里跑的测试为什么不需要 API key？三个模型客户端各用在哪？
4. `POST /tasks` 之后服务重启，`GET /tasks/{id}` 还能查到吗？为什么？
5. 服务镜像和沙箱镜像为什么分开？
6. 如果要把评测跑进 CI，需要解决哪三个问题？（key 管理、时长、非确定性）
7. `run_task` 为什么复制输入目录而不是原地工作？`--no-copy` 什么时候用？
