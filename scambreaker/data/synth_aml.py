"""Generate a small CSV in the exact IBM AMLworld schema, for tests and local development.

It is NOT used for any reported result; real results come from IBM's HI-Small file.
    python -m scambreaker.data.synth_aml --out work/synth.csv
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

HEADER = ("Timestamp,From Bank,Account,To Bank,Account,Amount Received,Receiving Currency,"
          "Amount Paid,Payment Currency,Payment Format,Is Laundering")


def generate(n_people=2500, n_companies=400, n_mules=120, n_banks=15, days=10, seed=7):
    rng = np.random.default_rng(seed)
    day0 = datetime(2022, 9, 1)
    banks = rng.integers(1, n_banks + 1, size=n_people + n_companies + n_mules)
    ids = [f"{0x80000000 + i:X}" for i in range(len(banks))]
    people = np.arange(n_people)
    companies = np.arange(n_people, n_people + n_companies)
    mules = np.arange(n_people + n_companies, len(banks))
    scale = rng.lognormal(np.log(80), 0.9, size=len(banks))
    rows = []

    def add(minute, s, d, amt, fmt, laund=0, cur="US Dollar"):
        ts = (day0 + timedelta(minutes=int(minute))).strftime("%Y/%m/%d %H:%M")
        amt = round(float(amt), 2)
        rows.append(f"{ts},{banks[s]:03d},{ids[s]},{banks[d]:03d},{ids[d]},{amt},{cur},{amt},{cur},{fmt},{laund}")

    payees = {p: rng.choice(np.concatenate([companies, people]), size=rng.integers(2, 7), replace=False)
              for p in people}
    for day in range(days):
        base = day * 1440
        for c in companies:  # payroll on day 0 and day 5, plus B2B wires
            if day in (0, 5):
                for p in rng.choice(people, size=4, replace=False):
                    add(base + rng.integers(480, 600), c, p, scale[p] * 20, "ACH")
            if rng.random() < 0.5:
                add(base + rng.integers(0, 1440), c, rng.choice(companies), scale[c] * 30, "Wire")
            if rng.random() < 0.3:
                add(base + rng.integers(0, 1440), c, c, scale[c] * 10, "Reinvestment")
        for p in people:
            for _ in range(rng.poisson(0.7)):
                fmt = rng.choice(["ACH", "Credit Card", "Cheque", "Wire", "Cash"], p=[0.55, 0.3, 0.05, 0.05, 0.05])
                add(base + rng.integers(360, 1440), p, rng.choice(payees[p]),
                    scale[p] * rng.lognormal(0, 0.6), fmt)
        for m in mules:  # mules also look normal some of the time
            if rng.random() < 0.4:
                add(base + rng.integers(0, 1440), m, rng.choice(companies), scale[m], "ACH")
        for _ in range(3):  # laundering: fan-in to a mule, then scatter onward
            mule = rng.choice(mules)
            t = base + rng.integers(0, 1200)
            srcs = rng.choice(np.concatenate([people, companies]), size=rng.integers(3, 7), replace=False)
            total = 0.0
            for s in srcs:
                a = rng.uniform(800, 9000)
                total += a
                add(t + rng.integers(0, 120), s, mule, a, rng.choice(["ACH", "Wire", "Bitcoin"]), 1)
            for d in rng.choice(mules, size=rng.integers(2, 5), replace=False):
                add(t + rng.integers(150, 360), mule, d, total / 4, rng.choice(["Wire", "ACH"]), 1)
    rows.sort()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--people", type=int, default=2500)
    args = ap.parse_args()
    rows = generate(n_people=args.people)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(HEADER + "\n" + "\n".join(rows) + "\n")
    print(f"wrote {len(rows):,} rows to {args.out}")


if __name__ == "__main__":
    main()
