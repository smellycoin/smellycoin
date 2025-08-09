# gui_core.py
import threading
import time
from typing import Callable, Dict, List

from .solo_miner import get_work, submit_work, build_merkle_root_for_job, header_serialize, pow_hash
from .config import get_config


class SoloMinerCore:
    """
    GUI-compatible core class wrapping client-side miner from solo_miner.py
    """
    def __init__(self):
        self._stop_evt = threading.Event()
        self._threads = []
        self._cfg = get_config()
        self._miner_address = self._cfg.get("miner.default_address", "SMELLY_SOLO")
        self._thread_count = int(self._cfg.get("miner.threads", 4))
        self._poll_ms = 200
        self._slice_ms = 250

        self._accepted_blocks = 0
        self._last_hashes = [0 for _ in range(self._thread_count)]
        self._hash_lock = threading.Lock()

        # GUI Callbacks
        self.on_log: Callable[[str, str], None] = lambda lvl, msg: None
        self.on_status: Callable[[str], None] = lambda st: None
        self.on_rates: Callable[[float, Dict[str, float]], None] = lambda t, p: None
        self.on_accepts: Callable[[int, int], None] = lambda near, blocks: None
        self.on_ticket: Callable[[Dict], None] = lambda t: None
        self.on_error: Callable[[str], None] = lambda e: None

    def set_config(self, host=None, port=None, addr=None, threads=None):
        if addr:
            self._miner_address = addr
        if threads:
            self._thread_count = int(threads)
            self._last_hashes = [0 for _ in range(self._thread_count)]

    def start(self):
        if self._threads:
            self.stop(join=True)

        self.on_log("info", f"Starting miner with {self._thread_count} threads")
        self.on_status("mining")
        self._stop_evt.clear()

        for tid in range(self._thread_count):
            th = threading.Thread(target=self._worker, args=(tid,), daemon=True)
            self._threads.append(th)
            th.start()

        reporter = threading.Thread(target=self._reporter_loop, daemon=True)
        reporter.start()

    def stop(self, join=False):
        self._stop_evt.set()
        if join:
            for t in self._threads:
                t.join(timeout=1.0)
        self._threads.clear()
        self.on_status("stopped")

    def restart(self):
        self.stop(join=True)
        self.start()

    def _worker(self, tid: int):
        while not self._stop_evt.is_set():
            work = get_work(self._miner_address)
            if not work:
                time.sleep(self._poll_ms / 1000.0)
                continue

            merkle_root, txids_used = build_merkle_root_for_job(work.height, work.txids)
            target_int = int(work.target_hex, 16)
            start = time.time()
            hashes = 0
            nonce = tid

            while (time.time() - start) * 1000.0 < self._slice_ms and not self._stop_evt.is_set():
                hdr_bytes = header_serialize(
                    version=work.version,
                    prev_hash_hex=work.prev_hash,
                    merkle_root_hex=merkle_root,
                    timestamp=work.timestamp,
                    target_hex=work.target_hex,
                    nonce=nonce,
                    miner_address=self._miner_address,
                    tx_count=len(txids_used),
                )
                digest = pow_hash(hdr_bytes, nonce)
                if int(digest.hex(), 16) <= target_int:
                    ok, res = submit_work(
                        job_id=work.job_id,
                        miner_address=self._miner_address,
                        nonce=nonce,
                        version=work.version,
                        timestamp=work.timestamp,
                        merkle_root_hex=merkle_root,
                    )
                    if ok:
                        self._accepted_blocks += 1
                        self.on_log("info", f"[T{tid}] Accepted block {res}")
                        self.on_accepts(self._accepted_blocks, 0)
                        break
                    else:
                        self.on_log("warn", f"[T{tid}] Submit rejected: {res}")
                        if res and ("stale" in res or "expired" in res):
                            break
                hashes += 1
                nonce += self._thread_count

            with self._hash_lock:
                self._last_hashes[tid] = hashes

            time.sleep(self._poll_ms / 1000.0)

    def _reporter_loop(self):
        while not self._stop_evt.is_set():
            time.sleep(2.0)
            with self._hash_lock:
                total = sum(self._last_hashes)
                per = {str(i): float(h) / 2.0 for i, h in enumerate(self._last_hashes)}
                self._last_hashes = [0 for _ in self._last_hashes]
            self.on_rates(total / 2.0, per)
