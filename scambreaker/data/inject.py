"""Inject labelled synthetic APP-scam episodes, hard negatives and demo personas into the
real IBM AMLworld stream, and attach app-session signals to every digital transfer.

Why synthetic: banks do not publish victim-side scam data. Episodes are injected into real
AMLworld customer histories and paid into real AMLworld laundering (mule) accounts, so the
payee-side signal stays real. Every injected row is labelled (kind != KIND_ORIG) and all
scam metrics are reported as "on simulated episodes".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from ..features.engine import FMT_CODE
from .events import EVENT_DTYPE, KIND_HARDNEG, KIND_ORIG, KIND_PERSONA, KIND_SCAM

TYP_INDEX = {t: i for i, t in enumerate(config.TYPOLOGIES)}
TYP_WEIGHTS = {"gov_impersonation": 0.25, "investment": 0.25, "job_task": 0.20,
               "romance": 0.15, "ecommerce": 0.15}

# Session-signal distributions. They overlap on purpose so that no single signal decides.
# (p_on_call, p_unknown_caller | on_call, p_remote_access, first-payment payee-added minutes
#  (median, sigma) or None for the legit mixture, session seconds (median, sigma),
#  hesitation z (mean, sd), failed-attempt Poisson rate, p_new_device)
SESSION = {
    "legit": (0.06, 0.25, 0.010, None, (140, 0.6), (0.0, 1.0), 0.08, 0.03),
    "hardneg": (0.10, 0.15, 0.010, None, (260, 0.6), (0.4, 1.0), 0.15, 0.03),
    "gov_impersonation": (0.85, 0.80, 0.30, (4, 0.8), (420, 0.5), (1.1, 1.0), 0.70, 0.12),
    "investment": (0.12, 0.50, 0.06, (45, 1.2), (200, 0.6), (0.35, 1.0), 0.20, 0.04),
    "job_task": (0.08, 0.50, 0.04, (6, 0.9), (180, 0.6), (0.45, 1.0), 0.25, 0.04),
    "romance": (0.18, 0.30, 0.03, (90, 1.2), (240, 0.6), (0.60, 1.0), 0.20, 0.03),
    "ecommerce": (0.07, 0.30, 0.01, (8, 1.0), (160, 0.6), (0.15, 1.0), 0.10, 0.03),
}

# Episode shapes, loosely following SPF typology descriptions (amount = multiple of the
# victim's usual payment). gap = minutes between payments; p_new = chance a follow-up payment
# goes to a new mule; hours = local hours the episode starts in.
EPISODE = {
    "gov_impersonation": dict(n=(1, 3), amount=("flat", 8.0, 0.6), gap=(5, 90), p_new=0.4,
                              fmts={"ACH": 0.6, "Wire": 0.4}, hours=(9, 17)),
    "investment": dict(n=(3, 8), amount=("escalate", (0.3, 1.0), (1.3, 2.2)), gap=(120, 1440),
                       p_new=0.25, fmts={"Bitcoin": 0.4, "ACH": 0.4, "Wire": 0.2}, hours=(10, 22)),
    "job_task": dict(n=(4, 12), amount=("escalate", (0.05, 0.3), (1.2, 1.8)), gap=(10, 120),
                     p_new=0.6, fmts={"ACH": 1.0}, hours=(18, 22)),
    "romance": dict(n=(1, 4), amount=("flat", 3.0, 0.5), gap=(60, 1440), p_new=0.2,
                    fmts={"ACH": 0.7, "Wire": 0.3}, hours=(19, 22)),
    "ecommerce": dict(n=(1, 1), amount=("flat", 0.8, 0.5), gap=(0, 0), p_new=1.0,
                      fmts={"ACH": 1.0}, hours=(10, 21)),
}

# Fictional demo customers (histories only; their scenario transfers are triggered live).
PERSONAS = [
    dict(slug="tan", key="DEMO:TAN-0072", name="Mdm Tan", age=72, lang="zh", every_min=2 * 1440,
         amt_sgd=(50, 300), n_payees=3, hours=(9, 18), contact="Grace (daughter)"),
    dict(slug="ravi", key="DEMO:RAVI-0034", name="Ravi", age=34, lang="en", every_min=720,
         amt_sgd=(20, 400), n_payees=6, hours=(8, 22), contact="Priya (sister)"),
    dict(slug="aisha", key="DEMO:AISHA-0045", name="Aisha", age=45, lang="en", every_min=1440,
         amt_sgd=(100, 1500), n_payees=4, hours=(8, 21), contact="Omar (husband)"),
]


def _periods(last_day: int) -> dict[str, tuple[int, int]]:
    return {"train": config.TRAIN_DAYS, "val": config.VAL_DAYS,
            "test": (config.TEST_DAYS[0], min(config.TEST_DAYS[0] + 1, last_day))}


def _lognormal(rng, median, sigma, size=None):
    return rng.lognormal(np.log(median), sigma, size=size)


def _rows(n: int) -> np.ndarray:
    r = np.zeros(n, dtype=EVENT_DTYPE)
    r["typ"] = -1
    r["ep"] = -1
    return r


def _fill_time(r: np.ndarray) -> None:
    r["hour"] = (r["t"] % 1440) // 60
    r["day"] = r["t"] // 1440


def _scam_episode(rng, typ, victim, mules, m, period, ep_id, banks):
    spec = EPISODE[typ]
    n = int(rng.integers(spec["n"][0], spec["n"][1] + 1))
    kind = spec["amount"][0]
    if kind == "flat":
        amts = m * _lognormal(rng, spec["amount"][1], spec["amount"][2], size=n)
    else:
        a0 = m * rng.uniform(*spec["amount"][1])
        growth = rng.uniform(*spec["amount"][2], size=n - 1)
        amts = a0 * np.concatenate([[1.0], np.cumprod(growth)])
    amts = np.clip(amts, 5.0, 250_000.0)

    gaps = rng.uniform(spec["gap"][0], spec["gap"][1], size=n - 1) if n > 1 else np.zeros(0)
    d_lo, d_hi = period
    avail = (d_hi - d_lo + 1) * 1440 - 180
    if gaps.sum() > 0.8 * avail:
        gaps *= 0.8 * avail / gaps.sum()
    span = float(gaps.sum())
    h_lo, h_hi = spec["hours"]
    t0 = d_lo * 1440 + 60
    for _ in range(30):
        day = int(rng.integers(d_lo, d_hi + 1))
        cand = day * 1440 + int(rng.integers(h_lo * 60, h_hi * 60 + 60))
        if cand + span <= (d_hi + 1) * 1440 - 1:
            t0 = cand
            break
    ts = (t0 + np.concatenate([[0.0], np.cumsum(gaps)])).astype(np.int32)

    r = _rows(n)
    r["t"] = ts
    r["src"] = victim
    payee = int(rng.choice(mules))
    p_call, p_unknown, p_remote, first_added, sess, hes, fail, p_dev = SESSION[typ]
    first_seen: dict[int, tuple[int, float]] = {}
    fmts = rng.choice(list(spec["fmts"]), p=list(spec["fmts"].values()), size=n)
    for i in range(n):
        if i > 0 and rng.random() < spec["p_new"]:
            payee = int(rng.choice(mules))
        r["dst"][i] = payee
        r["fmt"][i] = FMT_CODE[fmts[i]]
        r["cross_cur"][i] = 1 if fmts[i] == "Bitcoin" else int(fmts[i] == "Wire" and rng.random() < 0.25)
        if payee not in first_seen:
            first_seen[payee] = (int(ts[i]), float(_lognormal(rng, *first_added)))
            r["payee_added_min"][i] = first_seen[payee][1]
        else:
            t_first, added = first_seen[payee]
            r["payee_added_min"][i] = added + (int(ts[i]) - t_first)
        on_call = rng.random() < p_call
        r["on_call"][i] = on_call
        r["unknown_caller"][i] = on_call and rng.random() < p_unknown
        r["remote_access"][i] = rng.random() < p_remote
        r["session_s"][i] = _lognormal(rng, *sess)
        r["hesitation"][i] = rng.normal(*hes)
        r["failed"][i] = min(rng.poisson(fail), 9)
        r["new_device"][i] = rng.random() < p_dev
    r["amt"] = amts.astype(np.float32)
    r["same_bank"] = banks[victim] == banks[r["dst"]]
    r["is_scam"] = 1
    r["typ"] = TYP_INDEX[typ]
    r["ep"] = ep_id
    r["kind"] = KIND_SCAM
    r["digital"] = 1
    _fill_time(r)
    return r


def _hard_negative(rng, victim, payee, m, period, banks):
    r = _rows(1)
    d_lo, d_hi = period
    day = int(rng.integers(d_lo, d_hi + 1))
    r["t"] = day * 1440 + int(rng.integers(8 * 60, 22 * 60))
    r["src"] = victim
    r["dst"] = payee
    r["amt"] = np.float32(min(m * _lognormal(rng, 8.0, 0.6), 250_000.0))
    fmt = "ACH" if rng.random() < 0.7 else "Wire"
    r["fmt"] = FMT_CODE[fmt]
    r["same_bank"] = banks[victim] == banks[payee]
    r["kind"] = KIND_HARDNEG
    r["digital"] = 1
    _fill_time(r)
    return r


def _persona_history(rng, p, acct_id, payees, last_hist_day):
    rows = []
    t = int(rng.integers(6 * 60, 12 * 60))
    while t < (last_hist_day + 1) * 1440:
        h_lo, h_hi = p["hours"]
        t_day = (t // 1440) * 1440 + int(rng.integers(h_lo * 60, h_hi * 60))
        r = _rows(1)
        r["t"] = t_day
        r["src"] = acct_id
        r["dst"] = int(rng.choice(payees))
        r["amt"] = np.float32(rng.uniform(*p["amt_sgd"]) / config.SGD_PER_USD)
        r["fmt"] = FMT_CODE["ACH"]
        r["same_bank"] = 0
        r["kind"] = KIND_PERSONA
        r["digital"] = 1
        rows.append(r)
        t += int(p["every_min"] * rng.uniform(0.6, 1.4))
    out = np.concatenate(rows)
    _fill_time(out)
    return out


def _session_legit(rng, ev: np.ndarray, idx: np.ndarray, first_pair: np.ndarray, kind: str) -> None:
    """Vectorised legit session signals for rows `idx`."""
    n = len(idx)
    if n == 0:
        return
    p_call, p_unknown, p_remote, _, sess, hes, fail, p_dev = SESSION[kind]
    on_call = rng.random(n) < p_call
    ev["on_call"][idx] = on_call
    ev["unknown_caller"][idx] = on_call & (rng.random(n) < p_unknown)
    ev["remote_access"][idx] = rng.random(n) < p_remote
    ev["session_s"][idx] = _lognormal(rng, sess[0], sess[1], size=n)
    ev["hesitation"][idx] = rng.normal(hes[0], hes[1], size=n)
    ev["failed"][idx] = np.minimum(rng.poisson(fail, size=n), 9)
    ev["new_device"][idx] = rng.random(n) < p_dev
    fp = first_pair[idx]
    p_quick = 0.5 if kind == "legit" else 0.6
    quick = rng.random(n) < p_quick
    new_added = np.where(quick, _lognormal(rng, 3 if kind == "legit" else 5, 0.85, size=n),
                         _lognormal(rng, 3000 if kind == "legit" else 2000, 1.2, size=n))
    known_added = _lognormal(rng, 20000, 1.0, size=n)
    ev["payee_added_min"][idx] = np.where(fp, new_added, known_added)


def augment(ev0: np.ndarray, keys0: np.ndarray, n_train_episodes: int | None = None,
            seed: int = config.SEED):
    rng = np.random.default_rng(seed)
    n_acct = len(keys0)
    last_day = int(ev0["day"].max())
    periods = _periods(last_day)

    src, dst = ev0["src"], ev0["dst"]
    out_dig = ev0["digital"].astype(bool) & (src != dst)
    n_out_dig = np.bincount(src[out_dig], minlength=n_acct)
    med = pd.Series(ev0["amt"][out_dig]).groupby(src[out_dig]).median()
    med_amt = np.zeros(n_acct)
    med_amt[med.index.to_numpy()] = med.to_numpy()
    laund = ev0["is_laund"].astype(bool)
    involved = np.zeros(n_acct, dtype=bool)
    involved[src[laund]] = True
    involved[dst[laund]] = True
    retail = (n_out_dig >= 1) & (n_out_dig <= 300)
    victim_pool = np.flatnonzero(retail & (n_out_dig >= 3) & ~involved & (med_amt >= 10) & (med_amt <= 20_000))
    n_in_legit = np.bincount(dst[out_dig & ~laund], minlength=n_acct)
    legit_payees = np.flatnonzero((n_in_legit >= 5) & ~involved)
    if len(victim_pool) < 50 or len(legit_payees) < 10:
        raise ValueError(f"pools too small: victims={len(victim_pool)} legit_payees={len(legit_payees)}")

    bank_of = np.array([k.split(":", 1)[0] for k in keys0], dtype=object)
    banks = np.concatenate([bank_of, np.array(["DEMO"] * len(PERSONAS), dtype=object)])

    if n_train_episodes is None:
        n_train_episodes = int(min(3000, max(150, len(victim_pool) // 12)))
    n_eval = max(60, n_train_episodes // 3)
    counts = {"train": n_train_episodes, "val": n_eval, "test": n_eval}
    n_hn = {k: int(0.6 * v) for k, v in counts.items()}
    need = sum(counts.values()) + sum(n_hn.values())
    if need > len(victim_pool):
        scale = len(victim_pool) / need * 0.95
        counts = {k: max(10, int(v * scale)) for k, v in counts.items()}
        n_hn = {k: int(0.6 * v) for k, v in counts.items()}
    victims = rng.permutation(victim_pool)

    typs = list(TYP_WEIGHTS)
    probs = np.array([TYP_WEIGHTS[t] for t in typs])
    parts, ep_id, vi = [], 0, 0
    for split, (d_lo, d_hi) in periods.items():
        in_period = laund & (ev0["day"] >= d_lo) & (ev0["day"] <= d_hi)
        mule_counts = np.bincount(dst[in_period], minlength=n_acct)
        mules = np.flatnonzero(mule_counts > 0)
        if len(mules) == 0:
            raise ValueError(f"no laundering receivers in {split} period {d_lo}-{d_hi}")
        for _ in range(counts[split]):
            v = int(victims[vi]); vi += 1
            typ = typs[int(rng.choice(len(typs), p=probs))]
            m = float(np.clip(med_amt[v], 30.0, 3000.0))
            parts.append(_scam_episode(rng, typ, v, mules, m, (d_lo, d_hi), ep_id, banks))
            ep_id += 1
        for _ in range(n_hn[split]):
            v = int(victims[vi]); vi += 1
            m = float(np.clip(med_amt[v], 30.0, 3000.0))
            parts.append(_hard_negative(rng, v, int(rng.choice(legit_payees)), m, (d_lo, d_hi), banks))

    persona_meta = []
    for j, p in enumerate(PERSONAS):
        acct_id = n_acct + j
        payees = rng.choice(legit_payees, size=p["n_payees"], replace=False)
        parts.append(_persona_history(rng, p, acct_id, payees, config.TEST_DAYS[0] - 1))
        persona_meta.append({**{k: p[k] for k in ("slug", "key", "name", "age", "lang", "contact")},
                             "id": int(acct_id)})
    keys = np.concatenate([keys0.astype(object), np.array([p["key"] for p in PERSONAS], dtype=object)])

    inj = np.concatenate(parts)
    ev = np.concatenate([ev0, inj])
    order = np.lexsort((ev["kind"], ev["t"]))  # time, then original rows before injected ones
    ev = ev[order]

    first_pair = ~pd.DataFrame({"s": ev["src"], "d": ev["dst"]}).duplicated().to_numpy()
    dig = ev["digital"].astype(bool)
    kinds = ev["kind"]
    _session_legit(rng, ev, np.flatnonzero(dig & ((kinds == KIND_ORIG) | (kinds == KIND_PERSONA))), first_pair, "legit")
    _session_legit(rng, ev, np.flatnonzero(dig & (kinds == KIND_HARDNEG)), first_pair, "hardneg")

    retail_all = np.concatenate([retail, np.ones(len(PERSONAS), dtype=bool)])
    ev["pool"] = retail_all[ev["src"]] & dig & (ev["src"] != ev["dst"])

    # Scenario payees: busiest real mules in the test period, and a busy legit receiver.
    t_lo, t_hi = periods["test"]
    in_test = (ev["day"] >= t_lo) & (ev["day"] <= t_hi) & (ev["kind"] == KIND_ORIG)
    mule_in = np.bincount(ev["dst"][in_test & (ev["is_laund"] == 1)], minlength=len(keys))
    top_mules = np.argsort(-mule_in)[:2]
    legit_in = np.bincount(ev["dst"][in_test & (ev["is_laund"] == 0)], minlength=len(keys))
    legit_in[~np.isin(np.arange(len(keys)), legit_payees)] = 0
    scenario_payees = {"tan": int(top_mules[0]), "ravi": int(top_mules[1]),
                       "aisha": int(np.argmax(legit_in))}

    meta = {
        "periods": periods,
        "n_episodes": int(ep_id),
        "episodes_by_split": counts,
        "n_scam_payments": int((ev["kind"] == KIND_SCAM).sum()),
        "n_hardneg": int((ev["kind"] == KIND_HARDNEG).sum()),
        "n_persona_rows": int((ev["kind"] == KIND_PERSONA).sum()),
        "n_retail_accounts": int(retail_all.sum()),
        "n_victim_pool": int(len(victim_pool)),
        "n_legit_payees": int(len(legit_payees)),
        "personas": persona_meta,
        "scenario_payees": scenario_payees,
        "seed": seed,
    }
    return ev, keys.astype(str), retail_all, meta
