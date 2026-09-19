"""测试用共享夹具。"""
from __future__ import annotations

import os
import tempfile

import pytest

# 在导入应用之前指定独立测试数据库
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["CHAIN_DB_PATH"] = _tmp.name

from fastapi.testclient import TestClient  # noqa: E402

from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    init_db()
    with TestClient(app) as c:
        yield c
