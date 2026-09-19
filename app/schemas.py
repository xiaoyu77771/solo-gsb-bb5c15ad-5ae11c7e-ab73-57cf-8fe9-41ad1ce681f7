"""请求/响应模型（Pydantic v2）。"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


class CreateChainRequest(BaseModel):
    file_sha256: str = Field(..., description="文件的 SHA-256 摘要（64 位十六进制）")
    first_holder: str = Field(..., min_length=1, max_length=200, description="首位持有人标识")

    @field_validator("file_sha256")
    @classmethod
    def _check_sha256(cls, v: str) -> str:
        if not SHA256_RE.fullmatch(v.strip()):
            raise ValueError("file_sha256 必须是 64 位十六进制字符")
        return v.strip().lower()


class HandoffRequest(BaseModel):
    expected_tail_hash: str = Field(..., description="客户端持有的当前链尾哈希")
    receiver: str = Field(..., min_length=1, max_length=200, description="接收方标识")
    nonce: str = Field(
        ..., min_length=8, max_length=200, description="一次性随机数（同链内不可重放）"
    )

    @field_validator("expected_tail_hash")
    @classmethod
    def _check_hash(cls, v: str) -> str:
        if not HASH_RE.fullmatch(v.strip()):
            raise ValueError("expected_tail_hash 必须是 64 位十六进制字符")
        return v.strip().lower()

    @field_validator("receiver")
    @classmethod
    def _strip_receiver(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("receiver 不能为空")
        return v

    @field_validator("nonce")
    @classmethod
    def _check_nonce(cls, v: str) -> str:
        v = v.strip()
        if not NONCE_RE.fullmatch(v):
            raise ValueError("nonce 仅允许字母、数字以及 . _ - 字符")
        return v


class EventOut(BaseModel):
    seq: int
    kind: Literal["chain-create", "handoff"]
    holder: str
    prev_hash: str
    giver: str | None
    receiver: str | None
    nonce: str
    hash: str
    created_at: str


class ChainOut(BaseModel):
    chain_id: str
    file_sha256: str
    first_holder: str
    current_holder: str
    tail_hash: str
    length: int
    created_at: str
    events: list[EventOut]


class CreatedChainOut(BaseModel):
    chain_id: str
    seq: int
    holder: str
    hash: str
    created_at: str


class HandoffResult(BaseModel):
    chain_id: str
    seq: int
    giver: str
    receiver: str
    nonce: str
    prev_hash: str
    hash: str
    current_holder: str
    created_at: str


class VerifyResult(BaseModel):
    chain_id: str
    valid: bool
    length: int
    first_broken_seq: int | None = None
    reason: str | None = None
    tail_hash: str | None = None
    current_holder: str | None = None
