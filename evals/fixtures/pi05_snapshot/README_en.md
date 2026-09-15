# π₀.₅ on LIBERO: Evaluation, LoRA Fine-tuning, and Ablations

[简体中文](README.md)

A reproduction of π₀.₅ from [Physical Intelligence openpi](https://github.com/Physical-Intelligence/openpi), with every experiment run on a **single RTX 4090 (24 GB)**:

- evaluation of the official checkpoint on all four LIBERO suites, with an average success rate within 0.1 points of the reported numbers;
- a π₀.₅ LoRA training configuration that fits on a single 24 GB GPU (upstream provides none);
- three ablations: training data scale, language instruction rephrasing, and object initial-pose perturbation.

The model is π₀.₅ (PaliGemma 3B backbone + flow-matching action expert, 3.4 B parameters). Baseline versions: openpi `15a9616`, lerobot `0cf8648` (see `patches/versions.txt`).

---

## Results

### Official checkpoint evaluation (`pi05_libero`, 50 trials per task)

| LIBERO suite | This reproduction | Reported | Δ |
|---|---|---|---|
| Spatial | 98.2% | 98.8% | −0.6 |
| Object | 98.8% | 98.2% | +0.6 |
| Goal | 97.0% | 98.0% | −1.0 |
| Long (libero_10) | 93.8% | 92.4% | +1.4 |
| **Average** | **96.95%** | 96.85% | +0.1 |

### LoRA fine-tuning

Warm-started from the official `pi05_libero` checkpoint, training only the low-rank parameters (50 M, or 1.5% of 3.4 B). LIBERO-Spatial, 432 demonstrations, 4000 steps, batch size 16, 22.1 GB of training memory.

| Model | Spatial success rate (50 trials/task) |
|---|---|
| Official checkpoint | 98.2% |
| After LoRA fine-tuning | 99.2% |

![loss curves](results/loss_curves.png)

The loss falls from 0.043 to roughly 0.0047 and then converges. The three data-scale subsets produce nearly identical loss trajectories, which suggests that at this scale the bottleneck is data coverage rather than fitting capacity.

### Ablations (LIBERO-Spatial, 20 trials per task)

**Data scale.** Episode subsets were drawn with per-task stratified sampling (25% = 103 demonstrations, 50% = 213), each trained independently for 4000 steps.

![data scaling](results/data_scaling.png)

25% of the data already matches the official checkpoint, and going from 25% to 100% adds only 0.7 points — clear diminishing returns.

**Robustness.** Measured against the clean evaluation of the fine-tuned model (99.2%).

| Perturbation | Success rate | Δ |
|---|---|---|
| Synonym rephrasing of instructions (10 rules, e.g. pick up → grab, black bowl → dark bowl) | 92.5% | −6.7 |
| Object initial-pose perturbation (uniform ±2 cm noise on free-joint xy) | 90.0% | −9.2 |

The drop under rephrasing is comparatively mild, while spatial perturbation is the clearer weakness of the two. Rephrasing rules are in `ablations/main_rephrase.py`; perturbation is applied by injecting noise into MuJoCo free-joint `qpos`, see `ablations/main_perturb.py`. Sample rollout videos, including one failure caused by pose perturbation, are in `results/videos/`.

---

## Implementation notes

Upstream, `pi05_libero` is a full fine-tuning configuration with batch size 256, and the LoRA configurations only cover π₀ and π₀-FAST. Three changes to openpi were needed to run the experiments above on a single GPU; all of them are in `patches/openpi_patches.diff`.

**A `pi05_libero_lora` training configuration.** Composed after the π₀ LoRA recipe: `pi05=True` + `gemma_2b_lora` + `gemma_300m_lora` with the matching freeze filter, warm-started from `pi05_libero`.

**Episode-subset training.** An `OPENPI_EPISODES_FILE` environment variable passes an explicit episode list to lerobot's native `episodes=` argument. The data-scale ablation therefore needs no dataset copying or re-conversion, and data loading materializes only the subset.

**A lerobot subset-indexing fix.** lerobot v2.1 goes out of bounds when `episodes=` receives a non-contiguous subset: `_get_query_indices` looks up original episode ids in an index table built for the subset length. A three-line fix, described in `patches/lerobot_subset_fix.md`.

`tools/` additionally contains scripts to extract and merge LoRA weights. Non-LoRA parameters are frozen during training and stay bit-identical to the official base, so only the LoRA tensors need to be stored (177 MB); merging them back onto the official `pi05_libero` params restores a full checkpoint.

---

## Reproducing

Environment: Ubuntu 22.04 with CUDA 12.x, at least 100 GB of disk.

```bash
pip install uv
git clone --recurse-submodules https://github.com/Physical-Intelligence/openpi.git && cd openpi
git apply /path/to/this-repo/patches/openpi_patches.diff
GIT_LFS_SKIP_SMUDGE=1 uv sync
```

Evaluate the official checkpoint (11.6 GB, downloaded from `gs://openpi-assets` on first run):

```bash
uv run scripts/serve_policy.py --env LIBERO &
python examples/libero/main.py --args.task-suite-name libero_spatial
```

`scripts/full_eval.sh` runs all four suites sequentially.

LoRA fine-tuning (the dataset is `physical-intelligence/libero` on HuggingFace):

```bash
export OPENPI_EPISODES_FILE=/path/to/this-repo/ablations/spatial_episodes.json
uv run scripts/compute_norm_stats.py --config-name pi05_libero_lora
XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 uv run scripts/train.py pi05_libero_lora \
    --exp-name my_run --batch-size 16 --no-wandb-enabled
```

The full ablation pipeline is in `scripts/stage3.sh`.

### Notes

- **Headless rendering.** MuJoCo needs `MUJOCO_GL=egl`. An `eglQueryString ... NoneType` error means the glvnd dispatch layer is missing; install `libegl1 libgl1 libglvnd0`.
- **Evaluating inside a container.** The recommended `docker compose` setup cannot run on an instance that is itself a container. The evaluation is split into two processes instead — a policy server (websocket, port 8000) and the LIBERO client in its own Python 3.8 virtual environment. See `scripts/full_eval.sh`.
- **Memory.** π₀.₅ LoRA runs out of memory at batch size 32 on a 24 GB card; batch size 16 uses 22.1 GB.
- **Checkpoint disk usage.** Each save writes 5.9 GB of params plus 3 GB of train state, and with a small `save_interval` two copies briefly coexist. Insufficient free space aborts training with `RESOURCE_EXHAUSTED` during the save. For short runs, keep only the final step.
- **First LIBERO run.** LIBERO asks interactively for a dataset path; answer once and it is written to `~/.libero`.

---

## Weights

The three fine-tuned models are provided as LoRA-only npz files (177 MB each) for 100%, 50%, and 25% of the data. Use `tools/merge_lora.py` to merge them onto the official `pi05_libero` params.

## Repository layout

```
patches/     openpi patch, lerobot fix notes, version pins
scripts/     shell pipelines for setup, evaluation, training, ablations
ablations/   rephrasing and perturbation evaluation clients, episode subsets
tools/       LoRA extraction and merging
results/     loss data and figures, evaluation logs, norm stats, sample videos
```

`results/norm_stats/` holds the normalization statistics computed here, which are required for evaluation and for reusing the weights.

## Acknowledgements

[openpi](https://github.com/Physical-Intelligence/openpi) ·
[LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO) ·
[LeRobot](https://github.com/huggingface/lerobot)
