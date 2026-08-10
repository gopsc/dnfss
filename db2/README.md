# db2 — C++20 数据库 RPC 服务（libmsg + SQLite）

db 模块的 C++20 重实现，接口与 Python 版完全兼容：相同的 `execute` 路由、相同的请求/响应格式、相同的 `where` 语法。gateway 的 `DbC::call_msg()` 以及所有使用 pymsg `execute` 路由的调用方**零改动**即可切换。

## 与 Python db 的关系

| 特性 | db（Python） | db2（C++） |
|------|-------------|-----------|
| 传输协议 | pymsg TCP 帧 | pymsg TCP 帧（libmsg） |
| `execute` 路由接口 | ✅ | ✅ 完全对齐 |
| where 运算符 | ✅ | ✅ 完全对齐 |
| 响应格式 | ✅ | ✅ 完全对齐 |
| `$now` 哨兵值 | ✅ | ✅ |
| confs 配置拉取 | ✅ | ✅ 纯 CLI（已移除 confs 依赖） |
| Logs 自动建表 + 清理 | ✅ | ✅ |
| 数据库后端 | SQLite / PG / MySQL | SQLite（直接 C API） |
| 依赖 | Python 3 + SQLAlchemy | libmsg + boost::json + libsqlite3 |

## 快速开始

### 编译

```bash
./_set.sh     # 等同于 make，产物为 build/db2
```

需要系统已安装：`libboost-json-dev`、`libsqlite3-dev`，以及 `libmsg` 已构建（`../libmsg/build/libus_msg.so`）。

### 启动

```bash
./_run.sh                                          # 使用 services.yaml 传入的 CLI 参数
./_run.sh --addr 127.0.0.1 --port 8005 --workers 4   # 覆盖监听参数
```

配置全部经 CLI 传递（由 core/services.yaml 注入），不依赖 confs。

## 配置管理

配置通过三层加载（CLI > 环境变量 > 默认值），全部配置项由 core/services.yaml 经 CLI 参数传入，不依赖 confs。

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--host` / `--addr` / `-a` | 监听地址 | `127.0.0.1` |
| `--port` / `-p` | 监听端口 | `8005` |
| `--workers` / `-w` | 工作线程数 | `4` |
| `--db-url` | 数据库路径 | `sqlite:///database.db` |
| `--retention-days` | 日志保留天数 | `30` |
| `--max-records` | 日志最大条数 | `1000000` |
| `--cleanup-interval` | 日志清理间隔秒数 | `3600` |
| `--log-level` | 日志级别 | `info` |
| `--instance` | 实例名 | `db2` |

### 环境变量（覆盖默认值，被 CLI 覆盖）

| 环境变量 | 说明 |
|---------|------|
| `LISTEN_ADDR` | 监听地址 |
| `LISTEN_PORT` | 监听端口 |
| `WORKERS` | 工作线程数 |
| `DATABASE_URL` | 数据库文件路径 |
| `RETENTION_DAYS` | 日志保留天数 |
| `MAX_RECORDS` | 日志最大条数 |
| `CLEANUP_INTERVAL` | 日志清理间隔秒数 |

### services.yaml 示例

```yaml
- name: db2
  cmd: bash /opt/micro_servers/db2/_run.sh
  args:
    - "--instance"
    - "db2"
    - "--log-level"
    - "info"
    - "--host"
    - "127.0.0.1"
    - "--port"
    - "8005"
    - "--workers"
    - "4"
    - "--db-url"
    - "sqlite:///database.db"
    - "--retention-days"
    - "30"
    - "--max-records"
    - "1000000"
    - "--cleanup-interval"
    - "3600"
  depends_on: []
  sleep_before: 0
```

## 协议与 API

与 Python db 完全一致，可直接参考 [db/README.md](../db/README.md)。以下为要点摘要。

### 传输协议（pymsg）

帧格式：`[4 字节大端长度][UTF-8 JSON]`，TCP 长连接。

### execute 路由 — 通用 CRUD

```json
{
  "_route": "execute",
  "action": "select",
  "table": "Users",
  "columns": ["id", "username"],
  "values": {"username": "bob", "password_hash": "..."},
  "where": {
    "is_active": 1,
    "create_at": {"ge": "2026-01-01T00:00:00"}
  },
  "order_by": [{"column": "id", "desc": true}],
  "limit": 10,
  "offset": 0
}
```

### where 条件运算符

| 运算符 | SQL | 示例 |
|--------|-----|------|
| 直接值 | `col = ?` | `"name": "Alice"` |
| `eq` | `=` | `"age": {"eq": 30}` |
| `ne` | `!=` | `"status": {"ne": "deleted"}` |
| `gt` | `>` | `"score": {"gt": 80}` |
| `ge` | `>=` | `"score": {"ge": 60}` |
| `lt` | `<` | `"price": {"lt": 100}` |
| `le` | `<=` | `"price": {"le": 200}` |
| `like` | `LIKE` | `"name": {"like": "%Smith%"}` |
| `in` | `IN (...)` | `"id": {"in": [1,2,3]}` |

多条件自动 **AND** 连接。

### 响应格式

```json
// 成功
{"success": true, "data": [{"id": 1, "username": "Alice"}]}

// insert
{"success": true, "data": [{"lastrowid": 42}]}

// update / delete
{"success": true, "data": [{"rowcount": 3}]}

// 失败
{"success": false, "error": "table not found: Foo"}
```

### 日期时间列

列类型含 `DATETIME`/`DATE`/`TIME`，或列名含 `_at`/`time`/`date` 的列自动视为日期列：

| 写入值 | 行为 |
|--------|------|
| `"2026-07-28T22:30:00"` | 原样存储（UTC） |
| `"$now"` | 替换为服务端当前 UTC 时间 |
| 其他字符串 | 原样存储 |

## 使用示例（C++ gateway）

gateway 的 `DbC::call_msg()` 无需任何修改，直接指向 db2 端口即可：

```cpp
// DbC::init("127.0.0.1", 8005);  // 不变

// 查询用户
auto result = DbC::call_msg({
    {"action",  "select"},
    {"table",   "Users"},
    {"where",   {{"username", "alice"}}},
    {"limit",   1},
});

// 更新登录时间
DbC::call_msg({
    {"action", "update"},
    {"table",  "Users"},
    {"values", {{"last_login", "$now"}}},
    {"where",  {{"id", user_id}}},
});
```

## 使用示例（Python pymsg）

```python
from pymsg.rpc import RpcClient

client = RpcClient("127.0.0.1", 8005, "myservice")

# 查询
rows = client.call("execute", {
    "action": "select",
    "table":  "Users",
    "where":  {"is_active": 1},
    "order_by": [{"column": "id", "desc": True}],
    "limit":  20,
})

# 插入
result = client.call("execute", {
    "action": "insert",
    "table":  "Notes",
    "values": {"pid": 0, "owner": "alice", "title": "hello", "content": "world"},
})
print(result[0]["lastrowid"])
```

## 内置日志能力

db2 内置 Logs 表（启动时自动建表 + 索引），设计与 Python db 完全一致。

### Logs 表结构

```sql
CREATE TABLE IF NOT EXISTS Logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,       -- UTC ISO 8601
    service  TEXT NOT NULL,
    level    TEXT NOT NULL,       -- DEBUG/INFO/WARNING/ERROR/CRITICAL
    message  TEXT NOT NULL,       -- 写入时截断至 8192 字节
    trace_id TEXT,
    extra    TEXT                 -- 可选 JSON 字符串，截断至 4096 字节
);
-- 索引: (service,ts) / (level,ts) / (ts) / (trace_id)
```

外部服务通过 `execute` 路由 INSERT 到 Logs 表即可。可复用 [db/example_log_client.py](../db/example_log_client.py)。

### 自动清理

后台线程每 `cleanup_interval` 秒（默认 3600）执行：
1. 删除超过 `retention_days` 天（默认 30）的记录
2. 若总条数超过 `max_records`（默认 100 万），删除最旧的超出部分

## 安全机制

- 表名仅允许字母、数字、下划线，防止 SQL 注入
- 所有 values / where 值通过 `sqlite3_bind_text` 参数绑定，无字符串拼接
- 仅操作已有表（`PRAGMA table_info` 反射），无法建表或删表
- RpcServer 异常自动脱敏（过滤内部路径），错误细节仅写 stderr

## 实现说明

- **per-thread 连接**：每个 worker 线程持有独立 `sqlite3*` 连接（`thread_local`），避免跨线程共享 prepared statement
- **列 schema 缓存**：`PRAGMA table_info` 结果加锁缓存，仅首次请求时反射
- **SQLite PRAGMA**：`journal_mode=WAL`、`synchronous=NORMAL`、`foreign_keys=ON`、`busy_timeout=5000`、`mmap_size=268435456`，对齐 Python 版配置

## 文件说明

| 文件 | 说明 |
|------|------|
| `src/db2.cpp` | 全部实现（配置/连接池/路由/日志/清理） |
| `Makefile` | 编译规则，链接 `libus_msg` + `boost_json` + `sqlite3` |
| `_set.sh` | 一键编译 |
| `_run.sh` | 启动服务 |
| `docker-entrypoint.sh` | 容器入口：初始化 schema + 启动 |
