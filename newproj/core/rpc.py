from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import time
import uuid
import json
import hmac
import hashlib
import logging
from logging import Logger

from core.config import get_config
from core.consensus import (
    get_chain_height,
    get_header_by_height,
    get_header_by_hash,
    get_headers_range,
    append_block_header,
    add_genesis_if_needed,
    accept_external_header,
    validate_mempool_tx,
)
from core.db import get_db, BlockHeader, MempoolTx, FairnessEpoch, FairnessCredit, KV
from core.utils import ensure_dirs, now_ms
from core.pow.randomx_stub import difficulty_to_target
from sqlalchemy import func
import socket

# In-memory job cache for client-side mining (reset on restart)
_WORK_JOBS: Dict[str, Dict[str, Any]] = {}
_WORK_TTL_MS = 300_000  # 5 minutes

# Ticket mining defaults (can be overridden via configs)
_TICKET_WINDOW_MS = 4000
_NONCE_WINDOW_POW2 = 21  # 2^21
_NEAR_TARGET_RATE_PER_MIN = 3

app = FastAPI(title="SMELLY JSON-RPC", version="0.2")

# Structured + colorized logger (very verbose for deep diagnostics)
class _Color:
    RESET = "\x1b[0m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YEL = "\x1b[33m"
    BLUE = "\x1b[34m"
    MAG = "\x1b[35m"
    CYA = "\x1b[36m"
    DIM = "\x1b[2m"

class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        level = record.levelno
        color = _Color.RESET
        if level >= logging.ERROR:
            color = _Color.RED
        elif level >= logging.WARNING:
            color = _Color.YEL
        elif level >= logging.INFO:
            color = _Color.CYA
        else:
            color = _Color.DIM
        prefix = f"{color}[RPC]{_Color.RESET}"
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        return f"{prefix} {ts} {record.levelname} {record.getMessage()}"

def _mk_logger(name: str, level=logging.DEBUG) -> Logger:
    lg = logging.getLogger(name)
    lg.setLevel(level)
    if not lg.handlers:
        sh = logging.StreamHandler()
        sh.setFormatter(ColorFormatter())
        lg.addHandler(sh)
    return lg

rpc_logger = _mk_logger("smelly.rpc", level=logging.DEBUG)


class MineRequest(BaseModel):
    miner_address: str


class HeadersRequest(BaseModel):
    start_height: int
    count: int = 200


class GetWorkRequest(BaseModel):
    miner_address: Optional[str] = None


class SubmitWorkRequest(BaseModel):
    job_id: str
    miner_address: str
    nonce: int
    timestamp: int
    version: int
    merkle_root_hex: str
    prev_hash_hex: Optional[str] = None


class TxSubmitRequest(BaseModel):
    tx: Dict[str, Any]


# Solo ticketed mining
class SoloTicketRequest(BaseModel):
    addr: str


class SoloSubmitNear(BaseModel):
    ticket_id: str
    addr: str
    nonce: int
    digest_hex: str
    proof_level: int = 1
    payload: Optional[str] = None
    sig: Optional[str] = None


class SoloSubmitBlock(BaseModel):
    ticket_id: str
    addr: str
    nonce: int
    version: int
    timestamp: int
    merkle_root_hex: str
    payload: Optional[str] = None
    sig: Optional[str] = None


@app.on_event("startup")
def on_startup():
    ensure_dirs()
    db = get_db()
    add_genesis_if_needed()
    cfg = get_config()
    global _TICKET_WINDOW_MS, _NONCE_WINDOW_POW2, _NEAR_TARGET_RATE_PER_MIN
    _TICKET_WINDOW_MS = int(cfg.get("fairness.ticket_window_ms", 4000))
    _NONCE_WINDOW_POW2 = int(cfg.get("fairness.nonce_window_pow2", 21))
    _NEAR_TARGET_RATE_PER_MIN = int(cfg.get("fairness.target_near_rate_per_min", 3))
    _ensure_current_epoch()
    try:
        from core.pow.pow_backend import backend_name
        rpc_logger.info(
            f"startup: backend={backend_name()} ticket_window_ms={_TICKET_WINDOW_MS} "
            f"nonce_window_pow2={_NONCE_WINDOW_POW2} near_rate={_NEAR_TARGET_RATE_PER_MIN}"
        )
    except Exception as e:
        rpc_logger.warning(f"startup: backend=unknown err={e}")

    # DB sanity
    try:
        with db.session() as s:
            tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
            h = -1 if tip is None else tip.height
            rpc_logger.info(f"startup: tip_height={h}")
    except Exception as e:
        rpc_logger.error(f"startup: db_sanity_failed err={e}")


@app.get("/rpc/get_height")
def rpc_get_height():
    h = get_chain_height()
    rpc_logger.info("get_height -> %s", h)
    return {"height": h}


@app.get("/rpc/pow_backend")
def rpc_pow_backend():
    try:
        from core.pow.pow_backend import backend_name
        return {"backend": backend_name()}
    except Exception as e:
        return {"backend": "unknown", "error": str(e)}


@app.get("/rpc/get_header_by_height/{height}")
def rpc_get_header_by_height(height: int):
    h = get_header_by_height(height)
    if not h:
        raise HTTPException(status_code=404, detail="Header not found")
    return {
        "height": h.height,
        "hash": h.hash_hex,
        "prev_hash": h.prev_hash_hex,
        "timestamp": h.timestamp,
        "version": h.version,
        "target": h.target,
        "nonce": h.nonce,
        "miner": h.miner_address,
        "tx_count": h.tx_count,
        "work": h.work,
    }


@app.get("/rpc/get_header_by_hash/{hash_hex}")
def rpc_get_header_by_hash(hash_hex: str):
    h = get_header_by_hash(hash_hex)
    if not h:
        raise HTTPException(status_code=404, detail="Header not found")
    return {
        "height": h.height,
        "hash": h.hash_hex,
        "prev_hash": h.prev_hash_hex,
        "timestamp": h.timestamp,
        "version": h.version,
        "target": h.target,
        "nonce": h.nonce,
        "miner": h.miner_address,
        "tx_count": h.tx_count,
        "work": h.work,
    }


@app.post("/rpc/get_headers_range")
def rpc_get_headers_range(req: HeadersRequest):
    headers = get_headers_range(req.start_height, req.count)
    return [
        {
            "height": h.height,
            "hash": h.hash_hex,
            "prev_hash": h.prev_hash_hex,
            "timestamp": h.timestamp,
            "version": h.version,
            "target": h.target,
            "nonce": h.nonce,
            "miner": h.miner_address,
            "tx_count": h.tx_count,
            "work": h.work,
        }
        for h in headers
    ]


@app.post("/rpc/mine_one")
def rpc_mine_one(req: MineRequest):
    try:
        hh, err = append_block_header(req.miner_address)
        if err:
            return {"hash": None, "error": err}
        return {"hash": hh}
    except Exception as e:
        return {"hash": None, "error": str(e)}


def _build_work_snapshot(miner_address: Optional[str]) -> Dict[str, Any]:
    """
    Build a work package consistent with consensus merkle rules:
    - Height < 200: coinbase-only
    - Height >= 200: mempool txids ordered by fee desc, added_ms asc, lowercase hex
    Includes deep debug: selection, ordering, counts, and warnings at boundary.
    """
    db = get_db()
    cfg = get_config()
    MIN_FEE = float(cfg.get("mempool.min_fee", 0.000001))
    TXS_PER_BLOCK = int(cfg.get("consensus.txs_per_block_cap", 200))
    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        height = 0 if tip is None else tip.height + 1
        prev_hash = "00" * 32 if tip is None else tip.hash_hex

        if height < 200:
            target_hex = difficulty_to_target(1)
        else:
            target_hex = tip.target if (tip and tip.target) else difficulty_to_target(1)

        try:
            from core.utils import sha3_256_hex as _sha
            coinbase_txid = _sha(f"COINBASE:{height}".encode("utf-8")).lower()
        except Exception:
            coinbase_txid = hashlib.sha3_256(f"COINBASE:{height}".encode("utf-8")).hexdigest().lower()

        snapshot_txids: List[str] = [coinbase_txid]

        mem = []
        mem_count = 0
        if height >= 200:
            mem = (
                s.query(MempoolTx)
                .filter(MempoolTx.fee >= MIN_FEE)
                .order_by(MempoolTx.fee.desc(), MempoolTx.added_ms.asc())
                .limit(TXS_PER_BLOCK)
                .all()
            )
            mem_count = len(mem)
            for m in mem:
                txid_norm = ((m.txid or "").strip().lower())
                if txid_norm:
                    snapshot_txids.append(txid_norm)

        rpc_logger.debug(f"get_work: height={height} prev={prev_hash} target={target_hex} txs_snapshot_len={len(snapshot_txids)}")
        if height == 199:
            rpc_logger.warning("get_work: boundary approaching (next block will enable mempool inclusion)")
        if height == 200:
            rpc_logger.warning("get_work: boundary reached; mempool txids included after coinbase (coinbase-first ordering enforced)")
        if mem_count and height >= 200:
            preview = ",".join([ (m.txid or "")[:12] for m in mem[:10] ])
            rpc_logger.debug(f"get_work: mempool_considered={mem_count} top={preview}")

        job = {
            "job_id": uuid.uuid4().hex,
            "issued_ms": now_ms(),
            "ttl_ms": _WORK_TTL_MS,
            "height": height,
            "prev_hash": prev_hash,
            "target": (str(target_hex) or "").lower(),
            "version": int(cfg.get("consensus.block_version", 1)),
            "timestamp": int(time.time()),
            "miner_hint": miner_address or "",
            "txids": snapshot_txids,
        }
        rpc_logger.info(
            f"get_work: h={job['height']} prev={job['prev_hash'][:16]}.. target={job['target'][:16]}.. "
            f"txs={len(snapshot_txids)} job_id={job['job_id']} miner_hint={job['miner_hint']}"
        )
        if height >= 200:
            rpc_logger.info(f"get_work: mempool_count_considered={mem_count}")
        return job


def _store_job(job: Dict[str, Any]):
    _WORK_JOBS[job["job_id"]] = job
    nowm = now_ms()
    expired = [k for k, v in _WORK_JOBS.items() if nowm - int(v.get("issued_ms", 0)) > int(v.get("ttl_ms", _WORK_TTL_MS))]
    for k in expired:
        _WORK_JOBS.pop(k, None)


@app.post("/rpc/get_work")
def rpc_get_work(req: GetWorkRequest):
    try:
        job = _build_work_snapshot(req.miner_address if req and req.miner_address else None)
        _store_job(job)
        return job
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"get_work failed: {e}")


@app.post("/rpc/submit_work")
def rpc_submit_work(req: SubmitWorkRequest):
    """
    Submit a found nonce for issued job_id. Consensus recomputes merkle from the job snapshot.
    Emits extremely detailed logs for each stage.
    """
    rpc_logger.info(
        f"submit_work: job_id={getattr(req,'job_id',None)} miner={getattr(req,'miner_address',None)} "
        f"nonce={getattr(req,'nonce',None)} ts={getattr(req,'timestamp',None)} ver={getattr(req,'version',None)} "
        f"merkle={(getattr(req,'merkle_root_hex','') or '')[:16]}"
    )
    rpc_logger.debug(f"submit_work: payload={req.model_dump() if hasattr(req,'model_dump') else req.__dict__}")

    job = _WORK_JOBS.get(req.job_id)
    if not job:
        rpc_logger.warning(f"submit_work: unknown_or_expired_job job_id={req.job_id}")
        raise HTTPException(status_code=400, detail={"accepted": False, "error": "unknown or expired job"})

    if now_ms() - int(job.get("issued_ms", 0)) > int(job.get("ttl_ms", _WORK_TTL_MS)):
        _WORK_JOBS.pop(req.job_id, None)
        rpc_logger.warning(f"submit_work: stale_job job_id={req.job_id} issued_ms={job.get('issued_ms')} ttl_ms={job.get('ttl_ms')}")
        raise HTTPException(status_code=400, detail={"accepted": False, "error": "stale job"})

    height = int(job.get("height", 0))
    prev_from_job = str(job.get("prev_hash", "")).lower()
    target_hex = str(job.get("target", "")).lower()
    version = int(req.version)
    timestamp = int(req.timestamp)
    nonce = int(req.nonce)
    miner_address = req.miner_address

    if req.prev_hash_hex:
        client_prev = str(req.prev_hash_hex).strip().lower()
        if client_prev != prev_from_job:
            rpc_logger.error(f"submit_work: prev_mismatch job_prev={prev_from_job[:16]} client_prev={client_prev[:16]} height={height}")
            raise HTTPException(
                status_code=400,
                detail={
                    "accepted": False,
                    "error": "prev mismatch vs issued job",
                    "job_prev": prev_from_job,
                    "client_prev": client_prev,
                    "height": height,
                    "job_id": req.job_id,
                },
            )

    # Build the txids snapshot exactly as issued
    if height < 200:
        try:
            from core.utils import sha3_256_hex as _sha
            txids_snapshot = [_sha(f"COINBASE:{height}".encode("utf-8")).lower()]
        except Exception:
            txids_snapshot = [hashlib.sha3_256(f"COINBASE:{height}".encode("utf-8")).hexdigest().lower()]
    else:
        txids_snapshot = [str(t).strip().lower() for t in (job.get("txids") or []) if str(t).strip()]

    # Promote via consensus
    rpc_logger.debug(
        "submit_work: promoting via accept_external_header "
        f"prev={prev_from_job[:16]}.. target={target_hex[:16]}.. nonce={nonce} ver={version} ts={timestamp} "
        f"txids_len={(len(txids_snapshot) if 'txids_snapshot' in locals() else 'NA')}"
    )
    hh, err = accept_external_header(
        prev_hash_hex=prev_from_job,
        merkle_root_hex="",  # recomputed from txids_snapshot
        version=version,
        timestamp=timestamp,
        target_hex=target_hex,
        nonce=nonce,
        miner_address=miner_address,
        txids_snapshot=txids_snapshot,
    )

    if err:
        detail = {
            "accepted": False,
            "error": err,
            "height": height,
            "prev": prev_from_job,
            "target": target_hex,
            "submitted_merkle": (req.merkle_root_hex or "").lower(),
            "txids_len": len(txids_snapshot),
            "job_id": req.job_id,
        }
        if txids_snapshot:
            detail["txid0"] = txids_snapshot[0]
        rpc_logger.error("submit_work: reject %s", json.dumps(detail, separators=(",", ":"), sort_keys=True))
        # Highlight common failure classes
        low = (err or "").lower()
        if "merkle" in low:
            rpc_logger.error(_Color.RED + "HINT: Merkle mismatch; ensure coinbase-first ordering and lowercase txids." + _Color.RESET)
        if "prev" in low:
            rpc_logger.error(_Color.RED + "HINT: Prev mismatch (stale). Miner should refresh work more frequently." + _Color.RESET)
        if "pow" in low or "target" in low:
            rpc_logger.error(_Color.RED + "HINT: PoW not meeting target; verify backend and nonce space." + _Color.RESET)
        raise HTTPException(status_code=400, detail=detail)

    _WORK_JOBS.pop(req.job_id, None)
    rpc_logger.info(_Color.GREEN + f"submit_work: ACCEPTED h={height} hash={hh[:16]}.." + _Color.RESET)
    return {"accepted": True, "hash": hh, "height": height, "prev": prev_from_job, "job_id": req.job_id, "txids_len": len(txids_snapshot)}


@app.post("/rpc/tx/submit")
def rpc_tx_submit(req: TxSubmitRequest):
    tx = req.tx
    height = get_chain_height()
    ok, reason, txid = validate_mempool_tx(tx, height=height if height >= 0 else 0)
    if not ok:
        raise HTTPException(status_code=400, detail={"accepted": False, "error": reason, "txid": txid})

    db = get_db()
    with db.session() as s:
        raw_compact = json.dumps(tx, separators=(",", ":"), sort_keys=True)
        from_addr = None
        to_addr = None
        amount = None
        fee = float(tx.get("fee", 0.0))
        try:
            if tx.get("outputs"):
                first_out = tx["outputs"][0]
                to_addr = first_out.get("address")
                amount = float(first_out.get("amount", 0.0))
            if tx.get("inputs"):
                first_in = tx["inputs"][0]
                from_addr = first_in.get("address")
        except Exception:
            pass

        existing = s.query(MempoolTx).filter_by(txid=txid).first()
        if not existing:
            s.add(MempoolTx(
                txid=txid,
                raw=raw_compact,
                added_ms=now_ms(),
                fee=fee,
                from_addr=from_addr,
                to_addr=to_addr,
                amount=amount,
            ))
        else:
            if not existing.raw:
                existing.raw = raw_compact
            if existing.fee is None:
                existing.fee = fee
            if not existing.from_addr and from_addr:
                existing.from_addr = from_addr
            if not existing.to_addr and to_addr:
                existing.to_addr = to_addr
            if existing.amount is None and amount is not None:
                existing.amount = amount
        s.commit()

    return {"accepted": True, "txid": txid}


@app.get("/rpc/mempool")
def rpc_mempool():
    db = get_db()
    with db.session() as s:
        rows = s.query(MempoolTx).order_by(MempoolTx.added_ms.desc()).limit(500).all()
        out = []
        for m in rows:
            out.append({
                "txid": m.txid,
                "fee": m.fee,
                "from": m.from_addr,
                "to": m.to_addr,
                "amount": m.amount,
                "added_ms": m.added_ms,
            })
        return out


@app.get("/rpc/mempool_count")
def rpc_mempool_count():
    db = get_db()
    with db.session() as s:
        cnt = s.query(MempoolTx).count()
        return {"count": int(cnt)}


@app.post("/rpc/p2p/connect")
def rpc_p2p_connect(addr: str):
    """
    Attempt raw TCP connect as a quick connectivity probe. Returns {"connected":true} or 400.
    """
    try:
        host, port_str = addr.split(":")
        port = int(port_str)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((host, port))
        s.close()
        return {"connected": True, "addr": addr}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ========== Ticketed Solo Mining (diagnostics-first) ==========

def _epoch_lengths() -> Tuple[int, int]:
    cfg = get_config()
    return int(cfg.get("fairness.epoch_length_dev", 20)), int(cfg.get("fairness.epoch_length_main", 100))


def _pool_ratio() -> float:
    cfg = get_config()
    return float(cfg.get("fairness.pool_ratio", 0.30))


def _epoch_for_height(height: int) -> Tuple[int, int]:
    dev_len, main_len = _epoch_lengths()
    size = dev_len if int(get_config().get("network.dev_mode", 1)) else main_len
    start = (height // size) * size
    end = start + size - 1
    return start, end


def _ensure_current_epoch():
    db = get_db()
    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        cur_height = 0 if tip is None else tip.height
        start, end = _epoch_for_height(cur_height)
        ep = (
            s.query(FairnessEpoch)
            .filter(FairnessEpoch.start_height == start, FairnessEpoch.end_height == end)
            .first()
        )
        if not ep:
            ep = FairnessEpoch(
                start_height=start,
                end_height=end,
                pool_ratio=_pool_ratio(),
                settled=False,
                created_ms=now_ms(),
            )
            s.add(ep)
            s.commit()


def _ticket_secret() -> bytes:
    db = get_db()
    with db.session() as s:
        row = s.get(KV, "solo_ticket_secret")
        if not row or not row.v:
            import os
            val = os.urandom(32).hex()
            s.merge(KV(k="solo_ticket_secret", v=val))
            s.commit()
            return bytes.fromhex(val)
        return bytes.fromhex(row.v)


def _sign_ticket(payload: str) -> str:
    secret = _ticket_secret()
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha3_256).hexdigest()


@app.get("/rpc/solo/get_ticket")
def rpc_solo_get_ticket(addr: str):
    rpc_logger.info(f"solo_get_ticket: addr={addr}")
    if not addr or not addr.startswith("SMELLY_"):
        raise HTTPException(status_code=400, detail="invalid address")
    db = get_db()
    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        height = 0 if not tip else tip.height + 1
        prev_hash = "00" * 32 if not tip else tip.hash_hex
        if height < 200:
            target_hex = difficulty_to_target(1)
        else:
            target_hex = tip.target if tip and tip.target else difficulty_to_target(1)
    issued = now_ms()
    valid_to = issued + _TICKET_WINDOW_MS
    seed = uuid.uuid4().hex
    start_nonce = int(hashlib.sha3_256(seed.encode()).hexdigest(), 16) & ((1 << 32) - 1)
    window = 1 << _NONCE_WINDOW_POW2
    payload = json.dumps(
        {
            "addr": addr,
            "prev": prev_hash,
            "target": target_hex,
            "version": int(get_config().get("consensus.block_version", 1)),
            "issued": issued,
            "valid_to": valid_to,
            "nonce_start": start_nonce,
            "nonce_window": window,
            "seed": seed,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    sig = _sign_ticket(payload)
    ticket_id = uuid.uuid4().hex
    return {"ticket_id": ticket_id, "payload": payload, "sig": sig}


def _validate_ticket(payload: str, sig: str) -> Dict[str, Any]:
    if not payload or not sig:
        raise HTTPException(status_code=400, detail="missing ticket")
    if _sign_ticket(payload) != sig:
        raise HTTPException(status_code=400, detail="bad ticket signature")
    try:
        obj = json.loads(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad ticket payload: {e}")
    nowm = now_ms()
    if nowm > int(obj.get("valid_to", 0)):
        raise HTTPException(status_code=400, detail="ticket expired")
    return obj


def _near_target_threshold(target_hex: str) -> int:
    # Easier than target to achieve ~N/min near proofs
    t = int(target_hex, 16)
    return min((1 << 256) - 1, t << 12)


@app.post("/rpc/solo/submit_near_target")
def rpc_solo_submit_near(req: SoloSubmitNear):
    rpc_logger.debug(f"solo_submit_near: payload_len={len(req.payload or '')} addr={req.addr} nonce={req.nonce} level={req.proof_level}")
    db = get_db()
    if not req.payload or not req.sig:
        raise HTTPException(status_code=400, detail={"accepted": False, "error": "payload+sig required"})
    tk = _validate_ticket(req.payload, req.sig)

    diag: Dict[str, Any] = {
        "ticket_addr": tk.get("addr"),
        "req_addr": req.addr,
        "nonce": int(req.nonce),
        "proof_level": int(req.proof_level or 1),
    }

    if req.addr != tk.get("addr"):
        raise HTTPException(status_code=400, detail={"accepted": False, "error": "addr mismatch", "diag": diag})

    ns = int(tk["nonce_start"]) & 0xFFFFFFFF
    nw = int(tk["nonce_window"])
    diag["nonce_start"] = ns
    diag["nonce_window"] = nw
    if not (0 <= int(req.nonce) - ns < nw):
        raise HTTPException(status_code=400, detail={"accepted": False, "error": "nonce out of window", "diag": diag})

    near_thr = _near_target_threshold(tk["target"])
    try:
        dval = int(req.digest_hex, 16)
        diag["digest"] = req.digest_hex.lower()
    except Exception:
        raise HTTPException(status_code=400, detail={"accepted": False, "error": "bad digest", "diag": diag})
    if dval > near_thr:
        raise HTTPException(status_code=400, detail={"accepted": False, "error": "not a near-target", "diag": diag})

    # Credit fairness (retry on sqlite busy)
    max_retries = 6
    for attempt in range(max_retries):
        try:
            with db.session() as s:
                tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
                cur_height = 0 if tip is None else tip.height
                start, end = _epoch_for_height(cur_height)

                ep = (
                    s.query(FairnessEpoch)
                    .filter(FairnessEpoch.start_height == start, FairnessEpoch.end_height == end)
                    .first()
                )
                if not ep:
                    ep = FairnessEpoch(
                        start_height=start,
                        end_height=end,
                        pool_ratio=_pool_ratio(),
                        settled=False,
                        created_ms=now_ms(),
                    )
                    s.add(ep)
                    s.flush()

                row = (
                    s.query(FairnessCredit)
                    .filter(FairnessCredit.epoch_id == ep.id, FairnessCredit.miner_addr == req.addr)
                    .first()
                )
                if not row:
                    row = FairnessCredit(epoch_id=ep.id, miner_addr=req.addr, credit_units=0.0, last_ms=0)
                row.credit_units = float(row.credit_units or 0.0) + 1.0 * max(1, int(req.proof_level or 1))
                row.last_ms = now_ms()
                s.merge(row)
                s.commit()
            break
        except Exception as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.05 * (attempt + 1))
                continue
            raise

    try:
        with db.session() as s2:
            row = s2.get(KV, "solo_last_near") or KV(k="solo_last_near", v="")
            row.v = json.dumps({"accepted": True, "diag": diag}, separators=(",", ":"), sort_keys=True)
            s2.merge(row)
            s2.commit()
    except Exception:
        pass

    return {"accepted": True, "credited": 1, "diag": diag}


@app.post("/rpc/solo/submit_block")
def rpc_solo_submit_block(req: SoloSubmitBlock):
    rpc_logger.info(f"solo_submit_block: addr={req.addr} nonce={req.nonce} ver={req.version} ts={req.timestamp} merkle={(req.merkle_root_hex or '')[:16]}")
    if not req.payload or not req.sig:
        detail = {"accepted": False, "error": "payload+sig required"}
        rpc_logger.error("solo_submit_block reject %s", json.dumps(detail, separators=(",", ":"), sort_keys=True))
        raise HTTPException(status_code=400, detail=detail)
    tk = _validate_ticket(req.payload, req.sig)

    diag: Dict[str, Any] = {
        "ticket_addr": tk.get("addr"),
        "req_addr": req.addr,
        "nonce": int(req.nonce),
        "version": int(req.version),
        "timestamp": int(req.timestamp),
    }

    if req.addr != tk.get("addr"):
        diag["mismatch"] = "addr mismatch"
        detail = {"accepted": False, "error": "addr mismatch", "diag": diag}
        rpc_logger.error("solo_submit_block reject %s", json.dumps(detail, separators=(",", ":"), sort_keys=True))
        raise HTTPException(status_code=400, detail=detail)

    ns = int(tk["nonce_start"]) & 0xFFFFFFFF
    nw = int(tk["nonce_window"])
    diag["nonce_start"] = ns
    diag["nonce_window"] = nw
    if not (0 <= int(req.nonce) - ns < nw):
        diag["mismatch"] = "nonce out of window"
        detail = {"accepted": False, "error": "nonce out of window", "diag": diag}
        rpc_logger.error("solo_submit_block reject %s", json.dumps(detail, separators=(",", ":"), sort_keys=True))
        raise HTTPException(status_code=400, detail=detail)

    try:
        obj = json.loads(req.payload)
        prev_hash = str(obj.get("prev") or "").lower()
        target_hex = str(obj.get("target") or "").lower()
        diag["ticket_prev"] = prev_hash
        diag["ticket_target"] = target_hex
    except Exception as e:
        detail = {"accepted": False, "error": f"invalid ticket payload: {e}"}
        rpc_logger.error("solo_submit_block reject %s", json.dumps(detail, separators=(",", ":"), sort_keys=True))
        raise HTTPException(status_code=400, detail=detail)

    db = get_db()
    cfg = get_config()
    MIN_FEE = float(cfg.get("mempool.min_fee", 0.000001))
    TXS_PER_BLOCK = int(cfg.get("consensus.txs_per_block_cap", 200))
    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        next_h = 0 if tip is None else tip.height + 1
        diag["next_height"] = next_h
        try:
            from core.utils import sha3_256_hex as _sha
            coinbase_txid = _sha(f"COINBASE:{next_h}".encode("utf-8")).lower()
        except Exception:
            coinbase_txid = hashlib.sha3_256(f"COINBASE:{next_h}".encode("utf-8")).hexdigest().lower()

        txids: List[str] = [coinbase_txid]
        if next_h >= 200:
            mem = (
                s.query(MempoolTx)
                .filter(MempoolTx.fee >= MIN_FEE)
                .order_by(MempoolTx.fee.desc(), MempoolTx.added_ms.asc())
                .limit(TXS_PER_BLOCK)
                .all()
            )
            for m in mem:
                txid_norm = ((m.txid or "").strip().lower())
                if txid_norm:
                    txids.append(txid_norm)

        diag["rebuilt_coinbase_txid"] = txids[0]
        diag["txids_len"] = len(txids)

    with db.session() as s2:
        tip_now = s2.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        diag["tip_height"] = -1 if tip_now is None else tip_now.height
        diag["tip_hash"] = None if tip_now is None else tip_now.hash_hex
        if tip_now is not None and tip_now.hash_hex != prev_hash:
            diag["tip_prev_mismatch"] = True

    rpc_logger.debug("solo_submit_block promoting prev=%s.. target=%s.. nonce=%s ver=%s ts=%s txs_len=%s",
                     (prev_hash or "")[:16], (target_hex or "")[:16], req.nonce, req.version, req.timestamp, len(txids))

    # Submit authoritative merkle root we just rebuilt so consensus equality check passes at >=200
    rebuilt_merkle = ""
    try:
        from core.consensus import get_txids_for_merkle, calc_merkle_root  # type: ignore
        txids_for_merkle_list = get_txids_for_merkle(next_h, txids)
        rebuilt_merkle = calc_merkle_root(txids_for_merkle_list).lower()
        diag["rebuilt_merkle_sent"] = rebuilt_merkle
    except Exception as _e:
        rebuilt_merkle = ""
        diag["rebuilt_merkle_err"] = str(_e)

    hh, err = accept_external_header(
        prev_hash_hex=prev_hash,
        merkle_root_hex=rebuilt_merkle,
        version=int(req.version),
        timestamp=int(req.timestamp),
        target_hex=str(target_hex),
        nonce=int(req.nonce),
        miner_address=req.addr,
        txids_snapshot=txids,
    )

    if err:
        try:
            with db.session() as s3:
                row = s3.get(KV, "solo_last_reject") or KV(k="solo_last_reject", v="")
                row.v = json.dumps({"error": err, "diag": diag}, separators=(",", ":"), sort_keys=True)
                s3.merge(row)
                s3.commit()
        except Exception:
            pass
        detail = {"accepted": False, "error": err, "diag": diag}
        # Colorized hints
        low = (err or "").lower()
        if "prev" in low:
            rpc_logger.error(_Color.RED + "solo_submit_block: REJECT prev mismatch (stale). Tip moved vs ticket prev." + _Color.RESET)
        elif "merkle" in low:
            rpc_logger.error(_Color.RED + "solo_submit_block: REJECT merkle mismatch (ordering/normalization)." + _Color.RESET)
        elif "pow" in low or "target" in low:
            rpc_logger.error(_Color.RED + "solo_submit_block: REJECT target not met." + _Color.RESET)
        rpc_logger.error("solo_submit_block reject %s", json.dumps(detail, separators=(",", ":"), sort_keys=True))
        raise HTTPException(status_code=400, detail=detail)

    try:
        with db.session() as s4:
            row = s4.get(KV, "solo_last_accept") or KV(k="solo_last_accept", v="")
            row.v = json.dumps({"hash": hh, "diag": diag}, separators=(",", ":"), sort_keys=True)
            s4.merge(row)
            s4.commit()
    except Exception:
        pass

    rpc_logger.info(_Color.GREEN + f"solo_submit_block: ACCEPTED hash={hh[:16]}.. h_tip_next={diag.get('next_height')}" + _Color.RESET)
    return {"accepted": True, "hash": hh, "diag": diag}


@app.get("/rpc/_debug/solo_diag")
def rpc_debug_solo_diag():
    db = get_db()
    out: Dict[str, Any] = {}
    with db.session() as s:
        def g(key: str):
            row = s.get(KV, key)
            if row and row.v:
                try:
                    return json.loads(row.v)
                except Exception:
                    return {"raw": row.v}
            return None

        out["solo_last_near"] = g("solo_last_near")
        out["solo_last_reject"] = g("solo_last_reject")
        out["solo_last_accept"] = g("solo_last_accept")

        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        out["tip"] = None if tip is None else {
            "height": tip.height,
            "hash": tip.hash_hex,
            "target": tip.target,
            "timestamp": tip.timestamp,
        }
    return out


def run_rpc_server():
    import os
    cfg = get_config()
    env_host = os.environ.get("SMELLY_RPC_HOST")
    env_port = os.environ.get("SMELLY_RPC_PORT")
    host = env_host or cfg.get("network.rpc_host", "127.0.0.1")
    port = int(env_port) if env_port else int(cfg.get("network.rpc_port", 28445))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_rpc_server()
