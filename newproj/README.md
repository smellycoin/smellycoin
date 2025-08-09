# SMELLY Chain - Reference Implementation (Windows, No Docker)

Important: This is a reference implementation intended for development and demonstration. It is not audited. Do not use in production without a comprehensive security review, replacing dev crypto with audited libraries and hardened networking.

This repository provides a modular cryptocurrency stack in Python 3 for Windows deployments without Docker:
- Consensus/p2p node with header-first sync and JSON-RPC server
- Wallet with HD seed, subaddresses (Monero-inspired pattern), web wallet UI (yellow/black theme)
- ASIC-resistant PoW placeholder (RandomX-like stub with native binding hooks)
- Solo miner and pool miner
- Stratum-like mining pool (TCP)
- Masternode service with heartbeats/uptime and bootstrap endpoints
- Block explorer (simple)
- SQLite by default (Postgres optional later)
- Address prefix format: `SMELLY_<addr>`
- Emission cap: 100,000,000 with halving schedule; rewards + fees within cap
- Windows run scripts

Security and performance notes:
- PoW: Replace `core/pow/randomx_stub.py` with a native RandomX library binding for production.
- Crypto: Replace pure-Python Ed25519/Keccak with libsodium or equivalent audited libs.
- Networking: Add TLS, DoS protection, auth, rate limiting, and peer reputation scoring before production.
- Wallet: Use secure key storage, hardware wallet integration for real funds.
- Pool: Harden share validation, accounting, and payouts. Use persistent message queues and a robust DB (Postgres).
- RPC: Protect with authentication, allowlist, rate limits.
- Masternodes: Add consensus rules to punish/byzantine behavior; this is a simple heartbeat registry.

Quick start (Windows, Python 3.11+ recommended):
1. Install Python 3.11 and ensure `python` and `pip` are on PATH.
2. Create venv:
   - `python -m venv .venv`
   - `.venv\Scripts\activate`
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Initialize dev data:
   - `python tools\init_dev_data.py`
5. Start core node RPC:
   - `python apps\node\main.py`
6. Start wallet backend:
   - `python apps\wallet\backend.py`
7. Start web wallet UI:
   - `python apps\wallet\web_ui.py`
8. Start masternode:
   - `python apps\masternode\service.py`
9. Start pool:
   - `python apps\pool\stratum_server.py`
10. Start solo miner (in another terminal):
   - `python apps\miner\solo_miner.py`
11. Start pool miner (in another terminal):
   - `python apps\miner\pool_miner.py`
12. Start explorer:
   - `python apps\explorer\server.py`

Project layout:
- core/             Core libraries: consensus, P2P, crypto, DB, RPC, wallet logic, PoW placeholder
- apps/
  - node/           Full node with RPC server
  - wallet/         Wallet backend + web UI
  - miner/          Solo and pool miners
  - pool/           Stratum-like server and payout accounting
  - masternode/     Heartbeat and bootstrap services
  - explorer/       Simple block explorer
- tools/            Dev scripts (init, keygen, etc.)
- configs/          Default config files and genesis
- scripts/          Windows run helpers
- tests/            Unit and integration tests
- requirements.txt  Python dependencies
- LICENSE           MIT

Disclaimer:
This code is for educational purposes. Use at your own risk.
