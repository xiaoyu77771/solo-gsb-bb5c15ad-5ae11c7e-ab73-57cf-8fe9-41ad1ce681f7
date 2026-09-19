"""并发竞争与篡改检测：证明原子追加不分叉、校验能指出首个断裂序号。"""
from __future__ import annotations

import sqlite3
import threading

from fastapi.testclient import TestClient

from app.database import DB_PATH
from app.hashing import hash_handoff

SHA = "c" * 64


def test_concurrent_same_tail_only_one_succeeds(client: TestClient):
    cid_resp = client.post(
        "/chains", json={"file_sha256": SHA, "first_holder": "alice"}
    ).json()
    cid, tail = cid_resp["chain_id"], cid_resp["hash"]

    results: list[int] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        # 所有线程都基于同一个（创世）链尾提交
        r = client.post(
            f"/chains/{cid}/handoffs",
            json={
                "expected_tail_hash": tail,
                "receiver": f"user-{i}",
                "nonce": f"concurrent-nonce-{i:04d}",
            },
        )
        with lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(201) == 1, results
    assert results.count(409) == 11

    detail = client.get(f"/chains/{cid}").json()
    # 创世 + 恰好 1 个成功的移交 → 无分叉
    assert detail["length"] == 2
    verify = client.get(f"/chains/{cid}/verify").json()
    assert verify["valid"] is True


def _tamper(db_path: str, chain_id: str, seq: int, sql: str, params: tuple) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_verify_detects_tampered_receiver_and_reports_first_broken_seq(client: TestClient):
    cid_resp = client.post(
        "/chains", json={"file_sha256": SHA, "first_holder": "alice"}
    ).json()
    cid, tail = cid_resp["chain_id"], cid_resp["hash"]

    h1 = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "bob", "nonce": "tamper-001"},
    ).json()
    client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": h1["hash"], "receiver": "carol", "nonce": "tamper-002"},
    )

    # 篡改 seq=1 事件的 receiver，但保留原 hash → seq=1 重算哈希必然不符
    _tamper(
        DB_PATH,
        cid,
        1,
        "UPDATE events SET receiver='mallory' WHERE chain_id=? AND seq=?",
        (cid, 1),
    )

    result = client.get(f"/chains/{cid}/verify").json()
    assert result["valid"] is False
    assert result["first_broken_seq"] == 1


def test_verify_detects_broken_link(client: TestClient):
    cid_resp = client.post(
        "/chains", json={"file_sha256": SHA, "first_holder": "alice"}
    ).json()
    cid, tail = cid_resp["chain_id"], cid_resp["hash"]

    h1 = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "bob", "nonce": "link-0001"},
    ).json()
    client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": h1["hash"], "receiver": "carol", "nonce": "link-0002"},
    )

    # 仅替换 seq=2 的 prev_hash 并据此重算 hash：哈希自洽但与 seq=1 断链
    fake_prev = "0" * 64
    forged = hash_handoff(2, fake_prev, "bob", "carol", "link-0002")
    _tamper(
        DB_PATH,
        cid,
        2,
        "UPDATE events SET prev_hash=?, hash=? WHERE chain_id=? AND seq=?",
        (fake_prev, forged, cid, 2),
    )

    result = client.get(f"/chains/{cid}/verify").json()
    assert result["valid"] is False
    assert result["first_broken_seq"] == 2
    assert "前序哈希" in result["reason"]
