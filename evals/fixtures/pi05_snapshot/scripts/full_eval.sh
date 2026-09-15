#!/bin/bash
exec > /root/full_eval.log 2>&1
set -x
export PATH=/root/miniconda3/bin:/root/.local/bin:$PATH
unset http_proxy https_proxy
cd /root/autodl-tmp/openpi
source examples/libero/.venv/bin/activate
export PYTHONPATH=$PWD/third_party/libero:$PYTHONPATH
export MUJOCO_GL=egl
for suite in libero_spatial libero_object libero_goal libero_10; do
  echo "===== START $suite $(date) ====="
  python examples/libero/main.py --args.task-suite-name $suite --args.num-trials-per-task 50 > /root/eval_$suite.log 2>&1
  echo "===== END $suite code=$? $(date) ====="
  grep "Total success rate" /root/eval_$suite.log
done
echo ALL_EVAL_DONE
