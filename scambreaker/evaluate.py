"""Evaluation helpers. Everything here is computed on held-out test days only."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

from . import config


def binary_metrics(y: np.ndarray, p: np.ndarray, fpr: float = 0.01) -> dict:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, np.float64)
    out = {"n": int(len(y)), "positives": int(y.sum()), "base_rate": float(y.mean()) if len(y) else 0.0}
    if 0 < y.sum() < len(y):
        thr = float(np.quantile(p[y == 0], 1.0 - fpr))
        out.update({
            "auprc": float(average_precision_score(y, p)),
            "auroc": float(roc_auc_score(y, p)),
            "recall_at_1pct_fpr": float((p[y == 1] >= thr).mean()),
            "precision_at_1pct_fpr": float(y[p >= thr].mean()) if (p >= thr).any() else 0.0,
        })
    return out


def pr_curve(y: np.ndarray, p: np.ndarray, n: int = 200) -> dict:
    prec, rec, _ = precision_recall_curve(y, p)
    idx = np.linspace(0, len(prec) - 1, min(n, len(prec))).astype(int)
    return {"precision": prec[idx].round(4).tolist(), "recall": rec[idx].round(4).tolist()}


def policy_metrics(dec: np.ndarray, rows: np.ndarray) -> dict:
    """Friction on legitimate transfers vs interception of scam payments."""
    is_scam = rows["is_scam"] == 1
    legit = ~is_scam & (rows["is_laund"] == 0)
    hardneg = rows["kind"] == 2
    amt = rows["amt"].astype(np.float64)

    def rates(mask):
        n = int(mask.sum())
        if n == 0:
            return {"n": 0}
        d = dec[mask]
        return {"n": n, "nudge_or_more": float((d >= 1).mean()), "hold_or_more": float((d >= 2).mean()),
                "block": float((d == 3).mean())}

    out = {"legit": rates(legit), "hard_negatives": rates(legit & hardneg)}
    if is_scam.any():
        d = dec[is_scam]
        a = amt[is_scam]
        out["scam_payments"] = {
            "n": int(is_scam.sum()),
            "nudge_or_more": float((d >= 1).mean()),
            "hold_or_more": float((d >= 2).mean()),
            "block": float((d == 3).mean()),
            "usd_share_held": float(a[d >= 2].sum() / a.sum()),
        }
        typ = rows["typ"][is_scam]
        out["by_typology_hold_or_more"] = {
            config.TYPOLOGIES[k]: float((d[typ == k] >= 2).mean())
            for k in range(len(config.TYPOLOGIES)) if (typ == k).any()
        }
        out["episodes"] = episode_metrics(d, a, rows["ep"][is_scam], rows["t"][is_scam])
    laund = rows["is_laund"] == 1
    if laund.any():
        out["mule_transfers_in_scope"] = {"n": int(laund.sum()),
                                          "hold_or_more": float((dec[laund] >= 2).mean())}
    return out


def episode_metrics(dec: np.ndarray, amt: np.ndarray, ep: np.ndarray, t: np.ndarray) -> dict:
    df = pd.DataFrame({"ep": ep, "t": t, "amt": amt, "dec": dec}).sort_values(["ep", "t"], kind="stable")
    n_pay, first_hold, total, after_first = [], [], [], []
    for _, g in df.groupby("ep", sort=False):
        d = g["dec"].to_numpy()
        a = g["amt"].to_numpy()
        held = np.flatnonzero(d >= 2)
        n_pay.append(len(d))
        total.append(a.sum())
        if len(held):
            first_hold.append(int(held[0]))
            after_first.append(a[held[0]:].sum())
        else:
            first_hold.append(-1)
            after_first.append(0.0)
    first_hold = np.asarray(first_hold)
    caught = first_hold >= 0
    return {
        "n": int(len(n_pay)),
        "intervened_share": float(caught.mean()),
        "median_payments_before_first_hold": float(np.median(first_hold[caught])) if caught.any() else None,
        # Upper bound: assumes a hold stops the rest of the episode.
        "usd_share_at_or_after_first_hold": float(np.sum(after_first) / np.sum(total)),
    }
