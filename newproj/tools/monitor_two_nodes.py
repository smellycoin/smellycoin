"""
Monitor two SMELLY nodes, start them on distinct ports with isolated DBs, start explorer,
connect peers, and run a continuous client-side miner with structured logging.

Usage:
  python -m tools.monitor_two_nodes

What it does:
- Frees ports: A RPC 28445, A P2P 28447, B RPC 38555, B P2P 38557, Explorer 28448
- Starts Node A (mining) and Node B (non-mining) as subprocesses
- Starts Explorer (reads default explorer port 28448)
- Connects B -> A using /rpc/p2p/connect
- Runs a client-side miner loop against Node A via /rpc/get_work + /rpc/submit_work
- Every 5 seconds logs heights, tip hash, mempool size, and alerts on stalls/divergence
- On Ctrl+C, gracefully stops all children and exits
"""

import os
import sys
import time
import json
import threading
import subprocess
import signal
from datetime import datetime
from typing import Optional, Tuple

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

A_RPC = 28445
A_P2P = 28447
B_RPC = 38555
B_P2P = 38557
EXPLORER_PORT = 28448

A_BASE = f"http://127.0.0.1:{A_RPC}"
B_BASE = f"http://127.0.0.1:{B_RPC}"
EXP_URL = f"http://127.0.0.1:{EXPLORER_PORT}"

LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f"monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

STOP = False
PROCS = []  # child processes for cleanup


def log(msg: str):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def env_for(tag: str, rpc_port: int, p2p_port: int, db_path: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["SMELLY_DB_PATH"] = db_path
    env["SMELLY_RPC_HOST"] = "127.0.0.1"
    env["SMELLY_RPC_PORT"] = str(rpc_port)
    env["SMELLY_P2P_HOST"] = "127.0.0.1"
    env["SMELLY_P2P_PORT"] = str(p2p_port)
    env["SMELLY_NODE_TAG"] = tag
    env["SMELLY_FORCE_PORTS"] = "1"
    return env


def _kill_on_port(port: int):
    # Windows netstat + taskkill; no-op on error
    try:
        cmd = ["cmd", "/c", f'netstat -ano | findstr ":{port} "']
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True, cwd=ROOT)
        pids = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5:
                local = parts[1] if ":" in parts[1] else ""
                pid = parts[-1]
                if local.endswith(f":{port}"):
                    try:
                        pids.add(int(pid))
                    except Exception:
                        pass
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], check=False, cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log(f"Killed process on port {port}: PID {pid}")
            except Exception:
                pass
    except Exception:
        pass


def free_ports():
    for port in (A_RPC, A_P2P, B_RPC, B_P2P, EXPLORER_PORT):
        _kill_on_port(port)


def start_node(tag: str, rpc_port: int, p2p_port: int, mine: bool, miner_address: str, db_file: str) -> subprocess.Popen:
    args = [PY, "-m", "apps.node.main"]
    if mine:
        args += ["--mine", "--miner-address", miner_address]
    env = env_for(tag, rpc_port, p2p_port, db_file)
    proc = subprocess.Popen(
        args, cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, universal_newlines=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    )
    PROCS.append(proc)
    log(f"Started Node {tag} (RPC {rpc_port}, P2P {p2p_port}) PID={proc.pid}")
    return proc


def start_explorer() -> subprocess.Popen:
    args = [PY, "-m", "apps.explorer.server"]
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        args, cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, universal_newlines=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    )
    PROCS.append(proc)
    log(f"Started Explorer PID={proc.pid}")
    return proc


def follow_logs(name: str, proc: subprocess.Popen):
    try:
        if not proc.stdout:
            return
        for line in iter(proc.stdout.readline, ''):
            if STOP:
                break
            if line:
                log(f"[{name}] {line.rstrip()}")
    except Exception:
        pass


def wait_http_ok(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline and not STOP:
        try:
            r = requests.get(url, timeout=3)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def rpc_height(base: str) -> int:
    r = requests.get(f"{base}/rpc/get_height", timeout=5)
    r.raise_for_status()
    return int(r.json()["height"])


def rpc_mempool(base: str) -> int:
    try:
        r = requests.get(f"{base}/rpc/mempool", timeout=5)
        r.raise_for_status()
        return len(r.json())
    except Exception:
        return -1


def rpc_get_work(base: str, miner: str) -> Optional[dict]:
    try:
        r = requests.post(f"{base}/rpc/get_work", json={"miner_address": miner}, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def rpc_submit_work(base: str, payload: dict) -> Tuple[bool, str]:
    try:
        r = requests.post(f"{base}/rpc/submit_work", json=payload, timeout=10)
        if r.status_code == 200:
            return True, r.json().get("hash", "")
        else:
            try:
                d = r.json()
            except Exception:
                d = {"error": r.text}
            return False, str(d)
    except Exception as e:
        return False, str(e)


def _sha3_256_hex(b: bytes) -> str:
    import hashlib
    return hashlib.sha3_256(b).hexdigest()


def _coinbase_txid(height: int) -> str:
    return _sha3_256_hex(f"COINBASE:{height}".encode("utf-8"))


def _merkle_root(txids: list[str]) -> str:
    import hashlib
    def h(x: bytes) -> str:
        return hashlib.sha3_256(x).hexdigest()
    if not txids:
        return h(b"")
    layer = [bytes.fromhex(t) for t in txids]
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        nxt = []
        for i in range(0, len(layer), 2):
            nxt.append(bytes.fromhex(h(layer[i] + layer[i+1])))
        layer = nxt
    return layer[0].hex()


def _header_serialize(version: int, prev_hash_hex: str, merkle_root_hex: str, timestamp: int, target_hex: str, nonce: int, miner_address: str, tx_count: int) -> bytes:
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


def miner_loop(base: str, miner_addr: str, jitter_sec: float = 0.0, threads: int = 2, slice_ms: int = 250, poll_ms: int = 200):
    """
    Real client-side PoW miner:
    - fetches /rpc/get_work
    - builds coinbase+snapshot merkle
    - iterates nonces per thread, stride = thread_id
    - submits on hit via /rpc/submit_work
    """
    from core.pow.pow_backend import pow_hash  # import here to allow backend selection logs earlier
    if jitter_sec > 0:
        time.sleep(jitter_sec)

    stop_evt = threading.Event()
    stats_lock = threading.Lock()
    last_counts = [0 for _ in range(threads)]
    accepted = 0

    def worker(tid: int):
        nonlocal accepted
        while not stop_evt.is_set() and not STOP:
            job = rpc_get_work(base, miner_addr)
            if not job:
                time.sleep(max(0.1, poll_ms/1000.0))
                continue
            height = int(job.get("height", 0))
            version = int(job.get("version", 1))
            prev = job.get("prev_hash", "0"*64)
            target_hex = job.get("target", "f"*64)
            target_int = int(target_hex, 16)
            # snapshot txids: coinbase first, then given order
            txids = [_coinbase_txid(height)] + list(job.get("txids") or [])
            mr = _merkle_root(txids)
            ts = int(time.time())

            # iterate nonces for this slice
            start = time.time()
            nonce = tid
            local = 0
            while (time.time() - start) * 1000.0 < slice_ms and not stop_evt.is_set() and not STOP:
                hdr = _header_serialize(version, prev, mr, ts, target_hex, nonce, miner_addr, len(txids))
                digest = pow_hash(hdr, nonce, prev)
                if int(digest.hex(), 16) <= target_int:
                    ok, info = rpc_submit_work(base, {
                        "job_id": job["job_id"],
                        "miner_address": miner_addr,
                        "nonce": nonce,
                        "timestamp": ts,
                        "version": version,
                        "merkle_root_hex": mr,
                        "prev_hash_hex": prev,
                    })
                    if ok:
                        with stats_lock:
                            accepted += 1
                        log(f"[MINER {miner_addr} T{tid}] ACCEPTED {info} at nonce={nonce}")
                        # break to fetch fresh job
                        break
                local += 1
                nonce += threads
            with stats_lock:
                last_counts[tid] = local
            time.sleep(poll_ms/1000.0)

    # launch worker threads
    ths = []
    for i in range(threads):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        ths.append(t)

    try:
        while not STOP:
            time.sleep(5.0)
            with stats_lock:
                total = sum(last_counts)
                log(f"[MINER {miner_addr}] ~{total/5.0:.0f} H/s over 5s | accepted={accepted} | per-thread={last_counts}")
                for i in range(len(last_counts)):
                    last_counts[i] = 0
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        for t in ths:
            t.join(timeout=1.0)


def connect_peers():
    try:
        r = requests.post(f"{A_BASE}/rpc/p2p/connect", params={"addr": f"127.0.0.1:{B_P2P}"}, timeout=5)
        if r.status_code == 200:
            log("A -> B connect OK")
        else:
            log(f"A -> B connect error: {r.text}")
    except Exception as e:
        log(f"A -> B connect exception: {e}")
    try:
        r = requests.post(f"{B_BASE}/rpc/p2p/connect", params={"addr": f"127.0.0.1:{A_P2P}"}, timeout=5)
        if r.status_code == 200:
            log("B -> A connect OK")
        else:
            log(f"B -> A connect error: {r.text}")
    except Exception as e:
        log(f"B -> A connect exception: {e}")


def summary_loop():
    last_a = -1
    last_b = -1
    while not STOP:
        try:
            hA = rpc_height(A_BASE)
            hB = rpc_height(B_BASE)
            mp = rpc_mempool(A_BASE)
            if hA != last_a or hB != last_b:
                log(f"[SUMMARY] A={hA} B={hB} mempoolA={mp}")
                last_a, last_b = hA, hB
            # Simple stall/divergence alerts
            if hB >= 0 and abs(hA - hB) > 10:
                log("[ALERT] Height divergence exceeds 10 blocks")
        except Exception as e:
            log(f"[SUMMARY] error: {e}")
        time.sleep(5)


def stop_all():
    global STOP
    STOP = True
    for p in PROCS:
        try:
            if p.poll() is None:
                if os.name == "nt":
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    p.terminate()
        except Exception:
            pass
    # Final hard kill after grace
    time.sleep(2)
    for p in PROCS:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass


def main():
    log(f"Logging to {LOG_PATH}")
    free_ports()

    data_dir = os.path.join(ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    A_DB = os.path.join(data_dir, "smellyA.db")
    B_DB = os.path.join(data_dir, "smellyB.db")

    # Start nodes
    pa = start_node("A", A_RPC, A_P2P, mine=True, miner_address="SMELLY_MONITOR_A", db_file=A_DB)
    pb = start_node("B", B_RPC, B_P2P, mine=False, miner_address="", db_file=B_DB)
    # Start explorer
    pe = start_explorer()

    # Start log followers
    threading.Thread(target=follow_logs, args=("A", pa), daemon=True).start()
    threading.Thread(target=follow_logs, args=("B", pb), daemon=True).start()
    threading.Thread(target=follow_logs, args=("EXP", pe), daemon=True).start()

    # Wait for RPCs
    if not wait_http_ok(f"{A_BASE}/rpc/get_height", 45.0):
        log("ERROR: Node A RPC not ready")
        stop_all()
        sys.exit(1)
    if not wait_http_ok(f"{B_BASE}/rpc/get_height", 45.0):
        log("ERROR: Node B RPC not ready")
        stop_all()
        sys.exit(1)

    # Connect peers via RPC endpoint
    connect_peers()

    # Start two real client-side miners on Node A (offset start for variety)
    threading.Thread(target=miner_loop, args=(A_BASE, "SMELLY_MONITOR_A"), kwargs={"jitter_sec": 0.0, "threads": 2, "slice_ms": 250, "poll_ms": 200}, daemon=True).start()
    threading.Thread(target=miner_loop, args=(A_BASE, "SMELLY_MONITOR_B"), kwargs={"jitter_sec": 0.35, "threads": 2, "slice_ms": 250, "poll_ms": 200}, daemon=True).start()

    # Start summary loop
    try:
        summary_loop()
    except KeyboardInterrupt:
        pass
    finally:
        log("Shutting down children...")
        stop_all()
        log("Monitor finished.")


if __name__ == "__main__":
    main()
