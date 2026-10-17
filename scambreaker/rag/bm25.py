"""BM25 retrieval over the local knowledge base (typology playbooks, policy, regulation notes).
Runs entirely on the LinuxONE box; no embedding service or external API is involved."""
from __future__ import annotations

import re
from pathlib import Path

from rank_bm25 import BM25Okapi

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def load_chunks(root: Path) -> list[dict]:
    chunks = []
    for path in sorted(root.rglob("*.md")):
        title, buf, section = path.stem, [], None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                continue
            if line.startswith("## "):
                if buf and section:
                    chunks.append({"source": path.relative_to(root).as_posix(), "title": title,
                                   "section": section, "text": " ".join(buf).strip()})
                section, buf = line[3:].strip(), []
                continue
            if line.strip():
                buf.append(line.strip())
        if buf and section:
            chunks.append({"source": path.relative_to(root).as_posix(), "title": title, "section": section,
                           "text": " ".join(buf).strip()})
    return chunks


class Retriever:
    def __init__(self, root: Path):
        self.chunks = load_chunks(root)
        self.bm25 = BM25Okapi([_tokens(f"{c['title']} {c['section']} {c['text']}") for c in self.chunks])

    def search(self, query: str, k: int = 3) -> list[dict]:
        scores = self.bm25.get_scores(_tokens(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [{**self.chunks[i], "id": f"K{j + 1}", "score": round(float(scores[i]), 3)}
                for j, i in enumerate(order)]
