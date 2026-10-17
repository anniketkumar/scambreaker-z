"""Turn model evidence into short, human-readable reason codes.

Each reason names the model whose input it comes from (A payee risk, B behaviour,
C coercion) and carries a weight used only for ordering.
"""
from __future__ import annotations

import math

from ..features.engine import A_FEATURES, SEQ_FEATURES
from ..models.coercion_lr import FEATURES as C_FEATURES

_A = {n: i for i, n in enumerate(A_FEATURES)}
_S = {n: i for i, n in enumerate(SEQ_FEATURES)}


PAYEE_TEXT = {
    "r_in_amt_log": "Unusually large inflows to the payee in the last 24h",
    "r_out_cnt": "Payee is sending money onward rapidly",
    "r_out_amt_log": "Payee moved large sums onward in the last 24h",
    "r_since_in_log": "Payee received other payments minutes ago",
    "r_since_out_log": "Payee moved money out minutes ago",
    "r_n_in_log": "Payee has an unusually high number of incoming payments",
    "r_in_cross_frac": "Most inflows to the payee come from other banks",
    "r_new_acct": "Payee account has never been seen before",
}


def _r(code, text, model, weight):
    return {"code": code, "text": text, "model": model, "weight": round(float(weight), 3)}


def build(fa, step, session: dict, c_contrib: dict, a_contrib: dict | None, receiver: dict,
          sender: dict) -> list[dict]:
    out = []
    # --- B: behaviour of this customer (inputs the LSTM saw for the current step)
    ratio = math.exp(step[_S["amt_ratio_log"]])
    if sender.get("median_amount") and ratio >= 3:
        out.append(_r("AMOUNT_SPIKE", f"Amount is {ratio:.0f}x this customer's usual payment", "B",
                      min(3.0, math.log(ratio))))
    if step[_S["new_payee"]] > 0.5:
        out.append(_r("NEW_PAYEE", "First ever payment to this payee", "B", 0.8))
    esc = step[_S["escalation_log"]]
    if step[_S["new_payee"]] < 0.5 and esc > 0.3:
        out.append(_r("ESCALATION", f"{math.exp(esc):.1f}x the previous payment to the same payee", "B",
                      min(2.5, 1.5 * esc)))
    since = math.expm1(step[_S["since_prev_log"]])
    if 0 < since < 15:
        out.append(_r("RAPID", f"Sent {since:.0f} min after the previous transfer", "B", 0.5))

    # --- A: payee / mule risk (real AMLworld-trained model)
    a_w = a_contrib or {}
    if receiver.get("senders_24h", 0) >= 4:
        out.append(_r("FAN_IN", f"Payee received money from {receiver['senders_24h']} different senders in 24h",
                      "A", 0.6 + max(0.0, a_w.get("r_in_peers", 0.0) + a_w.get("r_in_cnt", 0.0))))
    if receiver.get("in_amt_24h", 0) > 0 and receiver.get("pass_through", 0) >= 0.5:
        out.append(_r("PASS_THROUGH", f"Payee forwarded {min(999, receiver['pass_through'] * 100):.0f}% of its "
                      "inflows within 24h", "A", 0.6 + max(0.0, a_w.get("r_pass", 0.0))))
    age_min = math.expm1(fa[_A["r_age_log"]])
    if age_min < 2 * 1440:
        out.append(_r("YOUNG_PAYEE", f"Payee account first active {age_min / 60:.0f}h ago", "A",
                      0.4 + max(0.0, a_w.get("r_age_log", 0.0))))
    if a_contrib:
        covered = {"r_in_peers", "r_in_cnt", "r_pass", "r_age_log"}
        top = sorted(((v, k) for k, v in a_contrib.items()
                      if v > 0.4 and k in PAYEE_TEXT and k not in covered), reverse=True)[:1]
        for v, k in top:
            out.append(_r("PAYEE_PATTERN", PAYEE_TEXT[k], "A", v))

    # --- C: coercion signals from the app session
    texts = {
        "on_call": "Customer is on a phone call" + (" with an unknown number" if session.get("unknown_caller") else ""),
        "remote_access": "A remote-access app is running on the device",
        "payee_added_log": f"Payee was added {session.get('payee_added_min', 0):.0f} min ago",
        "session_log": f"Unusually long session ({session.get('session_s', 0) / 60:.0f} min)",
        "hesitation": "Stop-start, hesitant input",
        "failed": f"{int(session.get('failed', 0))} failed attempts before this one",
        "new_device": "Payment made from a new device",
    }
    for f in C_FEATURES:
        if f == "unknown_caller":
            continue
        w = c_contrib.get(f, 0.0) + (c_contrib.get("unknown_caller", 0.0) if f == "on_call" else 0.0)
        if w > 0.35 and f in texts:
            if f == "payee_added_log" and session.get("payee_added_min", 1e9) > 24 * 60:
                continue
            out.append(_r(f.upper(), texts[f], "C", w))
    out.sort(key=lambda r: -r["weight"])
    return out[:6]
