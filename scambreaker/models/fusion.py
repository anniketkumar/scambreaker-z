"""Stacker and graduated-friction policy.

A logistic stacker combines the specialist models' log-odds. Thresholds for NUDGE / HOLD /
BLOCK are set on validation days so that at most BUDGET_* of legitimate digital retail
transfers receive that level of friction.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .. import config

EPS = 1e-6


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, np.float64), EPS, 1 - EPS)
    return np.log(p / (1 - p))


class Stacker:
    def __init__(self, inputs, coef, intercept, thresholds=None):
        self.inputs = list(inputs)
        self.coef = np.asarray(coef, np.float64)
        self.intercept = float(intercept)
        self.thresholds = thresholds  # dict: nudge, hold, block

    @staticmethod
    def _X(P: dict[str, np.ndarray], inputs) -> np.ndarray:
        return np.column_stack([logit(P[k]) for k in inputs])

    @classmethod
    def fit(cls, P: dict[str, np.ndarray], y: np.ndarray, inputs=("A", "B", "C")) -> Stacker:
        from sklearn.linear_model import LogisticRegression  # noqa: PLC0415

        lr = LogisticRegression(C=1.0, max_iter=2000)
        lr.fit(cls._X(P, inputs), y)
        return cls(inputs, lr.coef_[0], lr.intercept_[0])

    def predict(self, P: dict[str, np.ndarray]) -> np.ndarray:
        z = self._X(P, self.inputs) @ self.coef + self.intercept
        return 1.0 / (1.0 + np.exp(-z))

    def set_thresholds(self, legit_scores: np.ndarray) -> dict:
        q = lambda b: float(np.quantile(legit_scores, 1.0 - b))  # noqa: E731
        t_n, t_h, t_b = q(config.BUDGET_NUDGE), q(config.BUDGET_HOLD), q(config.BUDGET_BLOCK)
        t_h = max(t_h, t_n)
        t_b = max(t_b, t_h)
        self.thresholds = {"nudge": t_n, "hold": t_h, "block": t_b}
        return self.thresholds

    def decide(self, p: np.ndarray) -> np.ndarray:
        th = self.thresholds
        d = np.zeros(len(p), dtype=np.int8)
        d[p >= th["nudge"]] = 1
        d[p >= th["hold"]] = 2
        d[p >= th["block"]] = 3
        return d

    def to_dict(self) -> dict:
        return {"inputs": self.inputs, "coef": self.coef.tolist(), "intercept": self.intercept,
                "thresholds": self.thresholds}

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def from_json(cls, path: Path) -> Stacker:
        d = json.loads(path.read_text())
        return cls(d["inputs"], d["coef"], d["intercept"], d.get("thresholds"))
