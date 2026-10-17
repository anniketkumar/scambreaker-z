"""Platform probe: what TensorFlow sees on this machine and where ops are placed.

    ZDNN_LOGLEVEL=info python -m scambreaker.bench.probe
"""
from __future__ import annotations

import importlib.metadata as md
import os
import platform
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "1")


def main() -> None:
    print("machine:", platform.machine())
    feats = next((ln for ln in Path("/proc/cpuinfo").read_text().splitlines() if ln.startswith("features")), "")
    print("nnpa facility:", "nnpa" in feats.split())
    for pkg in ("ibm-zdnn-plugin", "tensorflow", "torch-nnpa", "llama_cpp_python"):
        try:
            print(f"{pkg}: {md.version(pkg)}")
        except md.PackageNotFoundError:
            print(f"{pkg}: not installed")
    try:
        files = [str(f) for f in md.files("ibm-zdnn-plugin") or [] if f.suffix in (".so", ".py")]
        print("zdnn plugin files:", files[:12])
    except md.PackageNotFoundError:
        pass
    import tensorflow as tf  # noqa: PLC0415

    print("physical devices:", tf.config.list_physical_devices())
    print("logical devices:", tf.config.list_logical_devices())
    tf.debugging.set_log_device_placement(True)
    a = tf.random.normal([256, 256])
    b = tf.random.normal([256, 256])
    c = tf.matmul(a, b)
    with tf.device("/CPU:0"):
        d = tf.matmul(a, b)
    print("default matmul device:", c.device)
    print("pinned  matmul device:", d.device)


def lstm_probe() -> None:
    """Is the zDNN kernel library mapped into this process, and does an LSTM call reach it?"""
    import numpy as np  # noqa: PLC0415

    maps = Path("/proc/self/maps").read_text()
    libs = sorted({ln.split()[-1] for ln in maps.splitlines() if "zdnn" in ln.lower() or "_z_ops" in ln})
    print("zDNN libraries mapped into this process:", libs or "none")
    import keras  # noqa: PLC0415

    m = keras.Sequential([keras.Input((8, 18)), keras.layers.LSTM(48), keras.layers.Dense(1)])
    x = np.random.default_rng(0).normal(size=(128, 8, 18)).astype("float32")
    print("lstm out:", float(m(x).numpy().mean()))


if __name__ == "__main__":
    main()
    if os.environ.get("PROBE_LSTM"):
        lstm_probe()
