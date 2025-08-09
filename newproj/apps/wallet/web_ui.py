from __future__ import annotations

import os
from typing import List

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
import uvicorn
import requests

from core.config import get_config
from core.utils import ensure_dirs

# Simple server-side rendered web wallet with yellow/black theme

app = FastAPI(title="SMELLY Web Wallet", version="0.1")

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
    # CSS
    css = """
:root {
  --primary: #FFD000; /* yellow */
  --secondary: #111111; /* black */
  --bg: #0b0b0b;
  --text: #f1f1f1;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0;
  font-family: Segoe UI, Roboto, Arial, sans-serif;
  background: var(--bg); color: var(--text);
}
.header {
  background: var(--secondary);
  color: var(--primary);
  padding: 16px 20px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.container { padding: 20px; max-width: 900px; margin: 0 auto; }
.card {
  background: #161616; border: 1px solid #222; border-radius: 8px;
  padding: 16px; margin-bottom: 16px;
}
h1,h2,h3 { color: var(--primary); margin: 0 0 12px 0; }
label { display: block; margin: 8px 0 6px; color: #ddd; }
input, select {
  width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #333;
  background: #0f0f0f; color: #eee;
}
button {
  background: var(--primary); color: #000; border: none; padding: 10px 16px;
  border-radius: 6px; cursor: pointer; font-weight: 700; margin-top: 10px;
}
.addr { font-family: Consolas, monospace; word-break: break-all; }
.table { width: 100%; border-collapse: collapse; }
.table th, .table td { border-bottom: 1px solid #2a2a2a; padding: 8px; text-align: left; }
.notice { color: #aaa; font-size: 12px; }
"""
    with open(os.path.join(STATIC_DIR, "style.css"), "w", encoding="utf-8") as f:
        f.write(css)

    # Templates
    index_html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>SMELLY Web Wallet</title>
  <link rel="stylesheet" href="/static/style.css"/>
</head>
<body>
  <div class="header">SMELLY Web Wallet</div>
  <div class="container">
    <div class="card">
      <h2>Create Wallet</h2>
      <form method="post" action="/create">
        <label>Name</label>
        <input name="name" value="Main"/>
        <label>Language</label>
        <select name="language">
          <option>english</option>
        </select>
        <button type="submit">Create</button>
      </form>
      <div class="notice">Mnemonic will be shown once. Store securely.</div>
    </div>

    <div class="card">
      <h2>Restore Wallet</h2>
      <form method="post" action="/restore">
        <label>Name</label>
        <input name="name" value="Restored"/>
        <label>Mnemonic</label>
        <input name="mnemonic" placeholder="word1 word2 ..."/>
        <button type="submit">Restore</button>
      </form>
    </div>

    {% if account %}
    <div class="card">
      <h2>Account</h2>
      <div>ID: {{account.id}}</div>
      <div>Name: {{account.name}}</div>
      {% if primary %}
      <div>Primary Address:</div>
      <div class="addr">{{primary.address}}</div>
      <div>Balance: {{primary.balance}}</div>
      {% endif %}
      <form method="post" action="/new_subaddress">
        <input type="hidden" name="account_id" value="{{account.id}}"/>
        <label>Major</label>
        <input name="major" value="0"/>
        <label>Minor</label>
        <input name="minor" value="1"/>
        <label>Label</label>
        <input name="label" value=""/>
        <button type="submit">Create subaddress</button>
      </form>
    </div>

    <div class="card">
      <h2>Subaddresses</h2>
      <table class="table">
        <thead><tr><th>ID</th><th>Index</th><th>Label</th><th>Address</th></tr></thead>
        <tbody>
          {% for s in subaddresses %}
          <tr>
            <td>{{s.id}}</td>
            <td>{{s.major}}/{{s.minor}}</td>
            <td>{{s.label}}</td>
            <td class="addr">{{s.address}}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}
  </div>
</body>
</html>
"""
    with open(os.path.join(TEMPLATES_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)


@app.on_event("startup")
def on_startup():
    ensure_dirs()
    write_default_assets()


def wallet_backend_url() -> str:
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    # Backend stays on web_wallet_port (now default 28450 in configs)
    port = int(cfg.get("network.web_wallet_port", 28450))
    return f"http://{host}:{port}".rstrip("/")


def backend_get_accounts():
    r = requests.get(f"{wallet_backend_url()}/wallet/accounts", timeout=5)
    r.raise_for_status()
    return r.json()


def backend_create_wallet(name: str, language: str):
    r = requests.post(f"{wallet_backend_url()}/wallet/create", json={"name": name, "language": language}, timeout=20)
    r.raise_for_status()
    return r.json()


def backend_restore_wallet(name: str, mnemonic: str):
    r = requests.post(f"{wallet_backend_url()}/wallet/restore", json={"name": name, "mnemonic": mnemonic}, timeout=20)
    r.raise_for_status()
    return r.json()


def backend_list_subaddresses(account_id: int):
    r = requests.get(f"{wallet_backend_url()}/wallet/{account_id}/subaddresses", timeout=10)
    r.raise_for_status()
    return r.json()


def backend_new_subaddress(account_id: int, major: int, minor: int, label: str):
    r = requests.post(f"{wallet_backend_url()}/wallet/new_subaddress", json={
        "account_id": account_id, "major": major, "minor": minor, "label": label
    }, timeout=10)
    r.raise_for_status()
    return r.json()


def backend_balance(address: str):
    r = requests.get(f"{wallet_backend_url()}/wallet/{address}/balance", timeout=5)
    r.raise_for_status()
    return r.json()


@app.get("/", response_class=HTMLResponse)
def index():
    # Show first account if exists
    accounts = []
    try:
        accounts = backend_get_accounts()
    except Exception:
        pass

    account = accounts[0] if accounts else None
    subaddresses = []
    primary_summary = None
    if account:
        subaddresses = backend_list_subaddresses(account["id"])
        primary = next((s for s in subaddresses if s["major"] == 0 and s["minor"] == 0), None)
        if primary:
            bal = backend_balance(primary["address"])
            primary_summary = {"address": primary["address"], "balance": bal.get("balance", 0.0)}

    template = env.get_template("index.html")
    return template.render(account=account, subaddresses=subaddresses, primary=primary_summary)


@app.post("/create")
def create(name: str = Form("Main"), language: str = Form("english")):
    try:
        backend_create_wallet(name, language)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url="/", status_code=303)


@app.post("/restore")
def restore(name: str = Form("Restored"), mnemonic: str = Form(...)):
    try:
        backend_restore_wallet(name, mnemonic)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url="/", status_code=303)


@app.post("/new_subaddress")
def new_subaddress(account_id: int = Form(...), major: int = Form(...), minor: int = Form(...), label: str = Form("")):
    try:
        backend_new_subaddress(int(account_id), int(major), int(minor), label)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url="/", status_code=303)


def run_web_wallet():
    # Serve UI on web_wallet_port to avoid conflicts with the Explorer (which uses explorer_port)
    cfg = get_config()
    host = cfg.get("network.rpc_host", "127.0.0.1")
    port = int(cfg.get("network.web_wallet_port", 28450))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_web_wallet()
