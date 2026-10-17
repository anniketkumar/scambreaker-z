"""Model A - payee / mule risk. XGBoost on point-in-time graph and velocity features,
trained on the real IBM AMLworld laundering labels."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import xgboost as xgb

from ..features.engine import A_FEATURES

PARAMS = dict(objective="binary:logistic", eval_metric="aucpr", tree_method="hist", max_depth=7,
              eta=0.08, subsample=0.8, colsample_bytree=0.8, min_child_weight=5, reg_lambda=1.0)


def train(Xtr: np.ndarray, ytr: np.ndarray, Xva: np.ndarray, yva: np.ndarray,
          rounds: int = 600, nthread: int | None = None) -> xgb.Booster:
    spw = float((ytr == 0).sum()) / max(1.0, float((ytr == 1).sum()))
    params = {**PARAMS, "scale_pos_weight": spw, "nthread": nthread or os.cpu_count() or 1, "seed": 7}
    dtr = xgb.DMatrix(Xtr, label=ytr, feature_names=list(A_FEATURES))
    dva = xgb.DMatrix(Xva, label=yva, feature_names=list(A_FEATURES))
    booster = xgb.train(params, dtr, rounds, evals=[(dva, "val")], early_stopping_rounds=50,
                        verbose_eval=100)
    return booster[: booster.best_iteration + 1]


def save(booster: xgb.Booster, path: Path) -> None:
    booster.save_model(str(path))


def load(path: Path) -> xgb.Booster:
    b = xgb.Booster()
    b.load_model(str(path))
    return b


def predict(booster: xgb.Booster, X: np.ndarray) -> np.ndarray:
    return booster.inplace_predict(np.ascontiguousarray(X, dtype=np.float32))


def contributions(booster: xgb.Booster, X: np.ndarray) -> np.ndarray:
    """Per-feature TreeSHAP contributions (log-odds); last column is the bias."""
    dm = xgb.DMatrix(np.ascontiguousarray(X, dtype=np.float32), feature_names=list(A_FEATURES))
    return booster.predict(dm, pred_contribs=True)
