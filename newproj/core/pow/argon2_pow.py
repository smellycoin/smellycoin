from __future__ import annotations

import struct
from typing import Optional

from argon2.low_level import hash_secret_raw, Type as Argon2Type
from core.config import get_config


# Argon2id PoW backend
# digest = Argon2id(secret = header_bytes || nonce_le_u64, salt = prev_hash_bytes,
#                   time_cost=T, memory_cost=MiB, parallelism=P, hash_len=32)
# Returns 32-byte digest.
def pow_hash(header_bytes: bytes, nonce: int, prev_hash_hex: str) -> bytes:
    cfg = get_config()
    time_cost = int(cfg.get("consensus.argon2.time_cost", 2))
    memory_mib = int(cfg.get("consensus.argon2.memory_mib", 64))
    parallelism = int(cfg.get("consensus.argon2.parallelism", 1))

    # secret = header || nonce_le_u64
    nonce_le8 = struct.pack("<Q", nonce & 0xFFFFFFFFFFFFFFFF)
    secret = header_bytes + nonce_le8

    # salt from prev hash (32 bytes expected from hex)
    try:
        salt = bytes.fromhex(prev_hash_hex)
    except Exception:
        salt = b"\x00" * 32

    digest = hash_secret_raw(
        secret=secret,
        salt=salt,
        time_cost=time_cost,
        memory_cost=memory_mib * 1024,  # Argon2 expects KiB
        parallelism=parallelism,
        hash_len=32,
        type=Argon2Type.ID,
        version=19,
    )
    return digest
