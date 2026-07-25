# dnfs — 树状网络文件系统

Dendrachy Network File-System，基于父子关系（pid）组织的树状节点服务。

监听 `127.0.0.1:8006`，提供节点的增删改查，支持以 Base64 编码形式在 `content` 中存储文件数据。所有写操作需携带由 gateway 签发的 Token。

## 架构

```
客户端 (static/dnfs.html)
    │  Bearer Token
    ▼
  dnfs :8006 (Flask + Waitress)
    │
    ├──► db :8005        数据持久化（SQL-over-HTTP，/execute）
    └──► ai_review :8007 内容审核（仅 add_note / update_note 时调用）
```

- 不直连数据库，所有数据操作通过 HTTP 调用 `db` 服务（`http://127.0.0.1:8005/execute`）。
- 新增/更新节点前调用 `ai_review` 服务（`http://127.0.0.1:8007/review`）进行内容审核，审核不通过则拒绝写入。
- 通常部署在 gateway 之后（`Proxy.opened` 白名单中），由 gateway 完成 Token 验签后转发，dnfs 仅做所有权校验。

## 数据模型

对应 `db` 服务中的 `Notes` 表（见 [db/create_sqlite3.sql](../db/create_sqlite3.sql)）：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 节点唯一标识 |
| pid | INTEGER NOT NULL | 父节点 id，0 表示根 |
| owner | VARCHAR(128) | 创建者账号（来自 Token payload 的 `user` 字段，默认 `'Unknow'`） |
| title | VARCHAR(128) NOT NULL | 节点标题 |
| content | TEXT NOT NULL | 节点内容（可含 Base64 文件数据） |

## 鉴权与所有权

写操作（`add_note` / `delete_note` / `update_note`）需在请求头携带：

```
Authorization: Bearer <base64_payload>_<rsa_sign>
```

- Token 由 gateway 在 `/login` 签发，dnfs 通过 [dnfs.py:163](dnfs.py#L163) `decode_token` 仅解 Base64 取 payload，**不验签**（验签由 gateway 完成）。
- payload 中取 `user` 字段作为操作者，写入 `owner`。
- `delete_note` 与 `update_note` 校验所有权：
  - 节点 `owner` 必须等于当前 `user`，否则 `403`；
  - **例外**：`user == 'admin'` 可删除任意节点（管理员豁免，仅 delete）。
- `get_notes` 不需要鉴权。

## 接口文档

所有接口均为 `POST`，`Content-Type: application/json`。

---

### POST /get_notes

获取指定父节点下的子节点列表。**无需鉴权**。

**Request**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| pid | int | 否 | 父节点 id，0 表示根，缺省默认 0 |

```bash
curl -X POST http://localhost:8006/get_notes \
  -H 'Content-Type: application/json' \
  -d '{"pid": 0}'
```

**Response 200** — 直接返回节点数组：

```json
[
  {"id": 1, "pid": 0, "owner": "admin", "title": "根节点", "content": "hello"},
  {"id": 2, "pid": 0, "owner": "alice", "title": "另一个", "content": "world"}
]
```

空目录返回 `[]`。

---

### POST /add_note

添加新节点。流程：校验参数 → 校验 Token → 校验父节点存在 → AI 内容审核 → 写入数据库（含 owner）。

**Request**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| pid | int | 是 | 父节点 id，0 为根，非 0 时校验父节点是否存在 |
| title | str | 是 | 节点标题，不可为空 |
| content | str | 是 | 节点内容，不可为空（可含 Base64 文件数据） |

请求头需带 `Authorization: Bearer <token>`。

```bash
curl -X POST http://localhost:8006/add_note \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{"pid": 0, "title": "新节点", "content": "节点内容"}'
```

**响应**

| 状态码 | 场景 | 响应体 |
|--------|------|--------|
| 200 | 成功 | `{"success": true}` |
| 400 | 缺少必填字段 | `{"error": "Pid, title and content are required"}` |
| 400 | 父节点不存在（pid≠0） | `{"error": "the new node need a parent"}` |
| 400 | AI 内容审核不通过 | `{"error": "review failed"}` |
| 400 | 数据库写入失败 | `{"success": false}` |
| 401 | 未携带 Token 或 Token 解析失败 | `{"error": "Warning: no Authorization"}` |

---

### POST /delete_note

删除指定节点。流程：校验参数 → 校验 Token → 检查子节点 → 校验所有权 → 删除。

存在子节点时拒绝删除（保护树结构完整性）。**admin 可删除任意节点**，普通用户仅能删除自己拥有的节点。

**Request**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | int | 是 | 要删除的节点 id |

请求头需带 `Authorization: Bearer <token>`。

```bash
curl -X POST http://localhost:8006/delete_note \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{"id": 1}'
```

**响应**

| 状态码 | 场景 | 响应体 |
|--------|------|--------|
| 200 | 成功 | `{"success": true}` |
| 400 | 缺少 id | `{"error": "Note ID is required"}` |
| 400 | 存在子节点 | `{"error": "this node has a child "}` |
| 400 | 数据库删除失败 | `{"success": false}` |
| 401 | 未携带 Token | `{"error": "Warning: no Authorization"}` |
| 403 | 非本人节点且非 admin | `{"error": "It's not your note"}` |

---

### POST /update_note

更新节点的 pid / title / content（pid 变更可用于迁移节点）。流程：校验参数 → 校验 Token → 校验所有权 → AI 内容审核 → 更新。

仅节点 owner 可更新；update 路径下 **admin 不享豁免**（与 delete 不同）。

**Request**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | int | 是 | 要更新的节点 id |
| pid | int | 是 | 新的父节点 id（迁移时改变此值） |
| title | str | 是 | 新标题，不可为空 |
| content | str | 是 | 新内容，不可为空 |

请求头需带 `Authorization: Bearer <token>`。

```bash
curl -X POST http://localhost:8006/update_note \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{"id": 1, "pid": 2, "title": "新标题", "content": "新内容"}'
```

**响应**

| 状态码 | 场景 | 响应体 |
|--------|------|--------|
| 200 | 成功 | `{"success": true}` |
| 400 | 缺少必填字段 | `{"error": "Invalid param"}` |
| 400 | AI 内容审核不通过 | `{"error": "review failed"}` |
| 400 | 数据库更新失败 | `{"success": false}` |
| 401 | 未携带 Token | `{"error": "Warning: no Authorization"}` |
| 403 | 非本人节点 | `{"error": "It's not your note"}` |

---

## 安全特性

- **安全响应头**（[security_middleware.py](security_middleware.py)）：`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy`、`Permissions-Policy`、CSP（`default-src 'self'`、`frame-ancestors 'none'`），并移除 `Server` 头。
- **Token 鉴权**：写操作强制校验，payload 取 `user` 字段。
- **所有权隔离**：普通用户只能改/删自己的节点。
- **内容审核**：写入与更新前调用 `ai_review`，色情/违禁/赌博/诈骗/暴力等内容会被拒绝。

## 配置

服务地址与上游依赖硬编码在 [dnfs.py](dnfs.py)：

| 常量 | 值 | 说明 |
|------|----|------|
| `ADDR` / `PORT` | `127.0.0.1` / `8006` | 监听地址 |
| `DB_SERVER_URL` | `http://127.0.0.1:8005/execute` | db 服务 |
| `REVIEW_SERVER_URL` | `http://127.0.0.1:8007/review` | ai_review 服务 |

调用超时均为 10 秒。

## 前端

三文件架构：

```
templates/index.html   页面结构（Font Awesome 图标）
static/css/style.css   样式
static/js/script.js    交互逻辑
```

功能：节点树浏览、新增/编辑/删除节点、文件 Base64 上传与下载、节点迁移（移动到其他父节点下）。

> 注：首页路由 `@app.route('/')` 当前已注释，前端页面实际由 `static` 服务的 `dnfs.html` 承载。

## 依赖

```
flask
flask-cors
waitress
nuitka        # 可选，编译为独立二进制
```

> `requirements.txt` 仍列有 `flask_sqlalchemy`，但代码已不使用（SQLAlchemy 直连代码已注释），属遗留依赖。

运行时需要 `db`（:8005）和 `ai_review`（:8007）服务已启动；对外访问需经 gateway 转发并验签。

## 运行

```bash
bash _set.sh   # 创建 venv + 安装依赖
bash _run.sh   # 启动服务（waitress）
```

## 已知问题与风险

- **`decode_token` 未验签**（[dnfs.py:167](dnfs.py#L167)）：仅解 Base64 取 payload，存在 `# FIXME: 验证数字签名` 标记。当前安全性依赖"必须经 gateway 转发"这一部署假设；若 dnfs 被直接访问（绕过 gateway），任意伪造的 Token 都能通过。建议补 RSA 验签或在部署上确保仅 gateway 可达。
- **`review_content` 返回类型不一致**（[dnfs.py:73-119](dnfs.py#L73)）：正常路径返回 `bool`（[L105](dnfs.py#L105)），HTTPError 路径返回 `dict`（[L113](dnfs.py#L113)）。调用处用 `True != review` 判断，HTTPError 时 `dict != True` 恒为真，会一律判为审核失败并吞掉具体错误信息。建议统一返回类型或在调用处 catch 异常。
- **CORS 全开**：`CORS(app)` 未配置白名单，允许任意 Origin 跨域访问。考虑到写操作需 Token，风险可控，但仍建议按 gateway 的 `[CORS] allowed_origins` 方式收紧。
- **`call_db_service` 异常未捕获**：调用处（[L208](dnfs.py#L208)、[L261](dnfs.py#L261)、[L318](dnfs.py#L318)、[L347](dnfs.py#L347)、[L440](dnfs.py#L440)）直接取 `result['success']` / `notes['data']`，若 db 服务返回非预期结构或网络异常会抛 500。
- **`get_notes` 不鉴权**：任何能访问 dnfs 的人都能枚举所有节点内容。若内容含敏感 Base64 文件，需在 gateway 层做路径级鉴权。
