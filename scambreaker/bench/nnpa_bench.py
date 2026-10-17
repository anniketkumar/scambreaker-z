"""Model B (LSTM) inference: default placement (Telum NNPA via ibm-zdnn-plugin) vs CPU.

Same trained model, same inputs, across batch sizes.
    python -m scambreaker.bench.nnpa_bench --models /secure/work/models --out /secure/work/results/nnpa_bench.json
    python -m scambreaker.bench.nnpa_bench --mode default --label no-plugin   # run in a container without the plugin
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np

from .. import config
from ..features.engine import N_SEQ
from ..models import behavior_lstm


def bench(model, x: np.ndarray, reps: int) -> dict:
    model.predict(x, normalized=True)  # trace + warm up
    model.predict(x, normalized=True)
    runs = []
    for _ in range(reps):
        t0 = time.perf_counter()
        model.predict(x, normalized=True)
        runs.append((time.perf_counter() - t0) * 1000)
    med = float(np.median(runs))
    return {"batch": len(x), "median_ms": round(med, 4), "p95_ms": round(float(np.percentile(runs, 95)), 4),
            "us_per_tx": round(med * 1000 / len(x), 3), "tx_per_s": round(len(x) / (med / 1000), 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", type=Path, default=config.MODEL_DIR)
    ap.add_argument("--out", type=Path, default=config.RESULTS_DIR / "nnpa_bench.json")
    ap.add_argument("--batches", default="1,8,32,128,512,2048")
    ap.add_argument("--reps", type=int, default=40)
    ap.add_argument("--mode", choices=["both", "default", "cpu"], default="both")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    rng = np.random.default_rng(0)
    models = {}
    if args.mode in ("both", "default"):
        models[args.label or "default"] = behavior_lstm.BehaviorModel(args.models, device="auto")
    if args.mode in ("both", "cpu"):
        models["cpu"] = behavior_lstm.BehaviorModel(args.models, device="cpu")
    tf = next(iter(models.values())).tf
    out = {"machine": platform.machine(), "tf_version": tf.__version__,
           "tf_devices": [f"{d.device_type}:{d.name}" for d in tf.config.list_physical_devices()],
           "results": {}}
    for b in [int(v) for v in args.batches.split(",")]:
        x = rng.normal(size=(b, config.SEQ_LEN, N_SEQ)).astype(np.float32)
        x[:, :, 0] = 1.0
        for name, m in models.items():
            r = bench(m, x, args.reps)
            out["results"].setdefault(name, []).append(r)
            print(f"{name:>10} batch {b:>5}: {r['median_ms']:9.3f} ms  {r['us_per_tx']:8.2f} us/tx  {r['tx_per_s']:>12,.0f} tx/s",
                  flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(args.out.read_text()) if args.out.exists() and args.mode != "both" else {}
    if prev:
        prev.setdefault("results", {}).update(out["results"])
        out = {**prev, "results": prev["results"]}
    args.out.write_text(json.dumps(out, indent=2))
    print("saved", args.out)


if __name__ == "__main__":
    main()
