"""Train a tiny two-layer MLP on a synthetic two-moons dataset (numpy only).

Usage:
    python train.py --epochs 50 --lr 0.1 --hidden 16 --seed 0 --out metrics.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml

from mlp.data import make_moons
from mlp.model import MLP, bce_loss


def load_defaults() -> dict:
    cfg_path = Path(__file__).parent / "config.yaml"
    with cfg_path.open() as f:
        return yaml.safe_load(f)


def main() -> None:
    d = load_defaults()
    ap = argparse.ArgumentParser(description="Train a tiny MLP.")
    ap.add_argument("--epochs", type=int, default=d["epochs"])
    ap.add_argument("--lr", type=float, default=d["lr"])
    ap.add_argument("--hidden", type=int, default=d["hidden"])
    ap.add_argument("--seed", type=int, default=d["seed"])
    ap.add_argument("--n-samples", type=int, default=d["n_samples"])
    ap.add_argument("--noise", type=float, default=d["noise"])
    ap.add_argument("--out", type=str, default=None, help="Write metrics JSON here.")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    X, y = make_moons(args.n_samples, noise=args.noise, rng=rng)
    n_train = int(0.8 * len(X))
    Xtr, ytr, Xte, yte = X[:n_train], y[:n_train], X[n_train:], y[n_train:]

    model = MLP(in_dim=2, hidden=args.hidden, rng=rng)
    history = []
    for epoch in range(1, args.epochs + 1):
        p = model.forward(Xtr)
        loss = bce_loss(p, ytr)
        model.backward(Xtr, ytr, lr=args.lr)
        history.append(loss)
        if epoch % max(1, args.epochs // 10) == 0 or epoch == args.epochs:
            print(f"epoch {epoch:4d}  loss {loss:.4f}")

    p_te = model.forward(Xte)
    acc = float(((p_te > 0.5).astype(int) == yte).mean())
    metrics = {
        "final_loss": float(history[-1]),
        "best_loss": float(min(history)),
        "test_accuracy": acc,
        "epochs": args.epochs,
        "lr": args.lr,
        "hidden": args.hidden,
        "seed": args.seed,
    }
    print(json.dumps(metrics))
    if args.out:
        Path(args.out).write_text(json.dumps(metrics, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
