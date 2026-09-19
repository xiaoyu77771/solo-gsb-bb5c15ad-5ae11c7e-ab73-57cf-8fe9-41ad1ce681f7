"""SQLite 持久化层。

- WAL 模式 + busy_timeout，降低读写互相阻塞的概率；
- 所有写操作在 ``BEGIN IMMEDIATE`` 事务内执行（``write_tx``），
  配合应用层锁，保证“校验链尾 → 追加”整体原子，不会产生分叉；
- nonce 在同一链条内有 UNIQUE 约束，重放由数据库最终把关。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

DB_PATH = os.environ.get("CHAIN_DB_PATH", "/data/chain.db")

# 串行化所有写事务：配合 BEGIN IMMEDIATE，使链尾检查与追加成为临界区。
_write_lock = threading.Lock()


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path or DB_PATH,
        timeout=30.0,
        isolation_level=None,  # autocommit；事务由代码显式控制
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS chains (
    id            TEXT PRIMARY KEY,
    file_sha256   TEXT NOT NULL,
    first_holder  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    chain_id   TEXT NOT NULL REFERENCES chains(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('chain-create', 'handoff')),
    holder     TEXT NOT NULL,              -- 该事件之后的当前持有人
    prev_hash  TEXT NOT NULL DEFAULT '',
    giver      TEXT NULL,
    receiver   TEXT NULL,
    nonce      TEXT NOT NULL DEFAULT '',
    hash       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (chain_id, seq)
);

-- nonce 在同一链条内不得重放；创世事件 nonce='' 多条不冲突（NULL 语义）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_nonce
    ON events(chain_id, nonce) WHERE nonce <> '';
"""


def init_db(db_path: str | None = None) -> None:
    """初始化（幂等）：创建数据库文件与表结构。"""
    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx() -> Iterator[sqlite3.Connection]:
    """获取一个已持有立即写锁的连接与事务。

    BEGIN IMMEDIATE 立即获取 RESERVED 锁，将“读链尾/查重 + 插入”
    整体串行化；提交或回滚后释放。
    """
    conn = _connect()
    try:
        with _write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
    finally:
        conn.close()
