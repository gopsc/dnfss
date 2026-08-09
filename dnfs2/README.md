# dnfs2 — C++20 树状网络文件系统

dnfs 的 C++ 重实现，接口与 Python dnfs 完全兼容，修复 P0/P1 安全问题。基于 libhttp + libmsg + boost::json。

## 架构

```
gateway (验签/转发)
   │  Bearer Token
   ▼
dnfs2 :8006 ──pymsg──► db2 :8005 (execute 路由) ──► SQLite
   │                     ▲
   │                  libmsg (RpcClient)
   └──HTTP──► ai_review :8007 (内容审核)
```

- **HTTP 服务**: libhttp `HttpServer`（Boost.Beast 异步，多线程）
- **Token 验签**: HttpClient POST gateway `/whoami`，fail-closed（503）
- **数据操作**: libmsg `RpcClient` 调 db2 `execute` 路由
- **内容审核**: HttpClient POST ai_review `/review`，fail-closed
- **异步日志**: libmsg `AsyncQueue` → db2 Logs 表

## 接口

| 路由 | 鉴权 | 说明 |
|------|------|------|
| POST /get_notes | 否（可选 Token，用于 can_operate） | 返回子节点数组 |
| POST /add_note | 是 | 校验 Token → 父节点存在 → AI 审核 → 插入 |
| POST /delete_note | 是 | 校验 Token → 检查子节点 → 所有权 → 删除 |
| POST /update_note | 是 | 校验 Token → 所有权 → 父节点存在 → AI 审核 → 更新 |

请求/响应 JSON 格式与 Python dnfs 完全一致，前端零改动。

## 配置

纯 CLI + 环境变量 + 默认值，不依赖 confs。

| 配置项 | CLI 参数 | 环境变量 | 默认值 |
|--------|---------|---------|--------|
| 绑定地址 | `--host` / `--addr` / `-a` | `LISTEN_ADDR` | 127.0.0.1 |
| 监听端口 | `--port` / `-p` | `LISTEN_PORT` | 8006 |
| 工作线程 | `--threads` / `-t` | — | 4 |
| 数据库地址 | `--db-host` | `DB_HOST` | 127.0.0.1 |
| 数据库端口 | `--db-port` | `DB_PORT` | 8005 |
| 数据库超时 | `--db-timeout` | — | 10 |
| AI 审核URL | `--review-url` | `REVIEW_URL` | http://127.0.0.1:8007/review |
| AI 审核超时 | `--review-timeout` | — | 10 |
| 网关验签URL | `--gateway-url` | `GATEWAY_URL` | http://127.0.0.1:8443/whoami |
| 网关超时 | `--gateway-timeout` | — | 5 |
| 日志级别 | `--log-level` | — | info |
| 实例名 | `--instance` | — | dnfs2 |

优先级: CLI > 环境变量 > 默认值

## 构建

```bash
./_set.sh    # 编译
./_run.sh    # 运行
```

依赖: libhttp, libmsg, boost::json, OpenSSL, libcurl

## 安全修复（相比 Python dnfs）

| 编号 | 问题 | 修复方案 |
|------|------|---------|
| P0-4 | 父节点删除 TOCTOU + 未检查 success | 统一封装 `_call_db()` 检查 `success` 后再访问 `data` |
| P1-6 | 请求体无限制 | `server.setMaxBodySize(10MB)` |
| P1-7 | log_client 配置不一致 | 日志客户端复用主配置 |
| P1-8 | update_note 改 pid 未校验父节点 | pid≠0 时校验父节点存在 |
| P1-9 | db RPC 未启用启动等待 | `RpcClient(retry_on_refused=true)` |
| P1-11 | note_id 无强制类型转换 | 显式 `as_int64()` + 类型校验 |

## 已知限制（P2，设计决策不修复）

- L-1: update_note 允许创建循环（需图遍历检测后代关系）
- L-2: delete_note TOCTOU 残余（需 db2 事务支持）
- L-4: 无速率限制（gateway/WAF 职责）
- L-5: title/content 无最大长度校验
- L-6: AI 审核不可用时阻断写入（fail-closed 设计决策）
