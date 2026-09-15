from __future__ import annotations

import numpy as np


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def bce_loss(p: np.ndarray, y: np.ndarray, eps: float = 1e-7) -> float:
    """Binary cross-entropy averaged over samples."""
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


class MLP:
    """2 -> hidden (tanh) -> 1 (sigmoid), trained with full-batch gradient descent."""

    def __init__(self, in_dim: int, hidden: int, rng: np.random.Generator) -> None:
        self.W1 = rng.normal(0, 0.5, size=(in_dim, hidden))
        self.b1 = np.zeros(hidden)
        self.W2 = rng.normal(0, 0.5, size=(hidden, 1))
        self.b2 = np.zeros(1)

    def forward(self, X: np.ndarray) -> np.ndarray:
        self.h = np.tanh(X @ self.W1 + self.b1)
        self.p = sigmoid(self.h @ self.W2 + self.b2).ravel()
        return self.p

    def backward(self, X: np.ndarray, y: np.ndarray, lr: float) -> None:
        n = len(X)
        dz2 = (self.p - y)[:, None] / n
        dW2 = self.h.T @ dz2
        db2 = dz2.sum(axis=0)
        dh = dz2 @ self.W2.T
        dz1 = dh * (1 - self.h**2)
        dW1 = X.T @ dz1
        db1 = dz1.sum(axis=0)
        self.W1 -= lr * dW1
        self.b1 -= lr * db1
        self.W2 -= lr * dW2
        self.b2 -= lr * db2
