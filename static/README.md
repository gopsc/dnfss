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
- 特殊节点（dnfs 管理员专属）：信封 JSON `{"_sp":...}` 内容差异化渲染 —— 链接（favicon 卡片）/ 网页代码（可审计可运行）/ 图片（直显）/ 文件（下载）；发布权限由 dnfs 后端按 `is_admin` 强制
- 路径遍历防护：`safe_path_join` 限制访问范围在 `directory.root` 内
- Nuitka 编译：可打包为独立二进制 `fsys.bin`（`sys.frozen` 检测 + BASE_DIR 锚定）
- 安全头注入：`security_middleware.py` 为所有响应注入 CSP、X-Frame-Options 等安全头
- 集中日志：`log_client.py` 通过 pymsg TCP 异步批量推送日志至 db:8005 的 Logs 表

## 配置

`config.ini`：

```ini
[server]
host = 127.0.0.1
port = 9200

[directory]
root = ./public

[ssl]
enabled = false
cert_file = cert.pem
key_file = key.pem

[cors]
# 允许跨域的 Origin 白名单（逗号分隔）
# 注意：生产环境请勿包含 localhost / 127.0.0.1 / 通配符 *
allowed_origins = https://qsont.me,https://yhdet.top
```

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
