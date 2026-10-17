"""End-to-end load test of the running stack: step the replay rate up and record the
throughput the scorer sustains and the bus->decision latency it delivers.

    docker compose exec web python -m scambreaker.bench.e2e_bench --steps 500,1000,2000,4000,0
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .. import config
from ..serve.bus import make_bus


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="500,1000,2000,4000,0", help="target tx/s per step (0 = unthrottled)")
    ap.add_argument("--hold", type=float, default=35.0, help="seconds per step")
    ap.add_argument("--settle", type=float, default=8.0)
    ap.add_argument("--out", type=Path, default=config.RESULTS_DIR / "e2e_bench.json")
    args = ap.parse_args()

    bus = make_bus()
    original = bus.get("replay:control") or {"tps": 1500, "paused": False}
    rows = []
    try:
        for target in [float(s) for s in args.steps.split(",")]:
            bus.set("replay:control", {"tps": target, "paused": False})
            time.sleep(args.settle)
            samples = []
            t_end = time.time() + args.hold
            while time.time() < t_end:
                m = bus.get("metrics") or {}
                if m:
                    samples.append((m["tps"], m["p50_ms"], m["p99_ms"], m.get("batch_ms", 0.0)))
                time.sleep(1.0)
            a = np.array(samples)
            row = {"target_tps": target or "max", "achieved_tps": round(float(np.median(a[:, 0])), 1),
                   "p50_ms": round(float(np.median(a[:, 1])), 2), "p99_ms": round(float(np.median(a[:, 2])), 2),
                   "batch_ms": round(float(np.median(a[:, 3])), 2)}
            rows.append(row)
            print(row, flush=True)
    finally:
        bus.set("replay:control", original)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"steps": rows, "hold_s": args.hold}, indent=2))
    print("saved", args.out)


if __name__ == "__main__":
    main()
