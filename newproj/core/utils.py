import os
import time
import json
import hashlib
import base64
import secrets
from typing import Any, Dict


def now_ms() -> int:
    return int(time.time() * 1000)


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha3_256_hex(data: bytes) -> str:
    # fallback to hashlib's sha3_256 (pysha3 for older versions is in requirements)
    return hashlib.sha3_256(data).hexdigest()


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def ensure_dirs():
    for d in ["logs", "data", "tmp", "cache"]:
        os.makedirs(d, exist_ok=True)


def rand_bytes(n: int = 32) -> bytes:
    return secrets.token_bytes(n)


def rand_hex(n: int = 32) -> str:
    return secrets.token_hex(n)
