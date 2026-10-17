import json

import numpy as np
import pytest

from scambreaker import config
from scambreaker.features.engine import A_FEATURES, N_A, N_SEQ, FeatureEngine
from scambreaker.models.fusion import Stacker
from scambreaker.security.audit import AuditLog, verify
from scambreaker.security.tokenize import Tokenizer

IDX = {n: i for i, n in enumerate(A_FEATURES)}


def _tx(eng, t, s, d, amt, **kw):
    return eng.process(t, s, d, amt, kw.get("fmt", 0), kw.get("same_bank", True),
                       kw.get("cross_cur", False), kw.get("hour", 12))


def test_features_are_point_in_time():
    """Features for an event must not change when *later* events are appended."""
    rng = np.random.default_rng(0)
    events = [(int(t), int(rng.integers(0, 30)), int(rng.integers(0, 30)), float(rng.uniform(1, 500)))
              for t in np.sort(rng.integers(0, 5000, 400))]
    full, prefix = FeatureEngine(), FeatureEngine()
    out_full = [_tx(full, *e) for e in events]
    out_prefix = [_tx(prefix, *e) for e in events[:200]]
    for (fa1, seq1, _), (fa2, seq2, _) in zip(out_full[:200], out_prefix):
        assert fa1 == fa2
        assert seq1 == seq2


def test_first_event_has_no_history():
    eng = FeatureEngine()
    fa, seq, _ = _tx(eng, 100, 1, 2, 50.0)
    assert len(fa) == N_A and len(seq) == config.SEQ_LEN and len(seq[0]) == N_SEQ
    assert fa[IDX["s_out_cnt"]] == 0 and fa[IDX["r_in_cnt"]] == 0
    assert fa[IDX["s_new_payee"]] == 1.0 and fa[IDX["r_new_acct"]] == 1.0
    assert all(v == 0.0 for step in seq[:-1] for v in step)


def test_window_expiry_and_fan_in():
    eng = FeatureEngine(window_min=60)
    for k in range(5):
        _tx(eng, 10 + k, 100 + k, 7, 10.0)  # five senders pay account 7
    fa, _, _ = _tx(eng, 20, 200, 7, 10.0)
    assert fa[IDX["r_in_cnt"]] == 5 and fa[IDX["r_in_peers"]] == 5
    fa, _, _ = _tx(eng, 200, 201, 7, 10.0)  # all previous inflows are older than 60 min
    assert fa[IDX["r_in_cnt"]] == 0


def test_new_payee_and_escalation():
    eng = FeatureEngine()
    _tx(eng, 0, 1, 2, 100.0)
    fa, seq, _ = _tx(eng, 10, 1, 2, 300.0)
    assert fa[IDX["s_new_payee"]] == 0.0
    cur = seq[-1]
    assert cur[-1] == pytest.approx(np.log(301 / 101))  # escalation vs last payment to payee
    assert cur[-2] == 1.0  # same payee as previous transfer


def test_audit_chain_detects_tampering(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(5):
        log.append({"i": i, "decision": "HOLD"})
    log.close()
    assert verify(path) == (True, 5, None)
    lines = path.read_text().splitlines()
    rec = json.loads(lines[2])
    rec["rec"]["decision"] = "ALLOW"
    lines[2] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    ok, n, bad = verify(path)
    assert not ok and bad == 3


def test_audit_resumes_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    a = AuditLog(path)
    a.append({"x": 1})
    a.close()
    b = AuditLog(path)
    b.append({"x": 2})
    b.close()
    assert verify(path) == (True, 2, None)


def test_tokenizer_is_deterministic_and_keyed():
    t1, t2 = Tokenizer(b"k" * 32), Tokenizer(b"j" * 32)
    assert t1.token("acct", 42) == t1.token("acct", 42)
    assert t1.token("acct", 42) != t2.token("acct", 42)
    assert t1.token("acct", 42).startswith("ACCT_")


def test_policy_thresholds_are_monotonic_and_respect_budget():
    rng = np.random.default_rng(1)
    p_legit = rng.beta(1, 30, 20000)
    st = Stacker(["A"], [1.0], 0.0)
    th = st.set_thresholds(p_legit)
    assert th["nudge"] <= th["hold"] <= th["block"]
    d = st.decide(p_legit)
    assert (d >= 1).mean() <= config.BUDGET_NUDGE + 1e-3
    assert (d >= 2).mean() <= config.BUDGET_HOLD + 1e-3
