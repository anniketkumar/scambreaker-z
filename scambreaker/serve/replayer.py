"""Replays the held-out test days into `tx.in` at a controllable rate.

Rate and pause can be changed live through the `replay:control` key (set by the web UI).
    python -m scambreaker.serve.replayer --tps 1500 [--loop]
"""
from __future__ import annotations

import argparse
import socket
import time
from pathlib import Path

import numpy as np

from .. import config
from ..data.events import SESSION_FIELDS, load_stream
from .bus import make_bus

CHUNK = 100


def to_msgs(rows: np.ndarray, base_idx: int) -> list[dict]:
    now = time.time()
    cols = {k: rows[k].tolist() for k in rows.dtype.names}
    out = []
    for j in range(len(rows)):
        m = {"id": f"r{base_idx + j}", "t": cols["t"][j], "src": cols["src"][j], "dst": cols["dst"][j],
             "amt": cols["amt"][j], "fmt": cols["fmt"][j], "same_bank": cols["same_bank"][j],
             "cross_cur": cols["cross_cur"][j], "pool": cols["pool"][j], "ingest_ts": now,
             "truth": {"is_scam": cols["is_scam"][j], "is_laund": cols["is_laund"][j], "typ": cols["typ"][j],
                       "ep": cols["ep"][j]}}
        if cols["digital"][j]:
            m["session"] = {k: cols[k][j] for k in SESSION_FIELDS}
        out.append(m)
    return out


def run(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=config.DATA_DIR)
    ap.add_argument("--tps", type=float, default=1500.0, help="0 = as fast as possible")
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args(argv)

    bus = make_bus()
    ev, _, _, _ = load_stream(args.data)
    start = int(np.searchsorted(ev["day"], config.TEST_DAYS[0], side="left"))
    n = len(ev) - start
    bus.set("replay:control", bus.get("replay:control") or {"tps": args.tps, "paused": False})
    # wait for a scorer that became ready *after* we started (ignore a stale flag from a previous run)
    started = time.time()
    while True:
        p = bus.get("proof:scorer") or {}
        if p.get("status") == "ready" and p.get("ready_at", 0) >= started - 5:
            break
        time.sleep(1.0)
    loop_no = 0
    while True:
        loop_no += 1
        sent, t0 = 0, time.time()
        i = start
        while i < len(ev):
            ctl = bus.get("replay:control") or {}
            if ctl.get("paused"):
                time.sleep(0.2)
                t0, sent = time.time(), 0
                continue
            tps = float(ctl.get("tps", args.tps))
            hi = min(len(ev), i + CHUNK)
            bus.xadd_many("tx.in", to_msgs(ev[i:hi], i))
            sent += hi - i
            i = hi
            if tps > 0:
                ahead = sent / tps - (time.time() - t0)
                if ahead > 0:
                    time.sleep(ahead)
                if time.time() - t0 > 5:  # re-anchor so rate changes apply quickly
                    t0, sent = time.time(), 0
            bus.set("proof:replayer", {"service": "replayer", "host": socket.gethostname(), "loop": loop_no,
                                       "progress": round((i - start) / n, 4), "events_total": n, "tps_target": tps,
                                       "ts": time.time()})
        if not args.loop:
            break
        reset_at = time.time()
        bus.xadd("tx.in", {"ctl": "reset"})
        while True:
            p = bus.get("proof:scorer") or {}
            if p.get("status") == "ready" and p.get("ready_at", 0) >= reset_at:
                break
            time.sleep(1.0)


if __name__ == "__main__":
    run()
