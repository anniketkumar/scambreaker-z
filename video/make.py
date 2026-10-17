"""Assemble the submission video: narration (edge-tts) + slides + live LinuxONE footage.

    python video/make.py --team "Team" --repo github.com/x/scambreaker-z
Inputs:  build/demo.webm + build/marks.json + build/live.json (video/record.py)
         build/term.webm (video/term.py), build/slides/*.png (video/slides.py)
         assets/l1cc_portal.png (screenshot of the LinuxONE Community Cloud instance page)
         docs/results/metrics.json, nnpa_bench.json
Output:  out/scambreaker-z.mp4 + out/scambreaker-z.srt
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

HERE = Path(__file__).resolve().parent
BUILD, OUT, ASSETS = HERE / "build", HERE / "out", HERE / "assets"
RESULTS = HERE.parent / "docs" / "results"
FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H, FPS = 1920, 1080, 30
VENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def ff(*args: str) -> None:
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def duration(path: Path) -> float:
    r = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        raise RuntimeError(f"cannot read duration of {path}")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def words(n: float) -> str:
    return f"{n:,.0f}"


def fill(text: str, live: dict, metrics: dict, nnpa: dict | None) -> str:
    pol = metrics["policy_test"]
    ravi = live.get("ravi") or []
    first = next((i for i, d in enumerate(ravi) if d in ("HOLD", "BLOCK")), None)
    if first is None:
        ravi_text = "Each payment gets a nudge, and the pattern goes to an analyst."
    elif first == 0:
        ravi_text = f"It is {'held' if ravi[0] == 'HOLD' else 'blocked'} from the very first payment."
    else:
        soft = "a gentle nudge" if any(d == "NUDGE" for d in ravi[:first]) else "no friction"
        n = {1: "first payment goes", 2: "first two payments go", 3: "first three payments go"}[first]
        ord_ = {1: "second", 2: "third", 3: "fourth"}[first]
        ravi_text = f"The {n} through with {soft}; the {ord_} is {'held' if ravi[first] == 'HOLD' else 'blocked'}."
    aisha = {"ALLOW": "goes straight through", "NUDGE": "gets only a quick payee check",
             "HOLD": "is held for a call-back, a false alarm we report honestly",
             "BLOCK": "is blocked, a false alarm we report honestly"}.get(live.get("aisha"), "goes through")
    nnpa_text = "The models run on IBM Z, scoring in micro-batches."
    if nnpa:
        res = nnpa.get("results", {})
        dflt = next((v for k, v in res.items() if k != "cpu"), None)
        cpu = res.get("cpu")
        if dflt and cpu and nnpa.get("machine") == "s390x":
            speed = cpu[-1]["us_per_tx"] / dflt[-1]["us_per_tx"]
            if speed >= 1.2:
                nnpa_text = (f"The behaviour model runs on the Telum processor's on-chip AI accelerator, "
                             f"{speed:.1f} times faster than the CPU in our benchmark.")
            else:
                nnpa_text = "The behaviour model is scored on the Telum processor in micro-batches of transfers."
    vals = {
        "tps": words(live.get("tps", 0)), "p99": f"{live.get('p99', 0):.0f}",
        "tan_decision": {"BLOCK": "Blocked", "HOLD": "Held"}.get(live.get("tan_decision"), "Flagged"),
        "tan_ms": f"{live.get('tan_ms', 0):.0f}", "ravi_text": ravi_text, "aisha_text": aisha,
        "nnpa_text": nnpa_text,
        "scam_usd": f"{100 * pol['scam_payments']['usd_share_held']:.0f}",
        "legit_hold": f"{100 * pol['legit']['hold_or_more']:.1f}",
    }
    return text.format(**vals)


def tts(text: str, voice: str, rate: str, stem: Path) -> float:
    subprocess.run([sys.executable, "-m", "edge_tts", "--voice", voice, f"--rate={rate}", "--text", text,
                    "--write-media", str(stem.with_suffix(".mp3")), "--write-subtitles", str(stem.with_suffix(".srt"))],
                   check=True, capture_output=True)
    return duration(stem.with_suffix(".mp3"))


def slide_part(png: Path, d: float, out: Path) -> None:
    ff("-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", str(png),
       "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0b0d10,"
              f"fade=t=in:st=0:d=0.25", *VENC, str(out))


def clip_part(src: Path, s: float, e: float, d: float, out: Path, max_speed: float = 6.0) -> None:
    """Fit footage [s, e] into d seconds: keep the first moment at real speed, compress the rest."""
    length = e - s
    if length <= d:
        ff("-ss", f"{s:.3f}", "-t", f"{length:.3f}", "-i", str(src),
           "-vf", f"fps={FPS},scale={W}:{H},tpad=stop_mode=clone:stop_duration={d - length + 0.05:.3f},fade=t=in:st=0:d=0.2",
           "-t", f"{d:.3f}", "-an", *VENC, str(out))
        return
    head = min(0.25 * d, 3.0)
    rest_src, rest_dst = length - head, d - head
    speed = min(max_speed, rest_src / rest_dst)
    rest_start = e - rest_dst * speed  # if even max speed can't fit, drop the middle, keep the end
    fc = (f"[0:v]trim=start={s:.3f}:end={s + head:.3f},setpts=PTS-STARTPTS,fps={FPS},scale={W}:{H}[a];"
          f"[0:v]trim=start={max(rest_start, s + head):.3f}:end={e:.3f},setpts=(PTS-STARTPTS)/{speed:.4f},fps={FPS},scale={W}:{H}[b];"
          f"[a][b]concat=n=2:v=1:a=0,fade=t=in:st=0:d=0.2[v]")
    ff("-i", str(src), "-filter_complex", fc, "-map", "[v]", "-t", f"{d:.3f}", "-an", *VENC, str(out))


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out))


def srt_merge(items: list[tuple[Path, float]], out: Path) -> None:
    def ts(sec):
        ms = int(round(sec * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    def parse(t):
        h, m, rest = t.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    n, lines = 0, []
    for path, offset in items:
        for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
            rows = block.strip().splitlines()
            if len(rows) < 3:
                continue
            a, b = (parse(x.strip()) for x in rows[1].split("-->"))
            n += 1
            lines.append(f"{n}\n{ts(a + offset)} --> {ts(b + offset)}\n" + "\n".join(rows[2:]) + "\n")
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=RESULTS)
    ap.add_argument("--name", default="scambreaker-z")
    args = ap.parse_args()
    script = json.loads((HERE / "script.json").read_text(encoding="utf-8"))
    live = json.loads((BUILD / "live.json").read_text())
    marks = json.loads((BUILD / "marks.json").read_text())
    metrics = json.loads((args.results / "metrics.json").read_text())
    nnpa = json.loads((args.results / "nnpa_bench.json").read_text()) if (args.results / "nnpa_bench.json").exists() else None
    parts_dir, tts_dir = BUILD / "parts", BUILD / "tts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    tts_dir.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    demo = BUILD / "demo.webm"

    plan, total = [], 0.0
    for seg in script["segments"]:
        text = fill(seg["say"], live, metrics, nnpa)
        dur = tts(text, script["voice"], script["rate"], tts_dir / seg["id"])
        d = max(seg["min_s"], dur + 0.45)
        plan.append((seg, text, d))
        total += d
        print(f"{seg['id']:>10}: {d:5.1f}s  {text}")
    print(f"total {total:.1f}s (limit {script['max_seconds']}s)")
    if total > script["max_seconds"]:
        raise SystemExit("too long - shorten the narration in video/script.json")

    vparts, aparts, srts, t = [], [], [], 0.0
    for seg, _, d in plan:
        kind, ref = seg["visual"].split(":", 1)
        out = parts_dir / f"{seg['id']}.mp4"
        if kind == "slide":
            slide_part(BUILD / "slides" / f"{ref}.png", d, out)
        elif ref == "platform":
            sub = []
            budget = d
            if (ASSETS / "l1cc_portal.png").exists():
                sub.append(("img", ASSETS / "l1cc_portal.png", 3.5))
                budget -= 3.5
            chart = args.results / "nnpa_vs_cpu.png"
            if chart.exists():
                sub.append(("img_end", chart, 4.0))
                budget -= 4.0
            term = BUILD / "term.webm"
            seq = []
            for kind2, path, secs in sub:
                if kind2 == "img":
                    seq.append(("img", path, secs))
            if term.exists():
                seq.append(("clip", term, 0.0, duration(term), budget * 0.6))
                seq.append(("clip", demo, *marks["proof"], budget * 0.4))
            else:
                seq.append(("clip", demo, *marks["proof"], budget))
            for kind2, path, secs in sub:
                if kind2 == "img_end":
                    seq.append(("img", path, secs))
            pieces = []
            for k, item in enumerate(seq):
                po = parts_dir / f"platform_{k}.mp4"
                if item[0] == "img":
                    slide_part(item[1], item[2], po)
                else:
                    clip_part(item[1], item[2], item[3], item[4], po)
                pieces.append(po)
            concat(pieces, out)
        else:
            s, e = marks[ref]
            clip_part(demo, s, e, d, out)
        vparts.append(out)
        wav = tts_dir / f"{seg['id']}.wav"
        ff("-i", str(tts_dir / f"{seg['id']}.mp3"), "-af", f"adelay=150|150,apad=whole_dur={d:.3f}",
           "-t", f"{d:.3f}", "-ar", "48000", "-ac", "2", str(wav))
        aparts.append(wav)
        srts.append((tts_dir / f"{seg['id']}.srt", t + 0.15))
        t += d

    video = BUILD / "video_noaudio.mp4"
    concat(vparts, video)
    audio = BUILD / "narration.wav"
    concat(aparts, audio)
    final = OUT / f"{args.name}.mp4"
    ff("-i", str(video), "-i", str(audio), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
       "-af", f"afade=t=out:st={t - 1.2:.2f}:d=1.2", "-movflags", "+faststart", "-shortest", str(final))
    srt_merge(srts, OUT / f"{args.name}.srt")
    got = duration(final)
    print(f"final {final} duration {got:.1f}s")
    if got > 179.5:
        raise SystemExit("FINAL VIDEO EXCEEDS 3:00")


if __name__ == "__main__":
    main()
