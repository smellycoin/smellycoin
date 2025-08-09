from __future__ import annotations

import argparse
import os
import threading
import time
import json
import math
import queue
import requests
from dataclasses import dataclass
from typing import Optional, List, Tuple

from core.config import get_config
from core.pow.randomx_stub import pow_hash


def rpc_url() -> str:
    cfg = get_config()
    return f"http://{cfg.get('network.rpc_host','127.0.0.1')}:{cfg.get('network.rpc_port',28445)}"


# -------- Legacy single-shot (server mines) --------
def mine_one(miner_address: str, timeout_sec: int = 25) -> Optional[str]:
    r = requests.post(
        f"{rpc_url()}/rpc/mine_one",
        json={"miner_address": miner_address},
        timeout=timeout_sec
    )
    r.raise_for_status()
    js = r.json()
    return js.get("hash") if isinstance(js, dict) else None


# -------- Client-side mining (multi-thread) --------
@dataclass
class Work:
    job_id: str
    height: int
    prev_hash: str
    target_hex: str
    version: int
    timestamp: int
    txids: List[str]
    miner_hint: str


def get_work(miner_address: Optional[str]) -> Optional[Work]:
    try:
        r = requests.post(
            f"{rpc_url()}/rpc/get_work",
            json={"miner_address": miner_address} if miner_address else {},
            timeout=10,
        )
        r.raise_for_status()
        w = r.json()
        return Work(
            job_id=w["job_id"],
            height=w["height"],
            prev_hash=w["prev_hash"],
            target_hex=w["target"],
            version=w["version"],
            timestamp=w["timestamp"],
            txids=w.get("txids", []),
            miner_hint=w.get("miner_hint", ""),
        )
    except Exception as e:
        print("get_work error:", e)
        return None


def submit_work(job_id: str, miner_address: str, nonce: int, version: int, timestamp: int, merkle_root_hex: str) -> Tuple[bool, Optional[str]]:
    try:
        r = requests.post(
            f"{rpc_url()}/rpc/submit_work",
            json={
                "job_id": job_id,
                "miner_address": miner_address,
                "nonce": nonce,
                "timestamp": timestamp,
                "version": version,
                "merkle_root_hex": merkle_root_hex,
                # prev_hash_hex optional; server checks anyway
            },
            timeout=15,
        )
        if r.status_code >= 400:
            try:
                d = r.json()
                # FastAPI HTTPException with dict detail
                detail = d.get("detail", d)
                if isinstance(detail, dict):
                    return False, detail.get("error") or str(detail)
                return False, str(detail)
            except Exception:
                return False, f"HTTP {r.status_code}"
        js = r.json()
        if js.get("accepted"):
            return True, js.get("hash")
        else:
            return False, js.get("error") or "rejected"
    except Exception as e:
        return False, str(e)


def header_serialize(version: int, prev_hash_hex: str, merkle_root_hex: str, timestamp: int, target_hex: str, nonce: int, miner_address: str, tx_count: int) -> bytes:
    # Must match consensus.Header.serialize()
    fields = [
        ("version", version),
        ("prev_hash_hex", prev_hash_hex),
        ("merkle_root_hex", merkle_root_hex),
        ("timestamp", timestamp),
        ("target", target_hex),
        ("nonce", nonce),
        ("miner_address", miner_address),
        ("tx_count", tx_count),
    ]
    return json.dumps(fields, separators=(",", ":"), sort_keys=False).encode("utf-8")


def merkle_root_from_txids(txids: List[str]) -> str:
    # Must match consensus.merkle_root_hex()
    import hashlib
    def sha3_256_hex(b: bytes) -> str:
        return hashlib.sha3_256(b).hexdigest()
    if not txids:
        return sha3_256_hex(b"")
    layer = [bytes.fromhex(t) for t in txids]
    while len(layer) > 1:
        nxt = []
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        for i in range(0, len(layer), 2):
            nxt.append(bytes.fromhex(sha3_256_hex(layer[i] + layer[i + 1])))
        layer = nxt
    return layer[0].hex()


def coinbase_txid_for_height(height: int) -> str:
    import hashlib
    return hashlib.sha3_256(f"COINBASE:{height}".encode("utf-8")).hexdigest()


def build_merkle_root_for_job(height: int, snapshot_txids: List[str]) -> Tuple[str, List[str]]:
    # coinbase first, then snapshot txids
    txids = [coinbase_txid_for_height(height)] + list(snapshot_txids or [])
    return merkle_root_from_txids(txids), txids


def mine_client_side(miner_address: str, threads: int, slice_ms: int, poll_ms: int):
    print(f"Client miner starting: threads={threads}, slice_ms={slice_ms}, poll_ms={poll_ms}")
    stop_evt = threading.Event()
    stats_lock = threading.Lock()
    last_hashes = [0 for _ in range(threads)]
    accepted_total = 0

    def worker(tid: int):
        nonlocal accepted_total
        rng = 0
        while not stop_evt.is_set():
            # Get or refresh work
            w = get_work(miner_address)
            if not w:
                time.sleep(max(0.1, poll_ms / 1000.0))
                continue

            mr, txids_used = build_merkle_root_for_job(w.height, w.txids)
            target_int = int(w.target_hex, 16)
            start = time.time()
            hashes = 0
            # thread-specific stride: try nonces tid, tid+threads, tid+2*threads...
            nonce = tid
            while (time.time() - start) * 1000.0 < slice_ms and not stop_evt.is_set():
                # Construct header JSON matching server format
                hdr_bytes = header_serialize(
                    version=w.version,
                    prev_hash_hex=w.prev_hash,
                    merkle_root_hex=mr,
                    timestamp=w.timestamp,  # static for this slice; server accepts submitted timestamp
                    target_hex=w.target_hex,
                    nonce=nonce,
                    miner_address=miner_address,
                    tx_count=len(txids_used),
                )
                h = pow_hash(hdr_bytes, nonce)
                if int(h.hex(), 16) <= target_int:
                    ok, res = submit_work(
                        job_id=w.job_id,
                        miner_address=miner_address,
                        nonce=nonce,
                        version=w.version,
                        timestamp=w.timestamp,
                        merkle_root_hex=mr,
                    )
                    if ok:
                        with stats_lock:
                            accepted_total += 1
                        print(f"[T{tid}] ACCEPTED block {res} at nonce={nonce}")
                        # after acceptance, fetch new work
                        break
                    else:
                        # If stale, break slice and refresh work immediately
                        if res and ("stale" in res or "stale-prev" in res or "expired" in res):
                            print(f"[T{tid}] submit stale: {res}")
                            break
                        else:
                            print(f"[T{tid}] submit rejected: {res}")
                hashes += 1
                nonce += threads

            with stats_lock:
                last_hashes[tid] = hashes
            # short pause before next slice
            time.sleep(max(0.0, poll_ms / 1000.0))

    # Launch workers
    threads = max(1, threads)
    ths: List[threading.Thread] = []
    for i in range(threads):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        ths.append(t)

    try:
        last_t = time.time()
        while True:
            time.sleep(2.0)
            with stats_lock:
                total_h = sum(last_hashes)
                print(f"Hashrate ~ {total_h/2.0:.0f} H/s | accepted={accepted_total} | per-thread={last_hashes}")
                # reset counters for next interval measurement
                for i in range(len(last_hashes)):
                    last_hashes[i] = 0
    except KeyboardInterrupt:
        print("Stopping miner...")
        stop_evt.set()
        for t in ths:
            t.join(timeout=1.0)


def main():
    parser = argparse.ArgumentParser(description="SMELLY Client/Legacy Miner")
    parser.add_argument("--miner-address", type=str, default="SMELLY_SOLO")
    parser.add_argument("--mode", type=str, choices=["client", "legacy"], default="client",
                        help="client: mines locally using get_work/submit_work; legacy: calls /rpc/mine_one")
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--slice-ms", type=int, default=250, help="time slice per work attempt per thread")
    parser.add_argument("--poll-ms", type=int, default=200, help="sleep between work polls")
    parser.add_argument("--loop", action="store_true", help="legacy only: continuously call mine_one()")
    args = parser.parse_args()

    print("Solo miner using RPC:", rpc_url())
    if args.mode == "legacy":
        if args.loop:
            while True:
                try:
                    hh = mine_one(args.miner_address, timeout_sec=25)
                    if hh:
                        print("Mined header:", hh)
                    else:
                        print("No block found this round.")
                except requests.exceptions.ReadTimeout:
                    print("Mine timeout: node did not respond within 25s; will retry...")
                except Exception as e:
                    print("Mine error:", e)
                time.sleep(0.5)
        else:
            try:
                hh = mine_one(args.miner_address, timeout_sec=25)
                print("Mined header:", hh)
            except requests.exceptions.ReadTimeout:
                print("Mine timeout: node did not respond within 25s.")
            except Exception as e:
                print("Mine error:", e)
    else:
        mine_client_side(args.miner_address, threads=args.threads, slice_ms=args.slice_ms, poll_ms=args.poll_ms)


if __name__ == "__main__":
    main()
