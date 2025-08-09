
import os
import yaml
from typing import Any, Dict

class Config:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, path: str = None) -> "Config":
        cfg_path = path or os.environ.get("SMELLY_CONFIG") or os.path.join("configs", "defaults.yaml")
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data)

    def get(self, key_path: str, default=None):
        parts = key_path.split(".")
        cur = self.data
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur

def save_config(new_data: dict, path: str = None):
    cfg_path = path or os.environ.get("SMELLY_CONFIG") or os.path.join("configs", "defaults.yaml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            current = yaml.safe_load(f) or {}
    except FileNotFoundError:
        current = {}

    if "host" in new_data:
        current.setdefault("network", {})["rpc_host"] = new_data["host"]
    if "port" in new_data:
        current.setdefault("network", {})["rpc_port"] = new_data["port"]
    if "addr" in new_data:
        current.setdefault("miner", {})["default_address"] = new_data["addr"]
    if "threads" in new_data:
        current.setdefault("miner", {})["threads"] = new_data["threads"]

    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(current, f, sort_keys=False)

_global_config: Config | None = None

def get_config() -> Config:
    global _global_config
    if _global_config is None:
        _global_config = Config.load()
    return _global_config
