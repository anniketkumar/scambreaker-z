"""Streaming, point-in-time feature engine.

The same code path is used for offline training (replaying the full history) and for
online scoring, so there is no train/serve skew. For every event the engine computes
features from state that only contains *earlier* events, and only then updates state.
"""
from __future__ import annotations

import math
from collections import deque

from .. import config

FMT_NAMES = config.PAYMENT_FORMATS
N_FMT = len(FMT_NAMES)
FMT_CODE = {name: i for i, name in enumerate(FMT_NAMES)}
_FMT_ONEHOT = tuple(tuple(1.0 if j == i else 0.0 for j in range(N_FMT)) for i in range(N_FMT))

LOG_NEVER = math.log1p(1e5)  # sentinel for "never happened" time gaps (~69 days)
_TWO_PI = 2.0 * math.pi
_HOUR_SIN = tuple(math.sin(_TWO_PI * h / 24.0) for h in range(24))
_HOUR_COS = tuple(math.cos(_TWO_PI * h / 24.0) for h in range(24))

A_FEATURES = (
    "amt_log",
    *(f"fmt_{n}" for n in FMT_NAMES),
    "same_bank", "cross_cur", "self_tx", "hour_sin", "hour_cos", "round_amt",
    "s_age_log", "s_out_cnt", "s_out_amt_log", "s_in_cnt", "s_in_amt_log", "s_out_peers",
    "s_since_out_log", "s_since_in_log", "s_pass", "s_amt_ratio_log", "s_new_payee", "s_n_out_log",
    "r_age_log", "r_in_cnt", "r_in_amt_log", "r_in_peers", "r_out_cnt", "r_out_amt_log",
    "r_pass", "r_since_in_log", "r_since_out_log", "r_n_in_log", "r_in_cross_frac", "r_new_acct",
)
N_A = len(A_FEATURES)

SEQ_FEATURES = (
    "valid", "amt_log", "amt_ratio_log", "new_payee", "since_prev_log", "hour_sin", "hour_cos",
    *(f"fmt_{n}" for n in FMT_NAMES),
    "cross_bank", "cross_cur", "same_payee_prev", "escalation_log",
)
N_SEQ = len(SEQ_FEATURES)
_PAD = (0.0,) * N_SEQ


class _Acct:
    """Per-account state. Only earlier events are ever stored here."""

    __slots__ = (
        "first_t", "in_q", "in_amt", "in_cross", "in_peers", "out_q", "out_amt", "out_peers",
        "last_in_t", "last_out_t", "n_in", "n_out", "payees", "seq", "amts", "last_dst",
    )

    def __init__(self, t: int):
        self.first_t = t
        self.in_q: deque = deque()
        self.in_amt = 0.0
        self.in_cross = 0
        self.in_peers: dict[int, int] = {}
        self.out_q: deque = deque()
        self.out_amt = 0.0
        self.out_peers: dict[int, int] = {}
        self.last_in_t = -1
        self.last_out_t = -1
        self.n_in = 0
        self.n_out = 0
        self.payees: dict[int, float] = {}  # payee -> last amount sent (LRU, bounded)
        self.seq: deque = deque(maxlen=config.SEQ_LEN - 1)  # previous outgoing step vectors
        self.amts: deque = deque(maxlen=config.SEQ_LEN)  # previous outgoing amounts
        self.last_dst = -1


def _since_log(t: int, last: int) -> float:
    if last < 0:
        return LOG_NEVER
    return math.log1p(t - last) if t > last else 0.0


class FeatureEngine:
    def __init__(self, window_min: int = config.WINDOW_MIN, payee_memory: int = config.PAYEE_MEMORY):
        self.window = window_min
        self.payee_memory = payee_memory
        self.acc: dict[int, _Acct] = {}
        self.n_events = 0

    # ------------------------------------------------------------------ state
    def _get(self, key: int, t: int) -> tuple[_Acct, bool]:
        a = self.acc.get(key)
        if a is None:
            a = _Acct(t)
            self.acc[key] = a
            return a, True
        return a, False

    def _expire(self, a: _Acct, t: int) -> None:
        lim = t - self.window
        q = a.in_q
        while q and q[0][0] <= lim:
            _, amt, peer, cross = q.popleft()
            a.in_amt -= amt
            a.in_cross -= cross
            c = a.in_peers[peer] - 1
            if c:
                a.in_peers[peer] = c
            else:
                del a.in_peers[peer]
        q = a.out_q
        while q and q[0][0] <= lim:
            _, amt, peer = q.popleft()
            a.out_amt -= amt
            c = a.out_peers[peer] - 1
            if c:
                a.out_peers[peer] = c
            else:
                del a.out_peers[peer]

    # ---------------------------------------------------------------- process
    def process(self, t: int, src: int, dst: int, amt: float, fmt: int, same_bank: bool,
                cross_cur: bool, hour: int, want_a: bool = True, want_seq: bool = True):
        """Score-then-update one transfer.

        Returns (features_a | None, sequence | None, step) where `sequence` is a list of
        SEQ_LEN step tuples (front-padded) ending with the current transfer.
        """
        self.n_events += 1
        s, _ = self._get(src, t)
        r, r_new = self._get(dst, t)
        self._expire(s, t)
        if r is not s:
            self._expire(r, t)
        self_tx = src == dst

        amt_log = math.log1p(amt)
        if s.amts:
            srt = sorted(s.amts)
            median_prev = srt[len(srt) // 2]
            amt_ratio_log = math.log((amt + 1.0) / (median_prev + 1.0))
        else:
            amt_ratio_log = 0.0
        prev_to_dst = s.payees.get(dst)
        new_payee = prev_to_dst is None and not self_tx
        esc_log = math.log((amt + 1.0) / (prev_to_dst + 1.0)) if prev_to_dst is not None else 0.0
        since_out = _since_log(t, s.last_out_t)
        hs = _HOUR_SIN[hour]
        hc = _HOUR_COS[hour]
        fmt_oh = _FMT_ONEHOT[fmt]

        step = (
            1.0, amt_log, amt_ratio_log, 1.0 if new_payee else 0.0, since_out, hs, hc,
            *fmt_oh,
            0.0 if same_bank else 1.0, 1.0 if cross_cur else 0.0,
            1.0 if dst == s.last_dst else 0.0, esc_log,
        )

        fa = None
        if want_a:
            s_out_amt = s.out_amt if s.out_amt > 0.0 else 0.0
            s_in_amt = s.in_amt if s.in_amt > 0.0 else 0.0
            r_out_amt = r.out_amt if r.out_amt > 0.0 else 0.0
            r_in_amt = r.in_amt if r.in_amt > 0.0 else 0.0
            n_r_in = len(r.in_q)
            fa = [
                amt_log, *fmt_oh,
                1.0 if same_bank else 0.0, 1.0 if cross_cur else 0.0, 1.0 if self_tx else 0.0,
                hs, hc, 1.0 if (amt >= 100.0 and amt % 100.0 < 1e-6) else 0.0,
                math.log1p(max(0, t - s.first_t)), float(len(s.out_q)), math.log1p(s_out_amt),
                float(len(s.in_q)), math.log1p(s_in_amt), float(len(s.out_peers)),
                since_out, _since_log(t, s.last_in_t), s_out_amt / (s_in_amt + 1.0),
                amt_ratio_log, 1.0 if new_payee else 0.0, math.log1p(s.n_out),
                math.log1p(max(0, t - r.first_t)), float(n_r_in), math.log1p(r_in_amt),
                float(len(r.in_peers)), float(len(r.out_q)), math.log1p(r_out_amt),
                r_out_amt / (r_in_amt + 1.0), _since_log(t, r.last_in_t),
                _since_log(t, r.last_out_t), math.log1p(r.n_in),
                (r.in_cross / n_r_in) if n_r_in else 0.0, 1.0 if r_new else 0.0,
            ]

        seq = None
        if want_seq:
            prev = list(s.seq)
            seq = [_PAD] * (config.SEQ_LEN - 1 - len(prev)) + prev + [step]

        # ---- update state (after features were computed) ----
        if not self_tx:
            s.out_q.append((t, amt, dst))
            s.out_amt += amt
            s.out_peers[dst] = s.out_peers.get(dst, 0) + 1
            s.last_out_t = t
            s.n_out += 1
            if prev_to_dst is not None:
                del s.payees[dst]
            s.payees[dst] = amt
            if len(s.payees) > self.payee_memory:
                del s.payees[next(iter(s.payees))]
            s.seq.append(step)
            s.amts.append(amt)
            s.last_dst = dst

            cross = 0 if same_bank else 1
            r.in_q.append((t, amt, src, cross))
            r.in_amt += amt
            r.in_cross += cross
            r.in_peers[src] = r.in_peers.get(src, 0) + 1
            r.last_in_t = t
            r.n_in += 1
        return fa, seq, step

    # --------------------------------------------------------------- inspect
    def sender_baseline(self, src: int) -> dict:
        """Human-readable baseline of a sender, used for reason codes and the UI."""
        a = self.acc.get(src)
        if a is None or not a.amts:
            return {"median_amount": None, "n_payees": 0, "n_out": 0}
        srt = sorted(a.amts)
        return {"median_amount": srt[len(srt) // 2], "n_payees": len(a.payees), "n_out": a.n_out}

    def receiver_profile(self, dst: int) -> dict:
        a = self.acc.get(dst)
        if a is None:
            return {"in_24h": 0, "senders_24h": 0, "in_amt_24h": 0.0, "out_amt_24h": 0.0, "pass_through": 0.0}
        in_amt = max(a.in_amt, 0.0)
        out_amt = max(a.out_amt, 0.0)
        return {
            "in_24h": len(a.in_q),
            "senders_24h": len(a.in_peers),
            "in_amt_24h": in_amt,
            "out_amt_24h": out_amt,
            "pass_through": out_amt / in_amt if in_amt > 0 else 0.0,
        }
