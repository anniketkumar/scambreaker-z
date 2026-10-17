"""Model B - sender-behaviour LSTM over the customer's recent outgoing transfers.

Inference runs through TensorFlow; on IBM z16/z17 the ibm-zdnn-plugin places supported ops
on the Telum on-chip AI accelerator (NNPA). `device="cpu"` pins it to the CPU so the two can
be compared on identical inputs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from .. import config
from ..features.engine import N_SEQ, SEQ_FEATURES

UNITS = 48
# Hour of day is excluded: AMLworld spreads activity evenly over 24h while injected episodes
# follow typology-specific hours, so the hour would be a simulation artifact, not a signal.
EXCLUDED = [SEQ_FEATURES.index("hour_sin"), SEQ_FEATURES.index("hour_cos")]
BATCH_BUCKETS = (1, 8, 32, 128, 512, 2048)


def _tf():
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import tensorflow as tf  # noqa: PLC0415  (heavy import, only when needed)
    return tf


def tf_available() -> bool:
    try:
        _tf()
        return True
    except Exception:  # noqa: BLE001
        return False


def build(units: int = UNITS):
    _tf()
    import keras  # noqa: PLC0415

    inp = keras.Input(shape=(config.SEQ_LEN, N_SEQ), name="seq")
    x = keras.layers.LSTM(units, name="lstm")(inp)
    x = keras.layers.Dense(24, activation="relu", name="dense")(x)
    out = keras.layers.Dense(1, activation="sigmoid", name="p_scam")(x)
    return keras.Model(inp, out, name="behavior_lstm")


def norm_stats(seq: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    valid = seq[:, :, 0] > 0.5
    flat = seq[valid]
    mu = flat.mean(0)
    sd = flat.std(0)
    sd[sd < 1e-6] = 1.0
    mu[0], sd[0] = 0.0, 1.0  # keep the 'valid' flag unscaled
    return mu.astype(np.float32), sd.astype(np.float32)


def normalize(seq: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    out = ((seq - mu) / sd).astype(np.float32)
    out[seq[:, :, 0] < 0.5] = 0.0  # padded steps stay exactly zero
    out[:, :, EXCLUDED] = 0.0
    return out


def train(seq_tr, y_tr, seq_va, y_va, out_dir: Path, epochs: int = 6, batch: int = 512) -> dict:
    tf = _tf()
    import keras  # noqa: PLC0415

    keras.utils.set_random_seed(config.SEED % (2**31))
    mu, sd = norm_stats(seq_tr)
    xtr, xva = normalize(seq_tr, mu, sd), normalize(seq_va, mu, sd)
    n_pos = max(1, int(y_tr.sum()))
    w_pos = float(min(50.0, (len(y_tr) - n_pos) / n_pos))
    model = build()
    model.compile(optimizer=keras.optimizers.Adam(2e-3), loss="binary_crossentropy",
                  metrics=[keras.metrics.AUC(curve="PR", name="auprc")])
    stop = keras.callbacks.EarlyStopping(monitor="val_auprc", mode="max", patience=2,
                                         restore_best_weights=True)
    hist = model.fit(xtr, y_tr.astype(np.float32), validation_data=(xva, y_va.astype(np.float32)),
                     epochs=epochs, batch_size=batch, class_weight={0: 1.0, 1: w_pos},
                     callbacks=[stop], verbose=2)
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_weights(str(out_dir / "model_b.weights.h5"))
    meta = {"units": UNITS, "seq_len": config.SEQ_LEN, "n_feat": N_SEQ, "mu": mu.tolist(),
            "sd": sd.tolist(), "w_pos": w_pos, "tf_version": tf.__version__,
            "history": {k: [float(v) for v in vals] for k, vals in hist.history.items()}}
    (out_dir / "model_b.json").write_text(json.dumps(meta, indent=2))
    return meta


class BehaviorModel:
    """Batched inference. Pads batches to fixed buckets so the accelerator sees static shapes."""

    def __init__(self, model_dir: Path, device: str = "auto"):
        tf = _tf()
        self.tf = tf
        meta = json.loads((model_dir / "model_b.json").read_text())
        self.mu = np.asarray(meta["mu"], np.float32)
        self.sd = np.asarray(meta["sd"], np.float32)
        self.model = build(meta["units"])
        self.model.load_weights(str(model_dir / "model_b.weights.h5"))
        self.device = device
        spec = tf.TensorSpec([None, config.SEQ_LEN, N_SEQ], tf.float32)
        self._fn = tf.function(lambda x: self.model(x, training=False), input_signature=[spec])

    def _run(self, x: np.ndarray) -> np.ndarray:
        if self.device == "cpu":
            with self.tf.device("/CPU:0"):
                return self._fn(x).numpy()[:, 0]
        return self._fn(x).numpy()[:, 0]

    def predict(self, seq_raw: np.ndarray, normalized: bool = False) -> np.ndarray:
        x = seq_raw if normalized else normalize(seq_raw, self.mu, self.sd)
        n = len(x)
        out = np.empty(n, dtype=np.float32)
        i = 0
        while i < n:
            take = min(n - i, BATCH_BUCKETS[-1])
            bucket = next(b for b in BATCH_BUCKETS if b >= take)
            chunk = x[i:i + take]
            if bucket != take:
                chunk = np.concatenate([chunk, np.zeros((bucket - take, *x.shape[1:]), np.float32)])
            out[i:i + take] = self._run(chunk)[:take]
            i += take
        return out
