#!/bin/bash
exec > /root/stage3.log 2>&1
set -x
export PATH=/root/miniconda3/bin:/root/.local/bin:$PATH
export UV_CACHE_DIR=/root/autodl-tmp/cache/uv
unset http_proxy https_proxy
cd /root/autodl-tmp/openpi
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.9

for pct in 25 50; do
  echo "===== TRAIN lora_$pct $(date) ====="
  OPENPI_EPISODES_FILE=/root/spatial_eps_$pct.json uv run scripts/train.py pi05_libero_lora --exp-name lora_$pct --batch-size 16 --overwrite --no-wandb-enabled
  echo "TRAIN_${pct}_CODE=$?"
  rm -rf checkpoints/pi05_libero_lora/lora_$pct/3999/train_state
done

run_eval() {
  name=$1; ckpt=$2; client=$3
  echo "===== EVAL $name $(date) ====="
  uv run scripts/serve_policy.py policy:checkpoint --policy.config pi05_libero_lora --policy.dir $ckpt > /root/server_$name.log 2>&1 &
  SPID=$!
  ok=0
  for i in $(seq 1 150); do grep -q "server listening" /root/server_$name.log && { ok=1; break; }; sleep 5; done
  if [ $ok -eq 0 ]; then echo "SERVER_FAIL_$name"; kill $SPID 2>/dev/null; return 1; fi
  ( source examples/libero/.venv/bin/activate
    export PYTHONPATH=$PWD/third_party/libero:$PYTHONPATH
    export MUJOCO_GL=egl
    python $client --args.task-suite-name libero_spatial --args.num-trials-per-task 20 --args.video-out-path data/libero/videos_$name > /root/eval_$name.log 2>&1 )
  echo "EVAL_${name}_CODE=$?"
  grep "Total success rate" /root/eval_$name.log
  kill $SPID 2>/dev/null; sleep 15
}

run_eval abl_data25 checkpoints/pi05_libero_lora/lora_25/3999 examples/libero/main.py
run_eval abl_data50 checkpoints/pi05_libero_lora/lora_50/3999 examples/libero/main.py
run_eval abl_rephrase checkpoints/pi05_libero_lora/lora_run1_bs16/3999 examples/libero/main_rephrase.py
run_eval abl_perturb checkpoints/pi05_libero_lora/lora_run1_bs16/3999 examples/libero/main_perturb.py
echo STAGE3_DONE
