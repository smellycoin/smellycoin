from __future__ import annotations

import os
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
import uvicorn

from core.config import get_config
from core.utils import ensure_dirs
from core.db import get_db, BlockHeader, UTXO, Reward, Transaction, MempoolTx, KV, FairnessEpoch, FairnessCredit
from sqlalchemy import func
import json
import time
import requests


app = FastAPI(title="SMELLY Explorer", version="0.1")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"])
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def write_default_assets():
    css = """
:root { --primary:#FFD000; --secondary:#111111; --bg:#0b0b0b; --text:#f1f1f1; }
*{box-sizing:border-box}
body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:Segoe UI,Roboto,Arial,sans-serif}
.header{display:flex;align-items:center;justify-content:space-between;background:linear-gradient(90deg,var(--secondary),#000);color:var(--primary);padding:12px 20px;font-weight:800;letter-spacing:.5px}
.header a{color:#FFC400;margin-left:12px}
.container{padding:20px;max-width:1200px;margin:0 auto}
.row{display:flex;gap:16px;flex-wrap:wrap}
.card{flex:1 1 520px;background:#161616;border:1px solid #222;border-radius:10px;padding:16px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.35)}
h1,h2,h3{color:var(--primary);margin:0 0 12px 0}
.table{width:100%;border-collapse:collapse}
.table th,.table td{border-bottom:1px solid #2a2a2a;padding:8px;text-align:left}
.mono{font-family:Consolas,monospace;word-break:break-all}
a{color:#FFC400;text-decoration:none}
a:hover{text-decoration:underline}
.searchbar{display:flex;gap:8px}
input[type="text"]{flex:1;padding:10px;border-radius:6px;border:1px solid #333;background:#0f0f0f;color:#eee}
button{background:var(--primary);color:#000;border:none;padding:10px 16px;border-radius:6px;cursor:pointer;font-weight:700}
.badge{display:inline-block;background:#222;color:#FFD000;border:1px solid #333;padding:2px 8px;border-radius:999px;font-size:12px}
.notice{color:#aaa;font-size:12px}
.small{font-size:12px;color:#aaa}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.right{float:right}
/* Loading overlay */
.loader-overlay{position:fixed;inset:0;background:#000;display:flex;align-items:center;justify-content:center;z-index:9999}
.spinner{width:64px;height:64px;border:6px solid #333;border-top-color:#FFD000;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.hidden{display:none}
"""
    with open(os.path.join(STATIC_DIR, "style.css"), "w", encoding="utf-8") as f:
        f.write(css)

    index_html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>SMELLY Explorer</title>
  <meta http-equiv="refresh" content="10"/>
  <link rel="stylesheet" href="/static/style.css"/>
  <script>
    // Hide loader once DOM is ready
    document.addEventListener('DOMContentLoaded', ()=>{
      const el = document.getElementById('loader'); if(el) el.classList.add('hidden');
    });
  </script>
</head>
<body>
  <div id="loader" class="loader-overlay"><div class="spinner"></div></div>
  <div class="header">
    <div>SMELLY Explorer</div>
    <div class="small"><a href="/">Home</a> | <a href="/mempool">Mempool</a> | <a href="/admin/fairness">Fairness</a></div>
  </div>
  <div class="container">
    <div class="row">
      <div class="card">
        <h2>Search</h2>
        <form method="get" action="/search" class="searchbar">
          <input type="text" name="q" placeholder="Block height / block hash / tx id / Address" value="{{q or ''}}"/>
          <button type="submit">Search</button>
        </form>
        <div class="notice">Examples: 1003, a1b2c3..., txid..., SMELLY_...</div>
        {% if search_error %}<div class="notice">Error: {{search_error}}</div>{% endif %}
        {% if search_result %}
          <div style="margin-top:12px">
            {% if search_result.type == 'block' %}
              <span class="badge">Block</span>
              <a href="/block/{{search_result.hash}}">{{search_result.title}}</a>
            {% elif search_result.type == 'tx' %}
              <span class="badge">Tx</span> <a class="mono" href="/tx/{{search_result.id}}">{{search_result.id}}</a>
            {% elif search_result.type == 'address' %}
              <span class="badge">Address</span> <a class="mono" href="/address/{{search_result.address}}">{{search_result.address}}</a>
            {% else %}
              <span class="badge">Not found</span> <span class="small">No match for '{{q}}'</span>
            {% endif %}
          </div>
        {% endif %}
      </div>

      <div class="card">
        <h2>Chain Overview</h2>
        <div>Height: {{height}}</div>
        <div>Latest: <span class="mono">{{headers[0].hash if headers else ''}}</span></div>
      </div>
    </div>

    <div class="card">
      <h2>Recent Blocks</h2>
      <table class="table">
        <thead><tr><th>Height</th><th>Hash</th><th>Time</th><th>Miner</th><th>Tx</th></tr></thead>
        <tbody>
        {% for h in headers %}
          <tr>
            <td><a href="/block/{{h.hash}}">{{h.height}}</a></td>
            <td class="mono"><a href="/block/{{h.hash}}">{{h.hash[:24]}}...</a></td>
            <td>{{h.timestamp}}</td>
            <td class="mono"><a href="/address/{{h.miner}}">{{h.miner[:22]}}...</a></td>
            <td>{{h.tx_count}}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
    with open(os.path.join(TEMPLATES_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)

    block_html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Block {{hdr.height}} - SMELLY</title>
  <link rel="stylesheet" href="/static/style.css"/>
  <script>
    document.addEventListener('DOMContentLoaded', ()=>{
      const el = document.getElementById('loader'); if(el) el.classList.add('hidden');
    });
  </script>
</head>
<body>
  <div id="loader" class="loader-overlay"><div class="spinner"></div></div>
  <div class="header">
    <div>SMELLY Explorer</div>
    <div class="small"><a href="/">Home</a> | <a href="/mempool">Mempool</a> | <a href="/admin/fairness">Fairness</a></div>
  </div>
  <div class="container">
    <div class="card">
      <h2>Block {{hdr.height}}</h2>
      <div><b>Hash:</b> <span class="mono">{{hdr.hash}}</span></div>
      <div><b>Prev:</b> <span class="mono">{{hdr.prev_hash}}</span></div>
      <div><b>Timestamp:</b> {{hdr.timestamp}}</div>
      <div><b>Version:</b> {{hdr.version}}</div>
      <div><b>Target:</b> <span class="mono">{{hdr.target}}</span></div>
      <div><b>Nonce:</b> {{hdr.nonce}}</div>
      <div><b>Miner:</b> <a class="mono" href="/address/{{hdr.miner}}">{{hdr.miner}}</a></div>
      <div><b>Tx Count:</b> {{hdr.tx_count}}</div>
      <div><b>Cumulative Work:</b> <span class="mono">{{hdr.work}}</span></div>
      <div><b>Coinbase Txid:</b> <a class="mono" href="/tx/{{hdr.coinbase_txid}}">{{hdr.coinbase_txid}}</a></div>
    </div>
    <a href="/">Back</a>
  </div>
</body>
</html>
"""
    with open(os.path.join(TEMPLATES_DIR, "block.html"), "w", encoding="utf-8") as f:
        f.write(block_html)

    address_html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Address - SMELLY</title>
  <link rel="stylesheet" href="/static/style.css"/>
</head>
<body>
  <div class="header">
    <div>SMELLY Explorer</div>
    <div class="small"><a href="/">Home</a> | <a href="/mempool">Mempool</a> | <a href="/admin/fairness">Fairness</a></div>
  </div>
  <div class="container">
    <div class="card">
      <h2>Address</h2>
      <div class="mono">{{addr}}</div>
      <div><b>Balance:</b> {{balance}}</div>
      <div><b>Total Rewards:</b> {{total_rewards}}</div>
    </div>

    <div class="card">
      <h3>Unspent UTXOs</h3>
      <table class="table">
        <thead><tr><th>Txid</th><th>Vout</th><th>Amount</th><th>Coinbase</th></tr></thead>
        <tbody>
        {% for u in utxos %}
          <tr>
            <td class="mono"><a href="/tx/{{u.txid}}">{{u.txid[:24]}}...</a></td>
            <td>{{u.vout}}</td>
            <td>{{u.amount}}</td>
            <td>{{'Yes' if u.coinbase else 'No'}}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Rewards</h3>
      <table class="table">
        <thead><tr><th>Height</th><th>Amount</th><th>Txid</th></tr></thead>
        <tbody>
        {% for r in rewards %}
          <tr>
            <td><a href="/block/{{r.block_hash}}">{{r.height}}</a></td>
            <td>{{r.amount}}</td>
            <td class="mono"><a href="/tx/{{r.txid}}">{{r.txid}}</a></td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
    with open(os.path.join(TEMPLATES_DIR, "address.html"), "w", encoding="utf-8") as f:
        f.write(address_html)

    tx_html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Tx - SMELLY</title>
  <link rel="stylesheet" href="/static/style.css"/>
  <script>
    document.addEventListener('DOMContentLoaded', ()=>{
      const el = document.getElementById('loader'); if(el) el.classList.add('hidden');
    });
  </script>
</head>
<body>
  <div id="loader" class="loader-overlay"><div class="spinner"></div></div>
  <div class="header">
    <div>SMELLY Explorer</div>
    <div class="small"><a href="/">Home</a> | <a href="/mempool">Mempool</a></div>
  </div>
  <div class="container">
    <div class="card">
      <h2>Transaction</h2>
      <div><b>Txid:</b> <span class="mono">{{tx.txid}}</span></div>
      <div><b>In Block:</b> {% if tx.in_block_hash %}<a class="mono" href="/block/{{tx.in_block_hash}}">{{tx.in_block_hash}}</a>{% else %}Mempool{% endif %}</div>
      <div><b>Fee:</b> {{tx.fee}}</div>
      <div class="row">
        <div class="card">
          <h3>Inputs</h3>
          <table class="table">
            <thead><tr><th>Source Txid</th><th>Vout</th><th>Amount</th><th>Address</th></tr></thead>
            <tbody>
            {% for i in inputs %}
              <tr>
                <td class="mono"><a href="/tx/{{i.txid}}">{{i.txid[:24]}}...</a></td>
                <td>{{i.vout}}</td>
                <td>{{i.amount}}</td>
                <td class="mono"><a href="/address/{{i.address}}">{{i.address[:22]}}...</a></td>
              </tr>
            {% endfor %}
            {% if not inputs %}<tr><td colspan="4" class="small">No inputs (coinbase or not recorded)</td></tr>{% endif %}
            </tbody>
          </table>
        </div>
        <div class="card">
          <h3>Outputs</h3>
          <table class="table">
            <thead><tr><th>Vout</th><th>Amount</th><th>Address</th><th>Coinbase</th></tr></thead>
            <tbody>
            {% for o in outputs %}
              <tr>
                <td>{{o.vout}}</td>
                <td>{{o.amount}}</td>
                <td class="mono"><a href="/address/{{o.address}}">{{o.address[:22]}}...</a></td>
                <td>{{'Yes' if o.coinbase else 'No'}}</td>
              </tr>
            {% endfor %}
            {% if not outputs %}<tr><td colspan="4" class="small">No outputs recorded</td></tr>{% endif %}
            </tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <h3>Raw</h3>
        <pre class="small">{{tx.raw}}</pre>
      </div>
    </div>
  </div>
</body>
</html>
"""
    with open(os.path.join(TEMPLATES_DIR, "tx.html"), "w", encoding="utf-8") as f:
        f.write(tx_html)

    mempool_html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Mempool - SMELLY</title>
  <meta http-equiv="refresh" content="10"/>
  <link rel="stylesheet" href="/static/style.css"/>
</head>
<body>
  <div class="header">
    <div>SMELLY Explorer</div>
    <div class="small"><a href="/">Home</a> | <a href="/admin/fairness">Fairness</a></div>
  </div>
  <div class="container">
    <div class="card">
      <h2>Mempool</h2>
      <table class="table">
        <thead><tr><th>Txid</th><th>From</th><th>To</th><th>Amount</th><th>Fee</th><th>Added</th></tr></thead>
        <tbody>
        {% for m in mempool %}
          <tr>
            <td class="mono"><a href="/tx/{{m.txid}}">{{m.txid}}</a></td>
            <td class="mono">{{m.from_addr or ''}}</td>
            <td class="mono">{{m.to_addr or ''}}</td>
            <td>{{m.amount or ''}}</td>
            <td>{{m.fee}}</td>
            <td>{{m.added_ms}}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
    with open(os.path.join(TEMPLATES_DIR, "mempool.html"), "w", encoding="utf-8") as f:
        f.write(mempool_html)

    pool_html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Pool - SMELLY</title>
  <meta http-equiv="refresh" content="10"/>
  <link rel="stylesheet" href="/static/style.css"/>
</head>
<body>
  <div class="header">
    <div>SMELLY Explorer</div>
    <div class="small"><a href="/">Home</a> | <a href="/mempool">Mempool</a></div>
  </div>
  <div class="container">
    <div class="card">
      <h2>Pool Overview</h2>
      <div>Backend: <span class="badge">{{backend}}</span></div>
      <div>Share Diff: <span class="badge">{{share_diff}}</span></div>
      <div>Total Hashrate (est.): {{total_hashrate}} H/s</div>
      <div>Connected Miners: {{miner_count}}</div>
      <div>Accepted Shares (5m): {{accepted_5m}} | Rejected (5m): {{rejected_5m}}</div>
    </div>

    <div class="card">
      <h3>Connected Miners</h3>
      <table class="table">
        <thead><tr><th>Miner</th><th>Accepted</th><th>Rejected</th><th>Last Submit (ms)</th><th>Hashrate est. (H/s)</th></tr></thead>
        <tbody>
        {% for m in miners %}
          <tr>
            <td class="mono">{{m.addr}}</td>
            <td>{{m.accepted}}</td>
            <td>{{m.rejected}}</td>
            <td>{{m.last_submit_ms}}</td>
            <td>{{m.hashrate}}</td>
          </tr>
        {% endfor %}
        {% if not miners %}<tr><td colspan="5" class="small">No miners connected</td></tr>{% endif %}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Recent Pool Blocks</h3>
      <table class="table">
        <thead><tr><th>Height</th><th>Hash</th><th>Miner</th><th>Time</th></tr></thead>
        <tbody>
        {% for b in blocks %}
          <tr>
            <td><a href="/block/{{b.hash}}">{{b.height}}</a></td>
            <td class="mono"><a href="/block/{{b.hash}}">{{b.hash[:24]}}...</a></td>
            <td class="mono">{{b.miner}}</td>
            <td>{{b.timestamp}}</td>
          </tr>
        {% endfor %}
        {% if not blocks %}<tr><td colspan="4" class="small">No recent pool-found blocks recorded</td></tr>{% endif %}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
    with open(os.path.join(TEMPLATES_DIR, "pool.html"), "w", encoding="utf-8") as f:
        f.write(pool_html)


@app.on_event("startup")
def on_startup():
    ensure_dirs()
    write_default_assets()
    get_db()  # ensure tables


@app.get("/search", response_class=HTMLResponse)
def search_route(q: str | None = Query(default=None, alias="q")):
    """
    Dedicated search endpoint that returns suggestions and, if an exact match is found,
    redirects to the appropriate detail page. Otherwise renders the home with suggestions.
    """
    if not q or not q.strip():
        # No query -> render home
        return index(q=None)

    db = get_db()
    suggestions: List[dict] = []
    q_raw = q.strip()
    q_norm = q_raw.lower()

    # 1) Height exact
    if q_raw.isdigit():
        hgt = int(q_raw)
        with db.session() as s:
            h = (
                s.query(BlockHeader)
                .filter(BlockHeader.height == hgt)
                .order_by(BlockHeader.id.desc())
                .first()
            )
            if h:
                # Direct redirect when exact height requested
                from fastapi.responses import RedirectResponse
                return RedirectResponse(url=f"/block/{h.hash_hex}", status_code=302)

    # 2) Address exact (SMELLY_ or stored)
    if q_raw.startswith("SMELLY_"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/address/{q_raw}", status_code=302)
    else:
        with db.session() as s:
            addr_row = s.query(UTXO.address).filter(UTXO.address == q_raw).first()
            if addr_row:
                from fastapi.responses import RedirectResponse
                return RedirectResponse(url=f"/address/{q_raw}", status_code=302)

    # 3) Block hash exact or prefix
    with db.session() as s:
        exact_block = s.query(BlockHeader).filter(BlockHeader.hash_hex == q_norm).first()
        if exact_block:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=f"/block/{exact_block.hash_hex}", status_code=302)
        blocks = (
            s.query(BlockHeader)
            .filter(BlockHeader.hash_hex.like(f"{q_norm}%"))
            .order_by(BlockHeader.height.desc())
            .limit(10)
            .all()
        )
        for b in blocks:
            suggestions.append({
                "kind": "block",
                "title": f"Block {b.height}",
                "subtitle": b.hash_hex,
                "href": f"/block/{b.hash_hex}",
            })

    # 4) Tx exact or prefix
    with db.session() as s:
        exact_tx = s.query(Transaction).filter(Transaction.txid == q_norm).first()
        if exact_tx:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=f"/tx/{exact_tx.txid}", status_code=302)
        # Order by most recent first using primary key/id if available (rowid is not a mapped attribute)
        txs = (
            s.query(Transaction)
            .filter(Transaction.txid.like(f"{q_norm}%"))
            .order_by(Transaction.id.desc() if hasattr(Transaction, "id") else Transaction.txid.desc())
            .limit(10)
            .all()
        )
        for t in txs:
            suggestions.append({
                "kind": "tx",
                "title": "Transaction",
                "subtitle": t.txid,
                "href": f"/tx/{t.txid}",
            })

    # If we got here, either we have suggestions or nothing matched. Render home with suggestions.
    return index(q=q)

@app.get("/", response_class=HTMLResponse)
def index(q: str | None = Query(default=None, alias="q")):
    db = get_db()
    with db.session() as s:
        tip = s.query(BlockHeader).order_by(BlockHeader.height.desc()).first()
        height = tip.height if tip else -1
        subq = (
            s.query(
                BlockHeader.height,
                func.max(BlockHeader.id).label("max_id")
            )
            .group_by(BlockHeader.height)
            .subquery()
        )
        recent = (
            s.query(BlockHeader)
            .join(subq, BlockHeader.id == subq.c.max_id)
            .order_by(BlockHeader.height.desc())
            .limit(25)
            .all()
        )
        headers = [
            {
                "height": h.height,
                "hash": h.hash_hex,
                "timestamp": h.timestamp,
                "miner": h.miner_address,
                "tx_count": h.tx_count,
            }
            for h in recent
        ]

    # Build rich search suggestions list for UX similar to major explorers
    suggestions: List[dict] = []
    search_error = None
    if q:
        try:
            q_raw = q.strip()
            q_norm = q_raw.lower()

            # 1) Height (all digits) -> canonical block row for that height
            if q_raw.isdigit():
                hgt = int(q_raw)
                with db.session() as s2:
                    h = (
                        s2.query(BlockHeader)
                        .filter(BlockHeader.height == hgt)
                        .order_by(BlockHeader.id.desc())
                        .first()
                    )
                    if h:
                        suggestions.append({
                            "kind": "block",
                            "title": f"Block {h.height}",
                            "subtitle": h.hash_hex,
                            "href": f"/block/{h.hash_hex}",
                        })

            # 2) Exact address if SMELLY_ or subaddress-like
            if q_raw.startswith("SMELLY_"):
                suggestions.append({
                    "kind": "address",
                    "title": "Address",
                    "subtitle": q_raw,
                    "href": f"/address/{q_raw}",
                })
            else:
                # Try to find exact match in UTXO addresses to hint address
                with db.session() as s2:
                    addr_row = s2.query(UTXO.address).filter(UTXO.address == q_raw).first()
                    if addr_row:
                        suggestions.append({
                            "kind": "address",
                            "title": "Address",
                            "subtitle": q_raw,
                            "href": f"/address/{q_raw}",
                        })

            # 3) Block hash prefix match (top 5)
            with db.session() as s3:
                blocks = (
                    s3.query(BlockHeader)
                    .filter(BlockHeader.hash_hex.like(f"{q_norm}%"))
                    .order_by(BlockHeader.height.desc())
                    .limit(5)
                    .all()
                )
                for b in blocks:
                    suggestions.append({
                        "kind": "block",
                        "title": f"Block {b.height}",
                        "subtitle": b.hash_hex,
                        "href": f"/block/{b.hash_hex}",
                    })

            # 4) Txid prefix match (top 5)
            with db.session() as s4:
                # Order by most recent using primary key if present; fallback to txid desc
                txs = (
                    s4.query(Transaction)
                    .filter(Transaction.txid.like(f"{q_norm}%"))
                    .order_by(Transaction.id.desc() if hasattr(Transaction, "id") else Transaction.txid.desc())
                    .limit(5)
                    .all()
                )
                for t in txs:
                    suggestions.append({
                        "kind": "tx",
                        "title": "Transaction",
                        "subtitle": t.txid,
                        "href": f"/tx/{t.txid}",
                    })

            # De-duplicate by href while keeping order
            seen_href = set()
            dedup_suggestions: List[dict] = []
            for sgg in suggestions:
                href = sgg.get("href")
                if href and href not in seen_href:
                    seen_href.add(href)
                    dedup_suggestions.append(sgg)
            suggestions = dedup_suggestions

        except Exception as e:
            search_error = str(e)

    # Render index with suggestions passed to template via q/search_error and a JSON blob if desired later
    tpl = env.get_template("index.html")
    return tpl.render(height=height, headers=headers, q=q, search_error=search_error, suggestions=suggestions)


@app.get("/block/{hash_hex}", response_class=HTMLResponse)
def block(hash_hex: str):
    db = get_db()
    with db.session() as s:
        h = s.query(BlockHeader).filter_by(hash_hex=hash_hex).first()
        if not h:
            raise HTTPException(status_code=404, detail="Not found")
        # Ensure this height maps to this canonical block (latest by id for the height)
        canon = (
            s.query(BlockHeader)
            .filter_by(height=h.height)
            .order_by(BlockHeader.id.desc())
            .first()
        )
        if canon and canon.hash_hex != h.hash_hex:
            # Redirect to canonical block if user hit a stale duplicate
            h = canon
        coinbase_txid = f"{h.merkle_root_hex}" if h.tx_count > 0 else ""
        hdr = {
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
            "coinbase_txid": coinbase_txid,
        }
    tpl = env.get_template("block.html")
    return tpl.render(hdr=hdr)


@app.get("/address/{addr}", response_class=HTMLResponse)
def address(addr: str):
    db = get_db()
    with db.session() as s:
        utxos = s.query(UTXO).filter_by(address=addr, spent=False).all()
        balance = sum(u.amount for u in utxos)
        rewards = s.query(Reward).filter_by(miner_address=addr).order_by(Reward.height.desc()).limit(100).all()
        # find block hash for each reward via height
        blocks = {b.height: b.hash_hex for b in s.query(BlockHeader).filter(BlockHeader.height.in_([r.height for r in rewards])).all()}
        rew_rows = [{"height": r.height, "amount": r.amount, "txid": r.txid, "block_hash": blocks.get(r.height, "")} for r in rewards]
        tpl = env.get_template("address.html")
        return tpl.render(addr=addr, balance=balance, total_rewards=sum(r.amount for r in rewards), rewards=rew_rows, utxos=utxos)


@app.get("/tx/{txid}", response_class=HTMLResponse)
def tx_view(txid: str):
    db = get_db()
    with db.session() as s:
        tx = s.query(Transaction).filter_by(txid=txid).first()
        if not tx:
            # Synthesize a coinbase tx presentation if not recorded in transactions
            tx = type("TxObj", (), {})()
            tx.txid = txid
            tx.in_block_hash = None
            tx.fee = 0.0
            tx.raw = "coinbase (synthetic)"
        # Inputs: UTXOs that were spent by this transaction (spent_txid == txid)
        inputs = s.query(UTXO).filter_by(spent_txid=txid).order_by(UTXO.amount.desc()).all()
        # Outputs: UTXOs created by this transaction (txid == txid)
        outputs = s.query(UTXO).filter_by(txid=txid).order_by(UTXO.vout.asc()).all()
        tpl = env.get_template("tx.html")
        return tpl.render(tx=tx, inputs=inputs, outputs=outputs)


@app.get("/mempool", response_class=HTMLResponse)
def mempool():
    db = get_db()
    with db.session() as s:
        mem = s.query(MempoolTx).order_by(MempoolTx.added_ms.desc()).limit(200).all()
    tpl = env.get_template("mempool.html")
    return tpl.render(mempool=mem)

# ========== FAIRNESS ADMIN ==========
def _epoch_lengths() -> tuple[int, int]:
    from core.config import get_config
    cfg = get_config()
    return int(cfg.get("fairness.epoch_length_dev", 20)), int(cfg.get("fairness.epoch_length_main", 100))

def _epoch_for_height(height: int) -> tuple[int, int]:
    dev_len, main_len = _epoch_lengths()
    # Use dev_len if network.dev_mode is set (default 1 for dev); otherwise main_len
    from core.config import get_config
    size = dev_len if int(get_config().get("network.dev_mode", 1)) else main_len
    start = (height // size) * size
    end = start + size - 1
    return start, end

@app.get("/admin/fairness", response_class=HTMLResponse)
def fairness_admin():
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
        # Collect current epoch credits aggregated
        credits = (
            s.query(FairnessCredit.miner_addr, func.sum(FairnessCredit.credit_units))
            .group_by(FairnessCredit.miner_addr)
            .order_by(func.sum(FairnessCredit.credit_units).desc())
            .limit(100)
            .all()
        )
        rows = [{"addr": addr, "credits": float(creds or 0.0)} for (addr, creds) in credits]
        # Diagnostics
        last_promo = s.get(KV, "diag_last_pool_promotion")
        last_promo_msg = last_promo.v if last_promo else ""
        tpl_html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Fairness Admin - SMELLY</title>
  <meta http-equiv="refresh" content="10"/>
  <link rel="stylesheet" href="/static/style.css"/>
</head>
<body>
  <div class="header">
    <div>SMELLY Explorer</div>
    <div class="small"><a href="/">Home</a> | <a href="/mempool">Mempool</a> | <a href="/admin/fairness">Fairness</a></div>
  </div>
  <div class="container">
    <div class="card">
      <h2>Fairness Epoch</h2>
      <div>Current Height: {{height}}</div>
      <div>Epoch Window: {{start}} - {{end}}</div>
      <div>Pool Ratio: {{pool_ratio}}</div>
      <div>Settled: {{settled}}</div>
    </div>

    <div class="card">
      <h3>Top Credits (current epoch)</h3>
      <table class="table">
        <thead><tr><th>Address</th><th>Credits</th></tr></thead>
        <tbody>
        {% for r in rows %}
          <tr>
            <td class="mono">{{r.addr}}</td>
            <td>{{"%.3f"|format(r.credits)}}</td>
          </tr>
        {% endfor %}
        {% if not rows %}<tr><td colspan="2" class="small">No credits recorded yet</td></tr>{% endif %}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Diagnostics</h3>
      <div class="small">Last Promotion: {{last_promo}}</div>
    </div>
  </div>
</body>
</html>
"""
        from jinja2 import Template
        tpl = Template(tpl_html)
        return tpl.render(
            height=cur_height,
            start=start,
            end=end,
            pool_ratio=(ep.pool_ratio if ep else 0.30),
            settled=(ep.settled if ep else False),
            rows=rows,
            last_promo=last_promo_msg,
        )

@app.get("/pool", response_class=HTMLResponse)
def pool():
    """
    Pool dashboard: pulls lightweight stats from KV (persisted by pool server) if present,
    and augments with backend info and latest pool-found blocks (heuristic: miner == 'POOL' or contains 'POOL').
    """
    # Backend in use
    backend = "unknown"
    try:
        r = requests.get("http://127.0.0.1:28445/rpc/pow_backend", timeout=1.0)
        if r.status_code == 200:
            backend = (r.json() or {}).get("backend", "unknown")
    except Exception:
        pass

    # Load pool stats snapshot from KV (if pool server writes it)
    miners: List[dict] = []
    share_diff = 64
    accepted_5m = 0
    rejected_5m = 0
    total_hashrate = 0.0
    db = get_db()
    with db.session() as s:
        try:
            snap_row = s.get(KV, "pool_snapshot_json")
            if snap_row and snap_row.v:
                snap = json.loads(snap_row.v)
                miners = snap.get("miners", [])
                share_diff = snap.get("share_diff", 64)
                accepted_5m = snap.get("accepted_5m", 0)
                rejected_5m = snap.get("rejected_5m", 0)
                total_hashrate = float(snap.get("total_hashrate", 0.0))
        except Exception:
            pass
        # Recent pool-found blocks: last 25 where miner contains 'POOL'
        blocks = (
            s.query(BlockHeader)
            .filter(BlockHeader.miner_address.like("%POOL%"))
            .order_by(BlockHeader.height.desc())
            .limit(25)
            .all()
        )
        blocks_rows = [{"height": b.height, "hash": b.hash_hex, "miner": b.miner_address, "timestamp": b.timestamp} for b in blocks]

    tpl = env.get_template("pool.html")
    return tpl.render(
        backend=backend,
        share_diff=share_diff,
        accepted_5m=accepted_5m,
        rejected_5m=rejected_5m,
        total_hashrate=f"{total_hashrate:.0f}",
        miner_count=len(miners),
        miners=miners,
        blocks=blocks_rows,
    )

# ========== DIAGNOSTICS ==========

@app.get("/admin/debug/mempool_dump")
def mempool_dump():
    """
    Return a normalized dump of mempool and confirmed txids for debugging.
    Compare normalization rules to see why rows might not be purged.
    """
    db = get_db()
    with db.session() as s:
        mem = s.query(MempoolTx).all()
        mem_rows = [{
            "txid": (m.txid or ""),
            "txid_norm": (m.txid or "").strip().lower(),
            "from": (m.from_addr or ""),
            "to": (m.to_addr or ""),
            "amount": m.amount,
            "fee": m.fee,
            "added_ms": m.added_ms,
        } for m in mem]

        confirmed = s.query(Transaction).filter(Transaction.in_block_hash.isnot(None)).all()
        conf_rows = [{
            "txid": (t.txid or ""),
            "txid_norm": (t.txid or "").strip().lower(),
            "in_block_hash": t.in_block_hash,
            "fee": t.fee,
        } for t in confirmed]

        # Intersections by normalized txid
        mem_norm = {r["txid_norm"] for r in mem_rows}
        conf_norm = {r["txid_norm"] for r in conf_rows}
        intersection = sorted(mem_norm.intersection(conf_norm))

        return {
            "mempool_count": len(mem_rows),
            "confirmed_count": len(conf_rows),
            "intersection_count": len(intersection),
            "intersection_norm_txids": intersection[:100],
            "mempool_rows": mem_rows[:200],
            "confirmed_rows": conf_rows[:200],
        }


@app.post("/admin/mempool/purge_confirmed")
def purge_confirmed():
    """
    Emergency maintenance endpoint to remove mempool entries whose txid exists
    in transactions with in_block_hash set. This normalizes and purges stragglers.
    """
    db = get_db()
    removed = 0
    with db.session() as s:
        # Build a normalized set of confirmed txids
        confirmed = [t[0] for t in s.query(Transaction.txid).filter(Transaction.in_block_hash.isnot(None)).all()]
        norm_confirmed = { (t or "").strip().lower() for t in confirmed }

        # Iterate mempool and delete matches (normalized)
        mem = s.query(MempoolTx).all()
        for m in mem:
            if (m.txid or "").strip().lower() in norm_confirmed:
                s.delete(m)
                removed += 1
        s.commit()

        # Record diagnostic
        try:
            row = s.get(KV, "diag_last_purge") or KV(k="diag_last_purge", v="")
            row.v = f"removed={removed} at_ms={int(time.time()*1000)}"
            s.merge(row)
            s.commit()
        except Exception:
            pass

    return {"removed": removed}


@app.post("/admin/mempool/purge_unspendable")
def purge_unspendable(min_age_ms: int = 60_000):
    """
    Remove mempool entries that have been in the queue longer than min_age_ms and
    repeatedly fail basic spendability checks (insufficient balance at 'from').
    Intended for dev/ops to clear stuck transactions without inclusion.
    """
    db = get_db()
    now_ms = int(time.time() * 1000)
    removed = 0
    checked = 0
    with db.session() as s:
        mem = s.query(MempoolTx).all()
        for m in mem:
            checked += 1
            age_ok = (m.added_ms is not None) and (now_ms - int(m.added_ms)) >= min_age_ms
            if not age_ok:
                continue

            # Parse lightweight envelope to get from/amount/fee
            try:
                parts = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in (m.raw or "").split(";") if "=" in kv}
                from_addr = (m.from_addr or parts.get("from") or "").strip()
                amount = float(m.amount if m.amount is not None else float(parts.get("amount", 0.0)))
                fee = float(m.fee if m.fee is not None else float(parts.get("fee", 0.0)))
            except Exception:
                # If malformed, consider it unspendable and purge
                s.delete(m)
                removed += 1
                continue

            # Basic spendability: require from_addr non-empty and sufficient unspent funds
            if not from_addr:
                s.delete(m)
                removed += 1
                continue

            bal = s.query(UTXO).with_entities(func.coalesce(func.sum(UTXO.amount), 0.0)).filter_by(address=from_addr, spent=False).scalar()  # type: ignore
            if (bal or 0.0) + 1e-12 < (amount + fee):
                s.delete(m)
                removed += 1

        s.commit()

        # Record diagnostic
        try:
            row = s.get(KV, "diag_last_purge_unspendable") or KV(k="diag_last_purge_unspendable", v="")
            row.v = f"checked={checked} removed={removed} min_age_ms={min_age_ms} at_ms={now_ms}"
            s.merge(row)
            s.commit()
        except Exception:
            pass

    return {"checked": checked, "removed": removed, "min_age_ms": min_age_ms}


def run_explorer():
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    port = int(cfg.get("network.explorer_port", 28448))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_explorer()
