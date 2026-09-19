FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CHAIN_DB_PATH=/data/chain.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh && mkdir -p /data

EXPOSE 8000

# 一条命令即完成：依赖已安装 -> entrypoint 初始化数据库 -> 启动服务
ENTRYPOINT ["./entrypoint.sh"]
