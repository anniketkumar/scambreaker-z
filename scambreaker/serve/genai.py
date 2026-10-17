"""GenAI worker: IBM Granite (llama.cpp, on the LinuxONE box) writes the customer warning,
the trusted-contact alert and the analyst case narrative for held payments.

Prompts contain only placeholders ({NAME}, {CONTACT}) and model evidence - never names or
account numbers. Output is checked by guardrails; failures fall back to vetted templates.

    SB_LLM_PATH=/models/granite.gguf python -m scambreaker.serve.genai
"""
from __future__ import annotations

import glob
import os
import platform
import socket
import time
from pathlib import Path

from .. import config
from ..rag.bm25 import Retriever
from . import guardrails as G
from .bus import make_bus

SYS_CUSTOMER = (
    "You write short safety messages inside a banking app. A transfer was paused because it matches a "
    "scam pattern. Rules: 2 or 3 short sentences; calm and kind, never blame the customer; mention the "
    "specific warning signs given; say what this kind of scammer usually claims; end by telling them not "
    "to send money and that the bank will call them on their registered number. Never include links, "
    "phone numbers, or ask for passwords or OTPs. Address the customer as {NAME}."
)
SYS_CONTACT = (
    "You write one SMS from a bank to a customer's trusted contact. At most 2 sentences. Say that a "
    "transfer by {NAME} was paused as a likely scam of the given type and ask {CONTACT} to call {NAME} "
    "now. No links and no phone numbers."
)
SYS_ANALYST = (
    "You assist a bank fraud analyst. Use only the case facts and the knowledge excerpts. Write exactly "
    "two parts. 'Summary:' two sentences on what happened and why it was flagged. 'Recommended action:' "
    "one or two sentences following the policy, citing excerpt ids such as [K1]. Do not invent facts."
)
LANG_NAME = {"en": "English", "zh": "Simplified Chinese"}


def find_model() -> str | None:
    env = os.environ.get("SB_LLM_PATH")
    if env and Path(env).exists():
        return env
    for pattern in ("/models/*.gguf", "/secure/models/llm/*.gguf", "/home/*/models/*.gguf",
                    "/opt/**/*granite*.gguf", "/home/**/*granite*.gguf"):
        hits = sorted(glob.glob(pattern, recursive=True))
        if hits:
            return hits[0]
    return None


class LLM:
    def __init__(self, path: str):
        from llama_cpp import Llama  # noqa: PLC0415

        self.path = path
        self.n_gpu_layers = int(os.environ.get("SB_LLM_GPU_LAYERS", "-1"))
        self.llm = Llama(model_path=path, n_ctx=int(os.environ.get("SB_LLM_CTX", "2048")),
                         n_threads=int(os.environ.get("SB_LLM_THREADS", str(os.cpu_count() or 4))),
                         n_gpu_layers=self.n_gpu_layers, verbose=False, seed=7)
        self.tokens_per_s: list[float] = []

    def stream(self, system: str, user: str, max_tokens: int, timeout_s: float):
        t0 = time.time()
        n = 0
        for chunk in self.llm.create_chat_completion(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens, temperature=0.2, top_p=0.9, stream=True):
            piece = chunk["choices"][0]["delta"].get("content")
            if piece:
                n += 1
                yield piece
            if time.time() - t0 > timeout_s:
                break
        if n:
            self.tokens_per_s.append(n / max(1e-6, time.time() - t0))


def backend_info(llm: LLM | None) -> dict:
    info: dict = {}
    try:
        import llama_cpp  # noqa: PLC0415

        info["llama_cpp_python"] = llama_cpp.__version__
        info["gpu_offload_supported"] = bool(llama_cpp.llama_supports_gpu_offload())
        libdir = Path(llama_cpp.__file__).parent
        zdnn = sorted(p.name for p in libdir.rglob("*zdnn*"))
        info["zdnn_libs"] = zdnn[:4]
    except Exception as e:  # noqa: BLE001
        info["llama_cpp_error"] = repr(e)[:200]
    if llm:
        info["model"] = Path(llm.path).name
        info["model_gb"] = round(Path(llm.path).stat().st_size / 1e9, 2)
        info["n_gpu_layers"] = llm.n_gpu_layers
    return info


def egress_blocked() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=2.5).close()
        return False
    except OSError:
        return True


def facts(case: dict) -> str:
    signs = "; ".join(r["text"] for r in case.get("reasons", [])[:5]) or "unusual payment"
    return (f"Scam type: {case['typology_label']}. Decision: {case['decision']}. "
            f"Amount: S${case['amount_sgd']:,.0f}. Warning signs: {signs}.")


class Worker:
    def __init__(self, bus):
        self.bus = bus
        self.retriever = Retriever(config.KNOWLEDGE_DIR)
        path = find_model()
        self.llm = None
        self.error = None
        if path and os.environ.get("SB_LLM_DISABLE") != "1":
            try:
                self.llm = LLM(path)
            except Exception as e:  # noqa: BLE001
                self.error = repr(e)[:300]
        self.proof = {"service": "genai", "host": socket.gethostname(), "machine": platform.machine(),
                      "mode": "granite" if self.llm else "template", "egress_blocked": egress_blocked(),
                      "error": self.error, **backend_info(self.llm), "started": time.time()}
        self.bus.set("proof:genai", self.proof)

    def _emit(self, case: dict, field: str, text: str, done: bool, source: str) -> None:
        case.setdefault("messages", {})[field] = {"text": text, "done": done, "source": source}
        self.bus.set(f"case:{case['id']}", case)
        self.bus.xadd("ui.events", {"type": "llm", "id": case["id"], "field": field, "text": text,
                                    "done": done, "source": source})

    def _generate(self, case, field, system, user, max_tokens, fallback, amounts, timeout_s=25.0):
        if not self.llm:
            self._emit(case, field, fallback, True, "template")
            return
        text, last = "", 0.0
        try:
            for piece in self.llm.stream(system, user, max_tokens, timeout_s):
                text += piece
                if time.time() - last > 0.15:
                    self._emit(case, field, text, False, "granite")
                    last = time.time()
        except Exception as e:  # noqa: BLE001
            text = ""
            self.error = repr(e)[:200]
        ok, why = G.check(text.strip(), amounts, max_chars=900 if field == "narrative" else 600)
        if ok:
            self._emit(case, field, text.strip(), True, "granite")
        else:
            self._emit(case, field, fallback, True, f"template ({why})")

    def handle(self, case_id: str) -> None:
        case = self.bus.get(f"case:{case_id}")
        if not case:
            return
        if case["decision"] == "ALLOW":  # nothing to explain to anyone
            case["llm"] = {"status": "skipped"}
            self.bus.set(f"case:{case_id}", case)
            return
        case["llm"] = {"status": "running", "started": time.time()}
        self.bus.set(f"case:{case_id}", case)
        amounts = [case["amount_sgd"], case["amount_usd"]]
        typ, lang = case["typology"], case.get("lang", "en")
        f = facts(case)
        if case["decision"] in ("HOLD", "BLOCK"):
            langs = ["en", "zh"] if lang == "zh" else ["en"]
            for lg in langs:
                self._generate(case, f"customer_{lg}", SYS_CUSTOMER, f"{f} Write in {LANG_NAME[lg]}.", 140,
                               G.customer_template(typ, case["amount_sgd"], case["decision"], lg), amounts)
            if case.get("contact"):
                self._generate(case, "contact", SYS_CONTACT, f"{f} Write in English.", 70,
                               G.contact_template(case["typology_label"], case["amount_sgd"]), amounts)
        hits = self.retriever.search(f"{case['typology_label']} {case['decision']} " +
                                     " ".join(r["code"] for r in case.get("reasons", [])), k=3)
        case["knowledge"] = [{k: h[k] for k in ("id", "source", "section", "score")} for h in hits]
        excerpts = "\n".join(f"[{h['id']}] ({h['source']} - {h['section']}) {h['text'][:600]}" for h in hits)
        self._generate(case, "narrative", SYS_ANALYST, f"Case facts: {f}\nKnowledge excerpts:\n{excerpts}", 180,
                       f"Summary: {f} Recommended action: follow the graduated-response policy for "
                       f"{case['decision']} decisions [K1].", amounts, timeout_s=40.0)
        case = self.bus.get(f"case:{case_id}") or case
        case["llm"] = {"status": "done", "finished": time.time()}
        self.bus.set(f"case:{case_id}", case)
        if self.llm and self.llm.tokens_per_s:
            recent = self.llm.tokens_per_s[-20:]
            self.proof["tokens_per_s"] = round(sum(recent) / len(recent), 1)
            self.bus.set("proof:genai", self.proof)


def run() -> None:
    bus = make_bus()
    w = Worker(bus)
    print(f"genai ready: mode={w.proof['mode']} model={w.proof.get('model')} "
          f"egress_blocked={w.proof['egress_blocked']}", flush=True)
    consumer = socket.gethostname()
    while True:
        for sid, job in bus.xreadgroup("genai.jobs", "genai", consumer, count=1, block_ms=500):
            try:
                w.handle(job["case_id"])
            finally:
                bus.xack("genai.jobs", "genai", [sid])


if __name__ == "__main__":
    run()
