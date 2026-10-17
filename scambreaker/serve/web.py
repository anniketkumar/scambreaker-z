"""Web console: analyst view, customer phone, trusted contact, scenario triggers, platform proof.

    gunicorn -k gthread --threads 32 -w 1 -b 0.0.0.0:8080 scambreaker.serve.web:app
"""
from __future__ import annotations

import json
import os
import platform
import queue
import socket
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request

from .. import config
from ..security.audit import verify as audit_verify
from .bus import make_bus

ROOT = config.ROOT
app = Flask(__name__, template_folder=str(ROOT / "web" / "templates"), static_folder=str(ROOT / "web" / "static"))
bus = make_bus()
DEMO_TOKEN = os.environ.get("SB_DEMO_TOKEN", "")
SCENARIOS = {json.loads(p.read_text())["slug"]: json.loads(p.read_text())
             for p in sorted(config.SCENARIO_DIR.glob("*.json"))}


def serve_meta() -> dict:
    p = config.MODEL_DIR / "serve_meta.json"
    return json.loads(p.read_text()) if p.exists() else {"personas": [], "scenario_payees": {}}


class Hub:
    """Fans bus events out to every connected browser (server-sent events)."""

    def __init__(self):
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q) -> None:
        with self.lock:
            self.clients.discard(q)

    def publish(self, msg: dict) -> None:
        data = json.dumps(msg, separators=(",", ":"), default=float)
        with self.lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass

    def _run(self) -> None:
        last_dec, last_ui, last_m = "$", "$", 0.0
        while True:
            try:
                items, last_dec = bus.xread("decisions", last_dec, count=500, block_ms=150)
                for _, d in items:
                    self.publish({"type": "decision", "d": d})
                items, last_ui = bus.xread("ui.events", last_ui, count=500, block_ms=5)
                for _, e in items:
                    self.publish(e)
                if time.time() - last_m >= 1.0:
                    last_m = time.time()
                    self.publish({"type": "metrics", "m": bus.get("metrics") or {}})
            except Exception as e:  # noqa: BLE001  (keep the hub alive across bus hiccups)
                print("hub error:", repr(e), flush=True)
                time.sleep(1.0)


hub = Hub()


def _authorised() -> bool:
    if not DEMO_TOKEN:
        return True
    return request.headers.get("X-Demo-Token") == DEMO_TOKEN or request.args.get("token") == DEMO_TOKEN


@app.get("/")
def index():
    meta = serve_meta()
    return render_template("index.html", product=config.PRODUCT_NAME, bank=config.BANK_NAME,
                           personas=meta.get("personas", []),
                           scenarios=[{k: s[k] for k in ("slug", "title", "persona", "story")} for s in SCENARIOS.values()],
                           token_required=bool(DEMO_TOKEN), sgd_per_usd=config.SGD_PER_USD)


@app.get("/api/stream")
def stream():
    q = hub.subscribe()

    def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    yield f"data: {q.get(timeout=15)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            hub.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/state")
def state():
    return jsonify({"metrics": bus.get("metrics") or {}, "cases": bus.recent("cases:recent", 30),
                    "replay": bus.get("replay:control") or {}})


@app.get("/api/case/<cid>")
def case(cid: str):
    c = bus.get(f"case:{cid}")
    if c is None:
        abort(404)
    return jsonify(c)


@app.post("/api/scenario/<slug>")
def scenario(slug: str):
    if not _authorised():
        abort(403)
    sc = SCENARIOS.get(slug)
    if sc is None:
        abort(404)
    meta = serve_meta()
    persona = next((p for p in meta["personas"] if p["slug"] == sc["persona"]), None)
    payee = meta["scenario_payees"].get(sc["payee_ref"])
    if persona is None or payee is None:
        abort(503, "models not trained yet")
    run_id = uuid.uuid4().hex[:6]
    msgs = []
    for k, step in enumerate(sc["steps"]):
        fmt = step["fmt"]
        msgs.append({"id": f"{slug}-{run_id}-{k + 1}", "t": None, "offset_min": step["offset_min"],
                     "src": persona["id"], "dst": payee, "amt": step["amount_sgd"] / config.SGD_PER_USD,
                     "fmt": fmt, "same_bank": 0, "cross_cur": 1 if fmt == "Bitcoin" else 0, "pool": 1,
                     "session": step["session"], "scenario": slug, "step": k + 1, "steps": len(sc["steps"]),
                     "payee_name": sc["payee_name"],
                     "truth": {"is_scam": sc["truth_is_scam"], "typ": sc.get("typology")}})
    gap = float(request.args.get("gap", 3.0))

    def send():
        for m in msgs:
            m["ingest_ts"] = time.time()
            bus.xadd("tx.in", m)
            time.sleep(gap)

    threading.Thread(target=send, daemon=True).start()
    return jsonify({"ok": True, "ids": [m["id"] for m in msgs], "gap_s": gap})


@app.post("/api/replay")
def replay():
    if not _authorised():
        abort(403)
    ctl = bus.get("replay:control") or {"tps": 1500, "paused": False}
    body = request.get_json(silent=True) or {}
    if "tps" in body:
        ctl["tps"] = max(0.0, float(body["tps"]))
    if "paused" in body:
        ctl["paused"] = bool(body["paused"])
    bus.set("replay:control", ctl)
    return jsonify(ctl)


def _host_proof() -> dict:
    """Facts the host writes with real commands (scripts/evidence.sh) into the shared volume."""
    p = config.WORK_DIR / "proof" / "host.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _crypto_drivers() -> list[str]:
    try:
        txt = Path("/proc/crypto").read_text()
    except OSError:
        return []
    return sorted({ln.split(":", 1)[1].strip() for ln in txt.splitlines()
                   if ln.startswith("driver") and "s390" in ln})


def _cpu() -> dict:
    out = {"nnpa_flag": False}
    try:
        txt = Path("/proc/cpuinfo").read_text()
        for ln in txt.splitlines():
            if ln.startswith("features"):
                out["nnpa_flag"] = "nnpa" in ln.split(":", 1)[1].split()
            if ln.startswith("processor 0") and "machine" in ln:
                out["processor0"] = ln.split(":", 1)[1].strip()
    except OSError:
        pass
    return out


@app.get("/api/proof")
def proof():
    services = {k.split(":", 1)[1]: bus.get(k) for k in bus.keys("proof:")}
    return jsonify({
        "web": {"host": socket.gethostname(), "machine": platform.machine(), "python": platform.python_version()},
        "cpu": _cpu(),
        "cpacf_crypto_drivers": _crypto_drivers(),
        "host": _host_proof(),
        "services": services,
        "metrics": bus.get("metrics") or {},
    })


@app.post("/api/audit/verify")
def audit():
    path = config.AUDIT_DIR / "decisions.jsonl"
    if not path.exists():
        return jsonify({"ok": False, "records": 0, "error": "no audit log yet"})
    t0 = time.time()
    ok, n, bad = audit_verify(path)
    return jsonify({"ok": ok, "records": n, "first_bad_line": bad, "ms": round((time.time() - t0) * 1000, 1)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), threaded=True)
