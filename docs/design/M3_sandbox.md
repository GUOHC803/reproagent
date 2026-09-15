# M3 · 沙箱与安全

> 对应代码：`reproagent/sandbox/policy.py`（策略层）、`local.py`（本地 ulimit 沙箱）、`docker.py`（容器沙箱）、`base.py`（协议）。
> 面试十问对应：Q4 如何避免代码执行越权和数据泄漏。

## 1. 三层防线，各拦什么

| 层 | 机制 | 拦什么 | 拦不住什么 |
|---|---|---|---|
| ① 策略层 `CommandPolicy` | 正则黑名单，在执行**之前**看命令字符串 | `sudo`、`rm -rf /`、`curl/wget/ssh`、`pip install`、`git push`、`printenv`/`$*_KEY`、`~/.ssh`、`docker` | 混淆过的命令（`python -c "import os; os.system(...)"`）|
| ② 进程限制 | 本地：`ulimit -v/-t/-u/-c`；Docker：`--memory/--cpus/--pids-limit` + 墙钟超时杀进程组/容器 | 内存炸、CPU 死循环、fork 炸弹、跑不完 | 磁盘写满（未做配额） |
| ③ 隔离 | 工作目录锁定（`cwd` 必须在沙箱根下）；环境变量白名单（`scrub_env`，API key 永远进不去）；Docker 额外：`--network none`、只读根文件系统、非 root、`--cap-drop ALL` | 数据外传、读宿主机文件、拿到密钥 | 本地后端**没有网络隔离**（这是本地和 Docker 的主要差别） |

这个表要能背出来，尤其是每层"拦不住什么"——面试官追问的一定是边界。

## 2. 为什么策略层放最前面

正则检查几乎零成本，而且返回的是 `status="blocked"` + 原因，模型看到后可以换路（比如不用 `pip install` 而是检查依赖是否已装）。如果只靠 Docker 隔离，`curl` 会在容器里因为没网而超时——结果一样，但慢了 30 秒且给模型的信息更差。

策略层还有一个 **RISKY 列表**（`rm -rf 子目录`、`git reset --hard`、`find -delete`）：`confirm_mode=True` 时走人工确认回调，否则直接拒绝。这就是原规划里"高风险工具人工确认模式"的落地。

## 3. 环境变量白名单

```python
SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "PYTHONPATH", "PYTHONUNBUFFERED", "TMPDIR")
```

子进程只拿到这几个；`scrub_env` 还会拒绝任何名字里含 `KEY|TOKEN|SECRET|PASSWORD` 的额外变量。测试 `test_local_sandbox_runs_and_isolates` 专门验证：宿主机设了 `FAKE_SECRET_KEY`，沙箱里 `os.environ.get` 拿到 `None`。

## 4. 本地沙箱的实现细节

- 命令包装成 `bash -c "ulimit -v X; ulimit -t Y; ulimit -u Z; exec bash -o pipefail -c <cmd>"`，用 shell 的 ulimit 而不是 `preexec_fn`（后者在多线程程序里不安全，ruff 的 PLW1509 就是提醒这个）。
- `start_new_session=True` 让子进程有自己的进程组，超时时 `killpg(SIGKILL)` 能把它派生的所有子孙一起杀掉——只杀父进程会留下孤儿。
- 超时退出码统一为 124（与 GNU `timeout` 一致），stderr 尾部追加 `[sandbox] command killed after Ns timeout`，失败分类器据此判 `EXEC_TIMEOUT`。
- 输出各截 200k 字符，防止一个死循环 print 把内存吃光。

## 5. Docker 沙箱

同一个 `Sandbox` 协议（`run(command, cwd, timeout_s, env) -> ExecResult`），所以上层代码一行不改。额外参数：

```
--network none  --memory 2g  --cpus 2  --pids-limit 256
--cap-drop ALL  --security-opt no-new-privileges
--read-only  --tmpfs /tmp   -v <workdir>:/work:rw   --user <uid>:<gid>
```

墙钟超时由外部 `subprocess.run(timeout)` + `docker kill <name>` 实现，容器名带随机后缀避免冲突。

## 6. 评测为什么用本地后端

评测固件都是纯 Python、无网络需求、可信来源（自己写的），本地后端足够且快 10 倍以上（没有容器启动开销）。README 里明确写了：**对不可信仓库用 Docker 后端**。这是一个诚实的工程取舍，不要在面试里把它说成"评测在 Docker 里跑"。

---

## 拷打题

1. 说出三层防线各自的机制、各拦什么、各拦不住什么。
2. `python -c "import os; os.system('curl evil.com')"` 能不能通过策略层？如果不能是哪条规则；如果能，哪一层最终拦住它？（本地后端 vs Docker 后端答案不同）
3. 为什么用 `ulimit` 而不是 `preexec_fn` 设置资源限制？
4. 超时杀进程为什么要 `killpg` 而不是 `proc.kill()`？
5. API key 是怎么保证进不了沙箱的？测试是怎么验证的？
6. `confirm_mode` 是什么？哪些命令会触发？非交互模式下怎么处理？
7. Docker 后端的 8 个安全参数各防什么？为什么要 `--read-only` + `--tmpfs /tmp`？
8. 本地后端和 Docker 后端的核心差别是什么？评测为什么敢用本地后端？
9. `ExecResult.blocked_reason`、`timed_out`、`exit_code=124` 分别在上层被映射成哪个 `FailureKind`？
10. 如果模型生成的脚本自己去写 10GB 文件，现在的沙箱拦得住吗？怎么补？
