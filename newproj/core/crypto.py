from __future__ import annotations

import os
import hmac
import struct
import json
from typing import Tuple, Optional, Any, Dict

import nacl.signing
import nacl.encoding
from nacl.exceptions import BadSignatureError
from mnemonic import Mnemonic

from core.utils import sha3_256_hex


# NOTE: For production, replace with audited libs and constant-time implementations.
# This module provides simple wrappers for:
# - Ed25519 keypairs for spending and viewing (Monero-like split concept, simplified)
# - Seed/mnemonic generation
# - Address encoding with custom prefix "SMELLY_"
# - Subaddress derivation pattern (Monero-inspired, simplified and NOT compatible with XMR)
# - Transaction digesting and signature verification helpers


ADDRESS_PREFIX = "SMELLY_"


def generate_seed(entropy_bits: int = 256, language: str = "english") -> Tuple[str, bytes]:
    if entropy_bits % 32 != 0 or entropy_bits < 128 or entropy_bits > 256:
        raise ValueError("entropy_bits must be 128..256 and multiple of 32")
    mnemo = Mnemonic(language)
    entropy = os.urandom(entropy_bits // 8)
    words = mnemo.to_mnemonic(entropy)
    seed = mnemo.to_seed(words, passphrase="")  # 512-bit seed
    return words, seed


def ed25519_keypair_from_seed(seed: bytes, ctx: bytes = b"smelly-chain") -> Tuple[bytes, bytes]:
    # Derive a 32-byte key using HMAC-SHA3
    digest_hex = sha3_256_hex(hmac.new(ctx, seed, digestmod="sha3_256").digest())
    sk_seed = bytes.fromhex(digest_hex)[:32]
    signing_key = nacl.signing.SigningKey(sk_seed)
    verify_key = signing_key.verify_key
    return bytes(signing_key), bytes(verify_key)


def keccak256_hex(data: bytes) -> str:
    """
    Use hashlib.sha3_256 (available in Python 3.6+) to avoid dependency on pynacl.hash algorithms.
    Returns lowercase hex string.
    """
    import hashlib
    return hashlib.sha3_256(data).hexdigest()


def encode_address(pub_view_key: bytes, pub_spend_key: bytes) -> str:
    core = pub_view_key + pub_spend_key
    checksum = keccak256_hex(core)[:8]
    addr_body = (core + bytes.fromhex(checksum)).hex()
    return f"{ADDRESS_PREFIX}{addr_body}"


def decode_address(address: str) -> Tuple[bytes, bytes]:
    if not address.startswith(ADDRESS_PREFIX):
        raise ValueError("Invalid address prefix")
    body_hex = address[len(ADDRESS_PREFIX):]
    body = bytes.fromhex(body_hex)
    if len(body) != 32 + 32 + 4:
        raise ValueError("Invalid address length")
    pub_view = body[:32]
    pub_spend = body[32:64]
    checksum = body[64:]
    if keccak256_hex(pub_view + pub_spend)[:8] != checksum.hex():
        raise ValueError("Invalid address checksum")
    return pub_view, pub_spend


def derive_subaddress(pub_view_key: bytes, pub_spend_key: bytes, major: int, minor: int) -> str:
    # Monero-like concept (NOT compatible). For demo only.
    data = pub_view_key + pub_spend_key + struct.pack(">II", major, minor)
    tweak_hex = keccak256_hex(data)
    tweak = bytes.fromhex(tweak_hex)[:32]
    # naive tweak: xor into spend key to get sub-spend key
    sub_spend = bytes(a ^ b for a, b in zip(pub_spend_key, tweak))
    return encode_address(pub_view_key, sub_spend)


def sign(message: bytes, sk: bytes) -> bytes:
    key = nacl.signing.SigningKey(sk)
    signed = key.sign(message)
    return signed.signature


def verify(message: bytes, sig: bytes, pk: bytes) -> bool:
    try:
        vk = nacl.signing.VerifyKey(pk)
        vk.verify(message, sig)
        return True
    except Exception:
        return False


# ---------- Transaction digesting + signature helpers ----------

def tx_canonical_json(tx_obj: Dict[str, Any]) -> bytes:
    """
    Stable canonical JSON encoding for transactions.
    Excludes any 'signatures' field to ensure deterministic digest for signing.
    """
    filtered = {k: v for k, v in tx_obj.items() if k != "signatures"}
    # Ensure stable key ordering and compact separators
    return json.dumps(filtered, sort_keys=True, separators=(",", ":")).encode("utf-8")


def tx_digest_hex(tx_obj: Dict[str, Any]) -> str:
    """
    Digest used for signing: sha3-256 hex of canonical JSON.
    """
    return sha3_256_hex(tx_canonical_json(tx_obj))


def ed25519_verify_hex(pubkey_hex: str, msg: bytes, sig_hex: str) -> bool:
    """
    Verify an Ed25519 signature from hex-encoded pubkey and signature.
    """
    try:
        vk = nacl.signing.VerifyKey(bytes.fromhex(pubkey_hex))
        vk.verify(msg, bytes.fromhex(sig_hex))
        return True
    except BadSignatureError:
        return False
    except Exception:
        return False
