"""端到端：建链、移交、链尾冲突、nonce 重放、查询、完整性校验。"""
from __future__ import annotations

from fastapi.testclient import TestClient

SHA = "f" * 64


def _create_chain(client: TestClient, holder: str = "alice") -> dict:
    r = client.post("/chains", json={"file_sha256": SHA, "first_holder": holder})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_chain_genesis(client: TestClient):
    chain = _create_chain(client)
    assert len(chain["chain_id"]) == 32
    assert chain["seq"] == 0
    assert len(chain["hash"]) == 64


def test_handoff_flow_and_chain_query(client: TestClient):
    chain = _create_chain(client)
    cid, tail = chain["chain_id"], chain["hash"]

    r = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "bob", "nonce": "nonce-0001"},
    )
    assert r.status_code == 201, r.text
    h1 = r.json()
    assert h1["seq"] == 1
    assert h1["giver"] == "alice"
    assert h1["receiver"] == "bob"
    assert h1["prev_hash"] == tail
    assert h1["current_holder"] == "bob"

    r = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": h1["hash"], "receiver": "carol", "nonce": "nonce-0002"},
    )
    assert r.status_code == 201, r.text
    h2 = r.json()
    assert h2["seq"] == 2
    assert h2["giver"] == "bob"

    detail = client.get(f"/chains/{cid}").json()
    assert detail["length"] == 3
    assert detail["current_holder"] == "carol"
    assert detail["tail_hash"] == h2["hash"]
    assert [e["seq"] for e in detail["events"]] == [0, 1, 2]
    assert detail["events"][0]["kind"] == "chain-create"
    assert detail["events"][1]["nonce"] == "nonce-0001"


def test_stale_tail_conflict_does_not_fork(client: TestClient):
    chain = _create_chain(client)
    cid, tail = chain["chain_id"], chain["hash"]

    ok = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "bob", "nonce": "nonce-1001"},
    )
    assert ok.status_code == 201

    # 另一请求仍持有旧的创世哈希 → 409
    stale = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "eve", "nonce": "nonce-1002"},
    )
    assert stale.status_code == 409
    body = stale.json()
    assert body["code"] == "tail_mismatch"
    assert body["current_tail_hash"] == ok.json()["hash"]

    # 冲突不得产生分叉：仍只有 2 个事件（0/1），持有人仍为 bob
    detail = client.get(f"/chains/{cid}").json()
    assert detail["length"] == 2
    assert detail["current_holder"] == "bob"


def test_nonce_replay_conflict(client: TestClient):
    chain = _create_chain(client)
    cid, tail = chain["chain_id"], chain["hash"]

    first = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "bob", "nonce": "dup-nonce"},
    )
    assert first.status_code == 201

    replay = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": first.json()["hash"], "receiver": "carol", "nonce": "dup-nonce"},
    )
    assert replay.status_code == 409
    assert replay.json()["code"] == "nonce_replay"

    # 链未增长
    assert client.get(f"/chains/{cid}").json()["length"] == 2


def test_nonce_replay_with_stale_tail_is_still_conflict(client: TestClient):
    chain = _create_chain(client)
    cid, tail = chain["chain_id"], chain["hash"]
    client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "bob", "nonce": "once-x000"},
    )
    # 同时携带旧链尾 + 已用 nonce：必须是 409（链尾检查先命中）
    r = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "eve", "nonce": "once-x000"},
    )
    assert r.status_code == 409


def test_chain_not_found(client: TestClient):
    assert client.get("/chains/nope").status_code == 404
    assert client.get("/chains/nope/verify").status_code == 404
    r = client.post(
        "/chains/nope/handoffs",
        json={"expected_tail_hash": "a" * 64, "receiver": "bob", "nonce": "n-2000000"},
    )
    assert r.status_code == 404


def test_verify_valid_chain(client: TestClient):
    chain = _create_chain(client)
    cid, tail = chain["chain_id"], chain["hash"]
    h1 = client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": tail, "receiver": "bob", "nonce": "v-000001"},
    ).json()
    client.post(
        f"/chains/{cid}/handoffs",
        json={"expected_tail_hash": h1["hash"], "receiver": "carol", "nonce": "v-000002"},
    )

    result = client.get(f"/chains/{cid}/verify").json()
    assert result["valid"] is True
    assert result["first_broken_seq"] is None
    assert result["length"] == 3


def test_invalid_inputs(client: TestClient):
    r = client.post(
        "/chains", json={"file_sha256": "zzz", "first_holder": "alice"}
    )
    assert r.status_code == 422

    chain = _create_chain(client)
    r = client.post(
        f"/chains/{chain['chain_id']}/handoffs",
        json={"expected_tail_hash": "short", "receiver": "bob", "nonce": "bad-nonce-1"},
    )
    assert r.status_code == 422
    r = client.post(
        f"/chains/{chain['chain_id']}/handoffs",
        json={"expected_tail_hash": chain["hash"], "receiver": "bob", "nonce": "short"},
    )
    assert r.status_code == 422
