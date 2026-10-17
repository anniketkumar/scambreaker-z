"""Model D - which scam typology a flagged payment most resembles (drives the wording of
the customer warning and the analyst playbook)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import xgboost as xgb

from .. import config
from ..features.engine import SEQ_FEATURES

_CUR = {name: i for i, name in enumerate(SEQ_FEATURES)}
STEP_COLS = ("amt_log", "amt_ratio_log", "new_payee", "since_prev_log",  # no hour of day (simulation artifact)
             "fmt_ACH", "fmt_Bitcoin", "fmt_Wire", "cross_bank", "cross_cur", "same_payee_prev",
             "escalation_log")


def matrix(seq: np.ndarray, coercion_X: np.ndarray) -> np.ndarray:
    cur = seq[:, -1, :]
    step = cur[:, [_CUR[c] for c in STEP_COLS]]
    n_prev = (seq[:, :-1, 0] > 0.5).sum(1, keepdims=True)
    same_prev = seq[:, :-1, _CUR["same_payee_prev"]].sum(1, keepdims=True)
    return np.hstack([step, n_prev, same_prev, coercion_X]).astype(np.float32)


def train(X: np.ndarray, y: np.ndarray) -> xgb.Booster:
    params = dict(objective="multi:softprob", num_class=len(config.TYPOLOGIES), max_depth=5,
                  eta=0.1, subsample=0.9, colsample_bytree=0.9, nthread=os.cpu_count() or 1, seed=7)
    return xgb.train(params, xgb.DMatrix(X, label=y), 200)


def load(path: Path) -> xgb.Booster:
    b = xgb.Booster()
    b.load_model(str(path))
    return b


def predict(booster: xgb.Booster, X: np.ndarray) -> np.ndarray:
    return booster.inplace_predict(np.ascontiguousarray(X, dtype=np.float32))
