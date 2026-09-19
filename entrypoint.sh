#!/bin/sh
# 容器入口：先初始化数据库（幂等），再启动服务。
set -e

echo "[entrypoint] initializing database ..."
python -m app.init_db

echo "[entrypoint] starting uvicorn on 0.0.0.0:8000 ..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
