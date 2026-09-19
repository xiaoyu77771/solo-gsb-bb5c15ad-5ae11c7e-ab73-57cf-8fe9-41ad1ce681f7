"""FastAPI 应用：多人文件交接留痕（哈希链）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import service
from .database import init_db
from .schemas import (
    ChainOut,
    CreateChainRequest,
    CreatedChainOut,
    HandoffRequest,
    HandoffResult,
    VerifyResult,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动即初始化（幂等）：单条命令启动时无需手动建库
    init_db()
    yield


app = FastAPI(
    title="文件交接链服务",
    description=(
        "基于 SHA-256 哈希链的多人文件交接留痕服务。"
        "建链提交文件摘要与首位持有人；每次移交须携带当前链尾哈希与一次性随机数，"
        "链尾不匹配或随机数重放返回 409，且绝不产生分叉。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"], summary="健康检查")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/chains",
    response_model=CreatedChainOut,
    status_code=201,
    tags=["chains"],
    summary="创建交接链",
)
def create_chain(req: CreateChainRequest) -> dict:
    return service.create_chain(req.file_sha256, req.first_holder)


@app.post(
    "/chains/{chain_id}/handoffs",
    response_model=HandoffResult,
    status_code=201,
    tags=["chains"],
    summary="移交文件（原子追加）",
    responses={
        404: {"description": "链条不存在"},
        409: {
            "description": "冲突：链尾不匹配或随机数重放（不会产生分叉）",
            "content": {
                "application/json": {
                    "example": {
                        "code": "tail_mismatch",
                        "message": "链尾哈希不匹配",
                        "current_tail_hash": "ab12...",
                    }
                }
            },
        },
    },
)
def add_handoff(chain_id: str, req: HandoffRequest):
    try:
        return service.add_handoff(
            chain_id, req.expected_tail_hash, req.receiver, req.nonce
        )
    except service.ChainNotFound:
        return JSONResponse(status_code=404, content={"code": "chain_not_found"})
    except service.Conflict as exc:
        return JSONResponse(
            status_code=409,
            content={
                "code": exc.code,
                "message": exc.message,
                "current_tail_hash": exc.current_tail_hash,
            },
        )


@app.get(
    "/chains/{chain_id}",
    response_model=ChainOut,
    tags=["chains"],
    summary="查询整条交接链",
)
def get_chain(chain_id: str):
    try:
        return service.get_chain(chain_id)
    except service.ChainNotFound:
        return JSONResponse(status_code=404, content={"code": "chain_not_found"})


@app.get(
    "/chains/{chain_id}/verify",
    response_model=VerifyResult,
    tags=["chains"],
    summary="完整性校验（指出首个断裂序号）",
)
def verify_chain(chain_id: str):
    try:
        return service.verify_chain(chain_id)
    except service.ChainNotFound:
        return JSONResponse(status_code=404, content={"code": "chain_not_found"})
