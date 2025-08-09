from __future__ import annotations

import argparse
import json
import socket
import threading
import time
import os
import curses
from typing import Optional, List, Tuple

from core.pow.pow_backend import pow_hash
import hashlib

# Lazy import guard for GUI
try:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
except Exception:
    QtCore = QtGui = QtWidgets = None  # type: ignore


class PoolMinerClient:
    def __init__(self, host: str, port: int, address: str, intensity: int = 1):
        self.host = host
        self.port = port
        self.address = address
        self.intensity = max(1, intensity)

        self.sock: Optional[socket.socket] = None
        self.file = None
        self.alive = False

        self.current_job = None  # dict job template
        self.lock = threading.Lock()

        # metrics for TUI
        self.accepted = 0
        self.rejected = 0
        self.per_thread_hashes = {}
        self.last_rate_ts = time.time()
        self.last_rates = {}

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self.file = self.sock.makefile(mode="rwb")
        self.alive = True

        # subscribe/authorize
        self._send({"id": 1, "method": "mining.subscribe", "params": []})
        self._send({"id": 2, "method": "mining.authorize", "params": [self.address]})
        # request job
        self._send({"id": 3, "method": "mining.get_job", "params": []})

        threading.Thread(target=self._reader_loop, daemon=True).start()
        for i in range(self.intensity):
            threading.Thread(target=self._worker_loop, name=f"worker-{i}", daemon=True).start()
        # metrics sampler
        threading.Thread(target=self._rate_loop, daemon=True).start()

    def _reader_loop(self):
        try:
            while self.alive:
                line = self.file.readline()
                if not line:
                    break
                msg = json.loads(line.decode("utf-8").strip())
                self._process_msg(msg)
        except Exception as e:
            print("Reader error:", e)
        finally:
            self.alive = False
            try:
                self.file.close()
                self.sock.close()
            except Exception:
                pass

    def _process_msg(self, msg: dict):
        if msg.get("method") == "mining.notify":
            params = msg.get("params") or {}
            tmpl = params.get("template") or {}
            with self.lock:
                self.current_job = {
                    "job_id": params.get("job_id"),
                    "prev_hash": tmpl.get("prev_hash"),
                    "version": int(tmpl.get("version", 1)),
                    "target_hex": tmpl.get("target"),
                    "timestamp": int(tmpl.get("timestamp", int(time.time()))),
                    "txids": tmpl.get("txids") or [],
                    "pool_target_hex": params.get("pool_target"),
                    "share_diff": params.get("share_diff", 64),
                }
            print("New job:", self.current_job["job_id"], "prev:", (self.current_job["prev_hash"] or "")[:16], "txids:", len(self.current_job["txids"] or []))
            return

        # responses
        if msg.get("id") in (1, 2, 3):
            # subscribe/authorize/get_job replies
            res = msg.get("result")
            err = msg.get("error")
            if err:
                print("RPC error:", err)
            else:
                if msg["id"] == 3 and isinstance(res, dict):
                    tmpl = res.get("template") or {}
                    with self.lock:
                        self.current_job = {
                            "job_id": res.get("job_id"),
                            "prev_hash": tmpl.get("prev_hash"),
                            "version": int(tmpl.get("version", 1)),
                            "target_hex": tmpl.get("target"),
                            "timestamp": int(tmpl.get("timestamp", int(time.time()))),
                            "txids": (tmpl.get("txids") or []),  # preserve exact order from pool
                            "pool_target_hex": res.get("pool_target"),
                            "share_diff": res.get("share_diff", 64),
                        }
                    print("Received job:", self.current_job["job_id"], "prev:", (self.current_job["prev_hash"] or "")[:16], "txids:", len(self.current_job["txids"] or []))
            return

        # submit reply
        if msg.get("id", 0) >= 1000000:
            if msg.get("error"):
                self.rejected += 1
                print("Share rejected:", msg["error"])
            else:
                self.accepted += 1
                print("Share accepted")
            return

    def _header_bytes(self, version: int, prev_hash: str, merkle_root_hex: str, ts: int, target_hex: str, nonce: int, miner_addr: str, tx_count: int) -> bytes:
        # Must match consensus serialization
        fields = [
            ("version", version),
            ("prev_hash_hex", prev_hash),
            ("merkle_root_hex", merkle_root_hex),
            ("timestamp", ts),
            ("target", target_hex),
            ("nonce", nonce),
            ("miner_address", miner_addr),
            ("tx_count", tx_count),
        ]
        return json.dumps(fields, separators=(",", ":"), sort_keys=False).encode("utf-8")

    def _build_merkle(self, height_hint: int, snapshot_txids: List[str]) -> Tuple[str, int]:
        """
        Compute merkle root EXACTLY like consensus.calc_merkle_root used by the node:
        - Order must match the txids array from get_work (coinbase first, then mempool snapshot)
        - If count == 1, root is that single 32-byte txid (as lowercase hex)
        - If count > 1, take pairs (left||right), sha3_256, duplicate last if odd count, iteratively
        - IMPORTANT: inputs and the resulting hex should be lowercase to match node normalization
        """
        txids = snapshot_txids or []
        tx_count = len(txids)
        def h(b: bytes) -> bytes:
            return hashlib.sha3_256(b).digest()
        if tx_count == 0:
            # Fallback; shouldn’t happen because server includes coinbase
            return hashlib.sha3_256(b"").hexdigest(), 0
        layer = [bytes.fromhex(t.lower()) for t in txids]
        while len(layer) > 1:
            if len(layer) % 2 == 1:
                layer.append(layer[-1])
            nxt = []
            for i in range(0, len(layer), 2):
                nxt.append(h(layer[i] + layer[i + 1]))
            layer = nxt
        return layer[0].hex(), tx_count

    def _worker_loop(self):
        # Use thread id as nonce stride
        tid = threading.current_thread().name.split("-")[-1]
        try:
            stride = int(tid)
        except Exception:
            stride = 0
        nonce = stride
        miner_addr = self.address
        report_t = time.time()
        hashes = 0

        while self.alive:
            tname = threading.current_thread().name
            with self.lock:
                job = dict(self.current_job) if self.current_job else None
            if not job or not job.get("job_id"):
                time.sleep(0.2)
                continue

            prev = job["prev_hash"]
            version = int(job["version"])
            target_hex = job["target_hex"]
            ts = int(job["timestamp"])
            txids = job.get("txids") or []
            # txids already include the coinbase first from pool (coinbase = sha3("COINBASE:{height}"))
            # DO NOT modify order; compute merkle exactly as consensus
            mr, tx_count = self._build_merkle(0, txids)
            # ensure lowercase hex for merkle (node expects lowercase hex normalization)
            mr = (mr or "").lower()

            try:
                for _ in range(10000):
                    hdr = self._header_bytes(version, prev, mr, ts, target_hex, nonce, miner_addr, tx_count)
                    digest = pow_hash(hdr, nonce, prev)
                    hashes += 1
                    if int(digest.hex(), 16) <= int(job["pool_target_hex"], 16):
                        # Submit share using template fields; include prev to avoid stale job_id races
                        self._submit_share(job["job_id"], nonce, ts, mr, version, prev)
                    nonce = (nonce + 1) & 0xFFFFFFFF
                # update per-thread hashes every second for TUI
                nowt = time.time()
                if nowt - report_t >= 1.0:
                    self.per_thread_hashes[tname] = hashes / (nowt - report_t)
                    hashes = 0
                    report_t = nowt
            except Exception as e:
                print("Worker error:", e)
                time.sleep(0.2)

    def _submit_share(self, job_id: str, nonce: int, timestamp: int, merkle_root_hex: str, version: int, prev_hash_hex: str):
        """
        Submit a share including prev_hash_hex to avoid stale job_id races.
        Always send the computed merkle_root_hex from txids (lowercase), matching node’s calc.
        """
        req_id = int(time.time() * 1000) + 1_000_000  # large id for submits
        self._send({
            "id": req_id,
            "method": "mining.submit",
            "params": [self.address, job_id, nonce, timestamp, merkle_root_hex.lower(), version, prev_hash_hex.lower()],
        })

    def _send(self, obj: dict):
        try:
            self.file.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.file.flush()
        except Exception as e:
            print("Send error:", e)
            self.alive = False

    # ===== Hashrate sampling for TUI and CLI =====
    def _rate_loop_once(self):
        """
        Aggregate per-thread instantaneous rates into last_rates once per second.
        """
        nowt = time.time()
        if nowt - self.last_rate_ts < 1.0:
            return
        # snapshot and decay slightly to stabilize the display
        snapshot = dict(self.per_thread_hashes)
        # simple EMA-like smoothing
        smoothed = {}
        for k, v in snapshot.items():
            prev = self.last_rates.get(k, 0.0)
            smoothed[k] = 0.6 * prev + 0.4 * max(0.0, float(v))
        self.last_rates = smoothed
        self.last_rate_ts = nowt

    def _rate_loop(self):
        while self.alive:
            try:
                self._rate_loop_once()
                time.sleep(1.0)
            except Exception:
                time.sleep(1.0)

    def close(self):
        self.alive = False
        try:
            self.file.close()
            self.sock.close()
        except Exception:
            pass


def _run_tui(stdscr, client: "PoolMinerClient"):
    curses.curs_set(0)
    stdscr.nodelay(True)
    color_ok = curses.has_colors()
    if color_ok:
        curses.start_color()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # title
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)   # ok
        curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)     # bad
        curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)    # info

    def bar(win, y, x, width, ratio, label, good=True):
        ratio = max(0.0, min(1.0, ratio))
        filled = int(width * ratio)
        col = curses.color_pair(2 if good else 3) if color_ok else 0
        win.addstr(y, x, "[" + "#" * filled + "-" * (width - filled) + "] ", col)
        win.addstr(y, x + width + 3, label)

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        title = f"SMELLY Pool Miner TUI  |  addr={client.address[:18]}...  host={client.host}:{client.port}  workers={client.intensity}  q=quit"
        stdscr.addstr(0, 0, title[:w-1], curses.color_pair(1) if color_ok else 0)
        # Job section
        with client.lock:
            job = dict(client.current_job) if client.current_job else None
        y = 2
        if job:
            stdscr.addstr(y, 0, f"Job: {job['job_id'][:16]}... prev={job['prev_hash'][:16]}.. target={job['target_hex'][:8]}.. txids={len(job.get('txids') or [])}", curses.color_pair(4) if color_ok else 0)
        else:
            stdscr.addstr(y, 0, "Job: (none)", curses.color_pair(3) if color_ok else 0)
        y += 2
        # Stats
        total_hs = sum(client.last_rates.values()) if client.last_rates else 0.0
        stdscr.addstr(y, 0, f"Accepted: {client.accepted}   Rejected: {client.rejected}   Total ~{int(total_hs)} H/s")
        y += 1
        # Per-thread bars
        max_bar_w = max(10, min(50, w - 20))
        rates = sorted(client.last_rates.items())
        for name, r in rates:
            bar(stdscr, y, 0, max_bar_w, min(1.0, r / (total_hs + 1e-9) if total_hs > 0 else 0.0), f"{name}: {int(r)} H/s")
            y += 1
            if y >= h - 2:
                break

        stdscr.refresh()
        # handle keys
        try:
            ch = stdscr.getch()
            if ch == ord('q'):
                break
        except Exception:
            pass
        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description="SMELLY Pool Miner (Stratum-like)")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28446)
    parser.add_argument("--address", type=str, default="SMELLY_POOL_MINER")
    parser.add_argument("--intensity", type=int, default=1, help="Worker threads")
    parser.add_argument("--tui", action="store_true", help="Show curses-based TUI (like top)")
    parser.add_argument("--gui", action="store_true", help="Launch SMELLY-Miner GUI")
    args = parser.parse_args()

    if args.gui:
        # Defer to GUI launcher without importing at module load to avoid PySide6 dependency for CLI-only usage
        try:
            from apps.miner.smelly_gui import launch_gui  # type: ignore
        except Exception as e:
            print("GUI not available. Ensure PySide6 is installed (pip install -r requirements.txt). Error:", e)
            return 1
        return launch_gui(default_host=args.host, default_port=args.port, default_address=args.address, default_intensity=args.intensity)

    client = PoolMinerClient(args.host, args.port, args.address, args.intensity)
    print(f"Connecting to pool {args.host}:{args.port} as {args.address} with {args.intensity} workers...")
    client.connect()

    try:
        if args.tui and os.name != "nt":  # Windows curses is limited in many terminals
            curses.wrapper(_run_tui, client)
        else:
            while True:
                # sample total rate every second for non-TUI mode
                client._rate_loop_once()
                time.sleep(1)
    except KeyboardInterrupt:
        client.close()


if __name__ == "__main__":
    main()
