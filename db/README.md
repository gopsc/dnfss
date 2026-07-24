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

## 切换数据库

只需修改环境变量 `DATABASE_URL`，并安装对应驱动：

- SQLite：`sqlite:///database.db`
- PostgreSQL：`postgresql://user:pass@localhost/dbname` （需 `pip install psycopg2`）
- MySQL：`mysql+pymysql://user:pass@localhost/dbname` （需 `pip install pymysql`）

SQLAlchemy 会自动生成适配各数据库的 SQL 方言，无需修改接口调用。

