"""Render the video's title/data slides (1920x1080 PNG) from HTML with Playwright + Edge.

    python video/slides.py --team "Team Name" --repo github.com/x/scambreaker-z --results docs/results
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

CSS = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&family=Noto+Sans+SC:wght@500&display=swap');
* { box-sizing: border-box; margin: 0; }
html, body { width: 1920px; height: 1080px; overflow: hidden; }
body { background: radial-gradient(1200px 700px at 78% 18%, #12203d 0%, #0b0d10 62%); color: #f2f4f8;
  font-family: 'IBM Plex Sans', 'Noto Sans SC', sans-serif; padding: 96px 120px; position: relative; }
.kicker { font-family: 'IBM Plex Mono', monospace; font-size: 30px; letter-spacing: .14em; text-transform: uppercase; color: #78a9ff; }
.hero { font-size: 250px; font-weight: 600; letter-spacing: -.02em; line-height: 1; margin-top: 24px; }
.h2 { font-size: 54px; font-weight: 300; color: #c1c7d0; margin-top: 18px; }
.callout { margin-top: 70px; font-size: 52px; font-weight: 400; line-height: 1.25; max-width: 1500px; }
.callout b { color: #fab219; font-weight: 600; }
.src { position: absolute; left: 120px; right: 120px; bottom: 56px; font-size: 21px; color: #8d96a5; line-height: 1.4; }
.title { font-size: 70px; font-weight: 500; letter-spacing: -.01em; line-height: 1.1; max-width: 1600px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 48px; margin-top: 64px; }
.card { background: #14171c; border: 1px solid #2f3540; border-radius: 22px; padding: 44px 48px; }
.card.hot { border-color: #8a3a2a; background: linear-gradient(180deg, #2a1712, #14171c); }
.card .tag { font-family: 'IBM Plex Mono', monospace; font-size: 26px; letter-spacing: .12em; text-transform: uppercase; color: #8d96a5; }
.card.hot .tag { color: #ec835a; }
.card p { font-size: 44px; margin-top: 20px; line-height: 1.25; }
.card p.dim { color: #8d96a5; font-size: 36px; }
.strip { margin-top: 52px; font-size: 36px; line-height: 1.4; color: #c1c7d0; border-left: 6px solid #d03b3b; padding: 6px 0 6px 28px; max-width: 1650px; }
.strip b { color: #f2f4f8; }
.flow { display: grid; grid-template-columns: 300px 60px 460px 60px 380px 60px 420px; align-items: center; margin-top: 120px; }
.box { background: #14171c; border: 1px solid #2f3540; border-radius: 18px; padding: 26px 28px; font-size: 28px; line-height: 1.3; }
.box h4 { font-family: 'IBM Plex Mono', monospace; font-size: 20px; letter-spacing: .1em; text-transform: uppercase; color: #8d96a5; margin-bottom: 10px; font-weight: 500; }
.arrow { text-align: center; font-size: 46px; color: #4a5160; }
.models { display: grid; gap: 16px; }
.m { display: grid; grid-template-columns: 54px 1fr; gap: 16px; align-items: center; background: #14171c; border: 1px solid #2f3540; border-radius: 16px; padding: 18px 20px; font-size: 25px; line-height: 1.25; }
.m .k { width: 54px; height: 54px; border-radius: 12px; display: grid; place-items: center; font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 28px; color: #0b0d10; }
.m small { display: block; color: #8d96a5; font-size: 20px; margin-top: 4px; }
.ladder { display: grid; gap: 12px; margin-top: 14px; }
.step { display: flex; align-items: center; gap: 14px; font-family: 'IBM Plex Mono', monospace; font-size: 24px; }
.step i { width: 14px; height: 14px; border-radius: 50%; display: inline-block; }
.foot { position: absolute; left: 120px; right: 120px; bottom: 70px; display: flex; gap: 18px; flex-wrap: wrap; }
.pill { font-family: 'IBM Plex Mono', monospace; font-size: 22px; padding: 10px 18px; border-radius: 999px; border: 1px solid #2c4f9e; background: #10203f; color: #d6e4ff; }
.grid2 { display: grid; grid-template-columns: 640px 1fr; gap: 56px; margin-top: 56px; align-items: start; }
.stat { margin-bottom: 40px; }
.stat .v { font-size: 104px; font-weight: 600; line-height: 1; letter-spacing: -.01em; }
.stat .l { font-size: 30px; color: #c1c7d0; margin-top: 8px; line-height: 1.3; }
.chart img { width: 100%; border-radius: 16px; border: 1px solid #2f3540; }
.brand { display: flex; align-items: center; gap: 30px; margin-top: 150px; }
.brand svg { width: 130px; height: 130px; color: #4589ff; }
.brand .n { font-size: 120px; font-weight: 600; letter-spacing: -.01em; }
.tagline { font-size: 50px; font-weight: 300; color: #c1c7d0; margin-top: 30px; }
.team { font-size: 38px; margin-top: 90px; }
.team span { color: #8d96a5; }
.mono { font-family: 'IBM Plex Mono', monospace; }
"""

SHIELD = ('<svg viewBox="0 0 32 32"><path d="M16 2 4 7v8c0 7.5 5.1 13.4 12 15 6.9-1.6 12-7.5 12-15V7L16 2Z" fill="none" '
          'stroke="currentColor" stroke-width="2"/><path d="M11 16.5 14.5 20 21.5 12" fill="none" stroke="currentColor" '
          'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def img_data(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def slides(ctx: dict) -> dict[str, str]:
    s = {}
    s["hook"] = """
<div class="kicker">Singapore · 2025</div>
<div class="hero">S$913.1M</div>
<div class="h2">lost to scams &nbsp;·&nbsp; 37,308 scam cases</div>
<div class="callout">In 2024, <b>4 in 5</b> scam cases were self-effected: the victim was manipulated into sending the money themself.</div>
<div class="src">Sources: Singapore Police Force, Annual Scams and Cybercrime Brief 2025 (losses, cases) and Brief 2024 (82.4% of reported scam cases were self-effected transfers).</div>"""
    s["problem"] = """
<div class="title">Fraud models are built for the wrong attacker</div>
<div class="cols">
  <div class="card"><div class="tag">Fraud</div><p>A criminal steals your card or password.</p><p class="dim">The login looks wrong, so the model catches it.</p></div>
  <div class="card hot"><div class="tag">Scam</div><p>You are manipulated into paying.</p><p class="dim">The login is real, so the model stays silent.</p></div>
</div>
<div class="strip"><b>Today's backstop is a police restriction order:</b> a last resort that freezes everything. 12 issued by Feb 2026. Graduated limits were shelved: banks need “significant system changes”.</div>
<div class="src">Source: Ministry of Home Affairs, parliamentary reply on Protection from Scams Act restriction orders, 4 March 2026.</div>"""
    s["solution"] = """
<div class="kicker">ScamBreaker-Z on IBM LinuxONE</div>
<div class="title" style="margin-top:18px">Detect · Decide · Respond — inside the transaction</div>
<div class="flow">
  <div class="box"><h4>Transfer</h4>Customer presses Send<br><span style="color:#8d96a5;font-size:22px">payee · amount · app session</span></div>
  <div class="arrow">→</div>
  <div class="models">
    <div class="m"><div class="k" style="background:#3987e5">A</div><div>Payee mule risk<small>XGBoost · real IBM AMLworld labels</small></div></div>
    <div class="m"><div class="k" style="background:#d95926">B</div><div>Customer behaviour<small>LSTM · Telum on-chip AI accelerator</small></div></div>
    <div class="m"><div class="k" style="background:#199e70">C</div><div>Coercion signals<small>on a call · payee just added · hesitation</small></div></div>
  </div>
  <div class="arrow">→</div>
  <div class="box"><h4>Decide · ms</h4>Stacked score → friction ladder
    <div class="ladder">
      <div class="step"><i style="background:#0ca30c"></i>ALLOW</div><div class="step"><i style="background:#fab219"></i>NUDGE</div>
      <div class="step"><i style="background:#ec835a"></i>HOLD 24h</div><div class="step"><i style="background:#d03b3b"></i>BLOCK + call</div>
    </div></div>
  <div class="arrow">→</div>
  <div class="box"><h4>Respond · IBM Granite on-box</h4>Warning in the customer's language<br>Alert to a trusted contact<br>Analyst case file (RAG)</div>
</div>
<div class="foot"><span class="pill">containers on LinuxONE</span><span class="pill">AI has no route to the internet</span><span class="pill">LUKS2 · CPACF crypto</span><span class="pill">hash-chained audit trail</span></div>"""
    chart = ctx.get("ablation_png")
    s["results"] = f"""
<div class="kicker">Results · held-out test days</div>
<div class="grid2">
  <div>
    <div class="stat"><div class="v">{ctx['scam_usd']}%</div><div class="l">of scam money held or blocked</div></div>
    <div class="stat"><div class="v">{ctx['legit_hold']}%</div><div class="l">of legitimate payments held</div></div>
    <div class="stat"><div class="v">{ctx['p99']} ms</div><div class="l">p99 decision latency at {ctx['tps']} tx/s</div></div>
  </div>
  <div class="chart">{f'<img src="{img_data(chart)}">' if chart else ''}</div>
</div>
<div class="src">Scam episodes are simulated and injected into IBM AMLworld (HI-Small) transactions; the payee model is trained and tested on AMLworld's real laundering labels (test AUPRC {ctx['auprc']}). Details and code: {ctx['repo']}</div>"""
    s["close"] = f"""
<div class="brand">{SHIELD}<div class="n">ScamBreaker-Z</div></div>
<div class="tagline">Detect · Decide · Respond — inside the transaction</div>
<div class="team">{ctx['team']} <span>· IBM Z Datathon 2026</span></div>
<div class="team mono" style="font-size:30px;margin-top:22px;color:#78a9ff">{ctx['repo']}</div>
<div class="foot"><span class="pill">IBM LinuxONE</span><span class="pill">Telum NNPA · ibm-zdnn-plugin</span><span class="pill">IBM Granite · llama.cpp</span><span class="pill">TensorFlow · XGBoost</span><span class="pill">IBM AMLworld data</span></div>"""
    return s


def render(ctx: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        for name, body in slides(ctx).items():
            html = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
            src = out_dir / f"{name}.html"
            src.write_text(html, encoding="utf-8")
            pg.goto(src.resolve().as_uri())
            pg.wait_for_timeout(1200)  # web fonts
            dst = out_dir / f"{name}.png"
            pg.screenshot(path=str(dst))
            paths.append(dst)
        b.close()
    return paths


def context_from_results(results: Path, team: str, repo: str, live: dict | None = None) -> dict:
    m = json.loads((results / "metrics.json").read_text())
    pol = m["policy_test"]
    live = live or {}
    e2e = {}
    if (results / "e2e_bench.json").exists():
        steps = json.loads((results / "e2e_bench.json").read_text()).get("steps", [])
        e2e = max(steps, key=lambda s: s["achieved_tps"]) if steps else {}
    return {
        "team": team, "repo": repo,
        "scam_usd": f"{100 * pol['scam_payments']['usd_share_held']:.0f}",
        "legit_hold": f"{100 * pol['legit']['hold_or_more']:.2f}",
        "auprc": f"{m['model_a_test_real_labels'].get('auprc', 0):.2f}",
        "p99": f"{live.get('p99', e2e.get('p99_ms', 0)):.0f}",
        "tps": f"{live.get('tps', e2e.get('achieved_tps', 0)):,.0f}",
        "ablation_png": results / "ablation.png" if (results / "ablation.png").exists() else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--results", type=Path, default=HERE.parent / "docs" / "results")
    ap.add_argument("--out", type=Path, default=BUILD / "slides")
    args = ap.parse_args()
    live = json.loads((BUILD / "live.json").read_text()) if (BUILD / "live.json").exists() else None
    for p in render(context_from_results(args.results, args.team, args.repo, live), args.out):
        print("slide", p)


if __name__ == "__main__":
    main()
