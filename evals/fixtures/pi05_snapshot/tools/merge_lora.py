"""Rebuild a full fine-tuned checkpoint from the official pi05_libero params + a LoRA npz.

The npz files under this repo's release (lora_only_*.npz) contain every tensor whose
path includes "lora", exactly as extracted by tools/extract_lora.py. All non-LoRA
tensors in our fine-tuned checkpoints are bit-identical to the warm-start checkpoint
(they were frozen via the config's freeze_filter), so overlaying the LoRA tensors on
the official params reproduces the fine-tuned model.

Note: this script mirrors the APIs used during training but was written after the
rented GPU machine expired, so it has not been re-executed end-to-end. If orbax
versions drift, adapt the save call accordingly.

Usage (inside the openpi repo):
    JAX_PLATFORMS=cpu uv run python tools/merge_lora.py \
        ~/.cache/openpi/openpi-assets/checkpoints/pi05_libero/params \
        lora_only_lora_run1_bs16.npz  ./merged_checkpoint/params
"""
import sys

import jax
import numpy as np
import orbax.checkpoint as ocp

from openpi.models import model as _model


def main(base_params_path: str, lora_npz_path: str, out_path: str) -> None:
    params = _model.restore_params(base_params_path, restore_type=np.ndarray)
    lora = dict(np.load(lora_npz_path))

    applied = 0

    def overlay(key_path, value):
        nonlocal applied
        key = "/".join(str(k.key) if hasattr(k, "key") else str(k) for k in key_path)
        if key in lora:
            applied += 1
            return lora[key]
        return value

    merged = jax.tree_util.tree_map_with_path(overlay, params)
    if applied != len(lora):
        raise SystemExit(f"only {applied}/{len(lora)} LoRA tensors matched — key paths drifted?")

    ocp.StandardCheckpointer().save(out_path, merged)
    print(f"merged checkpoint written to {out_path} ({applied} LoRA tensors applied)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
