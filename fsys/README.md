# fsys — 静态文件托管服务 (C++20)

基于 libhttp + libmsg 的 C++20 静态文件托管与前端页面服务，监听 `127.0.0.1:9200`。

Python/Flask `static` 模块的生产级 C++ 重写，前端零改动兼容（改进性差异：上游状态码透传使前端 401 跳转首次生效，详见审计报告 D-1）。

## 架构

```
waf :443 (TLS 终止 + 安全规则)
    │
    ▼
ca :8443 (认证 + 身份头注入 + 前置转发)
    │
    ▼
gateway :8445 (纯转发反向代理, 仅 loopback)
    │
    ▼
fsys :9200 (libhttp 异步多线程 + CORS)
    │
    ├── public/          静态文件根目录
    ├── dnfs2 :8006      笔记代理（反向代理）
    ├── db2 :8005        用户查询 + 日志写入
    └── (配置纯 CLI 传递，不依赖 confs)
```

## 路由

### 核心路由

| 路径 | 说明 |
|------|------|
| `GET /` | 首页（优先 index.html，回退 __view.html） |
| `GET /*` | 静态文件 / 目录浏览 |
| `GET /api/filelist?path=` | JSON 文件列表，过滤 `__` 前缀系统文件 |
| `GET /video/*` | 视频/音频流媒体（HTTP Range 206 分段） |
| `GET /health` | 健康检查 |

### 内部工具路由

| 路径 | 说明 |
|------|------|
| `POST /api/hash` | SHA-256 密码哈希（限速 20/min/IP） |
| `POST /api/check_user` | 检查用户是否已注册（限速 10/min/IP） |
| `GET /api/favicon?url=` | favicon SSRF 防护代理（512KB 上限，仅浏览器侧 Cache-Control 缓存 1 天，无服务端缓存） |

### dnfs2 代理路由

| 路径 | 说明 |
|------|------|
| `POST /get_notes` | 获取树状笔记列表（代理至 dnfs2:8006） |
| `POST /add_note` | 新增笔记（代理至 dnfs2:8006） |
| `POST /delete_note` | 删除笔记（代理至 dnfs2:8006） |
| `POST /update_note` | 更新笔记（代理至 dnfs2:8006） |

支持 HttpOnly Cookie 模式：`q4_token` Cookie 自动转为 `Authorization: Bearer` 头。

## 配置

纯 CLI + 环境变量 + 默认值（CLI > 环境变量 > 默认值），不依赖 confs；原 `static.ini` 各值已迁入 `core/services.yaml` 的 fsys 条目 args。

| 配置项 | CLI 参数 | 环境变量 | 默认值 |
|---------|---------|----------|--------|
| 监听地址 | `--addr` / `-a` | `LISTEN_ADDR` | 127.0.0.1 |
| 监听端口 | `--port` / `-p` | `LISTEN_PORT` | 9200 |
| 工作线程数 | `--threads` / `-t` | — | 4 |
| 静态根目录 | `--root` | `STATIC_ROOT` | ./public |
| TLS 开关 | `--ssl-enabled` | — | false |
| 证书 / 私钥 | `--cert-file` / `--key-file` | — | cert.pem / key.pem |
| CORS 白名单 | `--cors-origins` | — | 逗号分隔，必需 |
| db2 地址 | `--db-host` / `--db-port` | `DB_HOST` / `DB_PORT` | 127.0.0.1:8005 |
| dnfs2 上游 | `--dnfs-url` | `DNFS_URL` | http://127.0.0.1:8006 |
| 实例名 / 日志级别 | `--instance` / `--log-level` | — | fsys / info |

## 相比 Python static 的改进

| 改进 | 说明 |
|------|------|
| 请求体大小限制 | `setMaxBodySize(10MB)` 防 OOM |
| SSL 原生支持 | libhttp 构造器直接支持，无需回退开发服务器 |
| 异步 IO | Boost.Beast 异步多线程替代 Flask 同步 |
| 消除 Python 依赖 | 不再需要 venv/pip/Flask/waitress |
| 命令行参数 | 支持 `--addr/-a --port/-p --threads/-t` |
| 数值范围校验 | 所有端口/线程数有严格边界检查 |
| 统一日志配置 | 复用主配置（CLI/环境变量）的 db host/port |

## 运行

```bash
bash _set.sh   # 编译
bash _run.sh   # 启动
```

## 依赖

libhttp, libmsg, boost::json, OpenSSL, libcurl

## 许可证

MIT License
