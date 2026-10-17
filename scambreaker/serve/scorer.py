"""Real-time scorer: point-in-time features -> models A/B/C -> stacker -> graduated policy.

Consumes `tx.in` in micro-batches (one batch = one accelerator call for the LSTM), publishes
decisions, opens cases for held payments and queues them for the GenAI worker.

    python -m scambreaker.serve.scorer [--device auto|cpu]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import time
import uuid
from collections import deque
from pathlib import Path

import numpy as np

from .. import config
from ..data.events import load_stream
from ..features.engine import FMT_CODE, FMT_NAMES, N_A, N_SEQ, FeatureEngine
from ..models import behavior_lstm, coercion_lr, mule_xgb, typology
from ..models.fusion import Stacker
from ..security.audit import AuditLog
from ..security.tokenize import Tokenizer
from . import reasons as R
from .bus import make_bus

DECISIONS = config.DECISIONS
UI_ALLOW_PER_SEC = 12
GENAI_REPLAY_EVERY_S = 25.0
C_ORDER = coercion_lr.FEATURES


def session_vector(s: dict) -> np.ndarray:
    return np.array([
        s.get("on_call", 0), s.get("unknown_caller", 0), s.get("remote_access", 0),
        -np.log1p(min(float(s.get("payee_added_min", 1e5)), 1e5)), np.log1p(float(s.get("session_s", 120))),
        s.get("hesitation", 0.0), s.get("failed", 0), s.get("new_device", 0),
    ], dtype=np.float32)


def sim_clock(t: int) -> str:
    return f"Day {t // 1440 + 1} {(t % 1440) // 60:02d}:{t % 60:02d}"


class Scorer:
    def __init__(self, model_dir: Path, data_dir: Path, device: str = "auto"):
        t0 = time.time()
        self.model_dir = model_dir
        self.A = mule_xgb.load(model_dir / "model_a.json")
        self.B = behavior_lstm.BehaviorModel(model_dir, device=device)
        self.C = coercion_lr.CoercionModel.from_json(model_dir / "model_c.json")
        self.D = typology.load(model_dir / "model_d.json")
        self.stacker = Stacker.from_json(model_dir / "stacker.json")
        self.meta = json.loads((model_dir / "serve_meta.json").read_text())
        self.aml_threshold = float(self.meta.get("aml_threshold", 0.9))
        self.a_names = json.loads((model_dir / "feature_names.json").read_text())["A"]
        self.ev, self.keys, self.retail, _ = load_stream(data_dir)
        self.personas = {p["id"]: p for p in self.meta["personas"]}
        self.device = device
        self.tok = Tokenizer()
        self.engine = FeatureEngine()
        self.now_t = 0
        self.load_s = time.time() - t0

    # ----------------------------------------------------------- warm-up
    def warm_up(self) -> float:
        """Rebuild point-in-time state from history (days before the replayed test period)."""
        t0 = time.time()
        self.engine = FeatureEngine()
        start = config.TEST_DAYS[0]
        cut = int(np.searchsorted(self.ev["day"], start, side="left"))
        proc = self.engine.process
        for lo in range(0, cut, 500_000):
            c = self.ev[lo:min(cut, lo + 500_000)]
            cols = [c[k].tolist() for k in ("t", "src", "dst", "amt", "fmt", "same_bank", "cross_cur", "hour")]
            for t, s, d, a, f, sb, cc, h in zip(*cols):
                proc(t, s, d, a, f, sb, cc, h, want_a=False, want_seq=False)
        self.now_t = start * 1440
        return time.time() - t0

    # ------------------------------------------------------------- score
    def score_batch(self, msgs: list[dict]):
        n = len(msgs)
        FA = np.empty((n, N_A), np.float32)
        SEQ = np.zeros((n, config.SEQ_LEN, N_SEQ), np.float32)
        XC = np.zeros((n, len(C_ORDER)), np.float32)
        pool = np.zeros(n, bool)
        steps, ts = [], []
        proc = self.engine.process
        for i, m in enumerate(msgs):
            t = m.get("t")
            if t is None:  # live scenario event: stamp with the replay clock
                t = self.now_t + int(m.get("offset_min", 0))
            elif t > self.now_t:
                self.now_t = t
            fmt = m["fmt"] if isinstance(m["fmt"], int) else FMT_CODE[m["fmt"]]
            fa, seq, step = proc(t, m["src"], m["dst"], float(m["amt"]), fmt, bool(m["same_bank"]),
                                 bool(m["cross_cur"]), (t % 1440) // 60)
            FA[i] = fa
            SEQ[i] = seq
            XC[i] = session_vector(m.get("session") or {})
            pool[i] = bool(m.get("pool"))
            steps.append(step)
            ts.append(t)
        p_a = mule_xgb.predict(self.A, FA)
        p_b = np.zeros(n, np.float32)
        p_c = np.zeros(n, np.float32)
        p = np.zeros(n, np.float64)
        dec = np.zeros(n, np.int8)
        idx = np.flatnonzero(pool)
        if len(idx):
            p_b[idx] = self.B.predict(SEQ[idx])
            p_c[idx] = self.C.predict(XC[idx])
            p[idx] = self.stacker.predict({"A": p_a[idx], "B": p_b[idx], "C": p_c[idx]})
            dec[idx] = self.stacker.decide(p[idx])
        return dict(FA=FA, SEQ=SEQ, XC=XC, pool=pool, steps=steps, t=ts, p_a=p_a, p_b=p_b, p_c=p_c, p=p, dec=dec)

    def explain(self, m: dict, i: int, out: dict) -> dict:
        """Full case record for one scored transfer (only for held / scenario payments)."""
        fa, step = out["FA"][i], out["steps"][i]
        session = m.get("session") or {}
        c_contrib = self.C.contributions(out["XC"][i])
        a_vals = mule_xgb.contributions(self.A, fa[None, :])[0][:-1]
        a_contrib = dict(zip(self.a_names, a_vals.tolist()))
        receiver = self.engine.receiver_profile(m["dst"])
        sender = self.engine.sender_baseline(m["src"])
        reasons = R.build(fa, step, session, c_contrib, a_contrib, receiver, sender)
        probs = typology.predict(self.D, typology.matrix(out["SEQ"][i:i + 1], out["XC"][i:i + 1]))[0]
        k = int(np.argmax(probs))
        persona = self.personas.get(m["src"])
        amt = float(m["amt"])
        return {
            "reasons": reasons,
            "typology": config.TYPOLOGIES[k],
            "typology_label": config.TYPOLOGY_LABELS[config.TYPOLOGIES[k]],
            "typology_p": round(float(probs[k]), 3),
            "receiver": {k2: (round(v, 3) if isinstance(v, float) else v) for k2, v in receiver.items()},
            "sender": sender,
            "persona": persona["slug"] if persona else None,
            "customer": persona["name"] if persona else self.tok.token("cust", m["src"]),
            "contact": persona["contact"] if persona else None,
            "lang": persona["lang"] if persona else "en",
            "amount_usd": round(amt, 2),
            "amount_sgd": round(amt * config.SGD_PER_USD, 2),
            "payee_label": m.get("payee_name") or self.tok.token("payee", m["dst"]),
        }


def run(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", type=Path, default=config.MODEL_DIR)
    ap.add_argument("--data", type=Path, default=config.DATA_DIR)
    ap.add_argument("--device", default=os.environ.get("SB_DEVICE", "auto"))
    ap.add_argument("--batch", type=int, default=512)
    args = ap.parse_args(argv)

    bus = make_bus()
    consumer = socket.gethostname()
    sc = Scorer(args.models, args.data, device=args.device)
    proof = {"service": "scorer", "host": consumer, "machine": platform.machine(), "device": args.device,
             "status": "warming", "started": time.time()}
    bus.set("proof:scorer", proof)
    warm_s = sc.warm_up()
    proof.update(status="ready", warm_up_s=round(warm_s, 1), accounts=len(sc.engine.acc), **accel_probe(sc))
    bus.set("proof:scorer", proof)
    print(f"scorer ready: warm-up {warm_s:.1f}s, {len(sc.engine.acc):,} accounts, {proof.get('tf_devices')}", flush=True)

    audit = AuditLog(config.AUDIT_DIR / "decisions.jsonl")
    lat = deque(maxlen=5000)
    batch_ms = deque(maxlen=200)
    stamps = deque(maxlen=50_000)
    counts = {d: 0 for d in DECISIONS}
    totals = {"events": 0, "pool": 0, "usd_paused": 0.0, "aml_flags": 0, "scam_seen": 0, "scam_held": 0,
              "scam_usd_seen": 0.0, "scam_usd_held": 0.0, "legit_held": 0, "legit_seen": 0}
    ui_budget, ui_window = UI_ALLOW_PER_SEC, int(time.time())
    last_metrics, last_genai = 0.0, 0.0

    while True:
        items = bus.xreadgroup("tx.in", "scorers", consumer, count=args.batch, block_ms=50)
        now = time.time()
        if items:
            ids = [sid for sid, _ in items]
            msgs = []
            for _, m in items:
                if m.get("ctl") == "reset":
                    proof["status"] = "warming"
                    bus.set("proof:scorer", proof)
                    sc.warm_up()
                    proof["status"] = "ready"
                    bus.set("proof:scorer", proof)
                    continue
                msgs.append(m)
            if msgs:
                t0 = time.perf_counter()
                out = sc.score_batch(msgs)
                batch_ms.append((time.perf_counter() - t0) * 1000)
                done = time.time()
                pubs = []
                for i, m in enumerate(msgs):
                    d = int(out["dec"][i])
                    counts[DECISIONS[d]] += 1
                    totals["events"] += 1
                    latency = (done - m.get("ingest_ts", done)) * 1000
                    lat.append(latency)
                    stamps.append(done)
                    pa = float(out["p_a"][i])
                    is_pool = bool(out["pool"][i])
                    truth = m.get("truth") or {}
                    if is_pool:
                        totals["pool"] += 1
                        if truth.get("is_scam"):
                            totals["scam_seen"] += 1
                            totals["scam_usd_seen"] += float(m["amt"])
                            if d >= 2:
                                totals["scam_held"] += 1
                                totals["scam_usd_held"] += float(m["amt"])
                        elif not truth.get("is_laund"):
                            totals["legit_seen"] += 1
                            totals["legit_held"] += int(d >= 2)
                    if d >= 2:
                        totals["usd_paused"] += float(m["amt"])
                    aml = pa >= sc.aml_threshold
                    totals["aml_flags"] += int(aml)
                    scenario = m.get("scenario")
                    rec = {"id": m.get("id") or uuid.uuid4().hex[:12], "sim": sim_clock(out["t"][i]),
                           "amount_usd": round(float(m["amt"]), 2), "fmt": FMT_NAMES[FMT_CODE[m["fmt"]] if isinstance(m["fmt"], str) else m["fmt"]],
                           "decision": DECISIONS[d], "score": round(float(out["p"][i]), 4),
                           "scores": {"A": round(pa, 4), "B": round(float(out["p_b"][i]), 4),
                                      "C": round(float(out["p_c"][i]), 4)},
                           "pool": is_pool, "aml": bool(aml), "latency_ms": round(latency, 2),
                           "truth": truth, "scenario": scenario, "step": m.get("step"), "steps": m.get("steps")}
                    if d >= 2 or scenario:
                        rec.update(sc.explain(m, i, out))
                        rec["case"] = True
                        rec["created"] = done
                        rec["llm"] = {"status": "queued" if (scenario or now - last_genai > GENAI_REPLAY_EVERY_S) else "template"}
                        rec["messages"] = {}
                        bus.set(f"case:{rec['id']}", rec)
                        bus.push_recent("cases:recent", {k: rec[k] for k in ("id", "sim", "decision", "amount_usd",
                                                                              "typology_label", "customer", "scenario", "persona")})
                        if rec["llm"]["status"] == "queued":
                            bus.xadd("genai.jobs", {"case_id": rec["id"]})
                            if not scenario:
                                last_genai = now
                    if d >= 1 or scenario or (aml and is_pool) or np.random.random() < 0.01:
                        audit.append({"id": rec["id"], "sim": rec["sim"], "decision": rec["decision"],
                                      "score": rec["score"], "scores": rec["scores"],
                                      "src": sc.tok.token("acct", m["src"]), "dst": sc.tok.token("acct", m["dst"]),
                                      "amount_usd": rec["amount_usd"], "reasons": [r["code"] for r in rec.get("reasons", [])]})
                    sec = int(done)
                    if sec != ui_window:
                        ui_window, ui_budget = sec, UI_ALLOW_PER_SEC
                    if d >= 1 or scenario or aml or ui_budget > 0:
                        if d == 0 and not scenario and not aml:
                            ui_budget -= 1
                        pubs.append(rec)
                if pubs:
                    bus.xadd_many("decisions", pubs)
            bus.xack("tx.in", "scorers", ids)
        if now - last_metrics >= 1.0:
            last_metrics = now
            recent = [s for s in stamps if s >= now - 5.0]
            lat_arr = np.fromiter(lat, float) if lat else np.zeros(1)
            bus.set("metrics", {
                "ts": now, "tps": len(recent) / 5.0, "p50_ms": float(np.percentile(lat_arr, 50)),
                "p95_ms": float(np.percentile(lat_arr, 95)), "p99_ms": float(np.percentile(lat_arr, 99)),
                "batch_ms": float(np.mean(batch_ms)) if batch_ms else 0.0, "counts": counts, **totals,
                "sim": sim_clock(sc.now_t), "audit_records": audit.count, "device": args.device,
            })


def accel_probe(sc: Scorer) -> dict:
    """Same model, same inputs: accelerator placement vs pinned CPU (reported on the proof panel)."""
    info: dict = {}
    try:
        tf = sc.B.tf
        info["tf_version"] = tf.__version__
        info["tf_devices"] = [f"{d.device_type}:{d.name.split(':')[-1]}" for d in tf.config.list_physical_devices()]
        x = np.random.default_rng(0).normal(size=(128, config.SEQ_LEN, N_SEQ)).astype(np.float32)
        x[:, :, 0] = 1.0
        cpu = behavior_lstm.BehaviorModel(sc.model_dir, device="cpu")
        res = {}
        for name, model in (("default", sc.B), ("cpu", cpu)):
            model.predict(x, normalized=True)
            runs = []
            for _ in range(30):
                t0 = time.perf_counter()
                model.predict(x, normalized=True)
                runs.append((time.perf_counter() - t0) * 1000)
            res[name] = round(float(np.median(runs)), 3)
        info["lstm_batch128_ms"] = res
    except Exception as e:  # noqa: BLE001
        info["accel_probe_error"] = repr(e)[:300]
    return info


if __name__ == "__main__":
    run()
