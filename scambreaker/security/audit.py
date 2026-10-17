"""Append-only, hash-chained decision log.

Each line stores the SHA-256 of the previous line's hash plus its own canonical JSON body, so
editing, deleting or reordering any record breaks verification from that point on.

    python -m scambreaker.security.audit verify <path>
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

GENESIS = "0" * 64


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.prev = GENESIS
        self.count = 0
        self._lock = threading.Lock()
        if self.path.exists():
            with self.path.open("rb") as f:
                last = None
                for line in f:
                    if line.strip():
                        last = line
                        self.count += 1
            if last:
                self.prev = json.loads(last)["hash"]
        self._f = self.path.open("a", encoding="utf-8")

    def append(self, record: dict) -> str:
        with self._lock:
            h = hashlib.sha256((self.prev + _canon(record)).encode()).hexdigest()
            self._f.write(_canon({"prev": self.prev, "hash": h, "rec": record}) + "\n")
            self._f.flush()
            self.prev = h
            self.count += 1
            return h

    def close(self) -> None:
        self._f.close()


def verify(path: Path) -> tuple[bool, int, int | None]:
    """Returns (ok, records_checked, first_bad_line_number)."""
    prev = GENESIS
    n = 0
    with Path(path).open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                expect = hashlib.sha256((prev + _canon(obj["rec"])).encode()).hexdigest()
            except (ValueError, KeyError):
                return False, n, lineno
            if obj["prev"] != prev or obj["hash"] != expect:
                return False, n, lineno
            prev = obj["hash"]
            n += 1
    return True, n, None


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "verify":
        raise SystemExit("usage: python -m scambreaker.security.audit verify <path>")
    ok, n, bad = verify(Path(sys.argv[2]))
    if ok:
        print(f"OK - {n:,} records, hash chain intact")
    else:
        print(f"TAMPERED - chain breaks at line {bad} (after {n:,} valid records)")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
