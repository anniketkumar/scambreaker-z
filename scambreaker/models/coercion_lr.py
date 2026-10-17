"""Model C - coercion signals from the app session. Logistic regression, kept deliberately
simple so every contribution can be shown to the customer and the analyst."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FEATURES = ("on_call", "unknown_caller", "remote_access", "payee_added_log", "session_log",
            "hesitation", "failed", "new_device")

LABELS = {
    "on_call": "on a phone call while paying",
    "unknown_caller": "call is from an unknown number",
    "remote_access": "remote-access app active",
    "payee_added_log": "payee was added only minutes ago",
    "session_log": "unusually long session",
    "hesitation": "hesitant, stop-start input",
    "failed": "repeated failed attempts",
    "new_device": "new device",
}


def matrix(rows: np.ndarray) -> np.ndarray:
    """Build the model-C matrix from structured event rows (see data.events.EVENT_DTYPE)."""
    X = np.empty((len(rows), len(FEATURES)), dtype=np.float32)
    X[:, 0] = rows["on_call"]
    X[:, 1] = rows["unknown_caller"]
    X[:, 2] = rows["remote_access"]
    # log of minutes since payee was added, inverted so "recent" is a high value
    X[:, 3] = -np.log1p(np.minimum(rows["payee_added_min"].astype(np.float64), 1e5))
    X[:, 4] = np.log1p(rows["session_s"])
    X[:, 5] = rows["hesitation"]
    X[:, 6] = rows["failed"]
    X[:, 7] = rows["new_device"]
    return X


class CoercionModel:
    def __init__(self, mean, scale, coef, intercept):
        self.mean = np.asarray(mean, np.float64)
        self.scale = np.asarray(scale, np.float64)
        self.coef = np.asarray(coef, np.float64)
        self.intercept = float(intercept)

    @classmethod
    def fit(cls, X: np.ndarray, y: np.ndarray) -> CoercionModel:
        from sklearn.linear_model import LogisticRegression  # noqa: PLC0415

        mean = X.mean(0)
        scale = X.std(0)
        scale[scale < 1e-9] = 1.0
        lr = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)
        lr.fit((X - mean) / scale, y)
        return cls(mean, scale, lr.coef_[0], lr.intercept_[0])

    def logit(self, X: np.ndarray) -> np.ndarray:
        return ((X - self.mean) / self.scale) @ self.coef + self.intercept

    def predict(self, X: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.logit(X)))

    def contributions(self, x: np.ndarray) -> dict[str, float]:
        z = (x - self.mean) / self.scale
        return {f: float(c) for f, c in zip(FEATURES, z * self.coef)}

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps({"features": FEATURES, "mean": self.mean.tolist(),
                                    "scale": self.scale.tolist(), "coef": self.coef.tolist(),
                                    "intercept": self.intercept}, indent=2))

    @classmethod
    def from_json(cls, path: Path) -> CoercionModel:
        d = json.loads(path.read_text())
        return cls(d["mean"], d["scale"], d["coef"], d["intercept"])
