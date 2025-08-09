from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional, Any, Dict, List, Tuple

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    LargeBinary,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    create_engine,
    select,
    func,
    Boolean,
)
from sqlalchemy.orm import declarative_base, relationship, Session, sessionmaker
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from core.config import get_config
from core.utils import ensure_dirs

Base = declarative_base()


# ===== SQLAlchemy Models =====

class KV(Base):
    __tablename__ = "kv"
    k = Column(String(128), primary_key=True)
    v = Column(Text, nullable=False)


class Peer(Base):
    __tablename__ = "peers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    address = Column(String(255), unique=True, nullable=False)
    last_seen_ms = Column(Integer, nullable=False, default=0)
    reputation = Column(Float, nullable=False, default=0.0)


class BlockHeader(Base):
    __tablename__ = "block_headers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    height = Column(Integer, nullable=False, index=True)
    hash_hex = Column(String(64), unique=True, nullable=False)
    prev_hash_hex = Column(String(64), nullable=False, index=True)
    merkle_root_hex = Column(String(64), nullable=False)
    timestamp = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False)
    nonce = Column(String(64), nullable=False)
    target = Column(String(64), nullable=False)
    miner_address = Column(String(128), nullable=False)
    tx_count = Column(Integer, nullable=False, default=0)
    work = Column(String(64), nullable=False)  # cumulative work or difficulty metric
    __table_args__ = (
        UniqueConstraint("height", "hash_hex", name="uq_height_hash"),
        Index("idx_prev_hash", "prev_hash_hex"),
    )


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    txid = Column(String(64), unique=True, nullable=False, index=True)
    raw = Column(Text, nullable=False)
    added_ms = Column(Integer, nullable=False)
    in_block_hash = Column(String(64), nullable=True, index=True)  # null if mempool
    fee = Column(Float, nullable=False, default=0.0)


class WalletAccount(Base):
    __tablename__ = "wallet_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    public_view_key = Column(String(128), nullable=False)
    public_spend_key = Column(String(128), nullable=False)
    # secure mnemonic storage (Argon2id -> AES-GCM) + ownership
    owner_user_id = Column(Integer, nullable=True, index=True)
    enc_mnemonic = Column(Text, nullable=True)  # base64 ciphertext
    enc_salt = Column(Text, nullable=True)      # base64 salt
    enc_nonce = Column(Text, nullable=True)     # base64 nonce
    created_ms = Column(Integer, nullable=False)


class SubAddress(Base):
    __tablename__ = "subaddresses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("wallet_accounts.id"), nullable=False)
    index_major = Column(Integer, nullable=False)
    index_minor = Column(Integer, nullable=False)
    address = Column(String(255), unique=True, nullable=False)
    label = Column(String(255), nullable=True)
    __table_args__ = (
        UniqueConstraint("account_id", "index_major", "index_minor", name="uq_subaddr_idx"),
    )
    account = relationship("WalletAccount", backref="subaddresses")


class UTXO(Base):
    __tablename__ = "utxos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    txid = Column(String(64), nullable=False, index=True)
    vout = Column(Integer, nullable=False)
    address = Column(String(255), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    spent = Column(Boolean, nullable=False, default=False)
    spent_txid = Column(String(64), nullable=True)
    coinbase = Column(Boolean, nullable=False, default=False)
    __table_args__ = (
        UniqueConstraint("txid", "vout", name="uq_tx_vout"),
    )


class MasternodeHeartbeat(Base):
    __tablename__ = "masternode_heartbeats"
    id = Column(Integer, primary_key=True, autoincrement=True)
    node_id = Column(String(128), nullable=False, index=True)
    address = Column(String(255), nullable=False)
    last_heartbeat_ms = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="OK")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(128), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_ms = Column(Integer, nullable=False)


class Reward(Base):
    __tablename__ = "rewards"
    id = Column(Integer, primary_key=True, autoincrement=True)
    height = Column(Integer, nullable=False, index=True)
    miner_address = Column(String(255), nullable=False, index=True)
    amount = Column(Float, nullable=False, default=0.0)
    txid = Column(String(64), nullable=False, index=True)
    created_ms = Column(Integer, nullable=False)


# ===== Fairness (Solo-only) epoch accounting =====
class FairnessEpoch(Base):
    __tablename__ = "fairness_epoch"
    id = Column(Integer, primary_key=True, autoincrement=True)
    start_height = Column(Integer, nullable=False, index=True)
    end_height = Column(Integer, nullable=False, index=True)
    pool_ratio = Column(Float, nullable=False, default=0.30)  # e.g., 0.30 = 30% fairness pool
    settled = Column(Boolean, nullable=False, default=False)
    created_ms = Column(Integer, nullable=False, default=0)
    __table_args__ = (
        UniqueConstraint("start_height", "end_height", name="uq_epoch_range"),
    )


class FairnessCredit(Base):
    __tablename__ = "fairness_credit"
    id = Column(Integer, primary_key=True, autoincrement=True)
    epoch_id = Column(Integer, ForeignKey("fairness_epoch.id"), nullable=False, index=True)
    miner_addr = Column(String(255), nullable=False, index=True)
    credit_units = Column(Float, nullable=False, default=0.0)  # sum of near-target weights
    last_ms = Column(Integer, nullable=False, default=0)
    __table_args__ = (
        UniqueConstraint("epoch_id", "miner_addr", name="uq_epoch_addr"),
        Index("idx_credit_epoch_addr", "epoch_id", "miner_addr"),
    )
    epoch = relationship("FairnessEpoch", backref="credits")


class MempoolTx(Base):
    __tablename__ = "mempool"
    id = Column(Integer, primary_key=True, autoincrement=True)
    txid = Column(String(64), unique=True, nullable=False, index=True)
    raw = Column(Text, nullable=False)
    added_ms = Column(Integer, nullable=False)
    fee = Column(Float, nullable=False, default=0.0)
    from_addr = Column(String(255), nullable=True, index=True)
    to_addr = Column(String(255), nullable=True, index=True)
    amount = Column(Float, nullable=True)


# ===== Engine/Session utilities =====

@dataclass
class DBConfig:
    driver: str
    sqlite_path: str
    postgres_dsn: Optional[str] = None


def _build_engine(db_cfg: DBConfig):
    if db_cfg.driver == "sqlite":
        # SQLite tuning for concurrent FastAPI thread writes:
        # - check_same_thread=False: allow cross-thread usage
        # - timeout: busy timeout for writer lock contention
        # - WAL journal mode and reasonable synchronous for less writer blocking
        url = f"sqlite:///{db_cfg.sqlite_path}"
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 10.0},
            poolclass=StaticPool if db_cfg.sqlite_path == ":memory:" else None,
            echo=False,
            future=True,
        )
        # Apply WAL+journal tuning (no-op on some platforms)
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
                conn.exec_driver_sql("PRAGMA busy_timeout=10000")
        except Exception:
            pass
        return engine
    elif db_cfg.driver == "postgres":
        if not db_cfg.postgres_dsn:
            raise ValueError("Postgres selected but postgres_dsn not configured.")
        return create_engine(db_cfg.postgres_dsn, echo=False, future=True)
    else:
        raise ValueError(f"Unsupported DB driver: {db_cfg.driver}")


class DB:
    def __init__(self):
        ensure_dirs()
        cfg = get_config()
        driver = cfg.get("database.driver", "sqlite")
        sqlite_path = cfg.get("database.sqlite_path", "data/smelly.db")
        postgres_dsn = cfg.get("database.postgres_dsn", None)
        self.db_cfg = DBConfig(driver=driver, sqlite_path=sqlite_path, postgres_dsn=postgres_dsn)
        self.engine = _build_engine(self.db_cfg)
        self._SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, class_=Session)

    def create_all(self):
        # Auto-migrate (in-place): create tables/columns if missing
        Base.metadata.create_all(self.engine)

        # Add missing columns for simple migrations (SQLite supports ADD COLUMN)
        with self.engine.begin() as conn:  # transactional DDL
            # UTXO.coinbase column
            try:
                conn.execute(select(func.count()).select_from(UTXO))
                has_coinbase = False
                for row in conn.exec_driver_sql("PRAGMA table_info(utxos)").fetchall():
                    if row[1] == "coinbase":
                        has_coinbase = True
                        break
                if not has_coinbase:
                    conn.exec_driver_sql("ALTER TABLE utxos ADD COLUMN coinbase BOOLEAN NOT NULL DEFAULT 0")
            except Exception:
                # Table may not exist yet; created by metadata above
                pass

            # WalletAccount ownership + encryption columns
            try:
                conn.execute(select(func.count()).select_from(WalletAccount))
                wallet_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(wallet_accounts)").fetchall()}
                if "owner_user_id" not in wallet_cols:
                    conn.exec_driver_sql("ALTER TABLE wallet_accounts ADD COLUMN owner_user_id INTEGER")
                    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_wallet_owner ON wallet_accounts(owner_user_id)")
                if "enc_mnemonic" not in wallet_cols:
                    conn.exec_driver_sql("ALTER TABLE wallet_accounts ADD COLUMN enc_mnemonic TEXT")
                if "enc_salt" not in wallet_cols:
                    conn.exec_driver_sql("ALTER TABLE wallet_accounts ADD COLUMN enc_salt TEXT")
                if "enc_nonce" not in wallet_cols:
                    conn.exec_driver_sql("ALTER TABLE wallet_accounts ADD COLUMN enc_nonce TEXT")
            except Exception:
                pass

            # Fairness tables exist check (SQLite dialect)
            try:
                # Ensure indexes/uniques are present (CREATE IF NOT EXISTS semantics)
                conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_epoch_range ON fairness_epoch(start_height, end_height)")
                conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_epoch_addr ON fairness_credit(epoch_id, miner_addr)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_credit_epoch_addr ON fairness_credit(epoch_id, miner_addr)")
            except Exception:
                pass
        # commit handled by context manager

    def session(self) -> Session:
        return self._SessionLocal()

    # Simple KV helpers
    def get_kv(self, key: str) -> Optional[str]:
        with self.session() as s:
            row = s.get(KV, key)
            return row.v if row else None

    def set_kv(self, key: str, value: str):
        with self.session() as s:
            row = s.get(KV, key)
            if row:
                row.v = value
            else:
                row = KV(k=key, v=value)
                s.add(row)
            s.commit()


_db_singleton: Optional[DB] = None


def get_db() -> DB:
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = DB()
        _db_singleton.create_all()
    return _db_singleton
