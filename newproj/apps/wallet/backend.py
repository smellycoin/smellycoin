from __future__ import annotations

import os
import secrets
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from argon2 import PasswordHasher
from sqlalchemy import func
import hashlib

from core.config import get_config
from core.utils import ensure_dirs, now_ms
from core.db import get_db, WalletAccount, SubAddress, UTXO, Reward, Transaction, MempoolTx, User
from core.crypto import generate_seed, ed25519_keypair_from_seed, encode_address, derive_subaddress
import httpx

# Note: For production, add session signing keys loaded from config/secret
SESSION_COOKIE = "smelly_sid"
CSRF_HEADER = "x-smelly-csrf"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
PH = PasswordHasher()


app = FastAPI(title="SMELLY Web Wallet", version="0.2")


# ------------ Models (API v1) -------------

class CreateWalletRequest(BaseModel):
    name: str = "Main"
    language: str = "english"
    passphrase: str


class RestoreWalletRequest(BaseModel):
    name: str = "Restored"
    mnemonic: str
    passphrase: str


class NewSubAddressRequest(BaseModel):
    account_id: int
    major: int = 0
    minor: int = 1
    label: str = ""


class SendRequest(BaseModel):
    from_address: str
    to_address: str
    amount: float
    fee: float  # custom fee per tx
    memo: Optional[str] = ""

    def dedupe_key(self) -> str:
        # Deterministic idempotency key derived from payload (address pair + amount+fee+memo)
        h = hashlib.sha256()
        h.update((self.from_address or "").encode("utf-8"))
        h.update(b"|")
        h.update((self.to_address or "").encode("utf-8"))
        h.update(b"|")
        h.update(f"{float(self.amount):.12f}".encode("utf-8"))
        h.update(b"|")
        h.update(f"{float(self.fee):.12f}".encode("utf-8"))
        h.update(b"|")
        h.update((self.memo or "").encode("utf-8"))
        return h.hexdigest()


# ------------ Startup -------------

@app.on_event("startup")
def on_startup():
    ensure_dirs()
    get_db()  # ensure DB initialized


# ------------ Session + CSRF (minimal demo) -------------

def _get_session_id(req: Request) -> Optional[str]:
    return req.cookies.get(SESSION_COOKIE)

def _issue_session(resp: Response, user_id: int, account_id: int | None = None) -> str:
    # Very simple signed cookie: sid|uid|acct|nonce (improve to HMAC if needed)
    sid = secrets.token_hex(16)
    payload = f"{sid}|{user_id}|{account_id or 0}|{secrets.token_hex(8)}"
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=payload,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
        path="/",
    )
    return sid

def _parse_session(val: str | None) -> tuple[int | None, int | None]:
    # returns (user_id, account_id) or (None, None)
    if not val:
        return None, None
    try:
        parts = val.split("|")
        if len(parts) < 3:
            return None, None
        uid = int(parts[1])
        acct = int(parts[2])
        if acct == 0:
            acct = None
        return uid, acct
    except Exception:
        return None, None

def _require_csrf(req: Request):
    token = req.headers.get(CSRF_HEADER)
    if not token or len(token) < 16:
        raise HTTPException(status_code=403, detail="Missing/invalid CSRF token")


# ------------ Auth & Sessions (basic) -------------

class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/auth/register")
def auth_register(req: RegisterRequest):
    """
    Register a new user and automatically generate a default wallet account,
    then bind the session to that new account. This streamlines onboarding.
    """
    from base64 import b64encode
    from os import urandom
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import hashlib

    db = get_db()
    with db.session() as s:
        existing = s.query(User).filter_by(username=req.username).first()
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")

        # Create user
        pwd_hash = PH.hash(req.password)
        u = User(username=req.username, password_hash=pwd_hash, created_ms=now_ms())
        s.add(u)
        s.flush()  # get u.id

        # Auto-generate wallet (default "Main")
        # Generate mnemonic/seed and keys
        words, seed = generate_seed(language="english")
        sk_spend, pk_spend = ed25519_keypair_from_seed(seed, ctx=b"smelly-spend")
        sk_view, pk_view = ed25519_keypair_from_seed(seed, ctx=b"smelly-view")
        address = encode_address(pk_view, pk_spend)

        # Encrypt mnemonic using Argon2id-derived key -> AES-GCM
        salt = urandom(16)
        phs = PH.hash(req.password + b64encode(salt).decode("utf-8"))
        key = hashlib.sha256(phs.encode("utf-8")).digest()
        aesgcm = AESGCM(key)
        nonce = urandom(12)
        ct = aesgcm.encrypt(nonce, words.encode("utf-8"), None)

        wa = WalletAccount(
            name="Main",
            public_view_key=pk_view.hex(),
            public_spend_key=pk_spend.hex(),
            owner_user_id=u.id,
            enc_mnemonic=b64encode(ct).decode("utf-8"),
            enc_salt=b64encode(salt).decode("utf-8"),
            enc_nonce=b64encode(nonce).decode("utf-8"),
            created_ms=now_ms(),
        )
        s.add(wa)
        s.flush()

        # Primary subaddress 0/0
        sub = SubAddress(
            account_id=wa.id, index_major=0, index_minor=0, address=address, label="Primary"
        )
        s.add(sub)

        s.commit()

        # Issue session bound to the new account
        resp = JSONResponse({"ok": True, "account_id": wa.id, "address": address})
        _issue_session(resp, user_id=u.id, account_id=wa.id)
        return resp

@app.post("/auth/login")
def auth_login(req: LoginRequest):
    db = get_db()
    with db.session() as s:
        u = s.query(User).filter_by(username=req.username).first()
        if not u:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        try:
            PH.verify(u.password_hash, req.password)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        resp = JSONResponse({"ok": True})
        _issue_session(resp, user_id=u.id, account_id=None)
        return resp

@app.post("/auth/logout")
def auth_logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp

def require_auth(req: Request) -> tuple[int, int | None]:
    uid, acct = _parse_session(req.cookies.get(SESSION_COOKIE))
    if not uid:
        raise HTTPException(status_code=401, detail="Auth required")
    return uid, acct

def ensure_wallet_selected(req: Request) -> tuple[int, int]:
    uid, acct = require_auth(req)
    if not acct:
        raise HTTPException(status_code=400, detail="No wallet selected")
    return uid, acct

# ------------ API v1 -------------

@app.get("/api/v1/health")
def health():
    return {"ok": True, "ts": now_ms()}


@app.get("/api/v1/accounts")
def api_list_accounts(request: Request):
    uid, _ = require_auth(request)
    db = get_db()
    with db.session() as s:
        rows = s.query(WalletAccount).filter(
            (WalletAccount.owner_user_id == uid) | (WalletAccount.owner_user_id.is_(None))
        ).all()
        # Detect selected account to highlight in UI
        _, acct = _parse_session(request.cookies.get(SESSION_COOKIE))
        return [{"id": r.id, "name": r.name, "created_ms": r.created_ms, "selected": (acct == r.id)} for r in rows]


@app.post("/api/v1/wallet/create", response_model=None)
def api_create_wallet(req: CreateWalletRequest, request: Request):
    # Require auth; bind created wallet to session
    uid, _ = require_auth(request)
    if not req.passphrase or len(req.passphrase) < 4:
        raise HTTPException(status_code=400, detail="Passphrase required")
    # Generate mnemonic/seed and keys
    words, seed = generate_seed(language=req.language)
    sk_spend, pk_spend = ed25519_keypair_from_seed(seed, ctx=b"smelly-spend")
    sk_view, pk_view = ed25519_keypair_from_seed(seed, ctx=b"smelly-view")
    address = encode_address(pk_view, pk_spend)

    # Encrypt mnemonic using Argon2id->AES-GCM
    from base64 import b64encode
    from os import urandom
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    # Derive key with argon2 hash of passphrase+salt
    salt = urandom(16)
    # Use PH.hash to store password, but for KDF use a simple approach here by hashing passphrase+salt via argon2 and truncating.
    # In production, use a proper KDF (scrypt/pbkdf2/argon2 low-level). For simplicity, reuse argon2 and take bytes from encoded.
    phs = PH.hash(req.passphrase + b64encode(salt).decode("utf-8"))
    # Reduce to 32 bytes key by hashing string
    import hashlib
    key = hashlib.sha256(phs.encode("utf-8")).digest()
    aesgcm = AESGCM(key)
    nonce = urandom(12)
    ct = aesgcm.encrypt(nonce, words.encode("utf-8"), None)

    db = get_db()
    with db.session() as s:
        wa = WalletAccount(
            name=req.name,
            public_view_key=pk_view.hex(),
            public_spend_key=pk_spend.hex(),
            owner_user_id=uid,
            enc_mnemonic=b64encode(ct).decode("utf-8"),
            enc_salt=b64encode(salt).decode("utf-8"),
            enc_nonce=b64encode(nonce).decode("utf-8"),
            created_ms=now_ms(),
        )
        s.add(wa)
        s.flush()
        sub = SubAddress(
            account_id=wa.id, index_major=0, index_minor=0, address=address, label="Primary"
        )
        s.add(sub)
        s.commit()

        resp = JSONResponse({
            "account_id": wa.id,
            "address": address,
            "created": True
        })
        _issue_session(resp, user_id=uid, account_id=wa.id)
        return resp


@app.post("/api/v1/wallet/restore", response_model=None)
def api_restore_wallet(req: RestoreWalletRequest, request: Request):
    uid, _ = require_auth(request)
    if not req.passphrase or len(req.passphrase) < 4:
        raise HTTPException(status_code=400, detail="Passphrase required")
    from mnemonic import Mnemonic
    mn = Mnemonic("english")
    if not mn.check(req.mnemonic):
        raise HTTPException(status_code=400, detail="Invalid mnemonic")
    seed = mn.to_seed(req.mnemonic, passphrase="")
    sk_spend, pk_spend = ed25519_keypair_from_seed(seed, ctx=b"smelly-spend")
    sk_view, pk_view = ed25519_keypair_from_seed(seed, ctx=b"smelly-view")
    address = encode_address(pk_view, pk_spend)

    # Encrypt mnemonic
    from base64 import b64encode
    from os import urandom
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import hashlib
    salt = urandom(16)
    phs = PH.hash(req.passphrase + b64encode(salt).decode("utf-8"))
    key = hashlib.sha256(phs.encode("utf-8")).digest()
    aesgcm = AESGCM(key)
    nonce = urandom(12)
    ct = aesgcm.encrypt(nonce, req.mnemonic.encode("utf-8"), None)

    db = get_db()
    with db.session() as s:
        existing_sub = s.query(SubAddress).filter_by(address=address, index_major=0, index_minor=0).first()
        if existing_sub:
            # update ownership if not set
            acc = s.get(WalletAccount, existing_sub.account_id)
            if acc and (acc.owner_user_id is None or acc.owner_user_id == uid):
                acc.owner_user_id = uid if acc.owner_user_id is None else acc.owner_user_id
                s.commit()
            resp = JSONResponse({
                "account_id": existing_sub.account_id,
                "address": address,
                "restored": True,
                "existing": True
            })
            _issue_session(resp, user_id=uid, account_id=existing_sub.account_id)
            return resp

        wa = WalletAccount(
            name=req.name,
            public_view_key=pk_view.hex(),
            public_spend_key=pk_spend.hex(),
            owner_user_id=uid,
            enc_mnemonic=b64encode(ct).decode("utf-8"),
            enc_salt=b64encode(salt).decode("utf-8"),
            enc_nonce=b64encode(nonce).decode("utf-8"),
            created_ms=now_ms(),
        )
        s.add(wa)
        s.flush()

        sub = SubAddress(
            account_id=wa.id, index_major=0, index_minor=0, address=address, label="Primary"
        )
        s.add(sub)
        s.commit()

        resp = JSONResponse({"account_id": wa.id, "address": address, "restored": True, "existing": False})
        _issue_session(resp, user_id=uid, account_id=wa.id)
        return resp


@app.get("/api/v1/session")
def api_session(request: Request):
    uid, acct = require_auth(request)
    primary_addr = ""
    if acct:
        db = get_db()
        with db.session() as s:
            sub = s.query(SubAddress).filter_by(account_id=acct, index_major=0, index_minor=0).first()
            primary_addr = sub.address if sub else ""
    return {"user_id": uid, "account_id": acct, "primary_address": primary_addr}


class SelectWalletRequest(BaseModel):
    account_id: int

@app.post("/api/v1/wallet/select")
def api_select_wallet(req: SelectWalletRequest, request: Request):
    uid, _ = require_auth(request)
    db = get_db()
    with db.session() as s:
        acc = s.get(WalletAccount, req.account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Wallet not found")
        if acc.owner_user_id not in (None, uid):
            raise HTTPException(status_code=403, detail="Not your wallet")
        resp = JSONResponse({"ok": True, "account_id": acc.id})
        _issue_session(resp, user_id=uid, account_id=acc.id)
        return resp

@app.get("/api/v1/accounts/{account_id}/subaddresses")
def api_list_subaddresses(account_id: int, request: Request):
    uid, _ = require_auth(request)
    db = get_db()
    with db.session() as s:
        acc = s.get(WalletAccount, account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found")
        if acc.owner_user_id not in (None, uid):
            raise HTTPException(status_code=403, detail="Forbidden")
    db = get_db()
    with db.session() as s:
        subs = (
            s.query(SubAddress)
            .filter_by(account_id=account_id)
            .order_by(SubAddress.index_major, SubAddress.index_minor)
            .all()
        )
        return [
            {
                "id": sub.id,
                "major": sub.index_major,
                "minor": sub.index_minor,
                "address": sub.address,
                "label": sub.label or "",
            }
            for sub in subs
        ]


@app.post("/api/v1/subaddress/new")
def api_new_subaddress(req: NewSubAddressRequest, request: Request):
    _require_csrf(request)
    require_auth(request)
    db = get_db()
    with db.session() as s:
        acc = s.get(WalletAccount, req.account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found")
        pub_view = bytes.fromhex(acc.public_view_key)
        pub_spend = bytes.fromhex(acc.public_spend_key)
        addr = derive_subaddress(pub_view, pub_spend, req.major, req.minor)
        sub = SubAddress(
            account_id=req.account_id,
            index_major=req.major,
            index_minor=req.minor,
            address=addr,
            label=req.label or f"{req.major}/{req.minor}",
        )
        s.add(sub)
        s.commit()
        return {"id": sub.id, "address": addr}


@app.get("/api/v1/address/{address}/balance")
def api_get_balance(address: str, request: Request):
    require_auth(request)
    db = get_db()
    with db.session() as s:
        utxos = s.query(UTXO).filter_by(address=address, spent=False).order_by(UTXO.amount.asc()).all()
        rewards = s.query(Reward).filter_by(miner_address=address).all()
        bal = sum(u.amount for u in utxos)
        rtotal = sum(r.amount for r in rewards)
        # Provide spendability hints: smallest UTXO, largest UTXO, and simple coin-split suggestion threshold
        smallest = utxos[0].amount if utxos else 0.0
        largest = utxos[-1].amount if utxos else 0.0
        return {
            "address": address,
            "balance": bal,
            "rewards_total": rtotal,
            "utxos": [{"txid": u.txid, "vout": u.vout, "amount": u.amount, "coinbase": u.coinbase} for u in utxos],
            "utxo_stats": {"count": len(utxos), "smallest": smallest, "largest": largest}
        }

# New: simple diagnostics for mempool skip counts (insufficient funds/invalid)
@app.get("/api/v1/address/{address}/reasons")
def api_address_reasons(address: str, request: Request):
    require_auth(request)
    # For now, return global counters and the sender's available balance/need hints
    from sqlalchemy import func as sfunc
    db = get_db()
    with db.session() as s:
        # Balance
        avail = s.query(sfunc.coalesce(sfunc.sum(UTXO.amount), 0.0)).filter_by(address=address, spent=False).scalar() or 0.0
        # Global skip counter (incremented by consensus when nothing included)
        from core.db import KV
        kv = s.get(KV, "diag_mempool_skips")
        skips = int(kv.v) if kv and kv.v.isdigit() else 0
        return {"address": address, "available_balance": float(avail), "global_skipped_txs": skips}


@app.get("/api/v1/fees/suggest")
def api_fee_suggest():
    # Simple heuristic based on last blocks; placeholders for now
    return {
        "low": 0.00001,
        "medium": 0.0001,
        "high": 0.001,
        "tip": "Higher fees are mined first; choose 'high' for fastest confirmation."
    }

# List mempool (optionally filter by address)
@app.get("/api/v1/mempool")
def api_mempool(addr: str | None = None):
    # auth optional for read
    db = get_db()
    with db.session() as s:
        q = s.query(MempoolTx).order_by(MempoolTx.fee.desc(), MempoolTx.added_ms.desc())
        if addr:
            q = q.filter((MempoolTx.from_addr == addr) | (MempoolTx.to_addr == addr))
        rows = q.limit(500).all()
        return [{
            "txid": m.txid,
            "from_addr": m.from_addr,
            "to_addr": m.to_addr,
            "amount": m.amount,
            "fee": m.fee,
            "added_ms": m.added_ms
        } for m in rows]

# ----- Node RPC proxy + height helper -----
@app.get("/api/v1/node/height")
def api_node_height():
    cfg = get_config()
    node_url = f"http://{cfg.get('network.rpc_host','127.0.0.1')}:{cfg.get('network.rpc_port',28445)}"
    try:
        r = httpx.get(f"{node_url}/rpc/get_height", timeout=5.0)
        r.raise_for_status()
        j = r.json()
        return {"height": j.get("height", -1)}
    except Exception as e:
        return {"height": -1, "error": str(e)}

@app.api_route("/rpc/{path:path}", methods=["GET","POST"])
def proxy_rpc(path: str, request: Request):
    """
    Simple proxy so wallet UI can call /rpc/* against this server and hit Node RPC.
    """
    cfg = get_config()
    node_base = f"http://{cfg.get('network.rpc_host','127.0.0.1')}:{cfg.get('network.rpc_port',28445)}"
    target = f"{node_base}/rpc/{path}"
    try:
        if request.method == "GET":
            r = httpx.get(target, params=dict(request.query_params), timeout=10.0)
        else:
            body = {}
            try:
                body = httpx.Request("", "").json()  # placeholder
            except Exception:
                pass
            # try read request body
            try:
                body = request.json()
            except Exception:
                try:
                    body = request._body if hasattr(request, "_body") else {}
                except Exception:
                    body = {}
            r = httpx.post(target, json=body if isinstance(body, dict) else None, timeout=20.0)
        return JSONResponse(status_code=r.status_code, content=r.json() if r.headers.get("content-type","").startswith("application/json") else {"text": r.text})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RPC proxy error: {e}")


class ExportMnemonicRequest(BaseModel):
    account_id: int
    passphrase: str

@app.post("/api/v1/wallet/export_mnemonic")
def api_export_mnemonic(req: ExportMnemonicRequest, request: Request):
    uid, _ = require_auth(request)
    db = get_db()
    with db.session() as s:
        acc = s.get(WalletAccount, req.account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="Wallet not found")
        if acc.owner_user_id not in (None, uid):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not acc.enc_mnemonic or not acc.enc_salt or not acc.enc_nonce:
            raise HTTPException(status_code=400, detail="Mnemonic not available")

        from base64 import b64decode
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import hashlib, base64
        salt = b64decode(acc.enc_salt)
        nonce = b64decode(acc.enc_nonce)
        ct = b64decode(acc.enc_mnemonic)
        # Derive key like in creation
        phs = PH.hash(req.passphrase + base64.b64encode(salt).decode("utf-8"))
        key = hashlib.sha256(phs.encode("utf-8")).digest()
        aesgcm = AESGCM(key)
        try:
            words = aesgcm.decrypt(nonce, ct, None).decode("utf-8")
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid passphrase")
        return {"mnemonic": words}

@app.post("/api/v1/tx/send")
def api_send(req: SendRequest, request: Request):
    _require_csrf(request)
    require_auth(request)
    db = get_db()
    id_key = req.dedupe_key()
    nowm = now_ms()
    DEDUPE_WINDOW_MS = 30_000  # treat identical submits within 30s as duplicates

    with db.session() as s:
        # Strong idempotency: look up any mempool row with exact id_key in raw within window
        existing = (
            s.query(MempoolTx)
            .filter(MempoolTx.from_addr == req.from_address)
            .filter(MempoolTx.to_addr == req.to_address)
            .order_by(MempoolTx.added_ms.desc())
            .first()
        )
        if existing and existing.raw and f"key={id_key}" in existing.raw:
            # If recent, return same txid
            try:
                if existing.added_ms and (nowm - int(existing.added_ms)) <= DEDUPE_WINDOW_MS:
                    return {
                        "txid": existing.txid,
                        "status": "mempool",
                        "deduped": True,
                        "message": "Duplicate submit suppressed; existing mempool entry returned."
                    }
            except Exception:
                return {
                    "txid": existing.txid,
                    "status": "mempool",
                    "deduped": True,
                    "message": "Duplicate submit suppressed; existing mempool entry returned."
                }

        # Precheck: enforce spendability
        utxos = s.query(UTXO).filter_by(address=req.from_address, spent=False).order_by(UTXO.amount.desc()).all()
        avail = sum(u.amount for u in utxos)
        amount = float(req.amount)
        fee = float(req.fee)
        need = amount + fee
        if avail + 1e-12 < need:
            raise HTTPException(status_code=400, detail=f"Insufficient funds: available {avail:.6f}, need {need:.6f} (amount+fee).")
        if fee > 100.0:
            raise HTTPException(status_code=400, detail="Fee exceeds 100 SMELLY limit")

        # Enqueue mempool tx with embedded idempotency key; also enforce unique(txid) and unique key-in-raw
        raw = f"from={req.from_address};to={req.to_address};amount={amount};fee={fee};memo={req.memo or ''};key={id_key}"
        # Re-check with exact raw in case concurrent double submit in same millisecond
        existing2 = s.query(MempoolTx).filter(MempoolTx.raw == raw).first()
        if existing2:
            return {
                "txid": existing2.txid,
                "status": "mempool",
                "deduped": True,
                "message": "Duplicate submit suppressed; existing mempool entry returned."
            }

        # Generate a stable txid derived from id_key to be 100% idempotent
        import hashlib as _hh
        txid = _hh.sha3_256(("SMELLY_TX|" + id_key).encode("utf-8")).hexdigest()

        m = MempoolTx(
            txid=txid,
            raw=raw,
            added_ms=nowm,
            fee=fee,
            from_addr=req.from_address,
            to_addr=req.to_address,
            amount=amount,
        )
        # If same txid already present (race), return it
        try:
            s.add(m)
            s.commit()
        except Exception:
            s.rollback()
            again = s.query(MempoolTx).filter_by(txid=txid).first()
            if again:
                return {
                    "txid": again.txid,
                    "status": "mempool",
                    "deduped": True,
                    "message": "Duplicate submit suppressed; existing mempool entry returned."
                }
            # If truly failed for other reason, re-raise
            raise

        return {
            "txid": txid,
            "status": "mempool",
            "from": req.from_address,
            "to": req.to_address,
            "amount": amount,
            "fee": fee,
            "message": "Submitted to mempool. Higher fees are mined first."
        }


# ------------ Minimal UI entrypoints (multi-page to be added via templates) -------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(["html","xml"]))
    tpl = env.get_template("login.html")
    return HTMLResponse(tpl.render(title="Login - SMELLY Wallet"))

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    uid, acct = _parse_session(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return RedirectResponse(url="/login")
    # load primary address for the selected wallet account if available
    account_addr = ""
    account_id = acct
    if acct:
        db = get_db()
        with db.session() as s:
            primary = (
                s.query(SubAddress)
                .filter_by(account_id=acct, index_major=0, index_minor=0)
                .first()
            )
            if primary:
                account_addr = primary.address
    # render Jinja template
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(["html","xml"]))
    tpl = env.get_template("dashboard.html")
    return HTMLResponse(tpl.render(title="Dashboard - SMELLY Wallet", account_id=account_id, account_address=account_addr))

@app.get("/addresses", response_class=HTMLResponse)
def addresses_page(request: Request):
    uid, acct = _parse_session(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return RedirectResponse(url="/login")
    account_addr = ""
    account_id = acct
    if acct:
        db = get_db()
        with db.session() as s:
            primary = (
                s.query(SubAddress)
                .filter_by(account_id=acct, index_major=0, index_minor=0)
                .first()
            )
            if primary:
                account_addr = primary.address
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(["html","xml"]))
    tpl = env.get_template("addresses.html")
    return HTMLResponse(tpl.render(title="Addresses - SMELLY Wallet", account_id=account_id, account_address=account_addr))

@app.get("/send", response_class=HTMLResponse)
def send_page(request: Request):
    uid, acct = _parse_session(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return RedirectResponse(url="/login")
    account_addr = ""
    addresses: List[Dict[str, str | float]] = []
    if acct:
        db = get_db()
        with db.session() as s:
            subs = (
                s.query(SubAddress)
                .filter_by(account_id=acct)
                .order_by(SubAddress.index_major, SubAddress.index_minor)
                .all()
            )
            # Deduplicate by address in case of accidental duplicates
            seen = set()
            deduped = []
            for sub in subs:
                if sub.address in seen:
                    continue
                seen.add(sub.address)
                deduped.append(sub)
            # fetch balances for each unique subaddress
            for sub in deduped:
                bal = s.query(UTXO).with_entities(func.coalesce(func.sum(UTXO.amount), 0.0)).filter_by(address=sub.address, spent=False).scalar() or 0.0  # type: ignore
                addresses.append({"address": sub.address, "label": sub.label or f"{sub.index_major}/{sub.index_minor}", "balance": float(bal)})
            # default select primary if present
            prim = next((a for a in addresses if a["label"] == "Primary"), None)
            account_addr = prim["address"] if prim else (addresses[0]["address"] if addresses else "")
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(["html","xml"]))
    tpl = env.get_template("send.html")
    return HTMLResponse(tpl.render(
        title="Send - SMELLY Wallet",
        account_address=account_addr,
        address_options=addresses
    ))

@app.get("/txs", response_class=HTMLResponse)
def txs_page(request: Request):
    uid, acct = _parse_session(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return RedirectResponse(url="/login")
    account_addr = ""
    if acct:
        db = get_db()
        with db.session() as s:
            primary = (
                s.query(SubAddress)
                .filter_by(account_id=acct, index_major=0, index_minor=0)
                .first()
            )
            if primary:
                account_addr = primary.address
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(["html","xml"]))
    tpl = env.get_template("txs.html")
    return HTMLResponse(tpl.render(title="Transactions - SMELLY Wallet", account_address=account_addr))

@app.get("/utxos", response_class=HTMLResponse)
def utxos_page(request: Request):
    uid, acct = _parse_session(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return RedirectResponse(url="/login")
    account_addr = ""
    if acct:
        db = get_db()
        with db.session() as s:
            primary = (
                s.query(SubAddress)
                .filter_by(account_id=acct, index_major=0, index_minor=0)
                .first()
            )
            if primary:
                account_addr = primary.address
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(["html","xml"]))
    tpl = env.get_template("utxos.html")
    return HTMLResponse(tpl.render(title="UTXOs - SMELLY Wallet", account_address=account_addr))

@app.get("/blocks", response_class=HTMLResponse)
def blocks_page(request: Request):
    uid, acct = _parse_session(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return RedirectResponse(url="/login")
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(["html","xml"]))
    tpl = env.get_template("blocks.html")
    return HTMLResponse(tpl.render(title="Blocks - SMELLY Wallet"))

@app.get("/wallet", response_class=HTMLResponse)
def wallet_mgmt_page(request: Request):
    uid, _ = _parse_session(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return RedirectResponse(url="/login")
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(["html","xml"]))
    tpl = env.get_template("wallet.html")
    return HTMLResponse(tpl.render(title="Wallet Management - SMELLY Wallet"))

# Keep legacy inline UI routes for compatibility until full templates are added

WALLET_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>SMELLY Web Wallet</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root { --primary:#FFD000; --bg:#0b0b0b; --panel:#161616; --text:#f1f1f1; }
    body { background: var(--bg); color: var(--text); }
    .brand { color:#000; background:var(--primary) }
    .card { background:var(--panel); border:1px solid #222; border-radius:12px; }
    .mono { font-family: Consolas, monospace; word-break: break-all; }
  </style>
</head>
<body class="min-h-screen">
  <header class="brand px-4 py-3 font-extrabold tracking-wide flex justify-between items-center">
    <div>SMELLY Wallet</div>
    <nav class="text-sm">
      <a class="px-2" href="/wallet/ui">Home</a>
      <a class="px-2" href="/docs">API</a>
    </nav>
  </header>
  <div class="max-w-6xl mx-auto p-4" x-data="walletApp()">
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <aside class="md:col-span-1 card p-4">
        <h2 class="text-yellow-400 text-xl font-bold mb-2">Wallet</h2>
        <nav class="space-y-2">
          <button class="w-full text-left brand px-3 py-2 rounded" @click="tab='dashboard'">Dashboard</button>
          <button class="w-full text-left brand px-3 py-2 rounded" @click="tab='subaddresses'">Subaddresses</button>
          <button class="w-full text-left brand px-3 py-2 rounded" @click="tab='send'">Send</button>
          <button class="w-full text-left brand px-3 py-2 rounded" @click="tab='settings'">Settings</button>
        </nav>
      </aside>
      <section class="md:col-span-3 space-y-4">
        <template x-if="tab==='dashboard'">
          <div class="space-y-4">
            <section class="card p-4">
              <h2 class="text-yellow-400 text-xl font-bold mb-2">Create / Restore</h2>
              <div class="space-x-2">
                <button class="brand px-3 py-2 rounded" @click="create()">Create Wallet</button>
                <button class="brand px-3 py-2 rounded" @click="restore()">Restore Wallet</button>
              </div>
              <div class="mt-3 text-sm" x-show="mnemonic">
                <div class="font-bold text-yellow-400">Your Mnemonic (save securely!)</div>
                <div class="mono bg-black/30 p-2 rounded mt-1" x-text="mnemonic"></div>
              </div>
            </section>

            <section class="card p-4">
              <h2 class="text-yellow-400 text-xl font-bold mb-2">Account</h2>
              <div>Account ID: <span x-text="account_id || '-'"></span></div>
              <div>Primary Address:</div>
              <div class="mono bg-black/30 p-2 rounded mt-1" x-text="address || '-'"></div>
              <div class="mt-2">
                <div class="text-sm text-gray-300">Balance:</div>
                <div class="text-lg font-bold" x-text="balance_display"></div>
              </div>
            </section>
          </div>
        </template>
        <template x-if="tab==='subaddresses'">
          <section class="card p-4">
            <h2 class="text-yellow-400 text-xl font-bold mb-2">Subaddresses</h2>
            <div class="flex items-end gap-2">
              <div><label class="text-sm">Major</label><input class="bg-black/30 px-2 py-1 rounded" type="number" x-model.number="sub.major"></div>
              <div><label class="text-sm">Minor</label><input class="bg-black/30 px-2 py-1 rounded" type="number" x-model.number="sub.minor"></div>
              <div><label class="text-sm">Label</label><input class="bg-black/30 px-2 py-1 rounded" type="text" x-model="sub.label"></div>
              <button class="brand px-3 py-2 rounded" @click="newSub()">Create</button>
            </div>
            <table class="table-auto w-full mt-3">
              <thead><tr class="text-left"><th>ID</th><th>Index</th><th>Address</th><th>Label</th></tr></thead>
              <tbody>
                <template x-for="s in subaddresses" :key="s.id">
                  <tr>
                    <td x-text="s.id"></td>
                    <td x-text="s.major + '/' + s.minor"></td>
                    <td class="mono" x-text="s.address"></td>
                    <td x-text="s.label"></td>
                  </tr>
                </template>
              </tbody>
            </table>
          </section>
        </template>
        <template x-if="tab==='send'">
          <section class="card p-4">
            <h2 class="text-yellow-400 text-xl font-bold mb-2">Send</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
              <div><label class="text-sm">From</label><input class="w-full bg-black/30 px-2 py-1 rounded mono" x-model="send.from"></div>
              <div><label class="text-sm">To</label><input class="w-full bg-black/30 px-2 py-1 rounded mono" x-model="send.to"></div>
              <div><label class="text-sm">Amount</label><input class="w-full bg-black/30 px-2 py-1 rounded" type="number" x-model.number="send.amount"></div>
        <div>
          <label class="text-sm block">Fee</label>
          <div class="flex flex-col gap-2">
            <div class="flex items-center gap-2">
              <input class="bg-black/30 px-2 py-1 rounded w-28" type="number" step="0.00001" x-model.number="send.fee">
              <span class="text-xs text-gray-400">or use slider:</span>
            </div>
            <div class="flex items-center gap-2">
              <input type="range" min="0" max="100" step="1" x-model.number="fee_slider" @input="onFeeSlider()" class="w-full">
              <span class="w-12 text-right text-xs" x-text="fee_level_label"></span>
            </div>
            <div class="flex gap-2">
              <button class="brand px-2 rounded" @click="suggestFee('low')">Low</button>
              <button class="brand px-2 rounded" @click="suggestFee('medium')">Med</button>
              <button class="brand px-2 rounded" @click="suggestFee('high')">High</button>
            </div>
          </div>
          <div class="text-xs text-gray-400 mt-1" x-text="fee_tip"></div>
        </div>
            </div>
            <div class="mt-2"><label class="text-sm">Memo</label><input class="w-full bg-black/30 px-2 py-1 rounded" x-model="send.memo"></div>
            <div class="mt-3"><button class="brand px-3 py-2 rounded" @click="submitTx()">Submit</button></div>
            <div class="mt-2 text-sm text-green-400" x-show="last_txid">Submitted <span class="mono" x-text="last_txid"></span></div>
          </section>
        </template>
        <template x-if="tab==='settings'">
          <section class="card p-4">
            <h2 class="text-yellow-400 text-xl font-bold mb-2">Settings</h2>
            <div class="text-sm text-gray-400">Future: passkeys, TOTP, client-side keys.</div>
          </section>
        </template>
      </section>
    </div>
  </div>
      <section class="card p-4">
        <h2 class="text-yellow-400 text-xl font-bold mb-2">Create / Restore</h2>
        <div class="space-x-2">
          <button class="brand px-3 py-2 rounded" @click="create()">Create Wallet</button>
          <button class="brand px-3 py-2 rounded" @click="restore()">Restore Wallet</button>
        </div>
        <div class="mt-3 text-sm" x-show="mnemonic">
          <div class="font-bold text-yellow-400">Your Mnemonic (save securely!)</div>
          <div class="mono bg-black/30 p-2 rounded mt-1" x-text="mnemonic"></div>
        </div>
      </section>

      <section class="card p-4">
        <h2 class="text-yellow-400 text-xl font-bold mb-2">Account</h2>
        <div>Account ID: <span x-text="account_id || '-'"></span></div>
        <div>Primary Address:</div>
        <div class="mono bg-black/30 p-2 rounded mt-1" x-text="address || '-'"></div>
        <div class="mt-2">
          <div class="text-sm text-gray-300">Balance:</div>
          <div class="text-lg font-bold" x-text="balance_display"></div>
        </div>
      </section>
    </div>

    <section class="card p-4">
      <h2 class="text-yellow-400 text-xl font-bold mb-2">Subaddresses</h2>
      <div class="flex items-end gap-2">
        <div><label class="text-sm">Major</label><input class="bg-black/30 px-2 py-1 rounded" type="number" x-model.number="sub.major"></div>
        <div><label class="text-sm">Minor</label><input class="bg-black/30 px-2 py-1 rounded" type="number" x-model.number="sub.minor"></div>
        <div><label class="text-sm">Label</label><input class="bg-black/30 px-2 py-1 rounded" type="text" x-model="sub.label"></div>
        <button class="brand px-3 py-2 rounded" @click="newSub()">Create</button>
      </div>
      <table class="table-auto w-full mt-3">
        <thead><tr class="text-left"><th>ID</th><th>Index</th><th>Address</th><th>Label</th></tr></thead>
        <tbody>
          <template x-for="s in subaddresses" :key="s.id">
            <tr>
              <td x-text="s.id"></td>
              <td x-text="s.major + '/' + s.minor"></td>
              <td class="mono" x-text="s.address"></td>
              <td x-text="s.label"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </section>

    <section class="card p-4">
      <h2 class="text-yellow-400 text-xl font-bold mb-2">Send</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
        <div><label class="text-sm">From</label><input class="w-full bg-black/30 px-2 py-1 rounded mono" x-model="send.from"></div>
        <div><label class="text-sm">To</label><input class="w-full bg-black/30 px-2 py-1 rounded mono" x-model="send.to"></div>
        <div><label class="text-sm">Amount</label><input class="w-full bg-black/30 px-2 py-1 rounded" type="number" x-model.number="send.amount"></div>
        <div>
          <label class="text-sm block">Fee</label>
          <div class="flex gap-2">
            <input class="bg-black/30 px-2 py-1 rounded w-28" type="number" step="0.00001" x-model.number="send.fee">
            <button class="brand px-2 rounded" @click="suggestFee('low')">Low</button>
            <button class="brand px-2 rounded" @click="suggestFee('medium')">Med</button>
            <button class="brand px-2 rounded" @click="suggestFee('high')">High</button>
          </div>
          <div class="text-xs text-gray-400 mt-1" x-text="fee_tip"></div>
        </div>
      </div>
      <div class="mt-2"><label class="text-sm">Memo</label><input class="w-full bg-black/30 px-2 py-1 rounded" x-model="send.memo"></div>
      <div class="mt-3"><button class="brand px-3 py-2 rounded" @click="submitTx()">Submit</button></div>
      <div class="mt-2 text-sm text-green-400" x-show="last_txid">Submitted <span class="mono" x-text="last_txid"></span></div>
    </section>

  </main>

<script>
function walletApp() {
  return {
    tab: 'dashboard',
    csrf: (Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2)).slice(0,32),
    account_id: null,
    address: null,
    mnemonic: null,
    balance_display: "-",
    sub: {major:0, minor:1, label:""},
    subaddresses: [],
    send: {from:"", to:"", amount:0, fee:0, memo:""},
    fee_tip: "",
    last_txid: "",
    fee_slider: 0,
    fee_low: 0.00001,
    fee_med: 0.0001,
    fee_high: 0.001,
    fee_level_label: 'low',

    async create() {
      const r = await fetch('/api/v1/wallet/create', {method:'POST', headers:{'content-type':'application/json'}});
      const j = await r.json();
      this.account_id = j.account_id; this.address = j.address; this.mnemonic = j.mnemonic;
      this.send.from = this.address;
      this.refresh();
    },
    async restore() {
      const words = prompt("Enter BIP39 mnemonic:");
      if (!words) return;
      const r = await fetch('/api/v1/wallet/restore', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({name:'Restored', mnemonic:words})});
      const j = await r.json();
      this.account_id = j.account_id; this.address = j.address;
      this.send.from = this.address;
      this.refresh();
    },
    async refresh() {
      if (!this.address || !this.account_id) return;
      // balance
      let r = await fetch('/api/v1/address/' + this.address + '/balance');
      let j = await r.json();
      this.balance_display = j.balance + " SMELLY";
      // subaddresses
      r = await fetch('/api/v1/accounts/' + this.account_id + '/subaddresses');
      this.subaddresses = await r.json();
    },
    async newSub() {
      const r = await fetch('/api/v1/subaddress/new', {method:'POST', headers:{'content-type':'application/json','x-smelly-csrf':this.csrf}, body: JSON.stringify({account_id:this.account_id, major:this.sub.major, minor:this.sub.minor, label:this.sub.label})});
      if (r.ok) this.refresh();
    },
    async suggestFee(tier) {
      const r = await fetch('/api/v1/fees/suggest');
      const j = await r.json();
      this.fee_low = j.low; this.fee_med = j.medium; this.fee_high = j.high;
      this.send.fee = j[tier];
      this.fee_tip = j.tip;
      // Update slider position based on tier
      if (tier==='low') { this.fee_slider = 0; this.fee_level_label='low'; }
      else if (tier==='medium') { this.fee_slider = 50; this.fee_level_label='med'; }
      else { this.fee_slider = 100; this.fee_level_label='high'; }
    },
    onFeeSlider() {
      // Interpolate between low/med/high fee presets across 0..100
      const x = this.fee_slider;
      let fee = this.fee_low;
      if (x <= 50) {
        // low..med
        const t = x/50.0;
        fee = this.fee_low + (this.fee_med - this.fee_low) * t;
        this.fee_level_label = t < 0.33 ? 'low' : (t < 0.66 ? 'low+' : 'med-');
      } else {
        const t = (x-50)/50.0;
        fee = this.fee_med + (this.fee_high - this.fee_med) * t;
        this.fee_level_label = t < 0.33 ? 'med' : (t < 0.66 ? 'med+' : 'high');
      }
      this.send.fee = Number(fee.toFixed(6));
    },
    async submitTx() {
      const r = await fetch('/api/v1/tx/send', {method:'POST', headers:{'content-type':'application/json','x-smelly-csrf':this.csrf}, body: JSON.stringify({from_address:this.send.from, to_address:this.send.to, amount:this.send.amount, fee:this.send.fee, memo:this.send.memo})});
      const j = await r.json();
      this.last_txid = j.txid || '';
      alert('Submitted ' + this.last_txid + ' to mempool. Higher fees are mined first.');
    }
  }
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def root_wallet_ui(request: Request):
    # Redirect to dashboard if authenticated, else to login
    uid, _ = _parse_session(request.cookies.get(SESSION_COOKIE))
    if uid:
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")

@app.get("/wallet/ui", response_class=HTMLResponse)
def wallet_ui():
    return HTMLResponse(WALLET_HTML)


# Back-compat simple endpoints (kept; prefer /api/v1/*)

@app.get("/wallet/accounts")
def list_accounts():
    return api_list_accounts()


@app.post("/wallet/create")
def create_wallet(req: CreateWalletRequest, request: Request):
    return api_create_wallet(req, request)


@app.post("/wallet/restore")
def restore_wallet(req: RestoreWalletRequest, request: Request):
    return api_restore_wallet(req, request)


@app.get("/wallet/{account_id}/subaddresses")
def list_subaddresses(account_id: int):
    return api_list_subaddresses(account_id)


@app.post("/wallet/new_subaddress")
def new_subaddress(req: NewSubAddressRequest, request: Request):
    return api_new_subaddress(req, request)


@app.get("/wallet/{address}/balance")
def get_balance(address: str):
    return api_get_balance(address)


def run_wallet_backend():
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    port = int(cfg.get("network.web_wallet_port", 28450))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_wallet_backend()
