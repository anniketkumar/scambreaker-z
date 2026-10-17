"""Message bus: Redis Streams in production, an in-process twin for local runs / fallback.

Streams:  tx.in (replayer/web -> scorer) · decisions (scorer -> web) · genai.jobs (scorer -> genai)
          ui.events (genai -> web)
Keys:     case:<id> · cases:recent · metrics · proof:<service>
"""
from __future__ import annotations

import itertools
import json
import os
import threading
import time
from collections import deque

STREAM_MAXLEN = 200_000


def _dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), default=float)


class MemoryBus:
    def __init__(self):
        self._streams: dict[str, deque] = {}
        self._kv: dict[str, str] = {}
        self._lists: dict[str, deque] = {}
        self._groups: dict[tuple[str, str], int] = {}
        self._seq = itertools.count(1)
        self._cv = threading.Condition()

    def xadd(self, stream: str, msg: dict) -> str:
        with self._cv:
            sid = f"{next(self._seq)}-0"
            self._streams.setdefault(stream, deque(maxlen=STREAM_MAXLEN)).append((sid, _dumps(msg)))
            self._cv.notify_all()
            return sid

    def xadd_many(self, stream: str, msgs: list[dict]) -> None:
        for m in msgs:
            self.xadd(stream, m)

    def _after(self, stream: str, last: int, count: int):
        out = []
        for sid, data in self._streams.get(stream, ()):
            if int(sid.split("-")[0]) > last:
                out.append((sid, json.loads(data)))
                if len(out) >= count:
                    break
        return out

    def xreadgroup(self, stream, group, consumer, count=256, block_ms=100):
        deadline = time.time() + block_ms / 1000
        with self._cv:
            while True:
                last = self._groups.get((stream, group), 0)
                out = self._after(stream, last, count)
                if out:
                    self._groups[(stream, group)] = int(out[-1][0].split("-")[0])
                    return out
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._cv.wait(remaining)

    def xack(self, stream, group, ids) -> None:
        return None

    def xread(self, stream, last_id="$", count=500, block_ms=100):
        deadline = time.time() + block_ms / 1000
        with self._cv:
            if last_id == "$":
                q = self._streams.get(stream)
                last_id = q[-1][0] if q else "0-0"
                return [], last_id
            last = int(last_id.split("-")[0])
            while True:
                out = self._after(stream, last, count)
                if out:
                    return out, out[-1][0]
                remaining = deadline - time.time()
                if remaining <= 0:
                    return [], last_id
                self._cv.wait(remaining)

    def set(self, key: str, value) -> None:
        self._kv[key] = _dumps(value)

    def get(self, key: str):
        v = self._kv.get(key)
        return json.loads(v) if v is not None else None

    def push_recent(self, key: str, value, n: int = 200) -> None:
        with self._cv:
            self._lists.setdefault(key, deque(maxlen=n)).appendleft(_dumps(value))

    def recent(self, key: str, n: int = 50) -> list:
        return [json.loads(v) for v in list(self._lists.get(key, ()))[:n]]

    def keys(self, prefix: str) -> list[str]:
        return [k for k in self._kv if k.startswith(prefix)]


class RedisBus:
    def __init__(self, url: str):
        import redis  # noqa: PLC0415

        self.r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=10)
        for _ in range(60):
            try:
                self.r.ping()
                break
            except Exception:  # noqa: BLE001
                time.sleep(1)

    def xadd(self, stream, msg):
        return self.r.xadd(stream, {"d": _dumps(msg)}, maxlen=STREAM_MAXLEN, approximate=True)

    def xadd_many(self, stream, msgs):
        p = self.r.pipeline(transaction=False)
        for m in msgs:
            p.xadd(stream, {"d": _dumps(m)}, maxlen=STREAM_MAXLEN, approximate=True)
        p.execute()

    def _ensure_group(self, stream, group):
        try:
            self.r.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception as e:  # noqa: BLE001
            if "BUSYGROUP" not in str(e):
                raise

    def xreadgroup(self, stream, group, consumer, count=256, block_ms=100):
        if not getattr(self, "_groups_ok", None):
            self._groups_ok = set()
        if (stream, group) not in self._groups_ok:
            self._ensure_group(stream, group)
            self._groups_ok.add((stream, group))
        res = self.r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
        if not res:
            return []
        return [(sid, json.loads(f["d"])) for sid, f in res[0][1]]

    def xack(self, stream, group, ids):
        if ids:
            self.r.xack(stream, group, *ids)

    def xread(self, stream, last_id="$", count=500, block_ms=100):
        if last_id == "$":
            info = self.r.xrevrange(stream, count=1)
            return [], (info[0][0] if info else "0-0")
        res = self.r.xread({stream: last_id}, count=count, block=block_ms)
        if not res:
            return [], last_id
        items = [(sid, json.loads(f["d"])) for sid, f in res[0][1]]
        return items, items[-1][0]

    def set(self, key, value):
        self.r.set(key, _dumps(value))

    def get(self, key):
        v = self.r.get(key)
        return json.loads(v) if v is not None else None

    def push_recent(self, key, value, n=200):
        p = self.r.pipeline()
        p.lpush(key, _dumps(value))
        p.ltrim(key, 0, n - 1)
        p.execute()

    def recent(self, key, n=50):
        return [json.loads(v) for v in self.r.lrange(key, 0, n - 1)]

    def keys(self, prefix):
        return list(self.r.scan_iter(match=prefix + "*", count=500))


_MEMORY_SINGLETON: MemoryBus | None = None


def make_bus(url: str | None = None):
    url = url if url is not None else os.environ.get("SB_BUS", "memory")
    if url.startswith("redis://"):
        return RedisBus(url)
    global _MEMORY_SINGLETON
    if _MEMORY_SINGLETON is None:
        _MEMORY_SINGLETON = MemoryBus()
    return _MEMORY_SINGLETON
