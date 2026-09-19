"""核心业务逻辑：建链、原子移交、查询、完整性校验。"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from . import hashing
from .database import get_conn, write_tx


class ChainNotFound(Exception):
    pass


class Conflict(Exception):
    """409 冲突：链尾不匹配或 nonce 重放。"""

    def __init__(self, code: str, message: str, *, current_tail_hash: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_tail_hash = current_tail_hash


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_event(row: sqlite3.Row) -> dict:
    return {
        "seq": row["seq"],
        "kind": row["kind"],
        "holder": row["holder"],
        "prev_hash": row["prev_hash"],
        "giver": row["giver"],
        "receiver": row["receiver"],
        "nonce": row["nonce"],
        "hash": row["hash"],
        "created_at": row["created_at"],
    }


def create_chain(file_sha256: str, first_holder: str) -> dict:
    chain_id = uuid.uuid4().hex
    created_at = _now()
    # 创世事件：seq=0，记录文件摘要与首位持有人
    genesis_hash = hashing.hash_genesis(file_sha256, first_holder, seq=0)

    with write_tx() as conn:
        conn.execute(
            "INSERT INTO chains (id, file_sha256, first_holder, created_at)"
            " VALUES (?, ?, ?, ?)",
            (chain_id, file_sha256, first_holder, created_at),
        )
        conn.execute(
            "INSERT INTO events (chain_id, seq, kind, holder, prev_hash, giver,"
            " receiver, nonce, hash, created_at)"
            " VALUES (?, 0, 'chain-create', ?, '', NULL, NULL, '', ?, ?)",
            (chain_id, first_holder, genesis_hash, created_at),
        )

    return {
        "chain_id": chain_id,
        "seq": 0,
        "holder": first_holder,
        "hash": genesis_hash,
        "created_at": created_at,
    }


def add_handoff(chain_id: str, expected_tail_hash: str, receiver: str, nonce: str) -> dict:
    """在写锁事务内完成：取链尾 -> 校验 -> 计算新哈希 -> 原子追加。"""
    with write_tx() as conn:
        chain = conn.execute(
            "SELECT id FROM chains WHERE id = ?", (chain_id,)
        ).fetchone()
        if chain is None:
            raise ChainNotFound(chain_id)

        tail = conn.execute(
            "SELECT seq, holder, hash FROM events"
            " WHERE chain_id = ? ORDER BY seq DESC LIMIT 1",
            (chain_id,),
        ).fetchone()
        if tail is None:  # 理论上不会发生：建链即有创世事件
            raise Conflict("empty_chain", "链条为空，无法移交", current_tail_hash="")

        current_tail_hash = tail["hash"]
        current_holder = tail["holder"]
        next_seq = tail["seq"] + 1

        # 1) 链尾不匹配 → 冲突，拒绝追加（绝不产生分叉）
        if current_tail_hash != expected_tail_hash:
            raise Conflict(
                "tail_mismatch",
                "链尾哈希不匹配：链条已被其他移交推进，请基于最新链尾重试",
                current_tail_hash=current_tail_hash,
            )

        # 2) nonce 重放 → 冲突（唯一索引兜底，这里先给出明确错误）
        reused = conn.execute(
            "SELECT 1 FROM events WHERE chain_id = ? AND nonce = ? LIMIT 1",
            (chain_id, nonce),
        ).fetchone()
        if reused is not None:
            raise Conflict(
                "nonce_replay",
                "随机数已在本链条使用过：nonce 一次性有效，禁止重放",
                current_tail_hash=current_tail_hash,
            )

        created_at = _now()
        new_hash = hashing.hash_handoff(
            seq=next_seq,
            prev_hash=current_tail_hash,
            giver=current_holder,
            receiver=receiver,
            nonce=nonce,
        )
        try:
            conn.execute(
                "INSERT INTO events (chain_id, seq, kind, holder, prev_hash, giver,"
                " receiver, nonce, hash, created_at)"
                " VALUES (?, ?, 'handoff', ?, ?, ?, ?, ?, ?, ?)",
                (
                    chain_id,
                    next_seq,
                    receiver,           # 移交后持有人变为接收方
                    current_tail_hash,
                    current_holder,     # 交出方 = 当前链尾持有人（服务端确定）
                    receiver,
                    nonce,
                    new_hash,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:  # 竞态下由约束最终拦截
            raise Conflict(
                "nonce_replay",
                "随机数重放冲突",
                current_tail_hash=current_tail_hash,
            ) from exc

    return {
        "chain_id": chain_id,
        "seq": next_seq,
        "giver": current_holder,
        "receiver": receiver,
        "nonce": nonce,
        "prev_hash": current_tail_hash,
        "hash": new_hash,
        "current_holder": receiver,
        "created_at": created_at,
    }


def get_chain(chain_id: str) -> dict:
    with get_conn() as conn:
        chain = conn.execute(
            "SELECT * FROM chains WHERE id = ?", (chain_id,)
        ).fetchone()
        if chain is None:
            raise ChainNotFound(chain_id)

        rows = conn.execute(
            "SELECT * FROM events WHERE chain_id = ? ORDER BY seq ASC", (chain_id,)
        ).fetchall()

    events = [_row_to_event(r) for r in rows]
    return {
        "chain_id": chain["id"],
        "file_sha256": chain["file_sha256"],
        "first_holder": chain["first_holder"],
        "current_holder": events[-1]["holder"],
        "tail_hash": events[-1]["hash"],
        "length": len(events),
        "created_at": chain["created_at"],
        "events": events,
    }


def verify_chain(chain_id: str) -> dict:
    """重放整条链并校验，返回首个断裂序号；无断裂时 valid=True。"""
    with get_conn() as conn:
        chain = conn.execute(
            "SELECT * FROM chains WHERE id = ?", (chain_id,)
        ).fetchone()
        if chain is None:
            raise ChainNotFound(chain_id)
        rows = conn.execute(
            "SELECT * FROM events WHERE chain_id = ? ORDER BY seq ASC", (chain_id,)
        ).fetchall()

    if not rows:
        return {
            "chain_id": chain_id,
            "valid": False,
            "length": 0,
            "first_broken_seq": 0,
            "reason": "链条为空：缺少创世事件",
            "tail_hash": None,
            "current_holder": None,
        }

    prev_hash = ""
    for expected_seq, row in enumerate(rows):
        seq = row["seq"]

        # 序号必须从 0 起连续递增
        if seq != expected_seq:
            return {
                "chain_id": chain_id,
                "valid": False,
                "length": len(rows),
                "first_broken_seq": expected_seq,
                "reason": f"序号断裂：期望 {expected_seq}，实际 {seq}",
                "tail_hash": rows[-1]["hash"],
                "current_holder": rows[-1]["holder"],
            }

        # prev_hash 必须与前一事件哈希首尾相接
        if row["prev_hash"] != prev_hash:
            return {
                "chain_id": chain_id,
                "valid": False,
                "length": len(rows),
                "first_broken_seq": seq,
                "reason": "前序哈希与上一事件哈希不一致（链断裂或被篡改）",
                "tail_hash": rows[-1]["hash"],
                "current_holder": rows[-1]["holder"],
            }

        # 重新计算哈希并比对
        if seq == 0:
            if row["kind"] != "chain-create":
                return {
                    "chain_id": chain_id,
                    "valid": False,
                    "length": len(rows),
                    "first_broken_seq": 0,
                    "reason": "序号 0 必须是创世事件",
                    "tail_hash": rows[-1]["hash"],
                    "current_holder": rows[-1]["holder"],
                }
            expected_hash = hashing.hash_genesis(
                chain["file_sha256"], chain["first_holder"], seq=0
            )
            if row["holder"] != chain["first_holder"]:
                return {
                    "chain_id": chain_id,
                    "valid": False,
                    "length": len(rows),
                    "first_broken_seq": 0,
                    "reason": "创世事件持有人与首位持有人不一致",
                    "tail_hash": rows[-1]["hash"],
                    "current_holder": rows[-1]["holder"],
                }
        else:
            expected_hash = hashing.hash_handoff(
                seq=seq,
                prev_hash=row["prev_hash"],
                giver=row["giver"] or "",
                receiver=row["receiver"] or "",
                nonce=row["nonce"],
            )

        if row["hash"] != expected_hash:
            return {
                "chain_id": chain_id,
                "valid": False,
                "length": len(rows),
                "first_broken_seq": seq,
                "reason": "事件哈希与按规范重算结果不一致（内容被篡改）",
                "tail_hash": rows[-1]["hash"],
                "current_holder": rows[-1]["holder"],
            }

        prev_hash = row["hash"]

    return {
        "chain_id": chain_id,
        "valid": True,
        "length": len(rows),
        "first_broken_seq": None,
        "reason": None,
        "tail_hash": rows[-1]["hash"],
        "current_holder": rows[-1]["holder"],
    }
