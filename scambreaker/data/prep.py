"""Load IBM AMLworld transactions, normalise currencies and build the event stream.

Usage (paths are arguments; the raw CSV never lives inside the repo):
    python -I -m scambreaker.data.prep --csv /secure/data/HI-Small_Trans.csv --out /secure/work/data
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from ..features.engine import FMT_CODE
from . import inject
from .events import EVENT_DTYPE, KIND_ORIG, save_stream

RAW_COLS = ["ts", "from_bank", "from_acct", "to_bank", "to_acct", "amt_recv", "cur_recv",
            "amt_paid", "cur_paid", "fmt", "is_laund"]

# Used only if a currency never trades against US Dollar in the file (approx. Sep 2022).
FALLBACK_USD = {
    "Euro": 1.0, "UK Pound": 1.15, "Yen": 0.0070, "Yuan": 0.145, "Rupee": 0.0125,
    "Ruble": 0.0165, "Canadian Dollar": 0.76, "Australian Dollar": 0.68, "Swiss Franc": 1.03,
    "Shekel": 0.29, "Mexican Peso": 0.05, "Brazil Real": 0.19, "Saudi Riyal": 0.266,
    "Bitcoin": 20000.0,
}


def read_raw(csv_path: Path, nrows: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(
        csv_path, header=0, names=RAW_COLS, nrows=nrows,
        dtype={"from_bank": str, "from_acct": str, "to_bank": str, "to_acct": str,
               "cur_recv": str, "cur_paid": str, "fmt": str,
               "amt_recv": "float64", "amt_paid": "float64", "is_laund": "int8"},
    )
    df["ts"] = pd.to_datetime(df["ts"], format="%Y/%m/%d %H:%M")
    return df


def usd_rates(df: pd.DataFrame) -> dict[str, float]:
    """USD per unit of each currency, derived from the file's own cross-currency trades."""
    rates: dict[str, float] = {"US Dollar": 1.0}
    m = (df["cur_paid"] == "US Dollar") & (df["cur_recv"] != "US Dollar") & (df["amt_recv"] > 0)
    sub = df.loc[m]
    rates.update((sub["amt_paid"] / sub["amt_recv"]).groupby(sub["cur_recv"]).median().to_dict())
    m = (df["cur_recv"] == "US Dollar") & (df["cur_paid"] != "US Dollar") & (df["amt_paid"] > 0)
    sub = df.loc[m]
    for cur, r in (sub["amt_recv"] / sub["amt_paid"]).groupby(sub["cur_paid"]).median().items():
        rates.setdefault(cur, float(r))
    for cur, r in FALLBACK_USD.items():
        rates.setdefault(cur, r)
    return {k: float(v) for k, v in rates.items()}


def build_events(df: pd.DataFrame):
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    n = len(df)
    rates = usd_rates(df)
    unknown = set(df["cur_paid"].unique()) - set(rates)
    if unknown:
        raise ValueError(f"No USD rate for currencies: {unknown}")
    amt_usd = df["amt_paid"].to_numpy() * df["cur_paid"].map(rates).to_numpy(dtype=np.float64)

    src_key = df["from_bank"].astype(str) + ":" + df["from_acct"].astype(str)
    dst_key = df["to_bank"].astype(str) + ":" + df["to_acct"].astype(str)
    codes, uniques = pd.factorize(pd.concat([src_key, dst_key], ignore_index=True), sort=False)

    fmt_codes = df["fmt"].map(FMT_CODE)
    if fmt_codes.isna().any():
        raise ValueError(f"Unknown payment formats: {set(df.loc[fmt_codes.isna(), 'fmt'])}")

    day0 = df["ts"].iloc[0].normalize()
    t = ((df["ts"] - day0) // pd.Timedelta(minutes=1)).to_numpy().astype(np.int32)

    ev = np.zeros(n, dtype=EVENT_DTYPE)
    ev["t"] = t
    ev["src"] = codes[:n]
    ev["dst"] = codes[n:]
    ev["amt"] = np.maximum(amt_usd, 0.0).astype(np.float32)
    ev["fmt"] = fmt_codes.to_numpy().astype(np.int8)
    ev["same_bank"] = (df["from_bank"] == df["to_bank"]).to_numpy()
    ev["cross_cur"] = (df["cur_paid"] != df["cur_recv"]).to_numpy()
    ev["hour"] = df["ts"].dt.hour.to_numpy()
    ev["day"] = t // 1440
    ev["is_laund"] = df["is_laund"].to_numpy()
    ev["typ"] = -1
    ev["ep"] = -1
    ev["kind"] = KIND_ORIG
    ev["digital"] = np.isin(ev["fmt"], [FMT_CODE[f] for f in config.DIGITAL_FORMATS])
    keys = np.asarray(uniques, dtype=object).astype(str)
    meta = {"day0": str(day0.date()), "usd_rates": rates, "n_raw": n,
            "n_days": int(ev["day"].max()) + 1}
    return ev, keys, meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=config.DATA_DIR)
    ap.add_argument("--nrows", type=int, default=None)
    ap.add_argument("--episodes", type=int, default=None,
                    help="scam episodes in the training period (default scales with data)")
    args = ap.parse_args()

    t0 = time.time()
    df = read_raw(args.csv, args.nrows)
    print(f"read {len(df):,} rows in {time.time() - t0:.1f}s; "
          f"laundering={int(df['is_laund'].sum()):,}; range {df['ts'].min()} .. {df['ts'].max()}")
    ev0, keys0, meta = build_events(df)
    del df
    print(f"events built: {len(ev0):,} rows, {len(keys0):,} accounts, {meta['n_days']} days "
          f"({time.time() - t0:.1f}s)")
    ev, keys, retail, inj_meta = inject.augment(ev0, keys0, n_train_episodes=args.episodes)
    meta.update(inj_meta)
    save_stream(args.out, ev, keys, retail, meta)
    print(f"saved {len(ev):,} events to {args.out} ({time.time() - t0:.1f}s)")
    for k in ("n_episodes", "n_scam_payments", "n_hardneg", "n_persona_rows", "n_retail_accounts"):
        print(f"  {k}: {meta.get(k)}")


if __name__ == "__main__":
    main()
