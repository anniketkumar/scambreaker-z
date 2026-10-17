"""Record the live console on LinuxONE for the video: one continuous take, with a timestamp
window per clip and the live numbers that the narration quotes (build/live.json).

    python video/record.py --url http://<vm-ip>:8080 [--token <demo token>]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

SPOT_CSS = """
.sb-dim { opacity: .22 !important; filter: saturate(.4); transition: opacity .5s, filter .5s; }
.sb-lit { transition: opacity .5s, filter .5s; box-shadow: 0 0 0 2px #2c4f9e, 0 0 40px rgba(69,137,255,.18); }
"""
REGIONS = ["header.top", ".kpis", ".feed", ".phonecol", ".casecol", "footer.controls"]


def spotlight(page, lit: list[str]) -> None:
    page.evaluate("""([all, lit]) => {
        for (const sel of all) { const el = document.querySelector(sel); if (!el) continue;
          const on = lit.includes(sel); el.classList.toggle('sb-dim', !on && lit.length > 0);
          el.classList.toggle('sb-lit', on && lit.length > 0 && sel !== 'header.top' && sel !== 'footer.controls'); }
    }""", [REGIONS, lit])


def run_scenario(page, url: str, slug: str) -> list[str]:
    with page.expect_response(lambda r: f"/api/scenario/{slug}" in r.url, timeout=15000) as resp:
        page.click(f"button.scen[data-slug='{slug}']")
    return resp.value.json().get("ids", [])


def case(page, url: str, cid: str) -> dict:
    r = page.request.get(f"{url}/api/case/{cid}")
    return r.json() if r.ok else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", default="")
    ap.add_argument("--llm-timeout", type=float, default=150.0)
    args = ap.parse_args()
    url = args.url.rstrip("/")
    out = BUILD / "demo"
    out.mkdir(parents=True, exist_ok=True)
    marks: dict[str, list[float]] = {}
    live: dict = {}

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(out),
                            record_video_size={"width": 1920, "height": 1080})
        page = ctx.new_page()
        t0 = time.time()
        now = lambda: round(time.time() - t0, 2)  # noqa: E731
        page.goto(url, wait_until="domcontentloaded")
        if args.token:
            page.evaluate("t => localStorage.setItem('sb_token', t)", args.token)
            page.reload(wait_until="domcontentloaded")
        page.add_style_tag(content=SPOT_CSS)
        page.wait_for_timeout(6000)  # feed and charts fill up

        # live overview
        spotlight(page, [".kpis", ".feed"])
        s = now()
        page.wait_for_timeout(8000)
        marks["live"] = [s, now()]
        st = page.request.get(f"{url}/api/state").json()["metrics"]
        live.update(tps=st.get("tps", 0), p99=st.get("p99_ms", 0), p50=st.get("p50_ms", 0))

        # Mdm Tan
        spotlight(page, [".phonecol", ".casecol"])
        s = now()
        tan_ids = run_scenario(page, url, "tan_gov")
        page.wait_for_function("""() => {
            const srcs = document.querySelectorAll('#phone .msg .src').length;
            const typing = document.querySelector('#phone .cursor, #sms .cursor');
            return srcs >= 2 && !typing && document.querySelector('#sms b');
        }""", timeout=args.llm_timeout * 1000)
        page.wait_for_timeout(1500)
        marks["tan"] = [s, now()]
        c = case(page, url, tan_ids[0]) if tan_ids else {}
        live.update(tan_decision=c.get("decision"), tan_ms=c.get("latency_ms"),
                    tan_sources=[m.get("source") for m in (c.get("messages") or {}).values()])

        # analyst case file
        spotlight(page, [".casecol"])
        s = now()
        page.wait_for_function("() => { const n = document.querySelector('.narr'); return n && !n.querySelector('.cursor') && n.textContent.length > 40; }",
                               timeout=args.llm_timeout * 1000)
        for y in range(0, 1400, 140):
            page.evaluate("y => document.querySelector('#case').scrollTo({top: y, behavior: 'smooth'})", y)
            page.wait_for_timeout(350)
        page.wait_for_timeout(1200)
        page.evaluate("() => document.querySelector('#case').scrollTo({top: 0, behavior: 'smooth'})")
        marks["case"] = [s, now()]

        # Ravi: escalating investment scam
        spotlight(page, [".feed", ".phonecol"])
        s = now()
        ravi_ids = run_scenario(page, url, "ravi_invest")
        page.wait_for_function("() => [...document.querySelectorAll('#phone .meta')].some(e => e.textContent.includes('payment 4 of 4'))",
                               timeout=60000)
        page.wait_for_timeout(2500)
        marks["ravi"] = [s, now()]
        live["ravi"] = [case(page, url, i).get("decision") for i in ravi_ids]

        # Aisha: legitimate
        s = now()
        aisha_ids = run_scenario(page, url, "aisha_tuition")
        page.wait_for_function("() => document.querySelector('#phone-who') && document.querySelector('#phone-who').textContent.includes('Aisha')",
                               timeout=30000)
        page.wait_for_timeout(3500)
        marks["aisha"] = [s, now()]
        ca = case(page, url, aisha_ids[0]) if aisha_ids else {}
        live.update(aisha=ca.get("decision"), aisha_ms=ca.get("latency_ms"))

        # platform proof panel
        spotlight(page, [])
        s = now()
        page.click("#proof-btn")
        page.wait_for_timeout(2500)
        page.click("#audit-verify")
        page.wait_for_timeout(5000)
        marks["proof"] = [s, now()]
        live["proof"] = page.request.get(f"{url}/api/proof").json()
        video = page.video
        ctx.close()
        b.close()
        src = Path(video.path())
    dst = BUILD / "demo.webm"
    if dst.exists():
        dst.unlink()
    src.rename(dst)
    (BUILD / "marks.json").write_text(json.dumps(marks, indent=2))
    (BUILD / "live.json").write_text(json.dumps(live, indent=2, default=float))
    print("recorded", dst, marks)
    print("live", {k: v for k, v in live.items() if k != "proof"})


if __name__ == "__main__":
    main()
