"""哈希规范向量测试：键名排序的紧凑 JSON + SHA-256。"""
from __future__ import annotations

import hashlib

from app.hashing import canonical_json, genesis_payload, handoff_payload, sha256_hex

SHA = "a" * 64


def test_canonical_json_sorts_keys_and_is_compact():
    assert canonical_json({"b": 1, "a": "x"}) == '{"a":"x","b":1}'
    # 无空格、保留中文
    assert canonical_json({"c": "张三", "a": 1}) == '{"a":1,"c":"张三"}'


def test_genesis_vector():
    payload = genesis_payload(SHA, "alice", 0)
    assert payload == '{"file_sha256":"%s","holder":"alice","seq":0}' % SHA
    assert sha256_hex(payload) == hashlib.sha256(payload.encode()).hexdigest()


def test_handoff_vector_sorted_keys():
    payload = handoff_payload(
        seq=1,
        prev_hash="b" * 64,
        giver="alice",
        receiver="bob",
        nonce="n-001",
    )
    assert payload == (
        '{"giver":"alice","nonce":"n-001","prev_hash":"%s","receiver":"bob","seq":1}'
        % ("b" * 64)
    )
    digest = sha256_hex(payload)
    assert len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)
