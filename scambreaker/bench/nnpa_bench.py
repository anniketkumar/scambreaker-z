"""Model B (LSTM) inference with and without the IBM zDNN plugin (Telum NNPA).

The ibm-zdnn-plugin is a kernel library (tensorflow/core/kernels/_z_ops.so) that TensorFlow
loads at import; it swaps in zDNN-backed kernels behind the normal CPU device, so pinning to
/CPU:0 does not switch it off. The fair A/B is therefore two runs of the same model on the
same inputs: one normal, one in a container where the plugin directory is hidden.
    python -m scambreaker.bench.nnpa_bench --mode default --label nnpa
    (container with --tmpfs .../tensorflow/core/kernels)  ... --mode default --label cpu
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
    maps = Path("/proc/self/maps").read_text() if Path("/proc/self/maps").exists() else ""
    libs = sorted({ln.split()[-1].rsplit("/", 1)[-1] for ln in maps.splitlines() if "zdnn" in ln.lower() or "_z_ops" in ln})
    out = {"machine": platform.machine(), "tf_version": tf.__version__,
           "tf_devices": [f"{d.device_type}:{d.name}" for d in tf.config.list_physical_devices()],
           "zdnn_libs": {name: libs for name in models}, "results": {}}
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
        prev.setdefault("zdnn_libs", {}).update(out["zdnn_libs"])
        out = {**out, "results": prev["results"], "zdnn_libs": prev["zdnn_libs"]}
    args.out.write_text(json.dumps(out, indent=2))
    print("saved", args.out)


if __name__ == "__main__":
    main()
