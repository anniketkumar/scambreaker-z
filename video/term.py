"""Turn a real asciinema recording made on the LinuxONE VM into video footage (build/term.webm).

On the VM:   asciinema rec -c "bash scripts/demo_terminal.sh" term.cast
Locally:     python video/term.py --cast build/term.cast
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/asciinema-player@3.7.1/dist/bundle/asciinema-player.css">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
 html,body{margin:0;width:1920px;height:1080px;background:#0b0d10;overflow:hidden;font-family:'IBM Plex Mono',monospace}
 .frame{position:absolute;left:90px;top:70px;width:1740px;height:940px;border-radius:16px;overflow:hidden;
   border:1px solid #2f3540;box-shadow:0 30px 80px rgba(0,0,0,.5);background:#0f1115}
 .bar{height:44px;display:flex;align-items:center;gap:10px;padding:0 18px;background:#1c2027;color:#8d96a5;font-size:18px}
 .bar i{width:13px;height:13px;border-radius:50%;display:inline-block}
 #p{height:896px} .ap-wrapper{height:100%} .ap-player{--term-color-background:#0f1115}
</style></head><body>
<div class="frame"><div class="bar"><i style="background:#d03b3b"></i><i style="background:#fab219"></i><i style="background:#0ca30c"></i>
<span style="margin-left:14px">ssh linux1@__HOST__ · IBM LinuxONE Community Cloud</span></div><div id="p"></div></div>
<script src="https://cdn.jsdelivr.net/npm/asciinema-player@3.7.1/dist/bundle/asciinema-player.min.js"></script>
<script>
 const cast = __CAST__;
 window.player = AsciinemaPlayer.create({data: cast}, document.getElementById('p'),
   {autoPlay: true, speed: __SPEED__, idleTimeLimit: 0.7, fit: 'both', terminalFontFamily: "'IBM Plex Mono', monospace",
    terminalFontSize: '20px', controls: false, theme: 'asciinema'});
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cast", type=Path, default=BUILD / "term.cast")
    ap.add_argument("--host", default="LinuxONE")
    ap.add_argument("--speed", type=float, default=1.4)
    args = ap.parse_args()
    raw = args.cast.read_text(encoding="utf-8")
    lines = raw.strip().splitlines()
    last_t = json.loads(lines[-1])[0] if len(lines) > 1 else 10.0
    html = (PAGE.replace("__CAST__", json.dumps(raw)).replace("__SPEED__", str(args.speed))
            .replace("__HOST__", args.host))
    page_path = BUILD / "term.html"
    page_path.write_text(html, encoding="utf-8")
    out = BUILD / "term_rec"
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(out),
                            record_video_size={"width": 1920, "height": 1080})
        pg = ctx.new_page()
        pg.goto(page_path.resolve().as_uri())
        time.sleep(min(90.0, last_t / args.speed + 2.5))
        video = pg.video
        ctx.close()
        b.close()
        src = Path(video.path())
    dst = BUILD / "term.webm"
    if dst.exists():
        dst.unlink()
    src.rename(dst)
    print("terminal footage", dst)


if __name__ == "__main__":
    main()
