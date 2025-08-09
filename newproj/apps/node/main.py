import argparse
import threading
import time
import requests
import socket
import json
from typing import Dict, Set, Tuple, List

from core.rpc import run_rpc_server
from core.config import get_config
from core.utils import ensure_dirs, now_ms
from core.db import get_db, BlockHeader, MempoolTx, Transaction
from core.consensus import (
    add_genesis_if_needed,
    get_chain_height,
    get_headers_range,
    accept_external_header,
    Header,
)
from core.pow.randomx_stub import difficulty_to_target


# ----------------- P2P (JSON line protocol: VERSION/VERACK, INV, GETDATA, BLOCKHDR, TX, PING/PONG) -----------------

class PeerState:
    def __init__(self, addr: str, fp):
        self.addr = addr
        self.fp = fp
        self.last_seen = now_ms()


_seen_hdr: Set[str] = set()
_seen_tx: Set[str] = set()
_peers: Dict[str, PeerState] = {}  # addr -> state
_peers_lock = threading.Lock()


def _p2p_send(fp, obj: dict):
    try:
        fp.write((json.dumps(obj) + "\n").encode("utf-8"))
        fp.flush()
    except Exception:
        pass


def _announce_tip_to_peers():
    # Periodically announce local tip header hash
    db = get_db()
    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        if not tip:
            return
        inv = {"type": "INV", "items": [{"kind": "hdr", "hash": tip.hash_hex}]}
    with _peers_lock:
        for ps in list(_peers.values()):
            _p2p_send(ps.fp, inv)


def _broadcast_txinv(txid: str):
    inv = {"type": "INV", "items": [{"kind": "tx", "txid": txid}]}
    with _peers_lock:
        for ps in list(_peers.values()):
            _p2p_send(ps.fp, inv)


def _serve_peer(sock: socket.socket, peer_addr: str):
    fp = sock.makefile(mode="rwb")
    try:
        # handshake
        _p2p_send(fp, {"type": "VERSION", "time": now_ms()})
        _p2p_send(fp, {"type": "VERACK"})
        with _peers_lock:
            _peers[peer_addr] = PeerState(peer_addr, fp)

        # main loop
        while True:
            line = fp.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8").strip())
            except Exception:
                continue
            mtype = msg.get("type")

            # keepalive
            if mtype == "PING":
                _p2p_send(fp, {"type": "PONG", "time": now_ms()})
                continue
            if mtype == "PONG":
                continue

            if mtype == "INV":
                items = msg.get("items") or []
                need_items: List[dict] = []
                for it in items:
                    kind = it.get("kind")
                    if kind == "hdr":
                        h = (it.get("hash") or "").strip().lower()
                        if h and h not in _seen_hdr:
                            need_items.append({"kind": "hdr", "hash": h})
                    elif kind == "tx":
                        txid = (it.get("txid") or "").strip().lower()
                        if txid and txid not in _seen_tx:
                            need_items.append({"kind": "tx", "txid": txid})
                if need_items:
                    _p2p_send(fp, {"type": "GETDATA", "items": need_items})
                continue

            if mtype == "GETDATA":
                items = msg.get("items") or []
                db = get_db()
                with db.session() as s:
                    for it in items:
                        kind = it.get("kind")
                        if kind == "hdr":
                            hh = (it.get("hash") or "").strip().lower()
                            if not hh:
                                continue
                            h = s.query(BlockHeader).filter_by(hash_hex=hh).first()
                            if not h:
                                continue
                            # For BLOCKHDR response, include enough fields for accept_external_header,
                            # and also supply txids snapshot if known (we don't store full list; send empty for now)
                            hdr_msg = {
                                "type": "BLOCKHDR",
                                "headers": [{
                                    "prev": h.prev_hash_hex,
                                    "merkle": h.merkle_root_hex,
                                    "ver": h.version,
                                    "ts": h.timestamp,
                                    "target": h.target,
                                    "nonce": int(h.nonce),
                                    "miner": h.miner_address,
                                    "txids": [],  # unknown snapshot; external nodes will rebuild/compare
                                    "hash": h.hash_hex
                                }]
                            }
                            _p2p_send(fp, hdr_msg)
                        elif kind == "tx":
                            txid = (it.get("txid") or "").strip().lower()
                            if not txid:
                                continue
                            m = s.query(MempoolTx).filter_by(txid=txid).first()
                            if not m:
                                continue
                            try:
                                tx_obj = json.loads(m.raw) if m.raw else {}
                            except Exception:
                                tx_obj = {}
                            _p2p_send(fp, {"type": "TX", "tx": tx_obj, "txid": txid})
                continue

            if mtype == "BLOCKHDR":
                headers = msg.get("headers") or []
                for h in headers:
                    try:
                        prev = h.get("prev")
                        merkle = h.get("merkle")
                        ver = int(h.get("ver", 1))
                        ts = int(h.get("ts", int(time.time())))
                        tgt = h.get("target")
                        nonce = int(h.get("nonce", 0))
                        miner = h.get("miner") or "SMELLY_PEER"
                        txids_snap = h.get("txids") or []
                    except Exception:
                        continue

                    # Attempt accept; will reject stale-prev, mismatch, etc.
                    hh, err = accept_external_header(
                        prev_hash_hex=prev,
                        merkle_root_hex=merkle,
                        version=ver,
                        timestamp=ts,
                        target_hex=tgt,
                        nonce=nonce,
                        miner_address=miner,
                        txids_snapshot=txids_snap,
                    )
                    if hh:
                        _seen_hdr.add(hh.strip().lower())
                        # Re-announce header to other peers
                        _announce_tip_to_peers()
                continue

            if mtype == "TX":
                txid = (msg.get("txid") or "").strip().lower()
                if not txid:
                    continue
                if txid in _seen_tx:
                    continue
                # Store in mempool DB if valid via RPC endpoint for consistency,
                # but here we directly insert to mempool to avoid recursion.
                # We rely on consensus.validate_mempool_tx during mining/accept.
                db = get_db()
                with db.session() as s:
                    existing = s.query(MempoolTx).filter_by(txid=txid).first()
                    if not existing:
                        tx_obj = msg.get("tx") or {}
                        try:
                            raw_compact = json.dumps(tx_obj, separators=(",", ":"), sort_keys=True)
                        except Exception:
                            raw_compact = ""
                        fee = float((tx_obj or {}).get("fee", 0.0))
                        from_addr = None
                        to_addr = None
                        amount = None
                        try:
                            if tx_obj.get("outputs"):
                                to_addr = tx_obj["outputs"][0].get("address")
                                amount = float(tx_obj["outputs"][0].get("amount", 0.0))
                            if tx_obj.get("inputs"):
                                from_addr = tx_obj["inputs"][0].get("address")
                        except Exception:
                            pass
                        s.add(MempoolTx(
                            txid=txid,
                            raw=raw_compact,
                            added_ms=now_ms(),
                            fee=fee,
                            from_addr=from_addr,
                            to_addr=to_addr,
                            amount=amount,
                        ))
                        s.commit()
                _seen_tx.add(txid)
                # Re-announce
                _broadcast_txinv(txid)
                continue

            # Unknown message
            _p2p_send(fp, {"type": "ERR", "detail": f"unknown {mtype}"})
    except Exception as e:
        print("P2P conn error:", peer_addr, e)
    finally:
        try:
            with _peers_lock:
                _peers.pop(peer_addr, None)
            fp.close()
            sock.close()
        except Exception:
            pass


def start_p2p():
    # Allow overriding P2P bind via environment for multi-node testing
    import os
    cfg = get_config()
    host = os.environ.get("SMELLY_P2P_HOST", cfg.get("network.rpc_host", "127.0.0.1"))
    port = int(os.environ.get("SMELLY_P2P_PORT", cfg.get("network.p2p_port", 28447)))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(50)
    print(f"P2P listening on {host}:{port}")

    def _accept_loop():
        while True:
            conn, (h, p) = s.accept()
            threading.Thread(target=_serve_peer, args=(conn, f"{h}:{p}"), daemon=True).start()

    threading.Thread(target=_accept_loop, daemon=True).start()

    # Periodic announcer
    def _periodic():
        while True:
            try:
                _announce_tip_to_peers()
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=_periodic, daemon=True).start()


def connect_peer(addr: str):
    try:
        host, port_str = addr.split(":")
        port = int(port_str)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((host, port))
        fp = s.makefile(mode="rwb")
        # handshake
        _p2p_send(fp, {"type": "VERSION", "time": now_ms()})
        _p2p_send(fp, {"type": "VERACK"})
        with _peers_lock:
            _peers[addr] = PeerState(addr, fp)
        # On connect, ask for peer tip by sending an empty INV to trigger GETDATA or direct BLOCKHDR
        _p2p_send(fp, {"type": "PING", "time": now_ms()})
        return True
    except Exception as e:
        print("connect_peer error:", addr, e)
        return False


def sync_headers_from_peer(peer_host: str, peer_port: int):
    # Deprecated in favor of INV/GETDATA flow; keep a no-op stub.
    return


def start_rpc():
    # Allow overriding RPC bind via environment for multi-node testing
    import os
    from core.config import get_config
    cfg = get_config()
    env_host = os.environ.get("SMELLY_RPC_HOST")
    env_port = os.environ.get("SMELLY_RPC_PORT")
    if env_host or env_port:
        # Monkey-patch cfg getter so core.rpc.run_rpc_server picks these up
        try:
            cfg._overrides = getattr(cfg, "_overrides", {})
            if env_host:
                cfg._overrides["network.rpc_host"] = env_host
            if env_port:
                cfg._overrides["network.rpc_port"] = int(env_port)
        except Exception:
            pass
    run_rpc_server()


def main():
    parser = argparse.ArgumentParser(description="SMELLY Node (RPC + P2P + header-only chain)")
    parser.add_argument("--mine", action="store_true", help="Continuously mine headers on this node")
    parser.add_argument("--miner-address", type=str, default="SMELLY_LOCAL_MINER")
    parser.add_argument("--peer", type=str, default="", help="Optional peer host:port to header-sync from")
    args = parser.parse_args()

    ensure_dirs()
    get_db()
    add_genesis_if_needed()

    # Start RPC server in background thread
    t = threading.Thread(target=start_rpc, daemon=True)
    t.start()

    # Start P2P server in background thread
    tp2p = threading.Thread(target=start_p2p, daemon=True)
    tp2p.start()

    print("SMELLY Node RPC + P2P started.")
    cfg = get_config()
    rpc_url = f"http://{cfg.get('network.rpc_host','127.0.0.1')}:{cfg.get('network.rpc_port',28445)}"

    # Optional one-shot header sync from peer
    if args.peer:
        try:
            ph, pp = args.peer.split(":")
            print(f"Syncing headers from peer {args.peer} ...")
            sync_headers_from_peer(ph, int(pp))
        except Exception as e:
            print("Peer sync arg invalid:", e)

    if args.mine:
        print("Mining enabled. Submitting header-only blocks...")
        while True:
            try:
                r = requests.post(f"{rpc_url}/rpc/mine_one", json={"miner_address": args.miner_address}, timeout=10)
                if r.status_code == 200:
                    print("Mined header hash:", r.json().get("hash"))
                else:
                    print("Mine error:", r.text)
            except Exception as e:
                print("Mining request failed:", e)
            time.sleep(0.5)

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down.")


if __name__ == "__main__":
    main()
