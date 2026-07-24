# ai_review — AI 内容审核服务

基于 DeepSeek LLM + RAG 向量检索的文本内容审核微服务。接收纯文本，返回是否违规。

## 架构

```
Client ──POST /review──▶ Flask (:8007)
                              │
                        ReviewAgent.review()
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
              RAGStore.query()     DeepSeek API
              (ChromaDB top-3)     (chat completion)
                    └───────┬───────────┘
                            ▼
                   {"invalid": "true"/"false"}
```

审核流程：从 ChromaDB 中检索与待审文本最相似的 3 条已标注样本，注入 system prompt 作为参考，交由 DeepSeek 做单轮判定。违规类别涵盖色情、毒品、赌博、诈骗、仇恨/暴力/恐怖主义，灰色内容从严判定。

## 目录结构

```
ai_review/
├── ai_review.py            # Flask 入口 (waitress)
├── .env                    # 环境变量配置
├── requirements.txt        # Python 依赖
├── _set.sh                 # 初始化: 创建 venv、安装依赖
├── _run.sh                 # 启动服务
├── test_app.py             # Streamlit 调试 UI
└── agent/
    ├── __init__.py
    ├── review_agent.py     # 核心审核逻辑 (DeepSeek + RAG)
    ├── rag_store.py        # ChromaDB 向量存储 (MD5 去重)
    └── knowledge_base.json # 种子样本 (7 违规 + 7 安全)
```

## 接口文档

基础地址：`http://127.0.0.1:8007`

---

### POST /review

提交文本进行内容审核。

**Request**

```
Content-Type: application/json
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 待审核文本，不可为空 |

```json
{"content": "待审核的文本内容"}
```

**Response 200**

| 字段 | 类型 | 说明 |
|------|------|------|
| invalid | string | `"true"` 违规 / `"false"` 安全 |
| error | string | 仅异常时出现：`"api_error"` 或 `"parse_failed"` |
| detail | string | 仅 `api_error` 时出现，异常描述 |
| raw | string | 仅 `parse_failed` 时出现，模型原始输出 |

```json
{"invalid": "true"}
```

```json
{"invalid": "false"}
```

API 不可用时 fail-open：

```json
{"invalid": "false", "error": "api_error", "detail": "Connection timeout"}
```

**Response 400**

```json
{"error": "content is required"}
{"error": "content cannot be empty"}
```

**curl 示例**

```bash
curl -X POST http://127.0.0.1:8007/review \
  -H "Content-Type: application/json" \
  -d '{"content": "这是一段正常文本"}'
```

---

### POST /rag/feedback

人工纠偏接口。当 AI 审核结果与人工判断不一致时调用，将样本写入 RAG 知识库影响后续审核。

**Request**

```
Content-Type: application/json
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 被误判的文本 |
| correct_type | string | 否 | `"violation"`（AI 漏判）或 `"safe"`（AI 误杀），默认 `"violation"` |

```json
{"content": "被误判的文本", "correct_type": "safe"}
```

**Response 200**

| 字段 | 类型 | 说明 |
|------|------|------|
| added | boolean | 是否成功写入 |
| id | string/null | 新条目 ID（格式 `{v|s}_{md5前12位}`），重复时为 null |
| md5 | string | 文本的 MD5 哈希 |
| reason | string/null | 失败原因，成功时为 null，重复时为 `"duplicate"` |

新增成功：

```json
{"added": true, "id": "s_a1b2c3d4e5f6", "md5": "a1b2c3d4e5f6...", "reason": null}
```

重复提交：

```json
{"added": false, "id": null, "md5": "a1b2c3d4e5f6...", "reason": "duplicate"}
```

**Response 400**

```json
{"error": "content is required"}
{"error": "content cannot be empty"}
{"error": "correct_type must be 'violation' or 'safe'"}
```

**curl 示例**

```bash
curl -X POST http://127.0.0.1:8007/rag/feedback \
  -H "Content-Type: application/json" \
  -d '{"content": "这段文本被误杀了", "correct_type": "safe"}'
```

---

### GET /rag/list

列出 RAG 知识库中的全部条目。

**Request**

无参数。

**Response 200**

| 字段 | 类型 | 说明 |
|------|------|------|
| total | integer | 条目总数 |
| entries | array | 条目列表 |
| entries[].id | string | 条目 ID |
| entries[].text | string | 样本文本 |
| entries[].type | string | `"violation"` 或 `"safe"` |
| entries[].category | string | 分类标签（如 `adult_content`、`gambling`、`human_feedback` 等） |

```json
{
  "total": 14,
  "entries": [
    {"id": "v_001", "text": "...", "type": "violation", "category": "adult_service"},
    {"id": "s_001", "text": "...", "type": "safe", "category": "daily"}
  ]
}
```

**curl 示例**

```bash
curl http://127.0.0.1:8007/rag/list
```

---

### GET /health

健康检查。

**Request**

无参数。

**Response 200**

```json
{"status": "ok"}
```

**curl 示例**

```bash
curl http://127.0.0.1:8007/health
```

## 配置 (.env)

```
DEEPSEEK_API_KEY=<your-key>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_TEMPERATURE=0.0
DEEPSEEK_MAX_TOKENS=128
AI_REVIEW_HOST=127.0.0.1
AI_REVIEW_PORT=8007
```

## 快速开始

```bash
# 初始化环境
bash _set.sh

# 填入 .env 中的 DEEPSEEK_API_KEY

# 启动服务
bash _run.sh
```

服务默认监听 `127.0.0.1:8007`。

## 调试 UI

```bash
streamlit run test_app.py
```

提供文本提交、结果展示、人工纠错按钮、RAG 知识库浏览等功能。

## 设计要点

- **RAG 增强审核**：推理时检索相似已标注样本辅助判定，支持运行时持续学习。
- **人工反馈闭环**：通过 `/rag/feedback` 纠正误判，新样本即时持久化并影响后续审核。
- **MD5 去重**：防止向量库中出现重复条目。
- **Fail-open**：DeepSeek API 不可用时放行内容（返回 `invalid: "false"`）。

## 依赖

openai, flask, flask-cors, waitress, chromadb, python-dotenv
