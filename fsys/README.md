# fsys — 静态文件托管服务 (C++20)

基于 libhttp + libmsg 的 C++20 静态文件托管与前端页面服务，监听 `127.0.0.1:9200`。

Python/Flask `static` 模块的生产级 C++ 重写，接口 100% 兼容，前端零改动。

## 架构

```
waf :443 (TLS 终止 + 安全规则)
    │
    ▼
gateway :8443 (反向代理 + 认证)
    │
    ▼
fsys :9200 (libhttp 异步多线程 + CORS)
    │
    ├── public/          静态文件根目录
    ├── dnfs2 :8006      笔记代理（反向代理）
    ├── db2 :8005        用户查询 + 日志写入
    └── confs :8010      配置拉取
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
| `GET /api/favicon?url=` | favicon SSRF 防护代理（512KB 上限，1 天缓存） |

### dnfs2 代理路由

| 路径 | 说明 |
|------|------|
| `POST /get_notes` | 获取树状笔记列表（代理至 dnfs2:8006） |
| `POST /add_note` | 新增笔记（代理至 dnfs2:8006） |
| `POST /delete_note` | 删除笔记（代理至 dnfs2:8006） |
| `POST /update_note` | 更新笔记（代理至 dnfs2:8006） |

支持 HttpOnly Cookie 模式：`q4_token` Cookie 自动转为 `Authorization: Bearer` 头。

## 配置

与 `static` 模块完全兼容，使用相同的 `static.ini`（由 confs 服务分发）。

| Section | Key | 说明 |
|---------|-----|------|
| `[server]` | `host` | 监听地址（默认 127.0.0.1） |
| `[server]` | `port` | 监听端口（默认 9200） |
| `[directory]` | `root` | 静态文件根目录（相对路径基于工作目录，_run.sh 以模块目录启动） |
| `[ssl]` | `enabled` | TLS 开关（默认 false） |
| `[ssl]` | `cert_file` | 证书路径 |
| `[ssl]` | `key_file` | 私钥路径 |
| `[cors]` | `allowed_origins` | CORS 白名单，逗号分隔 |

### 环境变量

| 变量 | 说明 |
|------|------|
| `LISTEN_ADDR` | 覆盖监听地址 |
| `LISTEN_PORT` | 覆盖监听端口 |
| `STATIC_ROOT` | 覆盖静态文件根目录 |
| `DB_HOST` / `DB_PORT` | 覆盖 db 服务地址 |
| `DNFS_URL` | 覆盖 dnfs2 上游地址 |
| `CONFS_HOST` / `CONFS_PORT` | 覆盖 confs 引导地址 |

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--addr` / `-a` | 监听地址 |
| `--port` / `-p` | 监听端口 |
| `--threads` / `-t` | 工作线程数 |

## 相比 Python static 的改进

| 改进 | 说明 |
|------|------|
| 请求体大小限制 | `setMaxBodySize(10MB)` 防 OOM |
| SSL 原生支持 | libhttp 构造器直接支持，无需回退开发服务器 |
| 异步 IO | Boost.Beast 异步多线程替代 Flask 同步 |
| 消除 Python 依赖 | 不再需要 venv/pip/Flask/waitress |
| 命令行参数 | 支持 `--addr/-a --port/-p --threads/-t` |
| 数值范围校验 | 所有端口/线程数有严格边界检查 |
| 统一日志配置 | 复用主配置 confs 拉取的 db host/port |

## 运行

```bash
bash _set.sh   # 编译
bash _run.sh   # 启动
```

## 依赖

libhttp, libmsg, boost::json, OpenSSL, libcurl

## 许可证

MIT License
