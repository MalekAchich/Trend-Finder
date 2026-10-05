"""Weight suggestion (05-scoring): logistic regression of 👍/👎 on the four sub-scores → normalized weights."""
from __future__ import annotations

import numpy as np

FEATURES = ("fit", "feasibility", "momentum", "freshness")
MIN_RATED = 30


def suggest_weights(X: np.ndarray, y: np.ndarray, l2: float = 0.1, steps: int = 3000,
                    lr: float = 0.1) -> dict[str, float] | None:
    """X: (n, 4) sub-scores 0–100, y: 1 = 👍, 0 = 👎. None below MIN_RATED or without both classes."""
    if len(X) < MIN_RATED or len(set(np.asarray(y).tolist())) < 2:
        return None
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    w = np.zeros(Z.shape[1])
    b = 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        grad = Z.T @ (p - y) / len(y) + l2 * w
        w -= lr * grad
        b -= lr * float(np.mean(p - y))
    positive = np.clip(w, 0, None)
    if positive.sum() <= 0:
        return None
    norm = positive / positive.sum()
    out = {name: round(float(v), 3) for name, v in zip(FEATURES, norm, strict=True)}
    top = max(out, key=out.get)
    out[top] = round(out[top] + 1.0 - sum(out.values()), 3)  # keep the rounded weights summing to exactly 1
    return out
