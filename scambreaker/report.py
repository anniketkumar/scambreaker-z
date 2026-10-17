"""Render result charts (docs/results/*.png) from metrics.json and the benchmark files.

    python -m scambreaker.report --results docs/results
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from . import config  # noqa: E402

for _f in (Path(__file__).parent / "assets" / "fonts").glob("*.ttf"):
    font_manager.fontManager.addfont(str(_f))

SURFACE, PAGE = "#14171c", "#0b0d10"
INK, INK2, MUTED, GRID, AXIS = "#f2f4f8", "#c1c7d0", "#8d96a5", "#262b33", "#383835"
S1, S2 = "#3987e5", "#d95926"  # validated categorical slots 1-2 (dark)
plt.rcParams.update({
    "font.family": ["IBM Plex Sans", "Segoe UI", "DejaVu Sans"], "font.size": 12,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 1, "grid.linestyle": "-",
    "axes.spines.top": False, "axes.spines.right": False, "text.color": INK,
})


def _title(ax, title, sub=None):
    ax.set_title(title, loc="left", fontsize=15.5, fontweight="normal", color=INK, pad=22 if sub else 10)
    if sub:
        ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=11, color=MUTED, va="bottom")


def _hbar(ax, labels, values, highlight, fmt="{:.0%}", xmax=1.0):
    ax.set_axisbelow(True)
    y = range(len(labels))
    colors = [S1 if lab == highlight else "#4a5160" for lab in labels]
    ax.barh(list(y), values, height=0.55, color=colors)
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    ax.set_xlim(0, xmax * 1.12)
    ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    for i, v in enumerate(values):
        ax.text(v + xmax * 0.01, i, fmt.format(v), va="center", fontsize=11, color=INK if labels[i] == highlight else INK2)


def ablation(m: dict, out: Path) -> None:
    ab = m["ablation_test"]
    names = {"A": "A payee risk", "B": "B behaviour", "C": "C coercion"}
    rows = sorted(((k, v["scam_usd_share_held"] or 0.0) for k, v in ab.items()), key=lambda kv: -kv[1])
    labels = [" + ".join(names[x].split()[0] for x in k.split("+")) if "+" in k else names[k] for k, _ in rows]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    _hbar(ax, labels, [v for _, v in rows], highlight="A + B + C")
    _title(ax, "Scam money held, by model combination",
           f"test days · each combination held to the same friction budget (≤{config.BUDGET_HOLD:.1%} of legit payments)")
    fig.tight_layout()
    fig.savefig(out / "ablation.png", dpi=160)
    plt.close(fig)


def typology(m: dict, out: Path) -> None:
    by = m["policy_test"]["by_typology_hold_or_more"]
    rows = sorted(by.items(), key=lambda kv: -kv[1])
    labels = [config.TYPOLOGY_LABELS[k] for k, _ in rows]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    _hbar(ax, labels, [v for _, v in rows], highlight=None)
    _title(ax, "Scam payments held, by scam type", "test days · share of simulated scam payments that were held or blocked")
    fig.tight_layout()
    fig.savefig(out / "typology.png", dpi=160)
    plt.close(fig)


def model_a_pr(m: dict, out: Path) -> None:
    a = m["model_a_test_real_labels"]
    pr = a.get("pr_curve")
    if not pr:
        return
    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    ax.plot(pr["recall"], pr["precision"], color=S1, lw=2, solid_capstyle="round")
    ax.axhline(a["base_rate"], color=MUTED, lw=1)
    ax.text(0.99, a["base_rate"], f"  base rate {a['base_rate']:.2%}", color=MUTED, fontsize=10, ha="right", va="bottom")
    ax.set_xlabel("recall")
    ax.set_ylabel("precision")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    _title(ax, f"Payee-risk model on real IBM AMLworld labels · AUPRC {a['auprc']:.2f}",
           f"held-out test days · {a['positives']:,} laundering transfers in {a['n']:,}")
    fig.tight_layout()
    fig.savefig(out / "model_a_pr.png", dpi=160)
    plt.close(fig)


def nnpa(bench: dict, out: Path) -> None:
    res = bench["results"]
    keys = [k for k in res if k != "cpu"]
    if not keys or "cpu" not in res:
        return
    fig, ax = plt.subplots(figsize=(9, 4.8))
    on_z = bench.get("machine") == "s390x"
    series = [(keys[0], "default placement (Telum NNPA)" if on_z else "default placement", S1),
              ("cpu", "CPU only (pinned)", S2)]
    for key, label, color in series:
        b = [r["batch"] for r in res[key]]
        us = [r["us_per_tx"] for r in res[key]]
        ax.plot(b, us, color=color, lw=2, marker="o", ms=6, mec=SURFACE, mew=2, label=label)
        ax.text(b[-1] * 1.12, us[-1], f"{us[-1]:.1f} µs", color=INK2, va="center", fontsize=11)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    batches = [r["batch"] for r in res[keys[0]]]
    ax.set_xticks(batches, [str(b) for b in batches])
    ax.minorticks_off()
    ax.set_xlabel("micro-batch size (transfers scored per call)")
    ax.set_ylabel("µs per transfer")
    ax.legend(frameon=False, labelcolor=INK2, loc="upper right")
    acc, cpu = res[keys[0]][-1]["us_per_tx"], res["cpu"][-1]["us_per_tx"]
    verdict = (f"default placement is {cpu / acc:.1f}x faster than CPU at batch {batches[-1]}" if acc < cpu
               else f"CPU is {acc / cpu:.1f}x faster than default placement at batch {batches[-1]}")
    _title(ax, "LSTM scoring cost per transfer: accelerator vs CPU",
           f"same model, same inputs · {bench.get('machine', '')} · {verdict}")
    fig.tight_layout()
    fig.savefig(out / "nnpa_vs_cpu.png", dpi=160)
    plt.close(fig)


def e2e(bench: dict, out: Path) -> None:
    steps = bench.get("steps") or []
    if not steps:
        return
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    x = [s["achieved_tps"] for s in steps]
    y = [s["p99_ms"] for s in steps]
    ax.plot(x, y, color=S1, lw=2, marker="o", ms=6, mec=SURFACE, mew=2)
    i = max(range(len(x)), key=lambda k: x[k])
    ax.annotate(f"{x[i]:,.0f} tx/s · p99 {y[i]:.1f} ms", (x[i], y[i]), textcoords="offset points", xytext=(-10, 12),
                ha="right", color=INK, fontsize=11)
    ax.set_xlabel("sustained throughput (tx/s, one scorer)")
    ax.set_ylabel("p99 latency, bus → decision (ms)")
    ax.set_ylim(0, max(y) * 1.35)
    _title(ax, "End-to-end: throughput vs tail latency", "live stack on LinuxONE · replay rate stepped up every 35 s")
    fig.tight_layout()
    fig.savefig(out / "e2e.png", dpi=160)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=config.RESULTS_DIR)
    args = ap.parse_args()
    r = args.results
    m = json.loads((r / "metrics.json").read_text())
    ablation(m, r)
    typology(m, r)
    model_a_pr(m, r)
    for name, fn in (("nnpa_bench.json", nnpa), ("e2e_bench.json", e2e)):
        p = r / name
        if p.exists():
            fn(json.loads(p.read_text()), r)
    print("charts written to", r)


if __name__ == "__main__":
    main()
