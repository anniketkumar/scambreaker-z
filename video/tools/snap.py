"""Screenshot the live console after triggering a scenario (UI check / video stills).
    python video/tools/snap.py --url http://127.0.0.1:8090 --scenario tan_gov --out shot.png
"""
import argparse
import json
import time
import urllib.request

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8090")
ap.add_argument("--scenario", default="")
ap.add_argument("--out", default="shot.png")
ap.add_argument("--wait", type=float, default=6.0)
ap.add_argument("--width", type=int, default=1920)
ap.add_argument("--height", type=int, default=1080)
ap.add_argument("--proof", action="store_true")
args = ap.parse_args()

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": args.width, "height": args.height}, device_scale_factor=1)
    pg.goto(args.url, wait_until="domcontentloaded")
    time.sleep(2.5)
    if args.scenario:
        req = urllib.request.Request(f"{args.url}/api/scenario/{args.scenario}?gap=1.5", method="POST")
        print(json.loads(urllib.request.urlopen(req).read()))
    time.sleep(args.wait)
    if args.proof:
        pg.click("#proof-btn")
        time.sleep(2.0)
    pg.screenshot(path=args.out)
    b.close()
print("saved", args.out)
