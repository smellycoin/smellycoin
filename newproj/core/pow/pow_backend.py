from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from core.config import get_config
from core.utils import sha3_256_hex


class PowUnavailable(Exception):
    pass


class _BackendState:
    def __init__(self):
        self._chosen: Optional[str] = None
        self._pow_fn: Optional[Callable[[bytes, int, str], bytes]] = None
        self._lock = threading.Lock()

    def choose_backend(self):
        with self._lock:
            if self._chosen is not None and self._pow_fn is not None:
                return
            # Force Argon2id as the only backend
            try:
                from core.pow.argon2_pow import pow_hash as a2_pow_hash
            except Exception as e:
                raise PowUnavailable(f"Argon2 backend not available: {e}")
            self._chosen = "argon2"
            self._pow_fn = a2_pow_hash

    def pow_hash(self, header_bytes: bytes, nonce: int, prev_hash_hex: str) -> bytes:
        if self._pow_fn is None:
            self.choose_backend()
        assert self._pow_fn is not None
        return self._pow_fn(header_bytes, nonce, prev_hash_hex)

    def selected_name(self) -> str:
        if self._chosen is None:
            self.choose_backend()
        return self._chosen or "unknown"


_state = _BackendState()


def pow_hash(header_bytes: bytes, nonce: int, prev_hash_hex: str) -> bytes:
    """
    Compute PoW digest using Argon2 backend (argon2id).
    Returns 32-byte digest.
    """
    return _state.pow_hash(header_bytes, nonce, prev_hash_hex)


def backend_name() -> str:
    return _state.selected_name()
