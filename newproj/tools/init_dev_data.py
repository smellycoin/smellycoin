from __future__ import annotations

import os

from core.utils import ensure_dirs
from core.db import get_db
from core.consensus import add_genesis_if_needed


def main():
    ensure_dirs()
    db = get_db()
    add_genesis_if_needed()
    print("Initialized dev data and ensured genesis exists.")


if __name__ == "__main__":
    main()
