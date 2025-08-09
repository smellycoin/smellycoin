from __future__ import annotations

import socket
import threading
import json
import time
from typing import Dict, Optional, List, Tuple

import httpx
import traceback
import sys

from core.config import get_config
from core.utils import now_ms, sha3_256_hex
from core.consensus import Header, get_chain_height, get_header_by_height
from core.pow.randomx_stub import difficulty_to_target
from core.pow.pow_backend import pow_hash
from core.db import get_db, KV


# Minimal Stratum-like protocol (enhanced)
# Messages are JSON per line. Methods:
# - mining.subscribe -> {id, result: [session_id], error:null}
# - mining.authorize {"params":[address]} -> ok
# - mining.get_job -> returns current job {job_id, template:{prev_hash,version,target,txids,timestamp}, pool_target}
# - mining.submit {"params":[address, job_id, nonce, timestamp, merkle_root_hex, version]} -> share accept/reject
#
# Server verifies share using pow_backend; if hash <= network target, promotes via accept_external_header()
# to append a block. KV stats can be read by explorer for a dashboard.


class MiningJob:
    def __init__(self, job_id: str, prev_hash: str, version: int, target_hex: str, timestamp: int, txids: List[str], pool_diff: int):
        self.job_id = job_id
        self.prev_hash = prev_hash
        self.version = version
        self.target_hex = target_hex  # network target hint (from node tip or easy bootstrap)
        self.timestamp = timestamp
        self.txids = txids  # coinbase + snapshot txids
        self.pool_diff = pool_diff
        self.pool_target_hex = difficulty_to_target(pool_diff)
        self.created_ms = now_ms()

    def to_template(self) -> Dict[str, object]:
        return {
            "prev_hash": self.prev_hash,
            "version": self.version,
            "target": self.target_hex,
            "timestamp": self.timestamp,
            "txids": self.txids,
        }


class MinerConn:
    def __init__(self, sock: socket.socket, addr: str):
        self.sock = sock
        self.addr = addr
        self.file = sock.makefile(mode="rwb")
        self.address: Optional[str] = None
        self.alive = True
        self.accepted_shares = 0
        self.rejected_shares = 0
        self.last_submit_ms = 0
        self.hashes_5s = 0  # rough hashrate proxy from share attempts


def _c(code: str, text: str) -> str:
    # ANSI color helper (works in most terminals; Windows Terminal/VSCode okay)
    return f"\033[{code}m{text}\033[0m"


class StratumPool:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.server: Optional[socket.socket] = None
        self.clients: Dict[int, MinerConn] = {}
        self._client_id = 0
        self.lock = threading.Lock()
        self.current_job: Optional[MiningJob] = None
        # Make initial share difficulty trivial to avoid "Low difficulty share" spam.
        # Network target is extremely easy during bootstrap; accept most shares.
        self.pool_diff = 1
        # rolling counters for dashboard
        self._accepted_recent: List[Tuple[int, str]] = []  # [(ms, addr), ...]
        self._rejected_recent: List[Tuple[int, str]] = []
        # Node RPC base for job templating
        cfg = get_config()
        self.node_base = f"http://{cfg.get('network.rpc_host','127.0.0.1')}:{cfg.get('network.rpc_port',28445)}"
        # Static job mode (disables rotation except on successful block or explicit tip advance)
        self.static_job_mode = True

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(50)
        self.server = s
        print(_c("1;33", f"Stratum pool listening on {self.host}:{self.port}"))
        print(_c("36", f"[DEBUG] static_job_mode={self.static_job_mode} node_base={self.node_base}"))

        # Job producer and snapshot threads
        threading.Thread(target=self._job_loop, daemon=True).start()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()

        while True:
            client_sock, (chost, cport) = s.accept()
            conn = MinerConn(client_sock, f"{chost}:{cport}")
            with self.lock:
                cid = self._client_id
                self._client_id += 1
                self.clients[cid] = conn
            threading.Thread(target=self._handle_client, args=(cid, conn), daemon=True).start()

    def _broadcast_job(self):
        if not self.current_job:
            return
        msg = {
            "id": None,
            "method": "mining.notify",
            "params": {
                "job_id": self.current_job.job_id,
                "template": self.current_job.to_template(),
                "pool_target": self.current_job.pool_target_hex,
                "share_diff": self.pool_diff,
            },
        }
        self._broadcast(msg)

    def _broadcast(self, obj: dict):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self.lock:
            to_drop = []
            for cid, conn in self.clients.items():
                try:
                    conn.file.write(data)
                    conn.file.flush()
                except Exception:
                    to_drop.append(cid)
            for cid in to_drop:
                try:
                    self.clients[cid].file.close()
                    self.clients[cid].sock.close()
                except Exception:
                    pass
                del self.clients[cid]

    def _job_loop(self):
        """
        Fetch node-issued jobs to eliminate prev/target drift.
        If height >= 200, expect tx snapshot (txids) in get_work and propagate to miners
        so their computed merkle matches node's rebuild.
        """
        last_job_id = None
        while True:
            try:
                with httpx.Client(timeout=5.0) as c:
                    r = c.post(f"{self.node_base}/rpc/get_work", json={"miner_address": None})
                    if r.status_code != 200:
                        print("Job loop error: get_work", r.status_code, r.text)
                        time.sleep(2.0)
                        continue
                    job_json = r.json() or {}
                    # Normalize fields and enforce lowercase for prev/txids consistency
                    txids = [str(t).lower() for t in (job_json.get("txids") or [])]
                    prev = str(job_json.get("prev_hash") or "").lower()
                    tgt = str(job_json.get("target") or "").lower()
                    ver = int(job_json.get("version") or 1)
                    ts = int(job_json.get("timestamp") or int(time.time()))
                    jid = str(job_json.get("job_id") or str(now_ms()))
                    job = MiningJob(
                        job_id=jid,
                        prev_hash=prev,
                        version=ver,
                        target_hex=tgt,
                        timestamp=ts,
                        txids=txids,
                        pool_diff=max(1, self.pool_diff),
                    )
                    # Only broadcast if changed
                    if not self.current_job or job.job_id != last_job_id:
                        self.current_job = job
                        last_job_id = job.job_id
                        print(_c("34", f"[DEBUG] built job id={job.job_id} prev={job.prev_hash[:16]}.. target={job.target_hex[:8]}.. txids={len(job.txids)}"))
                        self._broadcast_job()
                time.sleep(2.0)
            except Exception as e:
                print("Job loop error:", e)
                time.sleep(2.0)

    def _handle_client(self, cid: int, conn: MinerConn):
        print(_c("32", f"Client connected: {cid} {conn.addr}"))
        print(_c("36", f"[DEBUG] send subscribe to cid={cid}"))
        try:
            # Send welcome
            self._send(conn, {"id": 0, "result": ["smelly-session"], "error": None, "method": "mining.subscribe"})
            if self.current_job:
                notify = {
                    "id": None,
                    "method": "mining.notify",
                    "params": {
                        "job_id": self.current_job.job_id,
                        "template": self.current_job.to_template(),
                        "pool_target": self.current_job.pool_target_hex,
                        "share_diff": self.pool_diff,
                    },
                }
                print(_c("36", f"[DEBUG] initial notify to cid={cid}: job_id={self.current_job.job_id} prev={self.current_job.prev_hash[:16]}.. pool_target={self.current_job.pool_target_hex[:8]}.."))
                self._send(conn, notify)
            while conn.alive:
                line = conn.file.readline()
                if not line:
                    break
                msg = json.loads(line.decode("utf-8").strip())
                self._process_msg(conn, msg)
        except Exception as e:
            print(_c("31", f"Client error: {cid} {e}"))
        finally:
            try:
                conn.file.close()
                conn.sock.close()
            except Exception:
                pass
            with self.lock:
                if cid in self.clients:
                    del self.clients[cid]
            print(_c("33", f"Client disconnected: {cid}"))

    def _send(self, conn: MinerConn, obj: dict):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        conn.file.write(data)
        conn.file.flush()

    def _reply(self, conn: MinerConn, id_val, result=None, error=None):
        self._send(conn, {"id": id_val, "result": result, "error": error})

    def _process_msg(self, conn: MinerConn, msg: dict):
        method = msg.get("method")
        if method == "mining.authorize":
            params = msg.get("params") or []
            if not params:
                return self._reply(conn, msg.get("id"), result=False, error="Address required")
            conn.address = params[0]
            print(_c("36", f"[DEBUG] authorize ok addr={conn.address} cid={id(conn)}"))
            return self._reply(conn, msg.get("id"), result=True, error=None)

        if method == "mining.get_job":
            if not self.current_job:
                return self._reply(conn, msg.get("id"), result=None, error="No job")
            job = self.current_job
            print(_c("36", f"[DEBUG] get_job -> job_id={job.job_id} prev={job.prev_hash[:16]}.. target={job.target_hex[:8]}.."))
            return self._reply(conn, msg.get("id"), result={
                "job_id": job.job_id,
                "template": job.to_template(),
                "pool_target": job.pool_target_hex,
                "share_diff": self.pool_diff,
            }, error=None)

        if method == "mining.submit":
            params = msg.get("params") or []
            try:
                # New schema includes prev_hash to harden against rotated job_id but same prev races:
                # [address, job_id, nonce, timestamp, merkle_root_hex, version, prev_hash_hex?]
                address, job_id, nonce, timestamp, merkle_root_hex, version = params[:6]
                prev_from_submit = params[6] if len(params) >= 7 else None
                nonce = int(nonce)
                timestamp = int(timestamp)
                version = int(version)
            except Exception:
                print(_c("31", f"[DEBUG] invalid submit params: {msg}"))
                return self._reply(conn, msg.get("id"), result=False, error="Invalid params")
            # Stale job check; allow small grace if prev_hash matches but job_id rotated recently
            if not self.current_job:
                print(_c("33", f"[DEBUG] stale job: no current_job"))
                return self._reply(conn, msg.get("id"), result=False, error="Stale job")
            if job_id != self.current_job.job_id:
                # Allow only if prev matches; otherwise stale
                current_prev = (self.current_job.prev_hash or "").lower()
                if prev_from_submit:
                    prev_from_submit = prev_from_submit.lower()
                if not prev_from_submit:
                    print(_c("33", f"[DEBUG] stale job (no prev provided) cur_job_id={self.current_job.job_id} submit_job_id={job_id}"))
                    return self._reply(conn, msg.get("id"), result=False, error="Stale job")
                if prev_from_submit != current_prev:
                    print(_c("33", f"[DEBUG] stale job: prev mismatch submit_prev={prev_from_submit[:16]}.. cur_prev={current_prev[:16]}.."))
                    return self._reply(conn, msg.get("id"), result=False, error="Stale job")
                print(_c("35", f"[DEBUG] accept rotated job_id with same prev={current_prev[:16]}.."))

            job = self.current_job
            # Build header bytes exactly like miners and consensus
            fields = [
                ("version", version),
                ("prev_hash_hex", (job.prev_hash or "").lower()),
                ("merkle_root_hex", (merkle_root_hex or "").lower()),
                ("timestamp", timestamp),
                ("target", (job.target_hex or "").lower()),
                ("nonce", nonce),
                ("miner_address", address),
                ("tx_count", len(job.txids)),
            ]
            hdr_bytes = json.dumps(fields, separators=(",", ":"), sort_keys=False).encode("utf-8")
            # Use prev_from_submit (already lowercase) to ensure identical digest path with miner
            digest = pow_hash(hdr_bytes, nonce, prev_from_submit or (job.prev_hash or ""))
            print(_c("36", f"[DEBUG] share submit addr={address} job_id={job_id} cur_job={job.job_id} prev={job.prev_hash[:16]}.. nonce={nonce} ts={timestamp} digest={digest.hex()[:16]}.. pool_target={job.pool_target_hex[:8]}.. net_target={job.target_hex[:8]}.."))

            # Share target check (pool difficulty). If pool_diff <= 1, accept all shares.
            if self.pool_diff > 1 and int(digest.hex(), 16) > int(job.pool_target_hex, 16):
                conn.rejected_shares += 1
                with self.lock:
                    self._rejected_recent.append((now_ms(), address))
                print(_c("33", f"[DEBUG] share low diff digest={digest.hex()[:16]}.. > pool_target (share_diff={self.pool_diff})"))
                return self._reply(conn, msg.get("id"), result=False, error="Low difficulty share")

            # Accept share
            conn.accepted_shares += 1
            conn.last_submit_ms = now_ms()
            with self.lock:
                self._accepted_recent.append((conn.last_submit_ms, address))
            self._reply(conn, msg.get("id"), result=True, error=None)
            print(_c("32", f"[DEBUG] share accepted addr={address} accepted={conn.accepted_shares} rejected={conn.rejected_shares}"))

            # If meets network target, promote via node; select merkle strategy based on height/job
            if int(digest.hex(), 16) <= int(job.target_hex, 16):
                try:
                    # Query height to decide bootstrap vs mempool-merkle mode
                    height_now = -1
                    with httpx.Client(timeout=3.0) as c:
                        r_h = c.get(f"{self.node_base}/rpc/get_height")
                        if r_h.status_code == 200:
                            height_now = int((r_h.json() or {}).get("height", -1))
                    # Always forward the miner-provided merkle and the exact txids snapshot;
                    # the node will decide to rebuild coinbase-only when height < bootstrap_cutoff.
                    payload = {
                        "job_id": job.job_id,
                        "miner_address": address,
                        "nonce": int(nonce),
                        "timestamp": int(timestamp),
                        "version": int(version),
                        "merkle_root_hex": (merkle_root_hex or "").lower(),
                        "prev_hash_hex": (prev_from_submit or job.prev_hash).lower(),
                        "txids": [t.lower() for t in (job.txids or [])],
                    }
                    with httpx.Client(timeout=10.0) as c:
                        resp = c.post(f"{self.node_base}/rpc/submit_work", json=payload)
                    if resp.status_code == 200 and isinstance(resp.json(), dict) and resp.json().get("accepted"):
                        hh = resp.json().get("hash")
                        print(_c("1;32", f"[POOL] FOUND BLOCK {hh} by {address} (h={height_now+1} prev={job.prev_hash[:16]}.. target={job.target_hex[:8]}.. merkle={'coinbase' if height_now<200 else 'txs'})"))
                        self._rotate_job_async()
                        return None
                    # Rejection diagnostics
                    try:
                        detail = resp.json()
                    except Exception:
                        detail = {"text": resp.text}
                    print(_c("1;31", f"[POOL] promotion rejected by node: {detail}"))
                    if isinstance(detail, dict):
                        det = detail.get("detail") or detail
                        err = str(det.get("error") if isinstance(det, dict) and "error" in det else det).lower()
                        # If merkle mismatch at >=200, force job refresh from node to sync txids snapshot
                        if "merkle" in err or "txids" in err:
                            print(_c("35", "[DEBUG] refreshing job from node due to merkle mismatch"))
                            self._rotate_job_async()
                        # If prev/lease issues, rotate as well
                        if any(k in err for k in ["stale", "prev", "expired", "unknown job"]):
                            print(_c("35", "[DEBUG] rotating job due to lease/prev issue"))
                            self._rotate_job_async()
                except Exception as e:
                    print(_c("1;31", f"[POOL] Promotion exception: {e}"))
                    traceback.print_exc()
                    self._rotate_job_async()
            return None

        # Unknown
        return self._reply(conn, msg.get("id"), result=None, error="Unknown method")


    def _rotate_job_async(self):
        # Trigger job rebuild without blocking submit thread
        def _do():
            try:
                # Simple way: set current_job to None; job loop will rebuild next tick
                self.current_job = None
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()


    def _snapshot_loop(self):
        """
        Every 5s compute a lightweight snapshot and persist to KV for Explorer /pool.
        Stores:
        - miners: [{addr, accepted, rejected, last_submit_ms, hashrate}]
        - share_diff, accepted_5m, rejected_5m, total_hashrate
        """
        db = get_db()
        WINDOW_MS = 5 * 60 * 1000
        while True:
            try:
                nowm = now_ms()
                with self.lock:
                    # prune recent lists
                    self._accepted_recent = [(t, a) for (t, a) in self._accepted_recent if nowm - t <= WINDOW_MS]
                    self._rejected_recent = [(t, a) for (t, a) in self._rejected_recent if nowm - t <= WINDOW_MS]
                    miners = []
                    total_h = 0.0
                    for _, conn in list(self.clients.items()):
                        # Estimate hashrate from accepted shares in window per miner (very rough)
                        acc = len([1 for (t, a) in self._accepted_recent if a == (conn.address or "")])
                        # scale shares per window by share difficulty into an H/s-ish proxy
                        # share_target ~ difficulty_to_target(share_diff); we simply use acc/window as proxy
                        hr = acc / max(1.0, WINDOW_MS / 1000.0)
                        total_h += hr
                        miners.append({
                            "addr": conn.address or "(unauth)",
                            "accepted": conn.accepted_shares,
                            "rejected": conn.rejected_shares,
                            "last_submit_ms": conn.last_submit_ms,
                            "hashrate": f"{hr:.2f}",
                        })
                    snap = {
                        "miners": miners,
                        "share_diff": self.pool_diff,
                        "accepted_5m": len(self._accepted_recent),
                        "rejected_5m": len(self._rejected_recent),
                        "total_hashrate": total_h,
                        "ts": nowm,
                    }
                # persist
                with db.session() as s:
                    row = s.get(KV, "pool_snapshot_json") or KV(k="pool_snapshot_json", v="")
                    row.v = json.dumps(snap, separators=(",", ":"), sort_keys=False)
                    s.merge(row)
                    s.commit()
            except Exception as e:
                print("[POOL] snapshot error:", e)
            time.sleep(5)


def run_pool():
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    port = int(cfg.get("network.pool_port", 28446))
    pool = StratumPool(host, port)
    pool.start()


if __name__ == "__main__":
    run_pool()
