# 跨数据库通用查询服务（基于 SQLAlchemy Core）

一个安全的 HTTP 服务，通过结构化 JSON 描述查询意图，后端自动转换为对应数据库的 SQL，无需手写 SQL，即可无缝切换 SQLite、PostgreSQL、MySQL 等数据库。

## 功能特点

- **跨数据库**：基于 SQLAlchemy Core 表达式语言，一套接口适配多种数据库
- **零 SQL 注入**：所有条件、值均通过 SQLAlchemy 参数绑定处理
- **统一 JSON 接口**：`action` + `table` + `conditions` 模式
- **支持增删改查**：select, insert, update, delete 全覆盖
- **单线程本地监听**：waitress + 127.0.0.1:8005，简单可靠

## 快速开始

### 安装依赖
```bash
pip install flask sqlalchemy waitress
# 根据需要安装数据库驱动，例如：
# pip install psycopg2-binary   # PostgreSQL
# pip install pymysql            # MySQL
```

### 启动服务（默认 SQLite）
```bash
python app.py
```
服务运行在 `http://127.0.0.1:8005`。

使用其他数据库：
```bash
export DATABASE_URL="postgresql://user:pass@localhost/dbname"
python app.py
```

**注意**：新接口依赖表已存在（通过反射获取结构）。首次使用时请先手动建表：
```bash
sqlite3 database.db "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"
```

## API 文档

### 请求格式

**端点**：`POST /execute`  
**Content-Type**：`application/json`

```json
{
  "action": "操作类型",       // select | insert | update | delete
  "table": "表名",
  "columns": ["列1", "列2"], // select/insert 时可选
  "values": {                 // insert/update 时必填
    "列名": "值"
  },
  "where": {                  // 条件，支持组合操作符
    "age": {"ge": 18, "lt": 60},
    "name": "Alice"
  },
  "order_by": [{"column": "age", "desc": true}],
  "limit": 10,
  "offset": 0
}
```

### where 条件支持的运算符

| 运算符 | 说明 | 示例 |
|--------|------|------|
| 直接值 | 等于 | `"name": "Alice"` |
| eq | 等于 | `"age": {"eq": 30}` |
| ne | 不等于 | `"status": {"ne": "deleted"}` |
| gt | 大于 | `"score": {"gt": 80}` |
| ge | 大于等于 | `"score": {"ge": 60}` |
| lt | 小于 | `"price": {"lt": 100}` |
| le | 小于等于 | `"price": {"le": 200}` |
| like | 模糊匹配 | `"name": {"like": "%Smith%"}` |
| in | 包含于列表 | `"id": {"in": [1,2,3]}` |

多个条件自动使用 **AND** 连接。

### 响应格式

成功：
```json
{
  "success": true,
  "data": [                  // 查询结果数组（insert/update/delete 时包含影响行数或 lastrowid）
    {"id": 1, "name": "Alice"}
  ]
}
```

失败：
```json
{
  "success": false,
  "error": "错误描述"
}
```

## 使用示例（curl）

**插入**：
```bash
curl -X POST http://127.0.0.1:8005/execute \
  -H "Content-Type: application/json" \
  -d '{"action":"insert","table":"users","values":{"name":"Bob","age":25}}'
```

**查询**：
```bash
curl -X POST http://127.0.0.1:8005/execute \
  -H "Content-Type: application/json" \
  -d '{"action":"select","table":"users","where":{"age":{"gt":20}},"order_by":[{"column":"id","desc":true}],"limit":5}'
```

**更新**：
```bash
curl -X POST http://127.0.0.1:8005/execute \
  -H "Content-Type: application/json" \
  -d '{"action":"update","table":"users","values":{"age":26},"where":{"name":"Bob"}}'
```

**删除**：
```bash
curl -X POST http://127.0.0.1:8005/execute \
  -H "Content-Type: application/json" \
  -d '{"action":"delete","table":"users","where":{"id":{"in":[1,2]}}}'
```

## 安全机制

- 强制使用 SQLAlchemy 参数绑定，杜绝 SQL 注入。
- 仅反射已有表，无法创建或删除表（保证结构安全）。
- 所有异常自动回滚事务。
- 默认监听 `127.0.0.1`，仅本机可访问。

## 内置日志模块

db 服务内置了统一日志收集能力，各业务服务通过 HTTP 推送日志到 `/log/*` 系列接口，db 自身的操作日志通过 `log_internal()` 直接写入（同进程，无 HTTP 往返，无循环依赖）。

### 为什么集成在 db 而不是独立服务

独立 logs 服务会导致**循环调用**：db 记日志→调 logs，logs 存库→调 db，无限递归。集成在 db 内部后，db 自己记日志直接写表，彻底消除循环依赖。

### 日志表

```sql
CREATE TABLE IF NOT EXISTS Logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,
    service   TEXT    NOT NULL,
    level     TEXT    NOT NULL,
    message   TEXT    NOT NULL,
    trace_id  TEXT,
    extra     TEXT
);
```

- `service`：来源服务名（`waf`/`gateway`/`static`/`dnfs`/`db`/`ai_review`/`captcha`/`core`）
- `level`：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`
- `trace_id`：可选，请求追踪 ID
- `extra`：可选，JSON 字符串，存放附加结构化字段

### 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/log/ingest` | POST | 推送日志（支持批量） |
| `/log/query` | POST | 查询日志（按服务/级别/时间/关键字过滤） |
| `/log/stats` | POST | 统计聚合（按 service/level 分组计数） |
| `/log/purge` | POST | 清理过期日志 |
| `/log/health` | GET | 健康检查 |

所有接口的响应格式与 `/execute` 统一：`{success, data, error}`。

#### POST /log/ingest

```json
请求:
{
  "logs": [
    {
      "service": "dnfs",
      "level": "INFO",
      "message": "节点新增成功 id=42",
      "ts": "2026-07-25T08:30:00.123Z",
      "trace_id": "req-abc-123",
      "extra": {"path": "/add_note", "status": 200}
    }
  ]
}

响应:
{"success": true, "data": {"inserted": 1}}
```

- `ts` 可选，缺省自动填当前 UTC 时间
- 单次最多 100 条
- `message` 上限 8KB，`extra` 上限 4KB

#### POST /log/query

```json
请求:
{
  "service": "dnfs",          // 可选，支持数组匹配多个
  "level": "WARNING",         // 可选，支持数组匹配多个
  "since": "2026-07-25T00:00:00Z",  // 可选
  "until": "2026-07-25T23:59:59Z",  // 可选
  "keyword": "节点",           // 可选，message 模糊搜索
  "trace_id": "req-abc-123",   // 可选
  "order_by": "ts",            // 默认 ts，可选 id/service/level
  "desc": true,                // 默认 true（最新在前）
  "limit": 50,                 // 默认 100，上限 1000
  "offset": 0                  // 分页偏移
}

响应:
{
  "success": true,
  "data": {
    "total": 128,
    "logs": [{"id":1024,"ts":"...","service":"dnfs","level":"INFO","message":"...","trace_id":"...","extra":{...}}]
  }
}
```

#### POST /log/stats

```json
请求:
{"since":"...","until":"...","group_by":"service"}

响应:
{
  "success": true,
  "data": {
    "groups": [{"service":"dnfs","level":"INFO","count":320}],
    "total": 325
  }
}
```

`group_by` 支持 `service` / `level` / `service,level`。

#### POST /log/purge

```json
请求: {"before": "2026-07-18T00:00:00Z"}
响应: {"success": true, "data": {"deleted": 1523}}
```

#### GET /log/health

```json
{"success": true, "data": {"status": "ok", "count": 10240}}
```

### 自动清理

- 服务启动时开启后台守护线程，每 1 小时清理一次
- 默认保留 30 天日志，超期自动删除
- 日志总条数超过 100 万时，按时间倒序保留最新 N 条

### 各服务接入方式

把 `db/log_client.py` 复制到各服务目录（或加入 `PYTHONPATH`），即可使用：

```python
from log_client import logs

# 替换原来的 print
logs.info("dnfs", "节点新增成功 id=42")
logs.error("dnfs", "数据库连接失败", extra={"errno": 2002})

# 带追踪 ID
logs.push("gateway", "WARNING", "登录失败次数超限",
          trace_id="req-abc", extra={"ip": "1.2.3.4"})
```

特性：
- 使用 `urllib.request`（标准库，不引入 requests）
- 异步非阻塞：内存队列 + 后台线程批量推送
- fail-safe：db 服务不可用时静默丢弃，绝不影响业务
- 缓冲批量：队列满 50 条或超时 2 秒时自动 flush

### 防递归设计

| 场景 | 调用路径 | 是否安全 |
|------|---------|---------|
| 外部服务记日志 | `服务 → POST /log/ingest → 写 Logs 表` | 安全 |
| db 自己记日志 | `/execute → log_internal() → 直接写 Logs 表` | 安全（无 HTTP） |
| /log/ingest 成功 | `写入成功 → 直接返回` | 安全（不再记日志） |
| /log/ingest 失败 | `写入失败 → log_internal() → 直接写表` | 安全（log_internal 不回调 ingest） |

## 切换数据库

只需修改环境变量 `DATABASE_URL`，并安装对应驱动：

- SQLite：`sqlite:///database.db`
- PostgreSQL：`postgresql://user:pass@localhost/dbname` （需 `pip install psycopg2`）
- MySQL：`mysql+pymysql://user:pass@localhost/dbname` （需 `pip install pymysql`）

SQLAlchemy 会自动生成适配各数据库的 SQL 方言，无需修改接口调用。

