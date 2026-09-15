"""Extract LoRA-only weights from a full openpi orbax checkpoint.

Full pi05 params are ~5.9 GB (3.4B params), but the LoRA adapters trained here
are only ~50M params (1.5%). This script pulls just the LoRA tensors into a
compressed npz so checkpoints can be archived / transferred cheaply.

Usage (inside the openpi repo, CPU is enough):
    JAX_PLATFORMS=cpu uv run python tools/extract_lora.py <checkpoint>/params <out.npz>
"""
import sys

import jax
import numpy as np

from openpi.models import model as _model


def main(params_path: str, out_path: str) -> None:
    params = _model.restore_params(params_path, restore_type=np.ndarray)
    flat = jax.tree_util.tree_flatten_with_path(params)[0]
    out, total, lora = {}, 0, 0
    for key_path, value in flat:
        key = "/".join(str(k.key) if hasattr(k, "key") else str(k) for k in key_path)
        total += value.size
        if "lora" in key.lower():
            out[key] = value
            lora += value.size
    np.savez_compressed(out_path, **out)
    print(f"total params: {total:,}  lora params: {lora:,}  tensors: {len(out)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
