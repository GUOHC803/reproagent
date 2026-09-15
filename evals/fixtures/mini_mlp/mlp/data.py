from __future__ import annotations

import numpy as np


def make_moons(n: int, noise: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Two interleaving half circles (a numpy re-implementation of sklearn's make_moons)."""
    n_out = n // 2
    n_in = n - n_out
    t_out = np.linspace(0, np.pi, n_out)
    t_in = np.linspace(0, np.pi, n_in)
    outer = np.stack([np.cos(t_out), np.sin(t_out)], axis=1)
    inner = np.stack([1 - np.cos(t_in), 1 - np.sin(t_in) - 0.5], axis=1)
    X = np.concatenate([outer, inner]) + rng.normal(0, noise, size=(n, 2))
    y = np.concatenate([np.zeros(n_out), np.ones(n_in)]).astype(int)
    perm = rng.permutation(n)
    return X[perm], y[perm]
