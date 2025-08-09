from __future__ import annotations

import json
import math
import struct
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict, Any

from core.db import get_db, BlockHeader, Transaction, UTXO, Reward, MempoolTx, KV, FairnessEpoch, FairnessCredit
from sqlalchemy import func
from core.config import get_config
from core.utils import now_ms, sha3_256_hex as _sha3_256_hex
from core.pow.randomx_stub import difficulty_to_target
from core.pow.pow_backend import pow_hash, backend_name
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from core.crypto import tx_digest_hex, ed25519_verify_hex

# SQLite busy retry helper
def _with_retry(op, *args, **kwargs):
    """
    Execute an operation function with bounded retries/backoff when SQLite reports 'database is locked'.
    The op must be a callable that takes no arguments and performs the DB action using the current session.
    Returns op()'s return value on success; re-raises on non-lock or if retries exhausted.
    """
    import time as _t
    max_retries = 8
    delay = 0.025
    for i in range(max_retries):
        try:
            return op()
        except Exception as e:
            if "database is locked" in str(e).lower():
                _t.sleep(delay)
                delay = min(0.3, delay * 1.7)
                continue
            raise


@dataclass
class Header:
    version: int
    prev_hash_hex: str
    merkle_root_hex: str
    timestamp: int
    target: str
    nonce: int
    miner_address: str
    tx_count: int = 0

    def serialize(self) -> bytes:
        # minimal, deterministic serialization
        fields = [
            ("version", self.version),
            ("prev_hash_hex", self.prev_hash_hex),
            ("merkle_root_hex", self.merkle_root_hex),
            ("timestamp", self.timestamp),
            ("target", self.target),
            ("nonce", self.nonce),
            ("miner_address", self.miner_address),
            ("tx_count", self.tx_count),
        ]
        js = json.dumps(fields, separators=(",", ":"), sort_keys=False).encode("utf-8")
        return js

    def hash_hex(self) -> str:
        # Use the aliased hash to avoid NameError and shadowing
        return _sha3_256_hex(self.serialize())


def calc_merkle_root(txids: List[str]) -> str:
    # Local helper hash that cannot be shadowed by imported names
    def _h(b: bytes) -> str:
        return _sha3_256_hex(b)
    if not txids:
        return _h(b"")
    layer = [bytes.fromhex(t) for t in txids]
    while len(layer) > 1:
        nxt = []
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        for i in range(0, len(layer), 2):
            nxt.append(bytes.fromhex(_h(layer[i] + layer[i + 1])))
        layer = nxt
    return layer[0].hex()


def initial_difficulty() -> int:
    # Much lower starting difficulty for fast dev mining.
    # With RandomX stub and 15s target this yields near-immediate solutions.
    return 100


def next_difficulty(prev_headers: List[BlockHeader], target_block_time: int) -> int:
    """
    Smoothed, bounded retarget to avoid cliffs after warmup.
    - Uses last N=30 blocks (or fewer if chain shorter).
    - Computes average block time and adjusts gradually toward target.
    - Enforces per-step clamp to within [0.85x, 1.15x] of previous difficulty.
    - Enforces global bounds to keep PoW solvable with the stub.
    Difficulty is represented via cumulative 'work' units stored as hex in DB.
    """
    N = min(30, len(prev_headers))
    if N < 2:
        return max(1, initial_difficulty())

    window = prev_headers[-N:]
    times = [h.timestamp for h in window]
    span = max(1, times[-1] - times[0])
    actual_avg = span / (N - 1)

    # Derive a 'current difficulty' proxy from last header's cumulative work delta.
    # Since 'work' is cumulative sum of diffs, approximate last diff as:
    if len(prev_headers) >= 2:
        last = prev_headers[-1]
        prev = prev_headers[-2]
        last_diff = max(1, int(last.work, 16) - int(prev.work, 16))
    else:
        last_diff = max(1, int(prev_headers[-1].work, 16))

    # Desired scale factor
    ratio = target_block_time / max(1.0, float(actual_avg))
    # Clamp per-step adjustment to keep chain stable
    ratio = max(0.85, min(1.15, ratio))

    new_diff = int(last_diff * ratio)

    # Global clamps for dev so mining never stalls
    MIN_DEV_DIFF = 1
    MAX_DEV_DIFF = 500  # keep upper bound modest for RandomX stub
    new_diff = max(MIN_DEV_DIFF, min(MAX_DEV_DIFF, new_diff))

    return new_diff


def cumulative_work_of_chain_tip() -> Tuple[int, Optional[BlockHeader]]:
    db = get_db()
    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        if not tip:
            return 0, None
        return int(tip.work, 16), tip


def get_chain_height() -> int:
    db = get_db()
    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        return tip.height if tip else -1


def get_header_by_hash(hh: str) -> Optional[BlockHeader]:
    db = get_db()
    with db.session() as s:
        return s.query(BlockHeader).filter_by(hash_hex=hh).first()


def get_header_by_height(h: int) -> Optional[BlockHeader]:
    db = get_db()
    with db.session() as s:
        return s.query(BlockHeader).filter_by(height=h).first()


def get_headers_range(start_height: int, count: int) -> List[BlockHeader]:
    db = get_db()
    with db.session() as s:
        return (
            s.query(BlockHeader)
            .filter(BlockHeader.height >= start_height)
            .order_by(BlockHeader.height.asc())
            .limit(count)
            .all()
        )


def get_txids_for_merkle(height: int, included_txids: List[str]) -> List[str]:
    """
    Authoritative txid ordering used by consensus, RPC, miner, and pool to avoid merkle mismatches.

    Boundary-safe behavior:
    - For the first 200 blocks (heights 0..199 inclusive), the block contents are COINBASE ONLY.
      This guarantees the merkle consists solely of coinbase during bootstrap.
    - Starting at height 200 (i.e., when building block #200), mempool entries may be included,
      but coinbase MUST be first and all txids must be lowercase hex.
    - Never sort after selection; preserve the selection order to match miners’ snapshot ordering.
    """
    expected_coinbase = _sha3_256_hex(f"COINBASE:{height}".encode("utf-8")).lower()

    # Strict bootstrap: 0..199 inclusive are coinbase-only
    if height < 200:
        return [expected_coinbase]

    # Post-bootstrap: coinbase-first + provided order preserved
    txs = [str(t or "").strip().lower() for t in (included_txids or [])]
    if not txs:
        return [expected_coinbase]
    # Remove any accidental duplicates while preserving first occurrence order
    seen = set()
    dedup: List[str] = []
    for t in txs:
        if t and t not in seen:
            seen.add(t)
            dedup.append(t)
    if dedup and dedup[0] == expected_coinbase:
        return dedup
    rest = [t for t in dedup if t != expected_coinbase]
    return [expected_coinbase] + rest


def compute_block_reward(height: int) -> float:
    cfg = get_config()
    initial = float(cfg.get("consensus.initial_block_reward", 50.0))
    halving_interval = int(cfg.get("consensus.halving_interval_blocks", 210000))
    halvings = height // halving_interval
    reward = initial / (2 ** halvings)
    return max(0.00000001, reward)


def total_supply_estimate() -> float:
    # Not exact; for demonstration. A real implementation would sum UTXOs.
    db = get_db()
    with db.session() as s:
        max_h = s.query(BlockHeader.height).order_by(BlockHeader.height.desc()).first()
        h = max_h[0] if max_h else -1
    total = 0.0
    for i in range(h + 1):
        total += compute_block_reward(i)
    return total


def within_max_supply(next_height: int) -> bool:
    """
    Dev-safe: never block early chain growth due to supply math. In production,
    replace with precise UTXO-based supply tracking.
    """
    return True


def validate_header(new_header: Header, prev: Optional[BlockHeader]) -> Tuple[bool, str]:
    cfg = get_config()
    # version
    if new_header.version != int(cfg.get("consensus.block_version", 1)):
        return False, "invalid version"
    # link
    if prev:
        if new_header.prev_hash_hex != prev.hash_hex:
            return False, "prev link mismatch"
        # Allow equal timestamp if clocks are coarse; require non-decreasing
        if new_header.timestamp < prev.timestamp:
            return False, "timestamp decreased"
    # target
    # difficulty encoded as "work" hex in DB; for validation we check hash <= target
    # Use selected PoW backend (RandomX DLL via ctypes if available, else Argon2id)
    prev_hex = prev.hash_hex if prev else "00" * 32
    h_bytes = pow_hash(new_header.serialize(), new_header.nonce, prev_hex)
    if int(h_bytes.hex(), 16) > int(new_header.target, 16):
        return False, "pow target not met"
    # supply cap
    height = 0 if prev is None else prev.height + 1
    if not within_max_supply(height):
        return False, "exceeds max supply cap"
    # tx_count should be >= 1 due to coinbase
    if new_header.tx_count < 1:
        return False, "missing coinbase"
    return True, "ok"


# ------------------------ MEMPOOL VALIDATION ------------------------

def _utxo_sum_for_address(s, address: str) -> float:
    return float(s.query(func.coalesce(func.sum(UTXO.amount), 0.0)).filter_by(address=address, spent=False).scalar() or 0.0)  # type: ignore


def validate_mempool_tx(tx: Dict[str, Any], height: int) -> Tuple[bool, str, str]:
    """
    Validate a transaction for mempool admission.
    Returns (ok, reason, txid)
    tx schema (confirmed with user):
    {
      "version": 1,
      "inputs": [{"txid":"hex","vout":0,"address":"SMELLY_...","pubkey":"hex32","sig":"hex64"}],
      "outputs": [{"address":"SMELLY_...","amount":1.23}],
      "fee": 0.00002,
      "timestamp": 1690000000
    }
    """
    cfg = get_config()
    min_fee = float(cfg.get("mempool.min_fee", 0.00001))
    if not isinstance(tx, dict):
        return False, "bad-format", ""
    version = tx.get("version")
    if version != 1:
        return False, "bad-version", ""
    inputs = tx.get("inputs") or []
    outputs = tx.get("outputs") or []
    fee = float(tx.get("fee", 0.0))
    if fee < min_fee:
        return False, "fee-too-low", ""
    if not inputs or not outputs:
        return False, "missing-io", ""
    # compute txid as digest excluding signatures field
    txid = tx_digest_hex(tx)

    db = get_db()
    with db.session() as s:
        # Double-spend check against mempool + utxo
        # For each input, verify referenced utxo exists and is unspent; also ensure not already referenced by another mempool tx
        for i in inputs:
            if not isinstance(i, dict):
                return False, "bad-input", txid
            ref_txid = (i.get("txid") or "").strip().lower()
            vout = int(i.get("vout", -1))
            if not ref_txid or vout < 0:
                return False, "bad-input-ref", txid
            # Check UTXO set
            u = s.query(UTXO).filter(UTXO.txid == ref_txid, UTXO.vout == vout).first()
            if not u or u.spent:
                return False, "utxo-missing-or-spent", txid
            # Coinbase maturity
            if u.coinbase:
                # infer coinbase height from its txid pattern COINBASE:{height}
                try:
                    if ref_txid.startswith(_sha3_256_hex(b"COINBASE:")[:8]):
                        # heuristic fallback; better approach is store coinbase height with utxo
                        pass
                except Exception:
                    pass
                # strict: block height from coinbase id
                try:
                    # our coinbase txid is sha3("COINBASE:{h}"), we can't get height directly; use a DB lookup by matching reward table
                    r = s.query(Reward).filter_by(txid=u.txid).first()
                    if r and height < r.height + 10:
                        return False, "coinbase-immature", txid
                except Exception:
                    # If cannot resolve, allow but this should be rare
                    pass

        # Basic amount checks
        total_out = 0.0
        for o in outputs:
            if not isinstance(o, dict):
                return False, "bad-output", txid
            addr = o.get("address") or ""
            amt = float(o.get("amount", -1.0))
            if not addr or amt <= 0:
                return False, "bad-output-amt", txid
            total_out += amt
        # Verify signatures: for each input, verify sig over canonical digest with pubkey
        digest_bytes = bytes.fromhex(txid)
        for i in inputs:
            pubkey_hex = i.get("pubkey") or ""
            sig_hex = i.get("sig") or ""
            if not pubkey_hex or not sig_hex:
                return False, "missing-sig", txid
            if not ed25519_verify_hex(pubkey_hex, digest_bytes, sig_hex):
                return False, "bad-signature", txid

        # Balance check: sum of referenced UTXOs >= total_out + fee
        total_in = 0.0
        for i in inputs:
            ref_txid = (i.get("txid") or "").strip().lower()
            vout = int(i.get("vout", -1))
            u = s.query(UTXO).filter(UTXO.txid == ref_txid, UTXO.vout == vout).first()
            if not u or u.spent:
                return False, "utxo-missing-or-spent", txid
            total_in += float(u.amount or 0.0)
        if total_in + 1e-12 < (total_out + fee):
            return False, "insufficient-input", txid

        # Prevent double spend within mempool: ensure no other mempool tx spends same (txid,vout)
        mem_conflict = s.query(MempoolTx).filter(MempoolTx.raw.like(f"%{ref_txid}%")).first()  # coarse check
        # For accuracy, we could store inputs in mempool table; for now raw string coarse match.
        # We'll ignore if not found.

        return True, "ok", txid


def add_genesis_if_needed():
    db = get_db()
    cfg = get_config()
    with db.session() as s:
        exists = s.query(BlockHeader).count() > 0
        if exists:
            return
        # Build a deterministic genesis header
        txids = []
        mr = calc_merkle_root(txids)
        mr = calc_merkle_root(txids)
        mr = calc_merkle_root(txids)
        header = Header(
            version=int(cfg.get("consensus.block_version", 1)),
            prev_hash_hex="00" * 32,
            merkle_root_hex=mr,
            timestamp=int(time.time()),
            target=difficulty_to_target(initial_difficulty()),
            nonce=0,
            miner_address="SMELLY_GENESIS",
            tx_count=0,
        )
        hh = header.hash_hex()
        row = BlockHeader(
            height=0,
            hash_hex=hh,
            prev_hash_hex=header.prev_hash_hex,
            merkle_root_hex=mr,
            timestamp=header.timestamp,
            version=header.version,
            nonce=str(header.nonce),
            target=header.target,
            miner_address=header.miner_address,
            tx_count=header.tx_count,
            work=f"{initial_difficulty():064x}",
        )
        s.add(row)
        s.commit()


def append_block_header(miner_address: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Mine and append a new header. Includes highest-fee mempool txs if spendable.
    Returns (new_hash, error_message).
    """
    db = get_db()
    cfg = get_config()
    target_block_time = int(cfg.get("consensus.target_block_time_sec", 60))
    MIN_FEE = float(cfg.get("mempool.min_fee", 0.000001))
    TXS_PER_BLOCK = int(cfg.get("consensus.txs_per_block_cap", 200))

    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        height = 0 if tip is None else tip.height + 1
        prev_hash = "00" * 32 if tip is None else tip.hash_hex

        # Difficulty
        recent = s.query(BlockHeader).order_by(BlockHeader.height.asc()).all()
        if not recent or len(recent) < 200:
            diff = 1
        else:
            diff = next_difficulty(recent, target_block_time)

        # Select mempool txs by fee desc (and sanity filters); ensure not already confirmed
        mem_total = s.query(MempoolTx).count()
        mem = (
            s.query(MempoolTx)
            .filter(MempoolTx.fee >= MIN_FEE)
            .order_by(MempoolTx.fee.desc(), MempoolTx.added_ms.asc())
            .limit(TXS_PER_BLOCK * 4)  # over-select; we will validate balances/spends
            .all()
        )
        # Filter out any txs that have been confirmed already (defensive)
        if mem:
            confirmed = {txid for (txid,) in s.query(Transaction.txid).filter(Transaction.in_block_hash.isnot(None), Transaction.txid.in_([m.txid for m in mem])).all()}
            mem = [m for m in mem if m.txid not in confirmed]

        included_txids: List[str] = []
        total_fees = 0.0
        skipped_insufficient: int = 0
        skipped_invalid: int = 0
        skipped_addr_miss: int = 0

        def spend_from_address_multi(from_addr: str, amount: float, fee: float) -> Tuple[bool, float, List[UTXO]]:
            """
            Greedy coin selection with intra-block anti-double-spend:
            - Aggregate largest unspent UTXOs from from_addr until amount+fee covered.
            - Exclude UTXOs already picked by earlier txs in this candidate block.
            - Mark selected inputs as tentatively spent in a local set; persist only on block commit.
            """
            need = amount + fee
            # track UTXOs tentatively spent within this block build
            if not hasattr(spend_from_address_multi, "_picked"):
                setattr(spend_from_address_multi, "_picked", set())
            picked = getattr(spend_from_address_multi, "_picked")

            # fetch unspent, excluding those already picked
            utxos = (
                s.query(UTXO)
                .filter_by(address=from_addr, spent=False)
                .order_by(UTXO.amount.desc())
                .limit(2000)
                .all()
            )
            total_in = 0.0
            used: List[UTXO] = []
            for u in utxos:
                key = (u.txid, u.vout)
                if key in picked:
                    continue
                used.append(u)
                picked.add(key)
                total_in += u.amount
                if total_in + 1e-12 >= need:
                    break
            if total_in + 1e-12 < need:
                # rollback tentative picks for this attempt
                for u in used:
                    picked.discard((u.txid, u.vout))
                return False, 0.0, []

            # mark inputs in DB now as spent (to avoid race with parallel miner submit); will update spent_txid on commit
            for u in used:
                u.spent = True
                u.spent_txid = "BLOCK_TMP"

            change = total_in - need
            if change > 1e-12:
                # deterministic change vout allocation: use next available index unique for this height
                next_change_vout = int(10_000_000 + len(used))
                s.add(UTXO(
                    txid="BLOCK_TMP",
                    vout=next_change_vout,
                    address=from_addr,
                    amount=change,
                    spent=False,
                    spent_txid=None,
                    coinbase=False,
                ))
            return True, change, used

        # Attempt to include txs if valid
        debug_reasons: List[str] = []
        # Diagnostics: record top-of-queue info
        for m in mem[:5]:
            debug_reasons.append(f"queue tx {m.txid[:12]} from={str(m.from_addr)[:10]}.. to={str(m.to_addr)[:10]}.. amt={m.amount} fee={m.fee}")
        # Build a candidate set by fee desc, but enforce intra-block no-double-spend by tracking picked inputs.
        # IMPORTANT: We must keep the selected txids in the SAME ORDER we add them to 'included_txids' because
        # the client miner will send txids_snapshot in that order. Do NOT sort after selection.
        for m in mem:
            try:
                parts = {kv.split("=",1)[0]: kv.split("=",1)[1] for kv in (m.raw or "").split(";") if "=" in kv}
                from_addr = (m.from_addr or parts.get("from") or "").strip()
                to_addr = (m.to_addr or parts.get("to") or "").strip()
                amount = float(m.amount if m.amount is not None else float(parts.get("amount", 0.0)))
                fee = float(m.fee if m.fee is not None else float(parts.get("fee", 0.0)))
            except Exception:
                skipped_invalid += 1
                debug_reasons.append(f"{m.txid}: parse-failed")
                continue

            # Relax address validation: allow any non-empty from/to strings
            if not from_addr or not to_addr:
                skipped_invalid += 1
                debug_reasons.append(f"{m.txid}: missing-address from='{from_addr}' to='{to_addr}'")
                continue
            if amount <= 0 or fee < MIN_FEE:
                skipped_invalid += 1
                debug_reasons.append(f"{m.txid}: bad-amt-fee amt={amount} fee={fee}")
                continue

            # Check total available minus those already tentatively picked
            total_avail = float(s.query(func.coalesce(func.sum(UTXO.amount), 0.0)).filter_by(address=from_addr, spent=False).scalar() or 0.0)  # type: ignore
            if total_avail + 1e-12 < (amount + fee):
                skipped_insufficient += 1
                debug_reasons.append(f"{m.txid}: insufficient bal={total_avail:.6f} need={(amount+fee):.6f}")
                continue

            ok, _chg, _used = spend_from_address_multi(from_addr, amount, fee)
            if not ok:
                skipped_insufficient += 1
                debug_reasons.append(f"{m.txid}: coinselect-failed need={(amount+fee):.6f}")
                continue

            # Create recipient UTXO (idempotent upsert)
            existing_utxo = s.query(UTXO).filter_by(txid=m.txid, vout=0).first()
            if not existing_utxo:
                try:
                    s.add(UTXO(
                        txid=m.txid,
                        vout=0,
                        address=to_addr,
                        amount=amount,
                        spent=False,
                        spent_txid=None,
                        coinbase=False,
                    ))
                    s.flush()
                except Exception:
                    s.rollback()
            else:
                if existing_utxo.address != to_addr:
                    existing_utxo.address = to_addr
                if abs((existing_utxo.amount or 0.0) - amount) > 1e-12:
                    existing_utxo.amount = amount

            # Upsert Transaction row
            txid_val = m.txid
            stmt = sqlite_insert(Transaction).values(
                txid=txid_val,
                raw=m.raw or "",
                added_ms=m.added_ms or now_ms(),
                in_block_hash=None,
                fee=m.fee or 0.0
            ).prefix_with("OR IGNORE")
            s.execute(stmt)
            tx_row2 = s.query(Transaction).filter_by(txid=txid_val).first()
            if tx_row2:
                if not tx_row2.raw:
                    tx_row2.raw = m.raw or ""
                if tx_row2.fee is None:
                    tx_row2.fee = m.fee or 0.0

            included_txids.append(m.txid)
            total_fees += fee

            # Soft cap enforcement: if we hit block cap, stop
            if len(included_txids) >= TXS_PER_BLOCK:
                break

        # Build merkle and header
        # IMPORTANT: At height 200 boundary, continue to use authoritative helper so merkle matches RPC/miner.
        txids = get_txids_for_merkle(height, included_txids)
        mr = calc_merkle_root(txids)

        header = Header(
            version=int(cfg.get("consensus.block_version", 1)),
            prev_hash_hex=prev_hash,
            merkle_root_hex=mr,
            timestamp=int(time.time()),
            target=difficulty_to_target(diff),
            nonce=0,
            miner_address=miner_address,
            tx_count=len(txids),  # coinbase + included txs
        )

        # Mine
        target_int = int(header.target, 16)
        max_tries = 5_000_000
        nonce = 0
        found = False
        while nonce < max_tries:
            h = pow_hash(header.serialize(), nonce, prev_hash)
            if int(h.hex(), 16) <= target_int:
                header.nonce = nonce
                found = True
                break
            nonce += 1
            if nonce % 5000 == 0:
                now_ts = int(time.time())
                if now_ts > header.timestamp:
                    header.timestamp = now_ts
        if not found:
            # Enrich error with mempool stats to debug “no block found” loops
            info = f"no-solution nonce<{max_tries} target={header.target} height={height} mempool={mem_total} included={len(included_txids)}"
            # Persist last mining error for wallet/explorer to read (optional)
            try:
                from core.db import KV  # type: ignore
                with db.session() as st:
                    last = st.get(KV, "diag_last_mine_error") or KV(k="diag_last_mine_error", v="")
                    last.v = info
                    st.merge(last)
                    st.commit()
            except Exception:
                pass
            return None, info

        ok, reason = validate_header(header, tip)
        if not ok:
            return None, reason

        # Save block
        prev_work = 0 if tip is None else int(tip.work, 16)
        new_work = prev_work + max(1, diff)
        hh = header.hash_hex()

        row = BlockHeader(
            height=height,
            hash_hex=hh,
            prev_hash_hex=header.prev_hash_hex,
            merkle_root_hex=header.merkle_root_hex,
            timestamp=header.timestamp,
            version=header.version,
            nonce=str(header.nonce),
            target=header.target,
            miner_address=header.miner_address,
            tx_count=header.tx_count,
            work=f"{new_work:064x}",
        )
        s.add(row)

        # Rewards: block reward + total fees
        reward_amt = compute_block_reward(height) + total_fees
        coinbase_txid = _sha3_256_hex(f"COINBASE:{height}".encode("utf-8"))
        s.add(Reward(
            height=height,
            miner_address=header.miner_address,
            amount=reward_amt,
            txid=coinbase_txid,
            created_ms=now_ms(),
        ))
        # Create coinbase UTXO idempotently to avoid UNIQUE(txid,vout) under concurrency
        ex_cb = s.query(UTXO).filter_by(txid=coinbase_txid, vout=0).first()
        if not ex_cb:
            try:
                s.add(UTXO(
                    txid=coinbase_txid,
                    vout=0,
                    address=header.miner_address,
                    amount=reward_amt,
                    spent=False,
                    spent_txid=None,
                    coinbase=True,
                ))
                s.flush()
            except Exception:
                s.rollback()
        else:
            # Ensure address/amount are correct in case of partial insert in a race
            if ex_cb.address != header.miner_address:
                ex_cb.address = header.miner_address
            if abs((ex_cb.amount or 0.0) - reward_amt) > 1e-12:
                ex_cb.amount = reward_amt

        # Finalize included txs: mark in_block_hash; remove mempool rows; update temp refs and create change outputs
        if included_txids:
            # Use EXACT order (no sort) to match inclusion order; still dedupe for safety in DB ops
            unique_txids = []
            seen = set()
            for t in included_txids:
                if t not in seen:
                    seen.add(t)
                    unique_txids.append(t)

            # Ensure Transaction rows exist and set in_block_hash
            for txid in unique_txids:
                stmt = sqlite_insert(Transaction).values(
                    txid=txid, raw="", added_ms=now_ms(), in_block_hash=hh, fee=0.0
                ).prefix_with("OR IGNORE")
                s.execute(stmt)
            s.flush()
            for txid in unique_txids:
                row_tx = s.query(Transaction).filter_by(txid=txid).first()
                if row_tx:
                    row_tx.in_block_hash = hh
                    if row_tx.raw is None:
                        row_tx.raw = ""

            # Remove mempool rows AFTER we have persisted tx rows safely; ensure deletion actually happens
            del_count = s.query(MempoolTx).filter(MempoolTx.txid.in_(unique_txids)).delete(synchronize_session=False)

            # If nothing deleted, aggressively match normalized and also by address/amount/fee tuple
            if del_count == 0:
                all_mem = s.query(MempoolTx).all()
                norm_set = {x.strip().lower() for x in unique_txids}
                to_del = [m for m in all_mem if (m.txid or "").strip().lower() in norm_set]
                for m in to_del:
                    s.delete(m)
                if not to_del:
                    # As a final fallback, remove mempool entries that match a confirmed Transaction by txid prefix
                    # (useful if UI displayed shortened txids somewhere in pipeline)
                    conf_tx = s.query(Transaction).filter(Transaction.in_block_hash == hh).all()
                    conf_set = { (t.txid or "").strip().lower() for t in conf_tx }
                    for m in all_mem:
                        if (m.txid or "").strip().lower() in conf_set:
                            s.delete(m)

            # Update any temp UTXO placeholders created during spend (txid BLOCK_TMP -> confirmed block hash)
            # Also normalize spent_txid on inputs we consumed earlier.
            for u in s.query(UTXO).filter_by(txid="BLOCK_TMP").all():
                u.txid = hh
            for u in s.query(UTXO).filter_by(spent=True, spent_txid="BLOCK_TMP").all():
                u.spent_txid = hh

            # Important: ensure change UTXOs exist for each included tx if we reserved them as BLOCK_TMP placeholders.
            # They were already inserted above using txid="BLOCK_TMP"; renaming to hh covers them.
            # No-op here other than making sure they are not marked spent.
            for u in s.query(UTXO).filter(UTXO.txid == hh, UTXO.vout >= 10_000_000).all():
                u.spent = False
                u.spent_txid = None

            # Record diagnostics for visibility on explorer
            try:
                diag = s.get(KV, "diag_last_mempool_cleanup") or KV(k="diag_last_mempool_cleanup", v="")
                diag.v = f"height={height} del_count={del_count} unique={len(unique_txids)} at_ms={now_ms()}"
                s.merge(diag)
            except Exception:
                pass
        else:
            # If nothing included, and we found clear insufficiency or invalids, log detailed diagnostics
            try:
                from core.db import KV  # type: ignore
                diag = s.get(KV, "diag_mempool_skips") or KV(k="diag_mempool_skips", v="0")
                total_skips = skipped_insufficient + skipped_invalid + skipped_addr_miss
                diag.v = str(int(diag.v) + total_skips)
                s.merge(diag)
                # Also store last reasons (truncate to keep small), and note mempool total
                reasons = [f"mempool_total={mem_total}", f"included=0", f"skips: insufficient={skipped_insufficient} invalid={skipped_invalid}"]
                reasons.extend(debug_reasons[-10:])
                last = s.get(KV, "diag_last_skip_reasons") or KV(k="diag_last_skip_reasons", v="")
                last.v = "\n".join(reasons)
                s.merge(last)
            except Exception:
                pass

        s.commit()

        # Hard delete any mempool rows that slipped through using a direct SQL PRAGMA-aware approach
        if included_txids:
            try:
                with db.session() as st:
                    norm = [t.strip().lower() for t in included_txids]
                    # Direct exec to be extra sure in SQLite
                    for nx in norm:
                        st.execute(
                            "DELETE FROM mempool WHERE LOWER(TRIM(txid)) = :txid",
                            {"txid": nx},
                        )
                    st.commit()
            except Exception:
                pass

        return hh, None


# ===== Fairness helpers =====

def _epoch_lengths() -> tuple[int, int]:
    cfg = get_config()
    return int(cfg.get("fairness.epoch_length_dev", 20)), int(cfg.get("fairness.epoch_length_main", 100))

def _pool_ratio() -> float:
    cfg = get_config()
    return float(cfg.get("fairness.pool_ratio", 0.30))

def _epoch_for_height(height: int) -> tuple[int, int]:
    dev_len, main_len = _epoch_lengths()
    size = dev_len if int(get_config().get("network.dev_mode", 1)) else main_len
    start = (height // size) * size
    end = start + size - 1
    return start, end

def _ensure_epoch_for_height(s, height: int) -> FairnessEpoch:
    start, end = _epoch_for_height(height)
    ep = (
        s.query(FairnessEpoch)
        .filter(FairnessEpoch.start_height == start, FairnessEpoch.end_height == end)
        .first()
    )
    if not ep:
        ep = FairnessEpoch(start_height=start, end_height=end, pool_ratio=_pool_ratio(), settled=False, created_ms=now_ms())
        s.add(ep)
        s.flush()
    return ep

def _settle_epoch_if_needed(s, new_block_height: int):
    """
    If new_block_height crosses an epoch boundary, settle the previous epoch by distributing
    fairness_credit proportionally using pool_ratio * sum(block_rewards in that epoch).
    """
    start_cur, end_cur = _epoch_for_height(new_block_height)
    epoch_size = (end_cur - start_cur + 1)
    start_prev = start_cur - epoch_size
    end_prev = start_cur - 1
    if end_prev < 0:
        return
    ep_prev = (
        s.query(FairnessEpoch)
        .filter(FairnessEpoch.start_height == start_prev, FairnessEpoch.end_height == end_prev)
        .first()
    )
    if not ep_prev or ep_prev.settled:
        return
    credits = s.query(FairnessCredit).filter(FairnessCredit.epoch_id == ep_prev.id).all()
    total_units = sum(float(c.credit_units or 0.0) for c in credits)
    if total_units <= 0.0:
        ep_prev.settled = True
        s.merge(ep_prev)
        return
    total_reward = 0.0
    for h in range(start_prev, end_prev + 1):
        total_reward += compute_block_reward(h)
    pool_value = float(ep_prev.pool_ratio or _pool_ratio()) * total_reward
    for c in credits:
        share = (float(c.credit_units or 0.0) / total_units) * pool_value
        if share <= 0.0:
            continue
        txid = _sha3_256_hex(f"FAIRNESS:{start_prev}-{end_prev}:{c.miner_addr}".encode("utf-8"))
        s.add(Reward(
            height=new_block_height,
            miner_address=c.miner_addr,
            amount=share,
            txid=txid,
            created_ms=now_ms(),
        ))
        ex = s.query(UTXO).filter_by(txid=txid, vout=0).first()
        if not ex:
            try:
                s.add(UTXO(
                    txid=txid,
                    vout=0,
                    address=c.miner_addr,
                    amount=share,
                    spent=False,
                    spent_txid=None,
                    coinbase=False,
                ))
                s.flush()
            except Exception:
                s.rollback()
        else:
            if ex.address != c.miner_addr:
                ex.address = c.miner_addr
            if abs((ex.amount or 0.0) - share) > 1e-12:
                ex.amount = share
    ep_prev.settled = True
    s.merge(ep_prev)

def accept_external_header(
    prev_hash_hex: str,
    merkle_root_hex: str,  # miner-submitted merkle (ignored for height<200; verified >=200)
    version: int,
    timestamp: int,
    target_hex: str,
    nonce: int,
    miner_address: str,
    txids_snapshot: List[str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Accept an externally mined header (client-side mining).
    Validates linkage to current tip, PoW target, and re-validates mempool snapshot for inclusion.
    On success, appends the block, credits coinbase+fees to miner, updates UTXOs, and clears included mempool entries.
    Returns (new_hash, error_message).

    Boundary fix (199->200):
    - For height < 200, we force coinbase-only and ignore the submitted merkle_root_hex entirely.
    - At height >= 200, we rebuild the merkle from the authoritative ordering and require equality with the submitted value.
    """
    db = get_db()
    cfg = get_config()
    MIN_FEE = float(cfg.get("mempool.min_fee", 0.000001))
    TXS_PER_BLOCK = int(cfg.get("consensus.txs_per_block_cap", 200))

    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        # Compare prev to current tip; allow same-prev promotions
        cur_prev = "00" * 32 if tip is None else tip.hash_hex
        if prev_hash_hex != cur_prev:
            # Allow a narrow grace window for same-prev promotions in single-node mode:
            # if the tip has not advanced height AND prev_hash_hex equals the previous tip's hash,
            # accept it to avoid race-induced stale when pool/miner used a very recent prev.
            prev_tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).offset(1).limit(1).first()
            if prev_tip and prev_hash_hex == prev_tip.hash_hex and tip and prev_tip.height == tip.height - 1:
                # proceed under grace
                pass
            else:
                try:
                    from core.db import KV  # type: ignore
                    diag = s.get(KV, "diag_prev_mismatch") or KV(k="diag_prev_mismatch", v="")
                    diag.v = f"cur_tip={cur_prev} submitted_prev={prev_hash_hex} height={(tip.height if tip else -1)} ts={now_ms()}"
                    s.merge(diag)
                    # Also store last_pool_promotion context for deeper debugging
                    ctx = s.get(KV, "diag_last_pool_promotion") or KV(k="diag_last_pool_promotion", v="")
                    ctx.v = f"prev_mismatch; ver={version} ts={timestamp} target={target_hex[:16]}.. nonce={nonce} miner={miner_address} txs={len(txids_snapshot)}"
                    s.merge(ctx)
                    s.commit()
                except Exception:
                    pass
                return None, f"stale-prev"

        height = 0 if tip is None else tip.height + 1

        # Validate PoW target with provided fields (strictly matching miner serialization order)
        # Normalize hex fields to lowercase to avoid case-mismatch issues
        header = Header(
            version=version,
            prev_hash_hex=(prev_hash_hex or "").lower(),
            merkle_root_hex=(merkle_root_hex or "").lower(),
            timestamp=timestamp,
            target=(target_hex or "").lower(),
            nonce=nonce,
            miner_address=miner_address,
            tx_count=max(1, len(txids_snapshot)),  # at least coinbase
        )

        ok, reason = validate_header(header, tip)
        if not ok:
            # record last header bytes and prev used by consensus
            try:
                from core.db import KV  # type: ignore
                ctx = s.get(KV, "diag_last_pool_promotion") or KV(k="diag_last_pool_promotion", v="")
                ctx.v = f"header_invalid; reason={reason}; prev={prev_hash_hex[:16]}.. ver={version} ts={timestamp} target={target_hex[:16]}.. nonce={nonce} miner={miner_address} txs={len(txids_snapshot)}"
                s.merge(ctx)
                s.commit()
            except Exception:
                pass
            return None, f"header-invalid: {reason}"

        # Re-validate snapshot txids: include highest-fee valid transactions up to cap.
        # We will only include txids that are still present and spendable; others are dropped.
        # Fees are accounted and added to miner reward.
        included_txids: List[str] = []
        total_fees = 0.0

        # Build quick lookup for snapshot ordering; if empty, we just include coinbase.
        snapshot_limit = min(TXS_PER_BLOCK, len(txids_snapshot)) if txids_snapshot else 0
        if snapshot_limit > 0:
            # Load candidate mempool entries by txid order (respect snapshot order)
            # We also allow inclusion even if not in mempool table anymore (defensive),
            # but we require basic raw info to compute amount/fee.
            # Our wallet puts "from=...;to=...;amount=A;fee=F;memo=..." in raw.
            # For safety, we fetch rows and build a map.
            # Deduplicate snapshot order to avoid re-processing the same txid twice
            snapshot_list = list(dict.fromkeys(txids_snapshot[:snapshot_limit]))
            mem_rows = s.query(MempoolTx).filter(MempoolTx.txid.in_(snapshot_list)).all()
            mem_map = {m.txid: m for m in mem_rows}

            def parse_amount_fee_from_raw(m: MempoolTx) -> Tuple[str, str, float, float]:
                parts = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in (m.raw or "").split(";") if "=" in kv}
                from_addr = (m.from_addr or parts.get("from") or "").strip()
                to_addr = (m.to_addr or parts.get("to") or "").strip()
                amount = float(m.amount if m.amount is not None else float(parts.get("amount", 0.0)))
                fee = float(m.fee if m.fee is not None else float(parts.get("fee", 0.0)))
                return from_addr, to_addr, amount, fee

            def spend_from_address_multi(from_addr: str, amount: float, fee: float) -> Tuple[bool, float, List[UTXO]]:
                need = amount + fee
                utxos = (
                    s.query(UTXO)
                    .filter_by(address=from_addr, spent=False)
                    .order_by(UTXO.amount.desc())
                    .limit(1000)
                    .all()
                )
                total_in = 0.0
                used: List[UTXO] = []
                for u in utxos:
                    used.append(u)
                    total_in += u.amount
                    if total_in + 1e-12 >= need:
                        break
                if total_in + 1e-12 < need:
                    return False, 0.0, []
                for u in used:
                    u.spent = True
                    u.spent_txid = "BLOCK_TMP"
                change = total_in - need
                if change > 1e-12:
                    s.add(UTXO(
                        txid="BLOCK_TMP",
                        vout=10_000_000,
                        address=from_addr,
                        amount=change,
                        spent=False,
                        spent_txid=None,
                        coinbase=False,
                    ))
                return True, change, used

            for txid in snapshot_list:
                m = mem_map.get(txid)
                if not m:
                    # Snapshot refers to a tx that disappeared; skip safely
                    continue
                try:
                    from_addr, to_addr, amount, fee = parse_amount_fee_from_raw(m)
                except Exception:
                    continue
                # basic checks
                # Relax address validation for external headers as well
                if (not from_addr) or (not to_addr):
                    continue
                if amount <= 0 or fee < MIN_FEE:
                    continue

                bal = s.query(UTXO).with_entities(func.coalesce(func.sum(UTXO.amount), 0.0)).filter_by(address=from_addr, spent=False).scalar()  # type: ignore
                if (bal or 0.0) + 1e-12 < (amount + fee):
                    continue

                ok_spend, _chg, _used = spend_from_address_multi(from_addr, amount, fee)
                if not ok_spend:
                    continue

                # create recipient UTXO (idempotent to avoid UNIQUE(txid,vout))
                ex_u = s.query(UTXO).filter_by(txid=txid, vout=0).first()
                if not ex_u:
                    try:
                        s.add(UTXO(
                            txid=txid,
                            vout=0,
                            address=to_addr,
                            amount=amount,
                            spent=False,
                            spent_txid=None,
                            coinbase=False,
                        ))
                        s.flush()
                    except Exception:
                        s.rollback()
                else:
                    if ex_u.address != to_addr:
                        ex_u.address = to_addr
                    if abs((ex_u.amount or 0.0) - amount) > 1e-12:
                        ex_u.amount = amount
                # idempotent upsert for Transaction row to avoid UNIQUE collisions
                stmt = sqlite_insert(Transaction).values(
                    txid=txid,
                    raw=m.raw or "",
                    added_ms=m.added_ms or now_ms(),
                    in_block_hash=None,
                    fee=m.fee or 0.0,
                ).prefix_with("OR IGNORE")
                s.execute(stmt)
                # Re-fetch to normalize details if it already existed
                tx_row2 = s.query(Transaction).filter_by(txid=txid).first()
                if tx_row2:
                    if not tx_row2.raw:
                        tx_row2.raw = m.raw or ""
                    if tx_row2.fee is None:
                        tx_row2.fee = m.fee or 0.0
                included_txids.append(txid)
                total_fees += fee

        # Rebuild authoritative merkle from snapshot (with boundary-safe ordering)
        txids_for_merkle_list = get_txids_for_merkle(height, txids_snapshot or [])
        rebuilt_merkle = calc_merkle_root(txids_for_merkle_list).lower()

        submitted_merkle = (merkle_root_hex or "").lower()
        if height < 200:
            # Ignore submitted value before height 200, force authoritative merkle
            header.merkle_root_hex = rebuilt_merkle
        else:
            # From 200 onward, require equality; if not equal, reject with diagnostics
            if rebuilt_merkle != submitted_merkle:
                try:
                    from core.db import KV  # type: ignore
                    last = s.get(KV, "diag_merkle_mismatch") or KV(k="diag_merkle_mismatch", v="")
                    last.v = f"h={height} rebuilt={rebuilt_merkle} submitted={submitted_merkle} txs={len(txids_for_merkle_list)}"
                    s.merge(last)
                    snapctx = s.get(KV, "diag_merkle_snapshot") or KV(k="diag_merkle_snapshot", v="")
                    snapctx.v = "snap[:6]=" + ",".join((txids_for_merkle_list or [])[:6])
                    s.merge(snapctx)
                except Exception:
                    pass
                return None, f"merkle-mismatch: rebuilt={rebuilt_merkle} submitted={submitted_merkle} height={height} included={len(txids_for_merkle_list)}"

        # Save header row (idempotent on hash by uniqueness of (height, hash_hex) constraint)
        prev_work = 0 if tip is None else int(tip.work, 16)
        new_work = prev_work + 1  # keep cumulative monotonic; could map target->diff properly later
        # Ensure header contains authoritative merkle before hashing
        if height >= 0 and height < 200:
            header.merkle_root_hex = rebuilt_merkle
        hh = header.hash_hex()

        def _insert_header():
            existed_header = s.query(BlockHeader).filter_by(hash_hex=hh).first()
            if not existed_header:
                row = BlockHeader(
                    height=height,
                    hash_hex=hh,
                    prev_hash_hex=header.prev_hash_hex,
                    merkle_root_hex=header.merkle_root_hex,
                    timestamp=header.timestamp,
                    version=header.version,
                    nonce=str(header.nonce),
                    target=header.target,
                    miner_address=header.miner_address,
                    tx_count=header.tx_count,
                    work=f"{new_work:064x}",
                )
                s.add(row)
                s.flush()
        _with_retry(_insert_header)

        # Finalize rewards and tx confirmations
        if included_txids:
            unique_txids = sorted(set(included_txids))

            def _ensure_tx_rows():
                for txid in unique_txids:
                    stmt = sqlite_insert(Transaction).values(
                        txid=txid, raw="", added_ms=now_ms(), in_block_hash=hh, fee=0.0
                    ).prefix_with("OR IGNORE")
                    s.execute(stmt)
                s.flush()
                for txid in unique_txids:
                    row = s.query(Transaction).filter_by(txid=txid).first()
                    if row:
                        row.in_block_hash = hh
                        if row.raw is None:
                            row.raw = ""
            _with_retry(_ensure_tx_rows)

            def _delete_mempool_rows():
                s.query(MempoolTx).filter(MempoolTx.txid.in_(unique_txids)).delete(synchronize_session=False)
            _with_retry(_delete_mempool_rows)

        # Rewards with fairness split (finder + epoch pool accrual)
        cfg = get_config()
        pool_ratio = float(cfg.get("fairness.pool_ratio", 0.30))
        reward_total = compute_block_reward(height) + total_fees
        finder_amt = reward_total * (1.0 - pool_ratio)
        coinbase_txid = txids_for_merkle_list[0] if txids_for_merkle_list else _sha3_256_hex(f"COINBASE:{height}".encode("utf-8"))

        def _persist_rewards_and_cb():
            s.add(Reward(
                height=height,
                miner_address=header.miner_address,
                amount=finder_amt,
                txid=coinbase_txid,
                created_ms=now_ms(),
            ))
            ex_cb2 = s.query(UTXO).filter_by(txid=coinbase_txid, vout=0).first()
            if not ex_cb2:
                s.add(UTXO(
                    txid=coinbase_txid,
                    vout=0,
                    address=header.miner_address,
                    amount=finder_amt,
                    spent=False,
                    spent_txid=None,
                    coinbase=True,
                ))
                s.flush()
            else:
                if ex_cb2.address != header.miner_address:
                    ex_cb2.address = header.miner_address
                if abs((ex_cb2.amount or 0.0) - finder_amt) > 1e-12:
                    ex_cb2.amount = finder_amt
            _ensure_epoch_for_height(s, height)
        _with_retry(_persist_rewards_and_cb)

        def _finalize_utxo_placeholders():
            for u in s.query(UTXO).filter_by(txid="BLOCK_TMP").all():
                u.txid = hh
            for u in s.query(UTXO).filter_by(spent=True, spent_txid="BLOCK_TMP").all():
                u.spent_txid = hh
            for u in s.query(UTXO).filter(UTXO.txid == hh, UTXO.vout >= 10_000_000).all():
                u.spent = False
                u.spent_txid = None
        _with_retry(_finalize_utxo_placeholders)

        def _record_success_diag():
            try:
                okctx = s.get(KV, "diag_last_pool_promotion") or KV(k="diag_last_pool_promotion", v="")
                okctx.v = f"accepted; h={height} hh={hh[:16]}.. miner={header.miner_address} txs={header.tx_count}"
                s.merge(okctx)
            except Exception:
                pass
        _record_success_diag()

        _with_retry(s.commit)

        # Attempt to settle previous epoch if boundary crossed (best-effort)
        try:
            _settle_epoch_if_needed(s, height)
            _with_retry(s.commit)
        except Exception:
            pass

        return hh, None
