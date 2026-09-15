#!/bin/bash
exec > /root/stage3b.log 2>&1
set -x
export PATH=/root/miniconda3/bin:/root/.local/bin:$PATH
export UV_CACHE_DIR=/root/autodl-tmp/cache/uv
unset http_proxy https_proxy
cd /root/autodl-tmp/openpi
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.9
until grep -q STAGE3_DONE /root/stage3.log; do sleep 60; done
echo "stage3 main finished, retraining lora_50 with final-only checkpointing"
OPENPI_EPISODES_FILE=/root/spatial_eps_50.json uv run scripts/train.py pi05_libero_lora --exp-name lora_50 --batch-size 16 --overwrite --no-wandb-enabled --save-interval 4000
echo "TRAIN_50B_CODE=$?"
rm -rf checkpoints/pi05_libero_lora/lora_50/3999/train_state
uv run scripts/serve_policy.py policy:checkpoint --policy.config pi05_libero_lora --policy.dir checkpoints/pi05_libero_lora/lora_50/3999 > /root/server_abl_data50b.log 2>&1 &
SPID=$!
ok=0
for i in $(seq 1 150); do grep -q "server listening" /root/server_abl_data50b.log && { ok=1; break; }; sleep 5; done
if [ $ok -eq 1 ]; then
  ( source examples/libero/.venv/bin/activate
    export PYTHONPATH=$PWD/third_party/libero:$PYTHONPATH
    export MUJOCO_GL=egl
    python examples/libero/main.py --args.task-suite-name libero_spatial --args.num-trials-per-task 20 --args.video-out-path data/libero/videos_abl_data50 > /root/eval_abl_data50.log 2>&1 )
  echo "EVAL_abl_data50_CODE=$?"
  grep "Total success rate" /root/eval_abl_data50.log
else
  echo SERVER_FAIL_abl_data50b
fi
kill $SPID 2>/dev/null
echo STAGE3B_DONE
