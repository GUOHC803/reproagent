#!/bin/bash
exec > /root/eval_ft.log 2>&1
set -x
export PATH=/root/miniconda3/bin:/root/.local/bin:$PATH
export UV_CACHE_DIR=/root/autodl-tmp/cache/uv
unset http_proxy https_proxy
cd /root/autodl-tmp/openpi
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.9
uv run scripts/serve_policy.py policy:checkpoint --policy.config pi05_libero_lora --policy.dir checkpoints/pi05_libero_lora/lora_run1_bs16/3999 &
SERVER_PID=$!
until grep -q "server listening" /root/eval_ft.log; do sleep 5; done
source examples/libero/.venv/bin/activate
export PYTHONPATH=$PWD/third_party/libero:$PYTHONPATH
export MUJOCO_GL=egl
python examples/libero/main.py --args.task-suite-name libero_spatial --args.num-trials-per-task 50 --args.video-out-path data/libero/videos_ft > /root/eval_ft_spatial.log 2>&1
echo CLIENT_CODE=$?
grep "Total success rate" /root/eval_ft_spatial.log
kill $SERVER_PID
echo EVAL_FT_DONE
