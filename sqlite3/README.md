
# Flask SQLite 执行服务

一个轻量级的 Flask HTTP 服务，用于安全地执行带参数的 SQLite SQL 语句，支持 `?` 和命名参数占位符，杜绝 SQL 注入。

## 功能特点

- 通过 JSON 接口发送 SQL 和参数
- 强制使用参数绑定（`execute(sql, params)`），无 SQL 注入风险
- 统一返回格式，所有语句执行结果均包含 `data` 数组
- 自动事务管理，异常时回滚
- 单线程单进程模式，绑定 127.0.0.1，仅本地访问

## 快速开始

### 环境要求

- Python 3.7+
- pip

### 安装依赖

```bash
pip install flask waitress
```

### 启动服务

```bash
python app.py
```

服务运行在 `http://127.0.0.1:5000`，仅允许本地访问。数据库文件 `database.db` 会在首次连接时自动创建（可提前建表或通过接口执行 `CREATE TABLE`）。

## API 文档

### 执行 SQL 语句

**端点**：`POST /execute`

**请求格式**：`Content-Type: application/json`

```json
{
  "sql": "SQL 语句，使用 ? 或 :key 作为占位符",
  "params": [] 或 {}
}
```

- `sql`：待执行的 SQL 语句（仅允许单条语句）
- `params`：参数列表 `[值1, 值2, ...]` 或参数字典 `{"key": 值}`

**成功响应**：

```json
{
  "success": true,
  "data": []          // 查询结果数组，每条记录为字典；非查询语句返回空数组
}
```

**错误响应**：

```json
{
  "success": false,
  "error": "错误描述"
}
```

## 使用示例

以下示例均通过 `curl` 和 Python `requests` 库演示。

### 1. 创建数据表

```bash
curl -X POST http://127.0.0.1:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, age INTEGER)"}'
```

Python 版本：

```python
import requests

url = "http://127.0.0.1:5000/execute"
payload = {
    "sql": "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, age INTEGER)"
}
resp = requests.post(url, json=payload)
print(resp.json())  # {"success": true, "data": []}
```

### 2. 插入数据（使用 `?` 占位符）

```bash
curl -X POST http://127.0.0.1:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "INSERT INTO users (name, age) VALUES (?, ?)", "params": ["Alice", 30]}'
```

Python 版本：

```python
payload = {
    "sql": "INSERT INTO users (name, age) VALUES (?, ?)",
    "params": ["Bob", 25]
}
resp = requests.post(url, json=payload)
print(resp.json())  # {"success": true, "data": []}
```

### 3. 插入数据（使用命名参数 `:key`）

```bash
curl -X POST http://127.0.0.1:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "INSERT INTO users (name, age) VALUES (:name, :age)", "params": {"name": "Charlie", "age": 28}}'
```

### 4. 查询全部数据

```bash
curl -X POST http://127.0.0.1:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM users"}'
```

响应示例：

```json
{
  "success": true,
  "data": [
    {"id": 1, "name": "Alice", "age": 30},
    {"id": 2, "name": "Bob", "age": 25},
    {"id": 3, "name": "Charlie", "age": 28}
  ]
}
```

Python 版本：

```python
payload = {"sql": "SELECT * FROM users"}
resp = requests.post(url, json=payload)
users = resp.json()["data"]
for user in users:
    print(user["name"], user["age"])
```

### 5. 按条件查询

```bash
curl -X POST http://127.0.0.1:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM users WHERE age > ?", "params": [26]}'
```

Python 版本：

```python
payload = {
    "sql": "SELECT * FROM users WHERE age > :min_age",
    "params": {"min_age": 26}
}
resp = requests.post(url, json=payload)
print(resp.json()["data"])
```

### 6. 更新数据

```bash
curl -X POST http://127.0.0.1:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "UPDATE users SET age = ? WHERE name = ?", "params": [31, "Alice"]}'
```

Python 版本：

```python
payload = {
    "sql": "UPDATE users SET age = :new_age WHERE id = :user_id",
    "params": {"new_age": 26, "user_id": 2}
}
resp = requests.post(url, json=payload)
print(resp.json())  # {"success": true, "data": []}
```

### 7. 删除数据

```bash
curl -X POST http://127.0.0.1:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "DELETE FROM users WHERE id = ?", "params": [3]}'
```

Python 版本：

```python
payload = {
    "sql": "DELETE FROM users WHERE name = :name",
    "params": {"name": "Charlie"}
}
resp = requests.post(url, json=payload)
print(resp.json())
```

### 8. 错误处理示例

当 SQL 或参数错误时，返回明确的错误信息：

```bash
curl -X POST http://127.0.0.1:5000/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM nonexistent_table"}'
```

响应：

```json
{
  "success": false,
  "error": "no such table: nonexistent_table"
}
```

## 安全机制

1. **参数绑定**：使用 `sqlite3` 官方推荐的 `execute(sql, params)` 方式传参，参数值与 SQL 结构完全分离。
2. **单语句执行**：`sqlite3.execute()` 仅执行第一条语句，无法通过分号注入多条命令。
3. **错误回滚**：若执行过程中出现异常，已修改的数据会回滚，确保数据一致性。
4. **本地绑定**：服务监听 `127.0.0.1`，仅本机可访问，避免网络暴露风险。

## 项目结构

```
.
├── app.py          # 主程序（使用 waitress 单线程模式）
└── README.md       # 本文档
```

数据库文件 `database.db` 会在运行时自动生成。

## 注意事项

- 服务采用 waitress 单线程模式（`threads=1`），确保请求顺序执行，适合低并发场景。
- 如需处理更高并发，可适当增加 `threads` 参数（例如 `threads=4`），但仍需注意 SQLite 的并发限制。
- 该服务无认证机制，推荐仅在本机或内网可信环境使用。如需对外服务，请添加鉴权并考虑使用 nginx 等反向代理。