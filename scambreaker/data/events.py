"""Compact event-stream format shared by training, replay and scoring."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

KIND_ORIG, KIND_SCAM, KIND_HARDNEG, KIND_PERSONA = 0, 1, 2, 3

EVENT_DTYPE = np.dtype([
    ("t", "i4"),             # minutes since midnight of the first day
    ("src", "i4"), ("dst", "i4"),
    ("amt", "f4"),           # amount paid, converted to USD
    ("fmt", "i1"),           # index into config.PAYMENT_FORMATS
    ("same_bank", "i1"), ("cross_cur", "i1"), ("hour", "i1"), ("day", "i1"),
    ("is_laund", "i1"),      # IBM AMLworld ground-truth label
    ("is_scam", "i1"),       # injected APP-scam payment (synthetic label)
    ("typ", "i1"),           # index into config.TYPOLOGIES, -1 if none
    ("ep", "i4"),            # scam episode id, -1 if none
    ("kind", "i1"),          # KIND_* constant
    ("digital", "i1"),       # app-initiated transfer (carries a session)
    ("pool", "i1"),          # digital transfer by a retail customer -> scam policy applies
    ("on_call", "i1"), ("unknown_caller", "i1"), ("remote_access", "i1"),
    ("payee_added_min", "f4"), ("session_s", "f4"), ("hesitation", "f4"),
    ("failed", "i1"), ("new_device", "i1"),
])

SESSION_FIELDS = ("on_call", "unknown_caller", "remote_access", "payee_added_min",
                  "session_s", "hesitation", "failed", "new_device")


def save_stream(out_dir: Path, ev: np.ndarray, keys: np.ndarray, retail: np.ndarray, meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "events.npy", ev)
    np.save(out_dir / "accounts.npy", keys.astype("U32"))
    np.save(out_dir / "retail.npy", retail.astype(np.int8))
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=float))


def load_stream(data_dir: Path, mmap: bool = True):
    ev = np.load(data_dir / "events.npy", mmap_mode="r" if mmap else None)
    keys = np.load(data_dir / "accounts.npy")
    retail = np.load(data_dir / "retail.npy")
    meta = json.loads((data_dir / "meta.json").read_text())
    return ev, keys, retail, meta
