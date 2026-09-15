# π₀.₅ on LIBERO:评测复现、LoRA 微调与消融

[English](README_en.md)

基于 [Physical Intelligence openpi](https://github.com/Physical-Intelligence/openpi) 的 π₀.₅ 复现，全部实验在**单张 RTX 4090（24 GB）** 上完成：

- LIBERO 四个 suite 的官方检查点评测，平均成功率与官方报告相差 0.1 个百分点；
- 一份可在 24 GB 单卡上运行的 π₀.₅ LoRA 训练配置（官方仓库未提供）；
- 数据量、语言指令改写、物体初始位姿扰动三组消融。

模型为 π₀.₅（PaliGemma 3B 主干 + Flow Matching 动作专家，3.4 B 参数）。基线版本：openpi `15a9616`，lerobot `0cf8648`（详见 `patches/versions.txt`）。

---

## 结果

### 官方检查点评测（`pi05_libero`，每任务 50 次试验）

| LIBERO suite | 本复现 | 官方报告 | 差值 |
|---|---|---|---|
| Spatial | 98.2% | 98.8% | −0.6 |
| Object | 98.8% | 98.2% | +0.6 |
| Goal | 97.0% | 98.0% | −1.0 |
| Long (libero_10) | 93.8% | 92.4% | +1.4 |
| **平均** | **96.95%** | 96.85% | +0.1 |

### LoRA 微调

从官方 `pi05_libero` 检查点热启，只训练 LoRA 低秩参数（50 M，占 3.4 B 的 1.5%）。LIBERO-Spatial 432 条轨迹，4000 步，batch 16，训练显存 22.1 GB。

| 模型 | Spatial 成功率（50 次/任务） |
|---|---|
| 官方检查点 | 98.2% |
| LoRA 微调后 | 99.2% |

![loss 曲线](results/loss_curves.png)

loss 由 0.043 降至约 0.0047 后收敛。三个数据量子集的 loss 轨迹几乎重合，说明在该规模下瓶颈不在拟合能力，而在数据覆盖。

### 消融（LIBERO-Spatial，每任务 20 次试验）

**数据量**　episode 子集按任务分层采样（25% = 103 条，50% = 213 条），各自独立训练 4000 步。

![数据量-成功率](results/data_scaling.png)

25% 数据即达到官方检查点水平，100% 相对 25% 仅提升 0.7 个百分点，收益递减明显。

**鲁棒性**　以微调后模型的干净评测（99.2%）为基准。

| 扰动 | 成功率 | 降幅 |
|---|---|---|
| 指令同义改写（10 条规则，如 pick up → grab、black bowl → dark bowl） | 92.5% | −6.7 |
| 物体初始位姿扰动（自由关节 xy 上 ±2 cm 均匀噪声） | 90.0% | −9.2 |

语言改写下的下降相对温和，而空间扰动是两者中更明显的短板。改写规则见 `ablations/main_rephrase.py`，扰动通过向 MuJoCo 自由关节 qpos 注入噪声实现，见 `ablations/main_perturb.py`。示例视频（含一个位姿扰动导致的失败回合）在 `results/videos/`。

---

## 实现说明

官方仓库中 `pi05_libero` 是 batch 256 的全量微调配置，LoRA 配置只覆盖 π₀ 与 π₀-FAST。为在单卡上完成上述实验，本仓库对 openpi 做了三处改动，均在 `patches/openpi_patches.diff` 中：

**`pi05_libero_lora` 训练配置**　参照 π₀ 的 LoRA 写法组合 `pi05=True` + `gemma_2b_lora` + `gemma_300m_lora` 及对应 freeze_filter，从 `pi05_libero` 热启。

**episode 子集训练**　新增 `OPENPI_EPISODES_FILE` 环境变量，将指定的 episode 列表传给 lerobot 原生的 `episodes=` 参数。数据量消融因此不需要复制或重新转换数据集，同时数据加载只展开子集。

**lerobot 子集索引修复**　lerobot v2.1 在 `episodes=` 传入非连续子集时会索引越界：`_get_query_indices` 用原始 episode 编号去查按子集长度建立的索引表。三行修复，说明见 `patches/lerobot_subset_fix.md`。

此外 `tools/` 下提供 LoRA 权重的提取与合并脚本：训练产物中非 LoRA 参数在训练时冻结，与官方底座逐位一致，因此只需保存 LoRA 张量（177 MB），使用时与官方 `pi05_libero` params 合并即可复原完整检查点。

---

## 复现

环境：Ubuntu 22.04 + CUDA 12.x，数据盘 ≥ 100 GB。

```bash
pip install uv
git clone --recurse-submodules https://github.com/Physical-Intelligence/openpi.git && cd openpi
git apply /path/to/this-repo/patches/openpi_patches.diff
GIT_LFS_SKIP_SMUDGE=1 uv sync
```

评测官方检查点（检查点 11.6 GB，首次运行自动从 `gs://openpi-assets` 下载）：

```bash
uv run scripts/serve_policy.py --env LIBERO &
python examples/libero/main.py --args.task-suite-name libero_spatial
```

`scripts/full_eval.sh` 串行跑完四个 suite。

LoRA 微调（数据集为 HuggingFace 上的 `physical-intelligence/libero`）：

```bash
export OPENPI_EPISODES_FILE=/path/to/this-repo/ablations/spatial_episodes.json
uv run scripts/compute_norm_stats.py --config-name pi05_libero_lora
XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 uv run scripts/train.py pi05_libero_lora \
    --exp-name my_run --batch-size 16 --no-wandb-enabled
```

三组消融的完整流水线见 `scripts/stage3.sh`。

### 需要注意的几点

- **无头渲染**　MuJoCo 需要 `MUJOCO_GL=egl`。若报 `eglQueryString ... NoneType`，是缺 glvnd 调度层，安装 `libegl1 libgl1 libglvnd0` 即可。
- **容器环境下的评测**　官方推荐的 `docker compose` 在本身已是容器的实例中无法使用。改为 policy server（websocket，端口 8000）与 LIBERO 客户端（独立 Python 3.8 虚拟环境）两个进程，见 `scripts/full_eval.sh`。
- **显存**　π₀.₅ LoRA 在 24 GB 卡上 batch 32 会 OOM，batch 16 实测占用 22.1 GB。
- **检查点磁盘峰值**　每份保存 params 5.9 GB + train_state 3 GB，`save_interval` 较小时新旧两份会短暂共存，磁盘余量不足会在保存时以 `RESOURCE_EXHAUSTED` 中断训练。短训建议只保存最后一步。
- **LIBERO 首次运行**　会交互式询问数据集路径，应答一次后写入 `~/.libero`。

---

## 权重

三个微调产物以 LoRA-only npz（各 177 MB）形式提供：100% 数据、50%、25%。用 `tools/merge_lora.py` 与官方 `pi05_libero` params 合并即可使用。

## 仓库结构

```
patches/     openpi 补丁、lerobot 修复说明、版本信息
scripts/     环境、评测、训练与消融的 shell 流水线
ablations/   改写与扰动评测客户端、episode 子集列表
tools/       LoRA 提取与合并
results/     loss 数据与图表、评测日志、norm stats、示例视频
```

`results/norm_stats/` 为本复现计算的归一化统计量，评测与复用权重时需要。

## 致谢

[openpi](https://github.com/Physical-Intelligence/openpi) ·
[LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO) ·
[LeRobot](https://github.com/huggingface/lerobot)
