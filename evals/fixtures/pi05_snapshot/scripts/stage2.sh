#!/bin/bash
exec > /root/stage2.log 2>&1
set -x
export PATH=/root/miniconda3/bin:/root/.local/bin:$PATH
export UV_CACHE_DIR=/root/autodl-tmp/cache/uv
export HF_TOKEN=${HF_TOKEN:?please set HF_TOKEN}
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
unset http_proxy https_proxy
export OPENPI_EPISODES_FILE=/root/spatial_episodes.json
cd /root/autodl-tmp/openpi

echo "[1/2] compute norm stats..."
uv run scripts/compute_norm_stats.py --config-name pi05_libero_lora || { echo NORM_FAIL; exit 1; }
echo NORM_OK

echo "[2/2] LoRA training..."
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.9
uv run scripts/train.py pi05_libero_lora --exp-name lora_run1 --overwrite --no-wandb-enabled
code=$?
if [ $code -ne 0 ] && grep -qiE "RESOURCE_EXHAUSTED|out of memory" /root/stage2.log; then
  echo "OOM at bs32, retry bs16"
  uv run scripts/train.py pi05_libero_lora --exp-name lora_run1_bs16 --batch-size 16 --overwrite --no-wandb-enabled
  code=$?
fi
echo TRAIN_CODE=$code
echo STAGE2_DONE
