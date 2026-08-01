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

- Token 由 gateway 在 `/login` 签发，dnfs 通过 [dnfs.py:304](dnfs.py#L304) `decode_token` 仅解 Base64 取 payload，**当前代码不验签**（代码中标注 `# FIXME: 验证数字签名`，依赖部署上经 gateway 转发的网络隔离）。
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

- **安全响应头**（[security_middleware.py](security_middleware.py)）：`X-Frame-Options: SAMEORIGIN`、`X-XSS-Protection`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`、`Permissions-Policy`（禁用摄像头/麦克风/地理位置）、CSP（`default-src 'self'` 等），并移除 `Server` 头。
- **Token 鉴权**：写操作强制校验，payload 取 `user` 字段。
- **所有权隔离**：普通用户只能改/删自己的节点。
- **特殊节点管控**：link/html/image/file 类型有格式校验与大小限制（URL 前缀、文件名长度、内容大小上限）。
- **内容审核**：写入与更新前调用 `ai_review`，非法内容会被拒绝（fail-closed 设计，见已知问题）。
- **异步日志**：通过 pymsg `AsyncRpcQueue` 异步批量写日志，fail-safe 不阻塞业务线程。
- **非 root 容器化运行**：Dockerfile 中使用 appuser 非特权用户运行服务。

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

审计日期: 2026-08-01

### 🔴 P0 — 严重问题（需立即修复）

1. **Token 无 RSA 签名验证，可伪造任意用户身份（含管理员）** — [dnfs.py:304-311](dnfs.py#L304-L311)
   - `decode_token()` 直接 base64 解码 payload，完全跳过 RSA 签名验证（代码中有 `# FIXME: 验证数字签名` 注释）
   - 若 dnfs 被直接访问（绕过 gateway），攻击者可构造 `{"user": "任意邮箱", "is_admin": true}` 的 payload，base64 编码后拼接下划线+任意字符串即可伪造管理员身份，绕过所有权限校验
   - **参考实现**: gateway `verifyWsAdminToken` 展示了正确流程：分割 `_` → base64 解码 → RSA 公钥验签 → 检查过期时间
   - **修复建议**: 实现 RSA 签名验证（持有 gateway 公钥）+ Token 过期校验（`iat`/`created_at` + `keep_time`）

2. **`review_content()` 错误处理断裂，fail-closed 失效** — [dnfs.py:149-195](dnfs.py#L149-L195)
   - 成功路径返回 `bool`，但 HTTPError/URLError/其他异常直接 `raise e`，没有返回 `False` 实现 fail-closed
   - 内层 `except json.JSONDecodeError as e` 变量名遮蔽外层 `HTTPError as e`
   - ai_review 不可用时异常向上传播导致 500，而非拒绝内容（违反 fail-closed 设计意图）
   - **修复建议**: 所有异常路径返回 `False` 或抛 DnfsExp 拒绝写入

3. **`delete_note` 和 `update_note` 缺少 `@_handle_exp` 装饰器** — [dnfs.py:428](dnfs.py#L428), [dnfs.py:531](dnfs.py#L531)
   - `add_note`/`get_notes` 使用了 `@_handle_exp` 统一异常处理，但 `delete_note`/`update_note` 没有
   - 抛出 `DnfsExp` 或其他异常时返回 Flask 默认 500 HTML 错误页，与其他接口 JSON 响应不一致

4. **有子节点的父节点可能被删除（树结构完整性保护缺陷）**
   - **数据库层缺失外键约束**: 虽然 dbv2 启用了 `PRAGMA foreign_keys=ON`，但 Notes 表建表 SQL（[db/create_sqlite3.sql:13-19](../db/create_sqlite3.sql#L13-L19)）中 `pid` 列只是普通 INTEGER，没有定义 `FOREIGN KEY (pid) REFERENCES Notes(id) ON DELETE RESTRICT`
   - **应用层 TOCTOU 竞态**: 子节点查询（SELECT）和删除（DELETE）是两个独立 RPC 调用，无事务保护，检查与删除之间可插入子节点导致父节点被删后子节点成为孤儿
   - **错误处理缺陷**: `_call_dbv2` 返回 `success=False` 时响应中无 `data` 键，直接访问 `notes['data']` 会抛 KeyError 而非正确处理
   - **修复建议**: (a) 添加数据库外键约束; (b) 应用层检查返回值 `success` 状态; (c) 使用数据库事务将检查+删除原子化

### 🟠 P1 — 高危问题

5. **Token 无过期校验** — payload 中包含 `iat`/`created_at`/`keep_time` 字段，但 `decode_token()` 完全不检查，泄露的 Token 可永久使用
6. **请求体大小无限制** — Waitress 未配置 `max_request_body_size`，可能被大请求体 DoS；建议设置 10MB 上限与 WAF 对齐
7. **`log_client.py` 配置与主模块不一致** — [log_client.py:32-33](log_client.py#L32-L33) 直接读 `DB_HOST`/`DB_PORT` 环境变量，不从 confs 拉取配置，可能连错地址
8. **`update_note` 修改 pid 时未验证目标父节点存在** — `add_note` 会检查 pid≠0 时父节点是否存在，但 `update_note` 直接更新不检查，可将节点移动到不存在的父节点下
9. **db RPC 客户端未启用启动等待** — `_rpc = RpcClient(...)` 未设置 `retry_on_refused=True`，db 未就绪时首次请求失败（虽然 confs 连接启用了重试）
10. **`delete_note`/`update_note` 中 `decode_token` 无 try-except** — `get_notes` 中包裹了异常处理，但这两个函数没有，base64 解码/JSON 解析错误直接抛 500
11. **`note_id` 缺少强制类型转换** — `note_id: int = data.get('id')` 只是类型提示，不会实际转换；传入字符串 `"5"` 依赖 SQLite 类型亲和性隐式匹配

### 🟡 P2 — 中危问题

12. **错误响应泄露内部信息** — 多处错误消息拼接 `request.remote_addr` 和用户名返回给客户端（如"未授权访问，从xxx"），应仅记录到服务端日志
13. **CORS 配置过于宽松** — `CORS(app)` 默认允许所有来源（`*`），建议限制为实际前端域名
14. **`_call_dbv2` 返回值访问前未统一检查 success** — 多处直接访问 `notes['data']` 而不先确认 `success==True`
15. **`pid` 参数判断逻辑不严谨** — `if not pid: pid = 0` 会将空字符串/None 等非法输入默默转为 0，掩盖参数错误
16. **遗留注释代码** — 文件中有大量注释掉的旧版 SQLAlchemy 代码和已废弃接口，影响可读性
