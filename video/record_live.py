"""Re-shoot the live-overview clip once the replay has covered most of a test day, so the KPI
tiles show representative running totals (not the first few minutes after midnight).

    python video/record_live.py --url http://<vm-ip>:8080 --token <demo token>
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
SPOT_CSS = (".sb-dim { opacity: .22 !important; filter: saturate(.4); }"
            ".sb-lit { box-shadow: 0 0 0 2px #2c4f9e, 0 0 40px rgba(69,137,255,.18); }")


def call(url: str, path: str, token: str, body: dict | None = None) -> dict:
    for _ in range(6):
        try:
            req = urllib.request.Request(f"{url}{path}", method="POST" if body is not None else "GET",
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", "X-Demo-Token": token})
            return json.loads(urllib.request.urlopen(req, timeout=15).read())
        except OSError:
            time.sleep(2)
    raise SystemExit(f"cannot reach {url}{path}")


def sim_minutes(sim: str) -> int:
    day, hm = sim.replace("Day ", "").split(" ")
    h, m = hm.split(":")
    return (int(day) - 1) * 1440 + int(h) * 60 + int(m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--until", default="Day 9 21:00", help="replay clock to reach before shooting")
    ap.add_argument("--fast-tps", type=float, default=4000)
    ap.add_argument("--tps", type=float, default=2000)
    args = ap.parse_args()
    url = args.url.rstrip("/")
    target = sim_minutes(args.until)
    m = call(url, "/api/state", args.token)["metrics"]
    if not m.get("sim") or sim_minutes(m["sim"]) < target:
        call(url, "/api/replay", args.token, {"tps": args.fast_tps, "paused": False})
        while True:
            m = call(url, "/api/state", args.token)["metrics"]
            if m.get("sim") and sim_minutes(m["sim"]) >= target:
                break
            print("replay clock", m.get("sim"), flush=True)
            time.sleep(5)
    call(url, "/api/replay", args.token, {"tps": args.tps, "paused": False})
    for _ in range(60):  # wait until any backlog has drained and tail latency is steady
        m = call(url, "/api/state", args.token)["metrics"]
        print("settling: p99", round(m.get("p99_ms", 0)), "ms at", round(m.get("tps", 0)), "tx/s", flush=True)
        if m.get("p99_ms", 1e9) < 150 and m.get("tps", 0) > 0.8 * args.tps:
            break
        time.sleep(5)
    out = BUILD / "live_rec"
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(out),
                            record_video_size={"width": 1920, "height": 1080})
        page = ctx.new_page()
        t0 = time.time()
        page.goto(url, wait_until="domcontentloaded")
        page.add_style_tag(content=SPOT_CSS)
        page.wait_for_timeout(16000)  # charts fill, metrics settle at the steady rate
        page.evaluate("""() => { for (const s of ['header.top','.phonecol','.casecol','footer.controls'])
                                   document.querySelector(s)?.classList.add('sb-dim');
                                 for (const s of ['.kpis','.feed']) document.querySelector(s)?.classList.add('sb-lit'); }""")
        start = time.time() - t0
        page.wait_for_timeout(9000)
        end = time.time() - t0
        m = call(url, "/api/state", args.token)["metrics"]
        video = page.video
        ctx.close()
        b.close()
        src = Path(video.path())
    dst = BUILD / "live.webm"
    if dst.exists():
        dst.unlink()
    src.rename(dst)
    live = json.loads((BUILD / "live.json").read_text())
    live.update(tps=m.get("tps", 0), p99=m.get("p99_ms", 0), p50=m.get("p50_ms", 0),
                live_clip=[round(start, 2), round(end, 2)],
                kpi_scam_usd_share=m["scam_usd_held"] / max(1.0, m["scam_usd_seen"]))
    (BUILD / "live.json").write_text(json.dumps(live, indent=2, default=float))
    print("live clip", dst, live["live_clip"], {k: live[k] for k in ("tps", "p99", "kpi_scam_usd_share")})


if __name__ == "__main__":
    main()
