from __future__ import annotations

import ctypes
import os
import threading
from typing import Optional

from core.config import get_config


# Minimal ctypes bindings for RandomX. We attempt to load a DLL from a fixed
# project path and from PATH. If loading or symbol resolution fails, this
# module reports unavailable so the pow_backend can fall back to Argon2.
#
# Expected exports (standard RandomX C API):
#   randomx_create_cache
#   randomx_init_cache
#   randomx_release_cache
#   randomx_create_vm
#   randomx_destroy_vm
#   randomx_vm_set_cache
#   randomx_calculate_hash
#
# We use "light" mode with cache only to avoid requiring the dataset for a first version.
# If your DLL supports dataset functions and you prefer full dataset, this can be extended.


_RANDX_DLL_FIXED = r"e:\newproj\third_party\randomx\win64\randomx.dll"

# Opaque pointer types
randomx_cache_p = ctypes.c_void_p
randomx_vm_p = ctypes.c_void_p

# Global state (per-process)
class _RXState:
    def __init__(self):
        self._loaded = False
        self._dll: Optional[ctypes.CDLL] = None
        # resolved funcs
        self._create_cache = None
        self._init_cache = None
        self._release_cache = None
        self._create_vm = None
        self._destroy_vm = None
        self._vm_set_cache = None
        self._calculate_hash = None

        self._cache: Optional[randomx_cache_p] = None
        self._vm: Optional[randomx_vm_p] = None
        self._seed_hex: Optional[str] = None
        self._lock = threading.Lock()

    def _try_load(self) -> bool:
        # Try fixed project path first
        paths = []
        paths.append(_RANDX_DLL_FIXED)
        # Then rely on PATH
        paths.append("randomx.dll")
        last_err = None
        for p in paths:
            try:
                dll = ctypes.WinDLL(p)
                self._dll = dll
                # Resolve symbols with standard signatures
                # void* randomx_create_cache(randomx_flags flags);
                self._create_cache = dll.randomx_create_cache
                self._create_cache.restype = randomx_cache_p
                self._create_cache.argtypes = [ctypes.c_uint32]
                # void randomx_init_cache(void* cache, const void* key, size_t keySize);
                self._init_cache = dll.randomx_init_cache
                self._init_cache.restype = None
                self._init_cache.argtypes = [randomx_cache_p, ctypes.c_void_p, ctypes.c_size_t]
                # void randomx_release_cache(void* cache);
                self._release_cache = dll.randomx_release_cache
                self._release_cache.restype = None
                self._release_cache.argtypes = [randomx_cache_p]
                # void* randomx_create_vm(randomx_flags flags, void* cache, void* dataset);
                self._create_vm = dll.randomx_create_vm
                self._create_vm.restype = randomx_vm_p
                self._create_vm.argtypes = [ctypes.c_uint32, randomx_cache_p, ctypes.c_void_p]
                # void randomx_destroy_vm(void* machine);
                self._destroy_vm = dll.randomx_destroy_vm
                self._destroy_vm.restype = None
                self._destroy_vm.argtypes = [randomx_vm_p]
                # void randomx_vm_set_cache(void* machine, void* cache);
                self._vm_set_cache = dll.randomx_vm_set_cache
                self._vm_set_cache.restype = None
                self._vm_set_cache.argtypes = [randomx_vm_p, randomx_cache_p]
                # void randomx_calculate_hash(void* machine, const void* input, size_t inputSize, void* output);
                self._calculate_hash = dll.randomx_calculate_hash
                self._calculate_hash.restype = None
                self._calculate_hash.argtypes = [randomx_vm_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]

                self._loaded = True
                return True
            except Exception as e:
                last_err = e
                self._dll = None
        return False

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        with self._lock:
            if self._loaded:
                return True
            return self._try_load()

    def _flags(self) -> int:
        # Basic flags: RANDOMX_FLAG_DEFAULT (0), optionally JIT (1), LARGE_PAGES etc.
        # Use JIT if DLL supports it; here we pass 1 to enable JIT.
        # If this causes issues, set to 0.
        cfg = get_config()
        use_jit = bool(int(cfg.get("consensus.randomx_use_jit", 1)))
        flags = 1 if use_jit else 0
        return flags

    def _ensure_vm_for_seed(self, seed_hex: str):
        if not self._ensure_loaded():
            raise RuntimeError("RandomX DLL not loaded")

        with self._lock:
            if self._seed_hex == seed_hex and self._vm:
                return

            # Tear down existing
            try:
                if self._vm:
                    self._destroy_vm(self._vm)
            except Exception:
                pass
            self._vm = None

            try:
                if self._cache:
                    self._release_cache(self._cache)
            except Exception:
                pass
            self._cache = None

            # Build new cache and VM
            key = bytes.fromhex(seed_hex) if seed_hex and len(seed_hex) >= 2 else b"\x00" * 32
            flags = self._flags()

            cache = self._create_cache(flags)
            if not cache:
                raise RuntimeError("randomx_create_cache failed")
            # init cache
            key_buf = (ctypes.c_ubyte * len(key)).from_buffer_copy(key)
            self._init_cache(cache, ctypes.cast(key_buf, ctypes.c_void_p), ctypes.c_size_t(len(key)))

            vm = self._create_vm(flags, cache, None)
            if not vm:
                # cleanup
                self._release_cache(cache)
                raise RuntimeError("randomx_create_vm failed")

            self._cache = cache
            self._vm = vm
            self._seed_hex = seed_hex

    def calculate_hash(self, data: bytes, seed_hex: str) -> bytes:
        self._ensure_vm_for_seed(seed_hex)
        assert self._vm is not None
        # Prepare input
        in_buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        out_buf = (ctypes.c_ubyte * 32)()
        self._calculate_hash(self._vm, ctypes.cast(in_buf, ctypes.c_void_p), ctypes.c_size_t(len(data)), ctypes.cast(out_buf, ctypes.c_void_p))
        return bytes(out_buf)


_state = _RXState()


def randomx_available() -> bool:
    return _state._ensure_loaded()


def _seed_from_prev(prev_hash_hex: str) -> str:
    # Seed by previous hash; if invalid, use zeros
    try:
        bytes.fromhex(prev_hash_hex)
        return prev_hash_hex
    except Exception:
        return "00" * 32


def pow_hash(header_bytes: bytes, nonce: int, prev_hash_hex: str) -> bytes:
    """
    Compute RandomX digest for header||nonce_le_u64 using a VM seeded by prev_hash_hex.
    Returns 32-byte digest.
    """
    nonce_le8 = nonce.to_bytes(8, byteorder="little", signed=False)
    data = header_bytes + nonce_le8
    seed = _seed_from_prev(prev_hash_hex)
    return _state.calculate_hash(data, seed)
