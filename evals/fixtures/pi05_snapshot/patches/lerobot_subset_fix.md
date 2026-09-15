# lerobot v2.1 episode 子集加载越界 bug 及修复

## 现象

给 `LeRobotDataset(repo_id, episodes=[...])` 传非连续的 episode 列表(例如从全量
LIBERO 数据集中只取 spatial suite 的 432 条)后,DataLoader 取样即崩:

```
IndexError: index 1261 is out of bounds for dimension 0 with size 432
  File ".../lerobot/common/datasets/lerobot_dataset.py", line 666, in _get_query_indices
    ep_start = self.episode_data_index["from"][ep_idx]
```

## 原因

`episode_data_index` 按子集长度构建(432 行、按子集位置索引),但
`_get_query_indices` 收到的 `ep_idx` 来自数据行里的 `episode_index` 列,
是**原始全量编号**(如 1261)。二者语义不一致,子集非连续时必然越界。

## 修复(3 行)

在 `_get_query_indices` 开头把原始编号映射回子集位置:

```python
def _get_query_indices(self, idx: int, ep_idx: int) -> tuple[dict[str, list[int | bool]]]:
    if self.episodes is not None:
        ep_idx = self.episodes.index(ep_idx)
    ep_start = self.episode_data_index["from"][ep_idx]
    ...
```

`lerobot_patch_applied.txt` 是打完补丁后该函数的实况快照(lerobot commit
`0cf864870cf29f4738d3ade893e6fd13fbd7cdb5`,即 openpi uv.lock 固定的版本)。

## 配套:openpi 侧的子集入口

openpi 的 `create_torch_dataset` 原本不暴露 `episodes` 参数。本复现给
`src/openpi/training/data_loader.py` 加了 `OPENPI_EPISODES_FILE` 环境变量
(见 `openpi_patches.diff`):指向一个 JSON 数组文件即可用任意 episode 子集
训练/统计,数据量消融(25%/50%/100%)全靠它,无需复制数据集。
