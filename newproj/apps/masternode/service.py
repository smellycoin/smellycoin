from __future__ import annotations

import time
import uuid
import threading
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import requests

from core.config import get_config
from core.utils import ensure_dirs, now_ms
from core.db import get_db, MasternodeHeartbeat


app = FastAPI(title="SMELLY Masternode", version="0.1")

NODE_ID = str(uuid.uuid4())


class HeartbeatIn(BaseModel):
    node_id: str
    address: str
    status: str = "OK"


@app.on_event("startup")
def on_startup():
    ensure_dirs()
    get_db()  # init db


@app.post("/mn/heartbeat")
def post_heartbeat(hb: HeartbeatIn):
    db = get_db()
    with db.session() as s:
        row = MasternodeHeartbeat(
            node_id=hb.node_id,
            address=hb.address,
            last_heartbeat_ms=now_ms(),
            status=hb.status,
        )
        s.add(row)
        s.commit()
    return {"ok": True}


@app.get("/mn/recent")
def get_recent(limit: int = 50):
    db = get_db()
    with db.session() as s:
        rows = (
            s.query(MasternodeHeartbeat)
            .order_by(MasternodeHeartbeat.last_heartbeat_ms.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "node_id": r.node_id,
                "address": r.address,
                "last_heartbeat_ms": r.last_heartbeat_ms,
                "status": r.status,
            }
            for r in rows
        ]


def start_self_heartbeat_thread():
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    mn_port = int(cfg.get("network.masternode_port", 28447))
    addr = f"{host}:{mn_port}"
    url = f"http://{addr}/mn/heartbeat"
    node_id = NODE_ID

    def loop():
        while True:
            try:
                requests.post(url, json={"node_id": node_id, "address": addr, "status": "OK"}, timeout=5)
            except Exception:
                pass
            time.sleep(10)

    t = threading.Thread(target=loop, daemon=True)
    t.start()


def run_masternode():
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    port = int(cfg.get("network.masternode_port", 28447))
    start_self_heartbeat_thread()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_masternode()
