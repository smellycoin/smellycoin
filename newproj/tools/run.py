"""
Unified launcher to ensure package imports work without manual PYTHONPATH tweaks.

Usage examples (from project root with venv activated):
  python -m tools.run init
  python -m tools.run node
  python -m tools.run wallet-backend
  python -m tools.run wallet-ui
  python -m tools.run masternode
  python -m tools.run pool
  python -m tools.run solo-miner
  python -m tools.run pool-miner
  python -m tools.run explorer
"""

import os
import sys
import importlib
import argparse
import threading
import time
import requests
from typing import Optional


def _ensure_project_on_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)


def _spin_background(target, name: str):
    th = threading.Thread(target=target, name=name, daemon=True)
    th.start()
    return th

def _wait_for_http(url: str, timeout_sec: int = 15) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False

def _ensure_node_rpc_running() -> None:
    # Try to reach /rpc/get_height; if not responding, start node RPC in a thread
    from core.config import get_config
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    port = int(cfg.get("network.rpc_port", 28445))
    url = f"http://{host}:{port}/rpc/get_height"
    try:
        requests.get(url, timeout=2).raise_for_status()
        return
    except Exception:
        pass
    # start node RPC server thread
    from apps.node.main import start_rpc, add_genesis_if_needed, get_db
    # ensure DB/genesis then spin RPC
    try:
        from core.db import get_db as _get_db
        from core.consensus import add_genesis_if_needed as _add_gen
        _get_db()
        _add_gen()
    except Exception:
        # fallback if import aliasing failed
        pass
    _spin_background(start_rpc, "node-rpc")
    _wait_for_http(url, timeout_sec=20)

def _ensure_wallet_backend_running() -> None:
    from core.config import get_config
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    port = int(cfg.get("network.web_wallet_port", 28449))
    url = f"http://{host}:{port}/wallet/accounts"
    try:
        requests.get(url, timeout=2).raise_for_status()
        return
    except Exception:
        pass
    from apps.wallet.backend import run_wallet_backend
    _spin_background(run_wallet_backend, "wallet-backend")
    _wait_for_http(url, timeout_sec=20)

def _ensure_masternode_running() -> None:
    from core.config import get_config
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    port = int(cfg.get("network.masternode_port", 28447))
    url = f"http://{host}:{port}/mn/recent"
    try:
        requests.get(url, timeout=2).raise_for_status()
        return
    except Exception:
        pass
    from apps.masternode.service import run_masternode
    _spin_background(run_masternode, "masternode")
    _wait_for_http(url, timeout_sec=20)

def _ensure_pool_running() -> None:
    # For TCP pool, just attempt connect later; no HTTP healthcheck
    from apps.pool.stratum_server import run_pool
    # Start only if not already started in this process; naive guard via attribute
    if getattr(_ensure_pool_running, "_started", False):
        return
    _spin_background(run_pool, "stratum-pool")
    setattr(_ensure_pool_running, "_started", True)

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.run",
        description="SMELLY unified launcher",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize database and genesis")

    sub.add_parser("node", help="Start node JSON-RPC")

    sub.add_parser("wallet-backend", help="Start wallet backend API")
    sub.add_parser("wallet-ui", help="Start web wallet UI")
    sub.add_parser("masternode", help="Start masternode service")
    sub.add_parser("pool", help="Start Stratum-like pool")
    sub.add_parser("explorer", help="Start block explorer")

    sp_solo = sub.add_parser("solo-miner", help="Run solo miner (ensures node RPC)")
    sp_solo.add_argument("--miner-address", type=str, default="SMELLY_SOLO", help="Miner payout address")
    sp_solo.add_argument("--loop", action="store_true", help="Continuously mine")

    sp_poolm = sub.add_parser("pool-miner", help="Run pool miner (ensures pool)")
    sp_poolm.add_argument("--host", type=str, default="127.0.0.1")
    sp_poolm.add_argument("--port", type=int, default=28446)
    sp_poolm.add_argument("--address", type=str, default="SMELLY_POOL_MINER")
    sp_poolm.add_argument("--intensity", type=int, default=1)

    return p

def main():
    _ensure_project_on_path()
    parser = build_parser()
    args = parser.parse_args()

    cmd = args.command

    if cmd == "init":
        mod = importlib.import_module("tools.init_dev_data")
        mod.main()
        return

    if cmd == "node":
        # Ensure DB/genesis then run RPC server in foreground if not already running
        from core.config import get_config
        cfg = get_config()
        host = cfg.get("network.rpc_host", "127.0.0.1")
        port = int(cfg.get("network.rpc_port", 28445))
        url = f"http://{host}:{port}/rpc/get_height"
        try:
            requests.get(url, timeout=2).raise_for_status()
            print(f"Node RPC already running at {url}")
            while True:
                time.sleep(3600)
        except Exception:
            from apps.node.main import start_rpc
            print("Starting Node RPC...")
            start_rpc()

    elif cmd == "wallet-backend":
        _ensure_node_rpc_running()
        from apps.wallet.backend import run_wallet_backend
        run_wallet_backend()

    elif cmd == "wallet-ui":
        _ensure_wallet_backend_running()
        from apps.wallet.web_ui import run_web_wallet
        run_web_wallet()

    elif cmd == "masternode":
        from apps.masternode.service import run_masternode
        run_masternode()

    elif cmd == "pool":
        _ensure_node_rpc_running()
        from apps.pool.stratum_server import run_pool
        run_pool()

    elif cmd == "solo-miner":
        # Ensure node is running; if chain not ready to accept mining, start a dedicated node miner thread
        _ensure_node_rpc_running()
        from apps.miner.solo_miner import main as solo_main
        # The miner main() parses its own args; rebuild argv accordingly so no extra "--" syntax is needed
        sys.argv = ["apps.miner.solo_miner"]
        if getattr(args, "miner_address", None):
            sys.argv += ["--miner-address", args.miner_address]
        if getattr(args, "loop", False):
            sys.argv += ["--loop"]
        solo_main()

    elif cmd == "pool-miner":
        _ensure_pool_running()
        from apps.miner.pool_miner import main as poolminer_main
        sys.argv = ["apps.miner.pool_miner"]
        if args.host:
            sys.argv += ["--host", str(args.host)]
        if args.port:
            sys.argv += ["--port", str(args.port)]
        if args.address:
            sys.argv += ["--address", str(args.address)]
        if args.intensity:
            sys.argv += ["--intensity", str(args.intensity)]
        poolminer_main()

    elif cmd == "explorer":
        _ensure_node_rpc_running()
        from apps.explorer.server import run_explorer
        run_explorer()

    else:
        parser.error(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
