"""GenAI worker: IBM Granite (llama.cpp, on the LinuxONE box) writes the customer warning,
the trusted-contact alert and the analyst case narrative for held payments.

Prompts contain only placeholders ({NAME}, {CONTACT}) and model evidence - never names or
account numbers. Output is checked by guardrails; failures fall back to vetted templates.

    SB_LLM_PATH=/models/granite.gguf python -m scambreaker.serve.genai
"""
from __future__ import annotations

import glob
import json
import os
import platform
import socket
import time
from pathlib import Path

from .. import config
from ..rag.bm25 import Retriever
from . import guardrails as G
from .bus import make_bus

# One shared system prompt: the llama.cpp server caches its KV state across all calls.
SYSTEM = (
    "You are the scam-prevention assistant inside Demo Bank's app. Write short, calm, plain-language text. "
    "Never blame the customer. Never include links, phone numbers, or requests for passwords or one-time codes. "
    "Write the placeholders {NAME} and {CONTACT} exactly as given and never invent names, numbers or amounts. "
    "Follow the task exactly and output only the requested text."
)
TASK_CUSTOMER = (
    "Task: warn {NAME}, whose transfer was {verb}. Write {n} short sentences in {lang}: name the warning "
    "signs below, say what this kind of scammer usually claims, and end by asking them not to send money "
    "because the bank will call their registered number."
)
TASK_CONTACT = (
    "Task: write one SMS of at most 2 sentences in English to {CONTACT}, the trusted contact of {NAME}: say a "
    "transfer by {NAME} was paused as a likely scam of the type below and ask {CONTACT} to call {NAME} now. "
    "Output only the SMS text: no subject line, no greeting, no sign-off."
)
TASK_ANALYST = (
    "Task: for the bank's fraud analyst, write exactly two parts using only the facts and excerpts. "
    "'Summary:' two sentences on what happened and why it was flagged. 'Recommended action:' one or two "
    "sentences following the policy, citing excerpt ids such as [K1]. At most 70 words in total."
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
    """IBM Granite via the llama.cpp server (SB_LLM_URL, preferred) or in-process llama-cpp-python."""

    def __init__(self, path: str | None = None, url: str | None = None):
        self.url = url.rstrip("/") if url else None
        self.path = path
        self.n_gpu_layers = int(os.environ.get("SB_LLM_GPU_LAYERS", "0"))
        self.tokens_per_s: list[float] = []
        if self.url:
            import requests  # noqa: PLC0415

            self.http = requests.Session()
            for _ in range(120):  # the server may still be loading the model
                try:
                    if self.http.get(f"{self.url}/health", timeout=3).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(2)
            try:
                props = self.http.get(f"{self.url}/props", timeout=5).json()
                self.path = props.get("model_path") or self.path
                self.build = props.get("build_info")
            except (requests.RequestException, ValueError):
                self.build = None
        else:
            from llama_cpp import Llama  # noqa: PLC0415

            self.llm = Llama(model_path=path, n_ctx=int(os.environ.get("SB_LLM_CTX", "2048")),
                             n_threads=int(os.environ.get("SB_LLM_THREADS", str(os.cpu_count() or 4))),
                             n_gpu_layers=self.n_gpu_layers, verbose=False, seed=7)

    def _pieces(self, messages, max_tokens, timeout_s):
        if self.url:
            r = self.http.post(f"{self.url}/v1/chat/completions", stream=True, timeout=(5, timeout_s + 15),
                               json={"messages": messages, "max_tokens": max_tokens, "temperature": 0.2,
                                     "top_p": 0.9, "stream": True, "cache_prompt": True})
            r.raise_for_status()
            buf = b""  # raw bytes: decode UTF-8 per complete SSE line (Chinese text spans chunks)
            for chunk in r.iter_content(chunk_size=None):
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        return
                    choices = json.loads(data.decode("utf-8")).get("choices") or []
                    if choices:
                        yield (choices[0].get("delta") or {}).get("content")
        else:
            for chunk in self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens,
                                                         temperature=0.2, top_p=0.9, stream=True):
                yield chunk["choices"][0]["delta"].get("content")

    def stream(self, system: str, user: str, max_tokens: int, timeout_s: float):
        """Yields text pieces; sets self.last_complete to False if the time budget ran out."""
        t0 = time.time()
        n = 0
        self.last_complete = False
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for piece in self._pieces(messages, max_tokens, timeout_s):
            if piece:
                n += 1
                yield piece
            if time.time() - t0 > timeout_s:
                break
        else:
            self.last_complete = True
        if n:
            self.tokens_per_s.append(n / max(1e-6, time.time() - t0))


def backend_info(llm: LLM | None) -> dict:
    info: dict = {}
    if llm and llm.url:  # served by the llama.cpp server container (s390x build with VXE)
        return {"server": "llama.cpp server · s390x VXE build", "model": Path(llm.path).name if llm.path else None,
                "build": getattr(llm, "build", None)}
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
        info["model"] = Path(llm.path).name if llm.path else None
        if llm.url:
            info["server"] = "llama.cpp server (s390x VXE build)"
            info["build"] = getattr(llm, "build", None)
        elif llm.path:
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
        url = os.environ.get("SB_LLM_URL")
        path = None if url else find_model()
        self.llm = None
        self.error = None
        if (url or path) and os.environ.get("SB_LLM_DISABLE") != "1":
            try:
                self.llm = LLM(path=path, url=url)
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

    def _generate(self, case, field, system, user, max_tokens, fallback, amounts, timeout_s=100.0):
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
            complete = self.llm.last_complete
        except Exception as e:  # noqa: BLE001
            text, complete = "", True
            self.error = repr(e)[:200]
        text = text.strip().strip('"“”').strip()
        ok, why = G.check(text, amounts, max_chars=900 if field == "narrative" else 600)
        if not complete:
            ok, why = False, "timed out"
        if ok:
            self._emit(case, field, text, True, "granite")
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
            verb = "stopped" if case["decision"] == "BLOCK" else "paused for 24 hours"
            def customer(lg):
                task = (TASK_CUSTOMER.replace("{verb}", verb).replace("{lang}", LANG_NAME[lg])
                        .replace("{n}", "2" if lg == "zh" else "2 or 3"))
                self._generate(case, f"customer_{lg}", SYSTEM, f"{task}\nFacts: {f}", 160 if lg == "zh" else 110,
                               G.customer_template(typ, case["amount_sgd"], case["decision"], lg), amounts)

            customer("en")  # what the customer sees first
            if case.get("contact"):  # then the family member, while the customer may still be on the call
                self._generate(case, "contact", SYSTEM, f"{TASK_CONTACT}\nFacts: {f}", 60,
                               G.contact_template(case["typology_label"], case["amount_sgd"]), amounts)
            if lang == "zh":
                customer("zh")
        hits = self.retriever.search(f"{case['typology_label']} {case['decision']} " +
                                     " ".join(r["code"] for r in case.get("reasons", [])), k=2)
        case["knowledge"] = [{k: h[k] for k in ("id", "source", "section", "score")} for h in hits]
        excerpts = "\n".join(f"[{h['id']}] ({h['source']} - {h['section']}) {h['text'][:380]}" for h in hits)
        self._generate(case, "narrative", SYSTEM, f"{TASK_ANALYST}\nFacts: {f}\nExcerpts:\n{excerpts}", 170,
                       f"Summary: {f} Recommended action: follow the graduated-response policy for "
                       f"{case['decision']} decisions [K1].", amounts, timeout_s=150.0)
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
    consumer = "genai-main"
    while True:
        stream = "genai.priority"  # live scenarios first
        items = bus.xreadgroup(stream, "genai", consumer, count=1, block_ms=200)
        if not items:
            stream = "genai.jobs"
            items = bus.xreadgroup(stream, "genai", consumer, count=1, block_ms=500)
        for sid, job in items:
            try:
                if stream == "genai.jobs" and time.time() - job.get("queued", time.time()) > 180:
                    continue  # stale background case: skip rather than build a backlog
                w.handle(job["case_id"])
            finally:
                bus.xack(stream, "genai", [sid])


if __name__ == "__main__":
    run()
