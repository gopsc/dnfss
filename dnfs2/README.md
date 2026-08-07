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

从 confs 拉取 `dnfs.ini`，环境变量覆盖，命令行参数最高优先级。

```ini
[server]
host = 127.0.0.1
port = 8006

[database]
host = 127.0.0.1
port = 8005
timeout = 10

[ai_review]
url = http://127.0.0.1:8007/review
timeout = 10

[gateway]
url = http://127.0.0.1:8443/whoami
timeout = 5
```

环境变量: `CONFS_HOST`, `CONFS_PORT`, `LISTEN_ADDR`, `LISTEN_PORT`, `DB_HOST`, `DB_PORT`, `REVIEW_URL`, `GATEWAY_URL`

命令行: `--addr`, `--port`, `--threads`

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
