"""Send one prompt to the llama.cpp server (non-streaming) and print the raw result.
    python -m scambreaker.bench.llm_probe [en|zh|contact]
"""
from __future__ import annotations

import json
import os
import sys
import time

import requests

from ..serve.genai import LANG_NAME, SYSTEM, TASK_CONTACT, TASK_CUSTOMER

FACTS = ("Scam type: Government official impersonation. Decision: HOLD. Amount: S$18,500. Warning signs: "
         "Payee was added 3 min ago; Amount is 129x this customer's usual payment; Customer is on a phone call "
         "with an unknown number.")


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "en"
    if which == "contact":
        user = f"{TASK_CONTACT}\nFacts: {FACTS}"
    else:
        user = (TASK_CUSTOMER.replace("{verb}", "paused for 24 hours").replace("{lang}", LANG_NAME[which]).replace("{n}", "2")
                + f"\nFacts: {FACTS}")
    url = os.environ.get("SB_LLM_URL", "http://llm:8081")
    t0 = time.time()
    r = requests.post(f"{url}/v1/chat/completions", timeout=300, json={
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "max_tokens": 110, "temperature": 0.2, "top_p": 0.9})
    d = r.json()
    ch = d["choices"][0]
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{time.time() - t0:.1f}s finish={ch.get('finish_reason')} usage={d.get('usage')}")
    print(repr(ch["message"]["content"]))
    print(json.dumps(d.get("timings", {}))[:300])




def stream_probe(which: str) -> None:
    """Same prompt through the worker's streaming client, to compare with the non-streaming call."""
    from ..serve.genai import LLM  # noqa: PLC0415

    llm = LLM(url=os.environ.get("SB_LLM_URL", "http://llm:8081"))
    user = (TASK_CUSTOMER.replace("{verb}", "paused for 24 hours").replace("{lang}", LANG_NAME[which]).replace("{n}", "2")
            + f"\nFacts: {FACTS}")
    t0 = time.time()
    pieces = list(llm.stream(SYSTEM, user, 110, 120.0))
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"stream: {len(pieces)} pieces in {time.time() - t0:.1f}s")
    print("".join(pieces))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "stream":
        stream_probe(sys.argv[1])
    else:
        main()
