"""Run the whole stack in one process on the in-memory bus (local development / fallback).

    python -m scambreaker.serve.allinone --tps 800
"""
from __future__ import annotations

import argparse
import os
import threading

os.environ.setdefault("SB_BUS", "memory")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tps", type=float, default=800.0)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    if args.no_llm:
        os.environ["SB_LLM_DISABLE"] = "1"

    from . import genai, replayer, scorer, web  # noqa: PLC0415  (bus must be configured first)

    threading.Thread(target=scorer.run, args=([],), daemon=True).start()
    threading.Thread(target=genai.run, daemon=True).start()
    threading.Thread(target=replayer.run, args=(["--tps", str(args.tps), "--loop"],), daemon=True).start()
    web.app.run(host="0.0.0.0", port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
