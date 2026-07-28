# 通用数据库服务 db（pymsg/TCP + SQLAlchemy Core）

一个安全的数据库访问服务。各业务服务通过自研 pymsg 协议（TCP 长度前缀帧 + JSON）发送结构化查询意图，db 翻译为 SQL 执行，全程参数绑定，业务侧无需直连数据库、无需手写 SQL。

## 功能特点

- **跨数据库**：基于 SQLAlchemy Core 表达式语言，一套接口适配 SQLite / PostgreSQL / MySQL
- **零 SQL 注入**：所有条件、值均通过 SQLAlchemy 参数绑定处理
- **统一 JSON 接口**：`_route` + `action` + `table` + `where` 模式
- **支持增删改查**：select / insert / update / delete 全覆盖
- **内置日志表**：启动自动建 Logs 表，供全系统集中写日志；带后台自动清理
- **仅监听本机**：默认 `127.0.0.1:8005`，不对外暴露

## 快速开始

### 安装依赖

```bash
./_set.sh     # 建 venv 并安装 requirements.txt（SQLAlchemy + 本地 ../pymsg）
```

### 启动服务

```bash
./_run.sh     # venv 中执行 python3 dbv2.py
```

服务监听 `tcp://127.0.0.1:8005`。生产环境由 core（Nidus）按 init.txt 统一拉起，守护靠 systemd。

**注意**：`execute` 路由依赖表已存在（通过反射获取结构）。建表见 `create_sqlite3.sql`（Users / Notes；Logs 表由服务启动时自建）。

## 协议与 API

### 传输协议（pymsg）

每条消息为一个完整 JSON，帧格式：`[4 字节大端长度][UTF-8 JSON]`。
请求中的 `_route` 字段指定路由，目前仅有一个路由：**`execute`**。

### execute 路由 — 通用 CRUD

```json
{
  "_route": "execute",
  "action": "select",          // select | insert | update | delete
  "table": "Users",
  "columns": ["id", "name"],   // select 时可选（缺省全列）
  "values": {"name": "Bob"},   // insert / update 时必填
  "where": {                   // 可选，条件见下表
    "age": {"ge": 18, "lt": 60},
    "name": "Alice"
  },
  "order_by": [{"column": "id", "desc": true}],
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
  "data": [
    {"id": 1, "username": "Alice", "create_at": "2026-07-28T14:30:00"}
  ]
}
```
- select → 行对象数组
- insert → `[{"lastrowid": N}]`
- update / delete → `[{"rowcount": N}]`

失败：
```json
{"success": false, "error": "错误描述"}
```

## 日期/时间列的处理

JSON 没有日期类型，db 内置了类型适配层：按反射得到的列类型自动在
ISO 8601 字符串与数据库 DATETIME / DATE / TIME 之间转换。

**写入（values / where 条件值）**：

| 输入 | 说明 |
|------|------|
| `"2026-07-28T22:30:00"` | ISO 8601（T 分隔），推荐 |
| `"2026-07-28 22:30:00"` | 空格分隔同样接受（SQLite 原生格式） |
| `"2026-07-28T22:30:00+08:00"` | 带时区偏移：自动转 UTC 后存储 |
| `"2026-07-28T14:30:00Z"` | Z 后缀等同 +00:00 |
| `"$now"` | 哨兵值：服务端当前 UTC 时间（DATETIME/DATE 列可用） |

存储统一为 UTC 朴素时间（无时区）。非法日期字符串会返回明确的错误：
`{"success": false, "error": "列 last_login 的日期时间值无法解析（需 ISO 8601 或 \"$now\"）: ..."}`

**读取（select 响应）**：日期时间值一律序列化为 ISO 8601（T 分隔），
如 `"2026-07-28T22:30:00"`，客户端可用 `datetime.fromisoformat()` 直接解析。

**注意**：`like` 运算符是字符串模式匹配，对日期列使用时不做转换。

```json
// 示例：登录后更新 last_login 为服务端当前时间
{
  "_route": "execute",
  "action": "update",
  "table": "Users",
  "values": {"last_login": "$now"},
  "where": {"username": "alice"}
}
```

## 使用示例（Python + pymsg）

```python
import json
from pymsg import MsgClient

client = MsgClient("127.0.0.1", 8005, "myservice")

# 查询
resp = client.call(json.dumps({
    "_route": "execute",
    "action": "select",
    "table": "Users",
    "where": {"id": {"gt": 0}},
    "order_by": [{"column": "id", "desc": True}],
    "limit": 5,
}))
print(json.loads(resp))

# 插入
client.call(json.dumps({
    "_route": "execute",
    "action": "insert",
    "table": "Users",
    "values": {"username": "bob", "password_hash": "..."},
}))
```

## 安全机制

- 强制使用 SQLAlchemy 参数绑定，杜绝 SQL 注入。
- 仅反射已有表，无法创建或删除表（保证结构安全）。
- 所有异常自动回滚事务。
- 仅监听 `127.0.0.1`，仅本机服务可访问。

## 内置日志能力

db 内置统一日志收集：全系统各服务（waf / gateway / static / dnfs / db / ai_review / captcha / core / smtp）的日志集中写入 db 的 `Logs` 表。

### 为什么集成在 db 而不是独立服务

独立 logs 服务会导致**循环调用**：db 记日志→调 logs，logs 存库→调 db，无限递归。集成在 db 内部后，db 自己记日志直接写表，彻底消除循环依赖。

### 日志表（启动时自建）

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
-- 索引: (service,ts) / (level,ts) / (ts) / (trace_id)
```

- `level`：`DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`
- `trace_id`：可选，请求追踪 ID
- `extra`：可选，JSON 字符串，存放附加结构化字段

### 写入方式

**外部服务**：复制 `example_log_client.py` 为各自目录下的 `log_client.py`，改 `service_name` 即用。客户端底层通过 pymsg 调 `execute` 路由逐条 INSERT 到 Logs 表：

```python
from log_client import logs

logs.info("dnfs", "节点新增成功 id=42")
logs.error("dnfs", "数据库连接失败", extra={"errno": 2002})
logs.push("gateway", "WARNING", "登录失败次数超限",
          trace_id="req-abc", extra={"ip": "1.2.3.4"})
```

客户端特性：
- 纯标准库实现，异步非阻塞：内存队列 + 后台线程批量推送
- fail-safe：db 不可用时静默丢弃，绝不影响业务
- 缓冲批量：队列满 50 条或超时 2 秒时自动 flush

**db 自身**：`_log_internal()` 直接写库，无网络往返（防递归）。

### 查询日志

日志查询直接走 `execute` 路由 select Logs 表，或用工具脚本 `_view_db.sqlite3.py`：

```bash
python3 _view_db.sqlite3.py                    # 列出所有表及行数
python3 _view_db.sqlite3.py Logs -n 20         # 最近 20 条日志
python3 _view_db.sqlite3.py Logs -w "level='ERROR'"
python3 _view_db.sqlite3.py -s "SELECT service, COUNT(*) FROM Logs GROUP BY service"
```

### 自动清理

- 服务启动时开启后台守护线程，每 1 小时清理一次
- 默认保留 30 天日志，超期自动删除
- 日志总条数超过 100 万时，按时间倒序保留最新 100 万条

## 切换数据库

只需修改环境变量 `DATABASE_URL`，并安装对应驱动：

- SQLite（默认）：`sqlite:///database.db`
- PostgreSQL：`postgresql://user:pass@localhost/dbname`（需 `pip install psycopg2`）
- MySQL：`mysql+pymysql://user:pass@localhost/dbname`（需 `pip install pymysql`）

SQLAlchemy 会自动生成适配各数据库的 SQL 方言，无需修改接口调用。

## 文件说明

| 文件 | 说明 |
|------|------|
| `dbv2.py` | 服务主程序（pymsg/TCP，唯一在跑的实现） |
| `example_log_client.py` | 日志客户端 SDK 模板，各服务复制为 `log_client.py` 使用 |
| `create_sqlite3.sql` | Users / Notes / Logs 建表脚本 |
| `_view_db.sqlite3.py` | 非交互式查看 database.db 的工具脚本 |
| `_set.sh` / `_run.sh` | 环境安装 / 启动脚本 |
