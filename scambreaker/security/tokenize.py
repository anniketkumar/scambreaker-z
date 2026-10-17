"""Keyed pseudonymisation (HMAC-SHA256). Prompts sent to the LLM only ever contain tokens
and placeholders, never names or account numbers. On s390x, OpenSSL's SHA-256 used by
hashlib runs on the CPACF crypto instructions."""
from __future__ import annotations

import hashlib
import hmac
import os

from .. import config


def load_key() -> bytes:
    env = os.environ.get("SB_TOKEN_KEY")
    if env:
        return bytes.fromhex(env)
    path = config.SECRETS_DIR / "token.key"
    if path.exists():
        return path.read_bytes()
    config.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


class Tokenizer:
    def __init__(self, key: bytes | None = None):
        self.key = key if key is not None else load_key()

    def token(self, kind: str, value: str | int) -> str:
        mac = hmac.new(self.key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:12]
        return f"{kind.upper()}_{mac}"
