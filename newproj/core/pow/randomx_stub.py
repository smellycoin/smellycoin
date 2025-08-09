import os
import struct
import time
import hashlib
from typing import Tuple

# NOTE: This is a placeholder for an ASIC-resistant PoW (e.g., RandomX).
# For production, replace with native bindings to an audited RandomX implementation.
# This stub mixes sha3_256 and memory-hardish loop to be CPU-friendly only for demo.


def _mix(data: bytes, rounds: int = 1000, mem_size: int = 1_000_000) -> bytes:
    buf = bytearray(mem_size)
    seed = int.from_bytes(hashlib.sha3_256(data).digest(), "big")
    idx = seed % mem_size
    val = seed
    for r in range(rounds):
        val = (val * 6364136223846793005 + 1) & ((1 << 64) - 1)
        idx = (idx + (val % 9973)) % mem_size
        buf[idx] = (buf[idx] + (val & 0xFF)) & 0xFF
        if r % 97 == 0:
            # occasional hashing to simulate latency
            h = hashlib.sha3_256(bytes(buf[idx:idx+64])).digest()
            val ^= int.from_bytes(h, "big")
    return hashlib.sha3_256(bytes(buf)).digest()


def pow_hash(header_bytes: bytes, nonce: int) -> bytes:
    # Simple header+nonce mixing
    data = header_bytes + struct.pack(">Q", nonce)
    a = hashlib.sha3_256(data).digest()
    b = _mix(a, rounds=500, mem_size=200_000)  # keep small for demo
    return hashlib.sha3_256(a + b).digest()


def meets_target(hash_bytes: bytes, target_hex: str) -> bool:
    # Compare as big integer with target
    h = int.from_bytes(hash_bytes, "big")
    t = int(target_hex, 16)
    return h <= t


def difficulty_to_target(difficulty: int) -> str:
    # Simplified: target = max_hash // difficulty
    if difficulty <= 0:
        difficulty = 1
    max_hash = (1 << 256) - 1
    target = max_hash // difficulty
    return f"{target:064x}"


def mine(header_bytes: bytes, difficulty: int, start_nonce: int = 0, max_tries: int = 1_000_000) -> Tuple[int, bytes]:
    target = difficulty_to_target(difficulty)
    nonce = start_nonce
    for i in range(max_tries):
        h = pow_hash(header_bytes, nonce)
        if meets_target(h, target):
            return nonce, h
        nonce += 1
    return -1, b""
