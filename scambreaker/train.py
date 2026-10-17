"""Train and evaluate all ScamBreaker-Z models on the prepared event stream.

    python -I -m scambreaker.train --data <data_dir> --models <model_dir>

One time-ordered pass of the feature engine materialises point-in-time features. Models are
trained on train days, the stacker and thresholds are fitted on validation days, and every
reported number comes from the held-out test days.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import time
from pathlib import Path

import numpy as np

from . import config
from .data.events import KIND_HARDNEG, KIND_ORIG, KIND_PERSONA, load_stream
from .evaluate import binary_metrics, policy_metrics, pr_curve
from .features.engine import N_A, N_SEQ, FeatureEngine
from .models import behavior_lstm, coercion_lr, mule_xgb, typology
from .models.fusion import Stacker

CHUNK = 500_000


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def featurize(ev: np.ndarray, need_a: np.ndarray, need_seq: np.ndarray):
    """Single time-ordered engine pass. Returns compact feature arrays for selected rows."""
    n = len(ev)
    pos_a = np.cumsum(need_a) - 1
    pos_s = np.cumsum(need_seq) - 1
    FA = np.zeros((int(need_a.sum()), N_A), np.float32)
    SEQ = np.zeros((int(need_seq.sum()), config.SEQ_LEN, N_SEQ), np.float32)
    eng = FeatureEngine()
    t0 = time.time()
    for lo in range(0, n, CHUNK):
        hi = min(n, lo + CHUNK)
        c = ev[lo:hi]
        cols = [c[k].tolist() for k in ("t", "src", "dst", "amt", "fmt", "same_bank", "cross_cur", "hour")]
        wa = need_a[lo:hi].tolist()
        ws = need_seq[lo:hi].tolist()
        pa = pos_a[lo:hi].tolist()
        ps = pos_s[lo:hi].tolist()
        process = eng.process
        for j, (t, s, d, a, f, sb, cc, h) in enumerate(zip(*cols)):
            fa, seq, _ = process(t, s, d, a, f, sb, cc, h, want_a=wa[j], want_seq=ws[j])
            if fa is not None:
                FA[pa[j]] = fa
            if seq is not None:
                SEQ[ps[j]] = seq
        rate = hi / max(1e-9, time.time() - t0)
        log(f"  engine {hi:,}/{n:,} events ({rate:,.0f} ev/s, {len(eng.acc):,} accounts)")
    return FA, SEQ, pos_a, pos_s


def platform_info() -> dict:
    info = {"machine": platform.machine(), "python": platform.python_version(),
            "cpus": os.cpu_count(), "nnpa_cpu_flag": False}
    try:
        txt = Path("/proc/cpuinfo").read_text()
        info["nnpa_cpu_flag"] = " nnpa" in txt or "nnpa " in txt
        for line in txt.splitlines():
            if line.startswith("machine") or "machine =" in line:
                info["cpuinfo_machine"] = line.strip()[:160]
                break
    except OSError:
        pass
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=config.DATA_DIR)
    ap.add_argument("--models", type=Path, default=config.MODEL_DIR)
    ap.add_argument("--results", type=Path, default=config.RESULTS_DIR)
    ap.add_argument("--legit-cap", type=int, default=400_000)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--holdout-typology", default="job_task",
                    help="also train B/C/stacker without this typology to test generalisation ('' to skip)")
    args = ap.parse_args()
    args.models.mkdir(parents=True, exist_ok=True)
    args.results.mkdir(parents=True, exist_ok=True)
    T0 = time.time()
    timings: dict[str, float] = {}

    ev, keys, retail, meta = load_stream(args.data, mmap=False)
    n = len(ev)
    day, kind = ev["day"], ev["kind"]
    split = np.full(n, -1, np.int8)
    for k, (lo, hi) in enumerate((config.TRAIN_DAYS, config.VAL_DAYS, config.TEST_DAYS)):
        split[(day >= lo) & (day <= hi)] = k
    rng = np.random.default_rng(config.SEED)
    h = rng.random(n)
    orig = kind == KIND_ORIG
    laund = ev["is_laund"] == 1
    sel_a = orig & (((split == 0) & (laund | (h < 0.10))) | ((split == 1) & (laund | (h < 0.20))) | (split == 2))
    pool = (ev["pool"] == 1) & (kind != KIND_PERSONA)
    legit_pool_train = pool & (split == 0) & (ev["is_scam"] == 0) & orig
    cap_rate = min(1.0, args.legit_cap / max(1, int(legit_pool_train.sum())))
    sel_b = pool & ((split >= 1) | (ev["is_scam"] == 1) | (kind == KIND_HARDNEG) | (h < cap_rate))
    need_a = sel_a | sel_b
    log(f"{n:,} events | model-A rows {int(sel_a.sum()):,} | policy rows {int(sel_b.sum()):,} "
        f"(legit train sample rate {cap_rate:.3f})")

    t = time.time()
    FA, SEQ, pos_a, pos_s = featurize(ev, need_a, sel_b)
    timings["feature_engine_s"] = time.time() - t

    # ------------------------------------------------------------- Model A
    def a_rows(mask):
        idx = np.flatnonzero(mask)
        return FA[pos_a[idx]], laund[idx].astype(np.int8)

    Xtr, ytr = a_rows(sel_a & (split == 0))
    Xva, yva = a_rows(sel_a & (split == 1))
    Xte, yte = a_rows(sel_a & (split == 2))
    log(f"Model A: train {len(ytr):,} ({int(ytr.sum()):,} laundering) | val {len(yva):,} | test {len(yte):,}")
    t = time.time()
    booster = mule_xgb.train(Xtr, ytr, Xva, yva)
    timings["model_a_train_s"] = time.time() - t
    mule_xgb.save(booster, args.models / "model_a.json")
    pa_te = mule_xgb.predict(booster, Xte)
    pa_va = mule_xgb.predict(booster, Xva)
    aml_threshold = float(np.quantile(pa_va[yva == 0], 0.999))  # back-office AML flag at 0.1% FPR
    metrics_a = binary_metrics(yte, pa_te)
    metrics_a["aml_flag_threshold"] = aml_threshold
    metrics_a["aml_flag_recall_test"] = float((pa_te[yte == 1] >= aml_threshold).mean()) if yte.any() else None
    metrics_a["pr_curve"] = pr_curve(yte, pa_te)
    imp = booster.get_score(importance_type="gain")
    metrics_a["top_features"] = sorted(imp.items(), key=lambda kv: -kv[1])[:12]
    log(f"Model A test: AUPRC {metrics_a.get('auprc', float('nan')):.4f} "
        f"AUROC {metrics_a.get('auroc', float('nan')):.4f} base rate {metrics_a['base_rate']:.5f}")

    # --------------------------------------------------- policy-row datasets
    idx_b = np.flatnonzero(sel_b)
    rows_b = ev[idx_b]
    split_b = split[idx_b]
    y_b = rows_b["is_scam"].astype(np.int8)
    XA_b = FA[pos_a[idx_b]]
    SEQ_b = SEQ[pos_s[idx_b]]
    XC_b = coercion_lr.matrix(rows_b)
    P_A = mule_xgb.predict(booster, XA_b)
    tr, va, te = split_b == 0, split_b == 1, split_b == 2
    log(f"policy rows: train {int(tr.sum()):,} ({int(y_b[tr].sum()):,} scam) | val {int(va.sum()):,} "
        f"({int(y_b[va].sum()):,}) | test {int(te.sum()):,} ({int(y_b[te].sum()):,})")

    # ------------------------------------------------------------- Model B
    use_tf = behavior_lstm.tf_available()
    t = time.time()
    if use_tf:
        behavior_lstm.train(SEQ_b[tr], y_b[tr], SEQ_b[va], y_b[va], args.models, epochs=args.epochs)
        model_b = behavior_lstm.BehaviorModel(args.models)
        P_B = model_b.predict(SEQ_b)
    else:
        raise SystemExit("TensorFlow is required to train model B")
    timings["model_b_train_s"] = time.time() - t
    metrics_b = binary_metrics(y_b[te], P_B[te])
    log(f"Model B test: AUPRC {metrics_b.get('auprc', float('nan')):.4f}")

    # ------------------------------------------------------------- Model C
    model_c = coercion_lr.CoercionModel.fit(XC_b[tr], y_b[tr])
    model_c.to_json(args.models / "model_c.json")
    P_C = model_c.predict(XC_b)
    metrics_c = binary_metrics(y_b[te], P_C[te])
    log(f"Model C test: AUPRC {metrics_c.get('auprc', float('nan')):.4f}")

    # ------------------------------------------------------------- Model D
    scam_tr = tr & (y_b == 1)
    scam_te = te & (y_b == 1)
    XD = typology.matrix(SEQ_b, XC_b)
    booster_d = typology.train(XD[scam_tr], rows_b["typ"][scam_tr])
    booster_d.save_model(str(args.models / "model_d.json"))
    pred_d = typology.predict(booster_d, XD[scam_te]).argmax(1)
    metrics_d = {"test_accuracy": float((pred_d == rows_b["typ"][scam_te]).mean()),
                 "n_test": int(scam_te.sum())}
    log(f"Model D typology accuracy {metrics_d['test_accuracy']:.3f}")

    # ------------------------------------------------- stacker + ablations
    P = {"A": P_A, "B": P_B, "C": P_C}
    legit_va = va & (y_b == 0) & (rows_b["is_laund"] == 0)
    subsets = [("A",), ("B",), ("C",), ("A", "B"), ("A", "C"), ("B", "C"), ("A", "B", "C")]
    ablation = {}
    stacker = None
    for sub in subsets:
        st = Stacker.fit({k: P[k][va] for k in sub}, y_b[va], inputs=sub)
        st.set_thresholds(st.predict({k: P[k][legit_va] for k in sub}))
        score_te = st.predict({k: P[k][te] for k in sub})
        pm = policy_metrics(st.decide(score_te), rows_b[te])
        ablation["+".join(sub)] = {
            "auprc": binary_metrics(y_b[te], score_te).get("auprc"),
            "scam_hold_or_more": pm.get("scam_payments", {}).get("hold_or_more"),
            "scam_usd_share_held": pm.get("scam_payments", {}).get("usd_share_held"),
            "legit_hold_or_more": pm["legit"].get("hold_or_more"),
            "legit_nudge_or_more": pm["legit"].get("nudge_or_more"),
        }
        if sub == ("A", "B", "C"):
            stacker = st
            score_full = score_te
            policy = pm
    stacker.to_json(args.models / "stacker.json")
    log(f"Stacker thresholds {stacker.thresholds}")
    log(f"Policy (test): {json.dumps(policy['scam_payments'])} | legit {json.dumps(policy['legit'])}")

    # -------------------------------------------- unseen-typology holdout
    holdout = {}
    if args.holdout_typology and use_tf:
        k = config.TYPOLOGIES.index(args.holdout_typology)
        keep = rows_b["typ"] != k
        tmp_dir = args.models / "holdout"
        behavior_lstm.train(SEQ_b[tr & keep], y_b[tr & keep], SEQ_b[va & keep], y_b[va & keep], tmp_dir,
                            epochs=args.epochs)
        pb_h = behavior_lstm.BehaviorModel(tmp_dir).predict(SEQ_b)
        pc_h = coercion_lr.CoercionModel.fit(XC_b[tr & keep], y_b[tr & keep]).predict(XC_b)
        Ph = {"A": P_A, "B": pb_h, "C": pc_h}
        st_h = Stacker.fit({x: Ph[x][va & keep] for x in "ABC"}, y_b[va & keep])
        st_h.set_thresholds(st_h.predict({x: Ph[x][legit_va & keep] for x in "ABC"}))
        d_h = st_h.decide(st_h.predict({x: Ph[x][te] for x in "ABC"}))
        is_k = rows_b["typ"][te] == k
        holdout = {"typology": args.holdout_typology,
                   "hold_or_more_when_unseen": float((d_h[is_k] >= 2).mean()),
                   "hold_or_more_when_seen": float((stacker.decide(score_full)[is_k] >= 2).mean()),
                   "n_test_payments": int(is_k.sum())}
        log(f"Holdout typology: {holdout}")

    timings["total_s"] = time.time() - T0
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "platform": platform_info(),
        "data": {k: meta.get(k) for k in ("day0", "n_raw", "n_days", "n_episodes", "episodes_by_split",
                                          "n_scam_payments", "n_hardneg", "n_retail_accounts")},
        "budgets": {"nudge": config.BUDGET_NUDGE, "hold": config.BUDGET_HOLD, "block": config.BUDGET_BLOCK},
        "model_a_test_real_labels": metrics_a,
        "model_b_test": metrics_b,
        "model_c_test": metrics_c,
        "model_d_test": metrics_d,
        "stacker": stacker.to_dict(),
        "policy_test": policy,
        "ablation_test": ablation,
        "holdout_typology": holdout,
        "timings": timings,
        "features_a": mule_xgb.NAMES,
    }
    (args.results / "metrics.json").write_text(json.dumps(report, indent=2, default=float))
    (args.models / "feature_names.json").write_text(json.dumps({"A": mule_xgb.NAMES}))
    serve_meta = {k: meta[k] for k in ("personas", "scenario_payees", "day0", "usd_rates", "periods")}
    serve_meta["aml_threshold"] = aml_threshold
    (args.models / "serve_meta.json").write_text(json.dumps(serve_meta, indent=2, default=float))
    log(f"done in {timings['total_s']:.0f}s -> {args.results / 'metrics.json'}")


if __name__ == "__main__":
    main()
