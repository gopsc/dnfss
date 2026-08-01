# dnfs — 树状网络文件系统

Dendrachy Network File-System，基于父子关系（pid）组织的树状节点服务。

监听 `127.0.0.1:8006`，提供节点的增删改查，支持以 Base64 编码形式在 `content` 中存储文件数据。所有写操作需携带由 gateway 签发的 Token。

支持**特殊节点**（管理员专属）：`content` 为信封 JSON 时，前端按类型渲染为链接卡片 / 可运行网页 / 图片 / 文件下载，详见[特殊节点](#特殊节点管理员专属)。

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
  - **例外**：管理员（Token payload `is_admin=true`，由 gateway 按 `[Admin] users` 白名单签发）可删除/更新任意节点。
- `get_notes` 不需要鉴权。

## 特殊节点（管理员专属）

`content` 为以下信封 JSON 时即为特殊节点，前端 `dnfs.js` 按 `_sp` 类型差异化渲染；普通 Markdown 内容不受影响：

| `_sp` | 信封字段 | 前端表现 |
|-------|---------|---------|
| `link` | `url`（http/https，≤2048）、`note?` | favicon 图标（经 static `/api/favicon` 代理拉取）+ 可点击链接 |
| `html` | `code`（≤512KB）、`note?` | "审计源码"（纯文本展示）+ "运行"（新窗口执行）按钮 |
| `image` | `name`、`data`（`data:image/` DataURL，≤3.5M 字符）、`note?` | 图片直接显示 |
| `file` | `name`、`mime`、`data`（Base64，≤3.5M 字符）、`note?` | 下载按钮（Base64→Blob→下载） |

规则：

- **仅管理员可发布/更新**（`add_note` / `update_note` 中按 `is_admin` 强制，非管理员返回 403）；前端发布面板也仅对管理员显示。
- AI 内容审核剔除 Base64 负载（体量大且无审核意义）：`link` 审 `title+url+note`，`html` 审 `title+code 前 4000 字符+note`，`image`/`file` 审 `title+name+note`。
- `{"_sp"` 开头但 JSON 非法的内容会被拒绝发布（400），防止半截信封流入。

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

仅节点 owner 可更新；管理员（Token payload `is_admin`）豁免，可更新任意节点。

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

dnfs 已接入 confs 统一配置服务，confs 是 dnfs 启动的**强依赖**：

1. **confs 统一配置服务** — 必须成功从 confs 拉取 `dnfs.ini`，否则 dnfs 启动失败直接退出（exit code 1）
2. **环境变量覆盖**（最高优先级）— Docker/部署环境通过环境变量覆盖 confs 中的值

### 配置文件

集中托管在 [confs/configs/dnfs.ini](../confs/configs/dnfs.ini)，由 confs 服务分发：

| Section | Key | 说明 |
|---------|-----|------|
| `[server]` | `host` | 监听地址 |
| `[server]` | `port` | 监听端口 |
| `[database]` | `host` | db 服务地址 |
| `[database]` | `port` | db 服务端口 |
| `[database]` | `timeout` | RPC 调用超时（秒） |
| `[ai_review]` | `url` | AI 审核接口地址 |
| `[ai_review]` | `timeout` | AI 审核请求超时（秒） |

### 引导配置（confs 连接地址）

confs 自身的连接地址不通过 confs 获取（避免循环依赖），通过以下方式确定（优先级从高到低）：
- 环境变量 `CONFS_HOST` / `CONFS_PORT`
- 默认值 `127.0.0.1:8010`

### 环境变量（最高优先级，覆盖 confs 配置值）

环境变量覆盖配置文件值，容器化部署时通过 docker-compose.yml 传入服务名：

| 变量 | 容器内值 | 说明 |
|------|---------|------|
| `LISTEN_ADDR` | `0.0.0.0` | 监听地址（容器内需绑定所有网卡） |
| `LISTEN_PORT` | `8006` | 监听端口 |
| `DB_HOST` | `db` | db 服务主机名（docker-compose 服务名） |
| `DB_PORT` | `8005` | db 服务端口 |
| `REVIEW_URL` | `http://ai_review:8007/review` | ai_review 服务地址 |
| `CONFS_HOST` | `confs` | confs 服务主机名（引导配置） |
| `CONFS_PORT` | `8010` | confs 服务端口（引导配置） |

### 启动顺序

dnfs 通过 pymsg `retry_on_refused=True` 自动阻塞等待 confs 就绪，**拉取配置阶段不严格要求启动顺序**：
- confs 先启动 → dnfs 立即拉取配置
- dnfs 先启动 → pymsg 自动重试（每秒一次，每 5 秒打印等待提示），confs 启动后自动连接成功
- 配置完整性校验：若 dnfs.ini 中缺少必需的 section/key 且无对应环境变量覆盖，仍然报错退出
- 启动成功时控制台打印：`配置来源: confs (dnfs.ini @ host:port)`

> 注意：dnfs 对 db/ai_review 的 RPC 调用是运行时按需连接（首次请求时才建立 TCP），db/ai_review 未就绪时相关 API 返回 502 但进程不退出；待依赖服务启动后自动恢复。

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
