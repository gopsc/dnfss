# DNFS — 树状网络文件系统

Dendrachy Network File-System，基于父子关系（pid）组织的树状节点服务。

监听 `127.0.0.1:8006`，提供节点的增删改查，支持文件以 Base64 编码形式存储在节点 content 中。

## 架构

```
客户端 (static/dnfs.html)
    │
    ▼
  dnfs :8006 (Flask + Waitress)
    │
    ├──► db :8005        数据持久化（SQL-over-HTTP）
    └──► ai_review :8007 内容审核（仅 add_note 时调用）
```

dnfs 不再直连数据库，所有数据操作通过 HTTP 调用 `db` 服务（`http://127.0.0.1:8005/execute`）完成。新增节点前会调用 `ai_review` 服务（`http://127.0.0.1:8007/review`）进行内容审核，审核不通过则拒绝写入。

## 接口文档

所有接口均为 `POST`，Content-Type 为 `application/json`。

---

### POST /get_notes

获取指定父节点下的子节点列表。

**Request**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| pid | int | 否 | 父节点 id，0 表示根节点，缺省默认 0 |

```bash
curl -X POST http://localhost:8006/get_notes \
  -H 'Content-Type: application/json' \
  -d '{"pid": 0}'
```

**Response 200**

直接返回节点数组：

```json
[
  {"id": 1, "pid": 0, "title": "根节点", "content": "hello"},
  {"id": 2, "pid": 0, "title": "另一个", "content": "world"}
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 节点唯一标识 |
| pid | int | 父节点 id |
| title | str | 节点标题 |
| content | str | 节点内容（可能含 Base64 文件数据） |

空目录返回 `[]`。

---

### POST /add_note

添加新节点。流程：校验参数 → 校验父节点存在 → AI 内容审核 → 写入数据库。

**Request**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| pid | int | 是 | 父节点 id，0 为根节点，非 0 时校验父节点是否存在 |
| title | str | 是 | 节点标题，不可为空 |
| content | str | 是 | 节点内容，不可为空（可含 Base64 文件数据） |

```bash
curl -X POST http://localhost:8006/add_note \
  -H 'Content-Type: application/json' \
  -d '{"pid": 0, "title": "新节点", "content": "节点内容"}'
```

**Response 200** — 成功

```json
{"success": true}
```

**Response 400** — 失败

| 场景 | 响应体 |
|------|--------|
| 缺少必填字段 | `{"error": "Pid, title and content are required"}` |
| 父节点不存在（pid≠0） | `{"error": "the new node need a parent"}` |
| AI 内容审核不通过 | `{"error": "review failed"}` |
| 数据库写入失败 | `{"success": false}` |

---

### POST /delete_note

删除指定节点。存在子节点时拒绝删除（保护树结构完整性）。

**Request**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | int | 是 | 要删除的节点 id |

```bash
curl -X POST http://localhost:8006/delete_note \
  -H 'Content-Type: application/json' \
  -d '{"id": 1}'
```

**Response 200** — 成功

```json
{"success": true}
```

**Response 400** — 失败

| 场景 | 响应体 |
|------|--------|
| 缺少 id 字段 | `{"error": "Note ID is required"}` |
| 存在子节点 | `{"error": "this node has a child"}` |
| 数据库删除失败 | `{"success": false}` |

---

### POST /update_note

更新节点的标题、内容和父节点（可用于迁移节点到其他位置）。

**Request**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | int | 是 | 要更新的节点 id |
| pid | int | 是 | 新的父节点 id（迁移时改变此值） |
| title | str | 是 | 新标题，不可为空 |
| content | str | 是 | 新内容，不可为空 |

```bash
curl -X POST http://localhost:8006/update_note \
  -H 'Content-Type: application/json' \
  -d '{"id": 1, "pid": 2, "title": "新标题", "content": "新内容"}'
```

**Response 200** — 成功

```json
{"success": true}
```

**Response 400** — 失败

| 场景 | 响应体 |
|------|--------|
| 缺少必填字段 | `{"error": "Invalid param"}` |
| 数据库更新失败 | `{"success": false}` |

---

## 前端

三文件架构：

```
templates/index.html   页面结构（Font Awesome 图标）
static/css/style.css   样式
static/js/script.js    交互逻辑
```

功能：节点树浏览、新增/编辑/删除节点、文件 Base64 上传与下载、节点迁移（移动到其他父节点下）。

> 注：首页路由 `@app.route('/')` 当前已注释，前端页面实际由 static 服务的 `dnfs.html` 承载。

## 依赖

```
flask
flask-cors
waitress
nuitka        # 可选，编译为独立二进制
```

运行时需要 `db`（:8005）和 `ai_review`（:8007）服务已启动。

## 运行

```bash
bash _set.sh   # 创建 venv + 安装依赖
bash _run.sh   # 启动服务
```

## 已知问题

- `delete_note` 中子节点检查逻辑有误：查询条件为 `where id = note_id`（查节点自身），应为 `where pid = note_id`（查子节点），导致任何节点都无法删除。
- `review_content` 返回值类型不一致：正常返回 `bool`，HTTPError 时返回 `dict`。
