"""IBM Granite on llama.cpp: tokens/s with layers offloaded to the zDNN backend vs CPU only.

    python -m scambreaker.bench.llm_bench --model /secure/work/llm/granite.gguf --layers -1
    python -m scambreaker.bench.llm_bench --model /secure/work/llm/granite.gguf --layers 0
(run each setting in its own process so memory is released between runs)
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .. import config

PROMPT = ("Scam type: Government official impersonation. Decision: BLOCK. Amount: S$18,500. Warning signs: "
          "customer is on a phone call with an unknown number; payee was added 3 minutes ago; amount is 60x the "
          "customer's usual payment. Write in English.")
SYSTEM = "You write short safety messages inside a banking app. 2 or 3 short sentences."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--layers", type=int, default=-1)
    ap.add_argument("--threads", type=int, default=int(os.environ.get("SB_LLM_THREADS", os.cpu_count() or 4)))
    ap.add_argument("--tokens", type=int, default=96)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", type=Path, default=config.RESULTS_DIR / "llm_bench.json")
    args = ap.parse_args()

    from llama_cpp import Llama  # noqa: PLC0415

    t0 = time.time()
    llm = Llama(model_path=args.model, n_ctx=1024, n_threads=args.threads, n_gpu_layers=args.layers,
                verbose=False, seed=7)
    load_s = time.time() - t0
    runs = []
    for _ in range(args.reps):
        t0 = time.time()
        first = None
        n = 0
        for chunk in llm.create_chat_completion(
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": PROMPT}],
                max_tokens=args.tokens, temperature=0.0, stream=True):
            if chunk["choices"][0]["delta"].get("content"):
                n += 1
                first = first or time.time()
        total = time.time() - t0
        runs.append({"tokens": n, "time_to_first_token_s": round((first or time.time()) - t0, 3),
                     "total_s": round(total, 3),
                     "gen_tokens_per_s": round((n - 1) / max(1e-6, total - ((first or t0) - t0)), 2)})
        print(runs[-1], flush=True)
    key = f"layers={args.layers}"
    prev = json.loads(args.out.read_text()) if args.out.exists() else {}
    prev[key] = {"model": Path(args.model).name, "threads": args.threads, "load_s": round(load_s, 2), "runs": runs,
                 "median_gen_tokens_per_s": sorted(r["gen_tokens_per_s"] for r in runs)[len(runs) // 2],
                 "median_ttft_s": sorted(r["time_to_first_token_s"] for r in runs)[len(runs) // 2]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(prev, indent=2))
    print("saved", args.out, prev[key]["median_gen_tokens_per_s"], "tok/s")


if __name__ == "__main__":
    main()
