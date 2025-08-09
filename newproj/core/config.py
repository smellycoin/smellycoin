import os
import yaml
from typing import Any, Dict


class Config:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, path: str = None) -> "Config":
        """
        Load YAML config. Defaults to configs/defaults.yaml, can be overridden with SMELLY_CONFIG env var.
        """
        cfg_path = path or os.environ.get("SMELLY_CONFIG") or os.path.join("configs", "defaults.yaml")
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data)

    def get(self, key_path: str, default=None):
        """
        Get nested config value via dot path, e.g. 'network.rpc_port'
        """
        parts = key_path.split(".")
        cur = self.data
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur


_global_config: Config | None = None


def get_config() -> Config:
    global _global_config
    if _global_config is None:
        _global_config = Config.load()
    return _global_config
