"""数据库初始化入口：python -m app.init_db"""
from __future__ import annotations

from .database import DB_PATH, init_db


def main() -> None:
    init_db()
    print(f"[init] database initialized at: {DB_PATH}")


if __name__ == "__main__":
    main()
