# 文件交接链服务（File Handover Chain）

基于 **FastAPI + SQLite** 的多人文件交接留痕服务。每次移交经 SHA-256 哈希链
固化为不可抵赖、不可静默篡改的记录；链尾不匹配或随机数重放一律拒绝并返回
`409 Conflict`，**绝不产生分叉**。

---

## 一条命令启动

### 方式 A：Docker Compose（推荐，含依赖安装 + 数据库初始化 + 启动）

```bash
docker compose up --build
```

该命令会自动完成：

1. 构建镜像并安装全部 Python 依赖；
2. 容器入口 `entrypoint.sh` 执行数据库初始化（幂等，自动建表）；
3. 启动 uvicorn 服务。

- **访问地址（本机）：** <http://localhost:8000>
- **监听端口：** `8000`（容器内 `0.0.0.0:8000`，可在 `docker-compose.yml` 的
  `ports` 中修改宿主机映射，如 `"9000:8000"`）
- **数据持久化：** SQLite 数据库位于卷 `chain-data` 挂载的 `/data/chain.db`
  （WAL 模式）。

停止并保留数据：`docker compose down`；连数据一起清除：
`docker compose down -v`。

### 方式 B：不用 Docker（本机 Python 3.11）

```bash
pip install -r requirements.txt
python -m app.init_db          # 初始化数据库 ./data/chain.db
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> 数据库路径由环境变量 `CHAIN_DB_PATH` 控制，默认 `/data/chain.db`。
> 本机裸跑建议：`export CHAIN_DB_PATH=./data/chain.db`（服务启动时也会自动
> 幂等初始化，因此 `init_db` 一步可省略）。

---

## 交互式文档

服务启动后访问：

- Swagger UI：<http://localhost:8000/docs>
- ReDoc：<http://localhost:8000/redoc>
- 健康检查：<http://localhost:8000/health> → `{"status":"ok"}`

---

## 哈希规则

所有哈希输入均为 **键名排序的紧凑 JSON**（`json.dumps(..., sort_keys=True,
separators=(",",":"), ensure_ascii=False)`），再按 UTF-8 字节取 SHA-256
十六进制摘要。

- **创世事件（seq = 0）：**

  ```json
  {"file_sha256":"<64位十六进制摘要>","holder":"<首位持有人>","seq":0}
  ```

- **移交事件（seq ≥ 1）：** 严格包含需求所列五个字段——序号、前序哈希、
  交出方、接收方、一次性随机数：

  ```json
  {"seq":1,"prev_hash":"<上一事件哈希>","giver":"<交出方>",
   "receiver":"<接收方>","nonce":"<一次性随机数>"}
  ```

  - `giver`（交出方）由**服务端根据当前链尾持有人确定**，客户端无法伪造；
  - `prev_hash` 必须等于客户端提交的当前链尾哈希，二者一致后服务端据此追加，
    序号自动取 `链尾序号 + 1`。

### 防分叉 / 防重放如何保证

- 每次移交在一个 `BEGIN IMMEDIATE` 事务内完成「读取链尾 → 比对链尾 →
  检查 nonce → 计算新哈希 → 插入」，事务外加应用级写锁串行化，
  因此并发请求中**只有一个**能成功，其余全部 409，链始终是一条直线；
- `nonce` 在同一链条内有部分唯一索引
  `UNIQUE(chain_id, nonce) WHERE nonce <> ''`，即使出现竞态，重放也会被
  数据库约束最终拦截（整事务回滚，不留任何记录）。

---

## API 一览

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| POST | `/chains` | 创建交接链（提交文件 SHA-256 与首位持有人） |
| POST | `/chains/{chain_id}/handoffs` | 移交文件（携带链尾哈希、接收方、一次性随机数） |
| GET  | `/chains/{chain_id}` | 查询整条链（含全部事件、当前持有人、链尾哈希） |
| GET  | `/chains/{chain_id}/verify` | 完整性校验，指出**首个断裂序号** |
| GET  | `/health` | 健康检查 |

### 1）创建链条

```bash
SHA=$(sha256sum handover.pdf | cut -d' ' -f1)
curl -s -X POST http://localhost:8000/chains \
  -H 'Content-Type: application/json' \
  -d "{\"file_sha256\":\"$SHA\",\"first_holder\":\"alice\"}"
```

响应 `201`：

```json
{"chain_id":"f97d86a4…","seq":0,"holder":"alice",
 "hash":"5dfd78ed…","created_at":"2026-09-19T03:22:19+00:00"}
```

请保存 `chain_id` 与 `hash`（即首个链尾哈希）。

### 2）移交文件

```bash
curl -s -X POST http://localhost:8000/chains/<chain_id>/handoffs \
  -H 'Content-Type: application/json' \
  -d '{"expected_tail_hash":"<当前链尾哈希>",
       "receiver":"bob",
       "nonce":"<一次性随机数，建议至少 16 字节>"}'
```

成功返回 `201`，其中 `giver` 为服务端认定的交出方，新的 `hash` 即下一次
移交要携带的链尾：

```json
{"chain_id":"…","seq":1,"giver":"alice","receiver":"bob",
 "nonce":"…","prev_hash":"5dfd78ed…","hash":"80c31c33…",
 "current_holder":"bob","created_at":"…"}
```

冲突响应（**不写入任何数据**），HTTP 状态码均为 `409`：

```json
{"code":"tail_mismatch",
 "message":"链尾哈希不匹配：链条已被其他移交推进，请基于最新链尾重试",
 "current_tail_hash":"<服务端当前真实链尾，便于客户端重试>"}
```

```json
{"code":"nonce_replay",
 "message":"随机数已在本链条使用过：nonce 一次性有效，禁止重放",
 "current_tail_hash":"…"}
```

> 收到 `tail_mismatch` 后，正确做法是先 `GET /chains/{id}` 拉取最新链尾
> 与当前持有人，再用**全新的** nonce 重试。

### 3）查询链条

```bash
curl -s http://localhost:8000/chains/<chain_id>
```

返回链元信息、`current_holder`、`tail_hash`、`length` 以及按序号升序的
全部事件。

### 4）完整性校验

```bash
curl -s http://localhost:8000/chains/<chain_id>/verify
```

服务端从创世事件开始逐条重算：校验序号连续性、`prev_hash` 首尾衔接、
以及每个事件哈希。正常：

```json
{"chain_id":"…","valid":true,"length":3,
 "first_broken_seq":null,"reason":null,
 "tail_hash":"…","current_holder":"carol"}
```

存在断裂/篡改时（示例：seq=1 的 receiver 被直接改库）：

```json
{"chain_id":"…","valid":false,"length":4,
 "first_broken_seq":1,
 "reason":"事件哈希与按规范重算结果不一致（内容被篡改）",
 "tail_hash":"…","current_holder":"dave"}
```

校验覆盖三类断裂：序号不连续、前序哈希断链、事件内容被篡改；
`first_broken_seq` 即**首个**异常事件的序号。

---

## 运行测试

```bash
pip install -r requirements.txt pytest httpx
python -m pytest -q
```

测试包含：哈希规范向量（用系统 `sha256sum` 可交叉验证）、建链/移交/查询、
链尾过期冲突、nonce 重放、**12 线程并发同链尾竞争（恰有 1 个成功、无分叉）**、
以及直接改库后的断链与篡改检出。

---

## 项目结构

```
.
├── app
│   ├── main.py        # FastAPI 路由与错误映射
│   ├── service.py     # 建链 / 原子移交 / 查询 / 校验
│   ├── database.py    # SQLite 连接、WAL、BEGIN IMMEDIATE 写事务
│   ├── hashing.py     # 规范 JSON 与 SHA-256 哈希规则
│   ├── schemas.py     # Pydantic 请求/响应模型与输入校验
│   └── init_db.py     # 数据库初始化入口
├── tests/             # pytest 测试（哈希向量、API、并发与篡改）
├── entrypoint.sh      # 容器入口：初始化数据库 → 启动服务
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
