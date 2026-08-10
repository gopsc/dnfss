# dnfs — 树状网络文件系统

> **已废弃**：生产已由 [dnfs2](../dnfs2/)（C++20 重写）平替，不再部署；本 README 为历史说明。

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
    ├──► confs :8010     启动时拉取配置（dnfs.ini，强依赖，retry_on_refused 等待就绪）
    ├──► db :8005        数据持久化（pymsg TCP RPC，execute 路由）
    └──► ai_review :8007 内容审核（HTTP POST /review，仅 add_note/update_note 时调用）
```

- 启动时通过 pymsg 从 confs 拉取 `dnfs.ini` 配置，拉取失败直接退出（强依赖）；使用 `retry_on_refused=True` 自动等待 confs 就绪。
- 不直连数据库，所有数据操作通过 pymsg TCP RPC 调用 `db` 服务（`_route: "execute"`），规避 SQL 注入风险。
- 新增/更新节点前调用 `ai_review` 服务进行内容审核（HTTP POST `{content: "..."}`），审核不通过则拒绝写入。
- 日志通过 pymsg `AsyncRpcQueue` 异步批量写入 db 的 `Logs` 表，fail-safe 不阻塞业务。
- 通常部署在 gateway/WAF 之后（`Proxy.opened` 白名单中），由 gateway 完成 Token RSA 验签后转发。

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

- Token 由 gateway 在 `/login` 签发；dnfs 通过 [dnfs.py:312](dnfs.py#L312) `decode_token` 将 Token 转发至 gateway `/whoami` 接口，由 gateway 完成 **RSA 签名验证 + 过期检查**（不再本地解码验签）；gateway 不可达时按 **fail-closed** 返回 503，验签失败返回 None。
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
| `js` | `code`（完整 HTML/CSS/JS 文档，≤512KB）、`note?` | "审计源码"（纯文本展示）+ "运行"（解析完整 HTML 文档注入当前页面：style 注入 head、script 在全局作用域执行可加特效、body 渲染到节点下方；console 输出捕获到下方）按钮 |
| `image` | `name`、`data`（`data:image/` DataURL，≤3.5M 字符）、`note?` | 图片直接显示 |
| `file` | `name`、`mime`、`data`（Base64，≤3.5M 字符）、`note?` | 下载按钮（Base64→Blob→下载） |

规则：

- **仅管理员可发布/更新**（`add_note` / `update_note` 中按 `is_admin` 强制，非管理员返回 403）；前端发布面板也仅对管理员显示。
- AI 内容审核剔除 Base64 负载（体量大且无审核意义）：`link` 审 `title+url+note`，`js` 审 `title+code 前 4000 字符+note`，`image`/`file` 审 `title+name+note`。
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

- **安全响应头**（[security_middleware.py](security_middleware.py)）：`X-Frame-Options: SAMEORIGIN`、`X-XSS-Protection`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`、`Permissions-Policy`（禁用摄像头/麦克风/地理位置）、CSP（`default-src 'self'` 等），并移除 `Server` 头。
- **Token 鉴权**：写操作强制校验，payload 取 `user` 字段。
- **所有权隔离**：普通用户只能改/删自己的节点。
- **特殊节点管控**：link/js/image/file 类型有格式校验与大小限制（URL 前缀、文件名长度、内容大小上限）。
- **内容审核**：写入与更新前调用 `ai_review`，非法内容会被拒绝（fail-closed 设计，见已知问题）。
- **异步日志**：通过 pymsg `AsyncRpcQueue` 异步批量写日志，fail-safe 不阻塞业务线程。
- **非 root 容器化运行**：Dockerfile 中使用 appuser 非特权用户运行服务。

## 配置

dnfs 已接入 confs 统一配置服务，confs 是 dnfs 启动的**强依赖**：

1. **confs 统一配置服务** — 必须成功从 confs 拉取 `dnfs.ini`，否则 dnfs 启动失败直接退出（exit code 1）
2. **环境变量覆盖**（最高优先级）— Docker/部署环境通过环境变量覆盖 confs 中的值

### 配置文件

集中托管在 `confs/configs/dnfs.ini`（已删除，配置经 CLI 传递），由 confs 服务分发：

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

前端页面由 `static` 服务承载，不在 dnfs 模块内：

```
static/public/dnfs.html    页面结构
static/public/css/         样式（index.css / aibox.css 等）
static/public/js/dnfs.js   交互逻辑
```

功能：节点树浏览、新增/编辑/删除节点、文件 Base64 上传与下载、节点迁移（移动到其他父节点下）。

> 注：首页路由 `@app.route('/')` 和 `render_template` 相关代码已注释，dnfs 仅提供 JSON API，不直接提供 HTML 页面。

## 依赖

```
flask
flask-cors
waitress
pymsg         # 内部 RPC 通信库（../pymsg/）
```

运行时需要 `confs`（:8010，启动强依赖）、`db`（:8005）和 `ai_review`（:8007）服务可达；对外访问需经 gateway 转发并验签。

> `requirements.txt` 中曾列有 `flask_sqlalchemy`，Dockerfile 中已添加 `pip uninstall -y flask_sqlalchemy` 清理遗留依赖；代码中的 SQLAlchemy 直连逻辑已全部注释，通过 pymsg RPC 调用 db 服务。

## 运行

```bash
bash _set.sh   # 创建 venv + 安装依赖
bash _run.sh   # 启动服务（waitress）
```

## 已知问题与风险

> 状态基准：2026-08-01 审计 + 2026-08-03 逐条代码验证。

### 🔴 P0 — 严重问题

1. **Token 无 RSA 签名验证，可伪造任意用户身份（含管理员）** — ✅ 已修复
   - 原 `decode_token()` 本地 base64 解码 payload、跳过 RSA 验签（`# FIXME: 验证数字签名`）。
   - 现已重写为转发 gateway `/whoami` 完成验签 + 过期检查（[dnfs.py:312-351](dnfs.py#L312-L351)），gateway 不可达时 fail-closed 返回 503。

2. **`review_content()` 错误处理 / fail-closed** — ⚠️ 基本修复
   - 已实现 fail-closed：HTTPError/URLError/JSONDecodeError 等异常路径均拒绝请求（写入/更新被拒、负载不送审）。
   - 遗留：函数返回类型不一致（成功返回 `bool`，而 `_validate_sp` 等期望 dict），建议代码清理（审计 P2-1）。

3. **`delete_note` / `update_note` 缺少 `@_handle_exp` 装饰器** — ✅ 已修复
   - 现两函数均已加 `@_handle_exp`（[dnfs.py:469](dnfs.py#L469)、[dnfs.py:572](dnfs.py#L572)），异常统一返回结构化 JSON，不再出 Flask 默认 500 页。

4. **有子节点的父节点可能被删除（树结构完整性保护缺陷）** — ❌ 未修复
   - **数据库层缺失外键约束**: Notes 表 `pid` 列未定义 `FOREIGN KEY ... ON DELETE RESTRICT`（[db/create_sqlite3.sql](../db/create_sqlite3.sql)）。
   - **应用层 TOCTOU 竞态**: 子节点查询（SELECT）与删除（DELETE）是两个独立 RPC，无事务保护。
   - **错误处理缺陷**: 多处直接访问 `notes['data']` 而未先确认 `success`（如 [dnfs.py:387](dnfs.py#L387)、[L519](dnfs.py#L519)），`success=False` 时可能抛 KeyError。

### 🟠 P1 — 高危问题

5. **Token 无过期校验** — ✅ 已修复（gateway `/whoami` 基于 `created_at` + `keep_time` 统一校验，dnfs 不再自行处理）
6. **请求体大小无限制** — ❌ 未修复（[dnfs.py:723](dnfs.py#L723) `serve(app, ...)` 未配置 `max_request_body_size`，建议设 10MB 与 WAF 对齐）
7. **`log_client.py` 配置与主模块不一致** — ❌ 未修复（[log_client.py:32-33](log_client.py#L32-L33) 直接读 `DB_HOST`/`DB_PORT` 环境变量，不拉取 confs）
8. **`update_note` 修改 pid 时未验证目标父节点存在** — ❌ 未修复（[update_note](dnfs.py#L573) 直接更新，不检查 pid≠0 时父节点是否存在）
9. **db RPC 客户端未启用启动等待** — ❌ 未修复（[dnfs.py:133](dnfs.py#L133) `_rpc = RpcClient(...)` 无 `retry_on_refused=True`）
10. **`delete_note`/`update_note` 中 `decode_token` 无 try-except** — ✅ 基本解决（两函数均由 `@_handle_exp` 统一捕获异常，不再裸抛 500）
11. **`note_id` 缺少强制类型转换** — ❌ 未修复（[dnfs.py:474](dnfs.py#L474)、[L577](dnfs.py#L577) 仅类型提示，依赖 SQLite 类型亲和性隐式匹配）

### 🟡 P2 — 中危问题

12. **错误响应泄露内部信息** — 多处错误消息拼接 `request.remote_addr` 和用户名返回给客户端，应仅记录到服务端日志
13. **CORS 配置过于宽松** — `CORS(app)` 默认允许所有来源（`*`），建议限制为实际前端域名
14. **`_call_dbv2` 返回值访问前未统一检查 success** — 多处直接访问 `notes['data']` 而不先确认 `success==True`
15. **`pid` 参数判断逻辑不严谨** — `if not pid: pid = 0` 会将空字符串/None 等非法输入默默转为 0，掩盖参数错误
16. **遗留注释代码** — 文件中有大量注释掉的旧版 SQLAlchemy 代码和已废弃接口，影响可读性
