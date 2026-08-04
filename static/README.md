# static — 前端托管服务

基于 Flask 的轻量级文件托管与前端页面服务，监听 `127.0.0.1:9200`。

承载"树状菜单"社区的全部前端页面，同时提供文件浏览与流媒体播放能力。

## 架构

```
waf :443 (TLS 终止 + 安全规则)
    │
    ▼
gateway :8443 (反向代理 + 认证)
    │
    ▼
static :9200 (Flask + CORS)
    │
    └── public/          托管目录
        ├── login.html   登录（默认首页）
        ├── register.html 注册
        ├── index.html   社区首页
        ├── forgot.html  忘记密码
        ├── dnfs.html    树状笔记（调用 dnfs:8006）
        ├── aibox.html   aibox 智能体 Web 客户端（管理员专属，经 gateway WS 反向代理）
        ├── __view.html  文件浏览器（系统文件，不在列表中显示）
        ├── allcolor.html 调色板
        ├── LICENSE.html 许可证页面
        ├── css/         theme.css（统一主题）+ view.css
        ├── js/          dnfs.js / login.js / register.js / validation.js / view.js
        ├── games/       HTML 小游戏合集
        └── favicon.ico
```

## 路由

### 核心路由

| 路径 | 说明 |
|------|------|
| `GET /` | 登录页（login.html），如不存在回退 `__view.html` |
| `GET /<path>` | 静态文件服务；目录返回 `__view.html` 由前端拉取列表 |
| `GET /api/filelist?path=` | 文件列表 API，过滤 `__` 前缀系统文件 |
| `GET /video/<path>` | 视频/音频流媒体（HTTP Range 206 分段） |
| `GET /<path>?download=true` | 强制下载 |
| `GET /health` | 健康检查 |

### 内部工具路由

| 路径 | 说明 |
|------|------|
| `POST /api/hash` | SHA256 密码哈希 |
| `POST /api/check_user` | 检查用户是否已注册（查询 gateway 数据库） |
| `POST /api/reset_password` | 重置用户密码（直接操作 gateway 数据库） |
| `GET /api/favicon?url=` | favicon 服务端代理（dnfs 链接卡片用；SSRF 防护：仅 http/https + 内网地址拦截 + 重定向校验，512KB 上限，缓存 1 天） |

### yhdet OAuth 路由

| 路径 | 说明 |
|------|------|
| `GET/POST /yhdet_callback` | yhdet.top OAuth 回调，自动注册/登录并跳转 dnfs 页 |
| `POST /api/yhdet_login` | yhdet OAuth 用户名密码登录 |
| `POST /api/yhdet_verify` | yhdet Token 验证 |
| `POST /api/proxy_send_code` | yhdet 验证码代理转发 |

### dnfs 代理路由

| 路径 | 说明 |
|------|------|
| `POST /get_notes` | 获取树状笔记列表（代理至 dnfs:8006） |
| `POST /add_note` | 新增笔记（代理至 dnfs:8006） |
| `POST /delete_note` | 删除笔记（代理至 dnfs:8006） |
| `POST /update_note` | 更新笔记（代理至 dnfs:8006） |

以上 4 个 dnfs 代理路由均支持 HttpOnly Cookie 模式：当请求携带 `q4_token` Cookie 时，自动转为 `Authorization: Bearer` 头转发。

> `/whoami` 已上移至 gateway，由 gateway 直接验签 Token 返回用户身份，不再经 static 代理到 dnfs。

## 功能

- 文件浏览：目录导航、面包屑、实时搜索（`/` 或 `Ctrl+F`）、文件统计
- 流媒体：视频（MP4/WebM/OGG/AVI/MOV/MKV）与音频（MP3/WAV/OGG/FLAC/AAC/M4A）在线播放，支持进度拖动
- 深色模式：`css/theme.css` 统一主题，自动适配系统偏好
- 表单组件：注册页图形/邮箱验证码统一 `.code-box` 行内布局（文字标签 + 输入框 + 控件同排，窄屏自动换行），验证码图片样式复用 `theme.css` 的 `.captcha-img` 类
- 特殊节点（dnfs 管理员专属）：信封 JSON `{"_sp":...}` 内容差异化渲染 —— 链接（favicon 卡片）/ JS 代码（可审计/可运行，插入页面 iframe 沙箱执行）/ 图片（直显）/ 文件（下载）；发布权限由 dnfs 后端按 `is_admin` 强制
- aibox Web 客户端：`aibox.html` 移动端适配聊天页，经 gateway `[WsProxy]` 反向代理（如 `/ws/aibox → ws://127.0.0.1:8765`）与 aibox 智能体通信，握手由 gateway 强制 `is_admin` 验签；协议与 BotOps/client.py 兼容（chunk 流式 / reasoning 思考区 / task 任务面板 / 指数退避重连）
- 路径遍历防护：`safe_path_join` 限制访问范围在 `directory.root` 内
- Nuitka 编译：可打包为独立二进制 `fsys.bin`（`sys.frozen` 检测 + BASE_DIR 锚定）
- 安全头注入：`security_middleware.py` 为所有响应注入 CSP、X-Frame-Options 等安全头
- 集中日志：`log_client.py` 通过 pymsg TCP 异步批量推送日志至 db:8005 的 Logs 表

## 配置

static 已接入 confs 统一配置服务，confs 是 static 启动的**强依赖**：

1. **confs 统一配置服务** — 必须成功从 confs 拉取 `static.ini`，否则 static 启动失败直接退出（exit code 1）
2. **环境变量覆盖**（最高优先级）— Docker/部署环境通过环境变量覆盖 confs 中的值

### 配置文件

集中托管在 [confs/configs/static.ini](../confs/configs/static.ini)，由 confs 服务分发：

| Section | Key | 说明 |
|---------|-----|------|
| `[server]` | `host` | 监听地址 |
| `[server]` | `port` | 监听端口 |
| `[directory]` | `root` | 静态文件根目录（相对 BASE_DIR） |
| `[ssl]` | `enabled` | 是否启用 TLS（`true`/`false`，默认 `false`） |
| `[ssl]` | `cert_file` | SSL 证书路径（默认 `cert.pem`） |
| `[ssl]` | `key_file` | SSL 私钥路径（默认 `key.pem`） |
| `[cors]` | `allowed_origins` | CORS 白名单，逗号分隔 |

### 引导配置（confs 连接地址）

confs 自身的连接地址不通过 confs 获取（避免循环依赖），通过以下方式确定（优先级从高到低）：
- 环境变量 `CONFS_HOST` / `CONFS_PORT`
- 默认值 `127.0.0.1:8010`

### 环境变量（最高优先级，覆盖 confs 配置值）

| 变量 | 说明 |
|------|------|
| `LISTEN_ADDR` | 监听地址（容器内需绑定 `0.0.0.0`） |
| `LISTEN_PORT` | 监听端口 |
| `STATIC_ROOT` | 静态文件根目录 |
| `CONFS_HOST` | confs 服务主机名（引导配置，Docker 环境使用服务名） |
| `CONFS_PORT` | confs 服务端口（引导配置） |

### 启动顺序

static 通过 pymsg `retry_on_refused=True` 自动阻塞等待 confs 就绪，**拉取配置阶段不严格要求启动顺序**：
- confs 先启动 → static 立即拉取配置
- static 先启动 → pymsg 自动重试（每秒一次，每 5 秒打印等待提示），confs 启动后自动连接成功
- 配置完整性校验：若 static.ini 中缺少必需的 section/key（server.host/port、directory.root、cors.allowed_origins）且无对应环境变量覆盖，仍然报错退出
- ssl 段为可选项，未配置时默认 `enabled=false`、cert/key 使用默认文件名

### 历史遗留：本地 config.ini 已删除

旧版本的 `static/config.ini` 本地回退逻辑已移除，配置统一由 confs 服务分发。

## 依赖

```
flask>=2.3.0,<3.0.0
flask-cors>=4.0.0,<5.0.0
waitress>=2.1.2,<4.0.0    # 生产级 WSGI 服务器（非 SSL 模式默认使用）
pymsg                     # 集中日志推送（log_client 依赖）
nuitka                    # 可选，编译为独立二进制
```

## 运行

```bash
bash _set.sh   # 创建 venv + 安装依赖
bash _run.sh   # 启动服务（当前使用 python3，Nuitka 二进制已注释）
```

## 备注

- 以 `__` 开头的文件/目录（`__view.html`、`__icons/`）为系统内部使用，文件列表 API 中不显示。
- ICP 备案：蜀ICP备2024107064号-2，链接至 beian.miit.gov.cn，已置于 index/login/register/dnfs 四页 footer。
- 入口文件名为 `fsys.py`（旧 README 中的 `file_server.py` 已废弃）。

## 许可证

MIT License
