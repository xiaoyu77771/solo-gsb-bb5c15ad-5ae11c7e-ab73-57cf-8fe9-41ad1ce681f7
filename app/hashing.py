"""交接链哈希计算。

两类事件，均以“键名排序的紧凑 JSON”（UTF-8、无空白、无 ASCII 转义）
作为 SHA-256 的输入：

1. 建链（创世）事件，序号 0，字段：
   {"seq": 0, "file_sha256": <文件摘要>, "holder": <首位持有人>}

2. 移交事件，序号从 1 起，字段严格为需求所列五项：
   {"seq": <序号>, "prev_hash": <链尾哈希>, "giver": <交出方>,
    "receiver": <接收方>, "nonce": <一次性随机数>}
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """按键名排序、无空白的紧凑 JSON。ensure_ascii=False，按 UTF-8 编码。"""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def genesis_payload(file_sha256: str, first_holder: str, seq: int = 0) -> str:
    obj = {
        "file_sha256": file_sha256.lower(),
        "holder": first_holder,
        "seq": seq,
    }
    return canonical_json(obj)


def handoff_payload(
    seq: int,
    prev_hash: str,
    giver: str,
    receiver: str,
    nonce: str,
) -> str:
    obj = {
        "seq": seq,
        "prev_hash": prev_hash,
        "giver": giver,
        "receiver": receiver,
        "nonce": nonce,
    }
    return canonical_json(obj)


def hash_genesis(file_sha256: str, first_holder: str, seq: int = 0) -> str:
    return sha256_hex(genesis_payload(file_sha256, first_holder, seq))


def hash_handoff(
    seq: int,
    prev_hash: str,
    giver: str,
    receiver: str,
    nonce: str,
) -> str:
    return sha256_hex(handoff_payload(seq, prev_hash, giver, receiver, nonce))
