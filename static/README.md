# static — 前端托管服务

基于 Flask 的轻量级文件托管与前端页面服务，监听 `127.0.0.1:9200`。

承载"树状菜单"社区的全部前端页面，同时提供文件浏览与流媒体播放能力。

## 架构

```
gateway :443 (反向代理)
    │
    ▼
static :9200 (Flask + CORS)
    │
    └── public/          托管目录
        ├── index.html   社区首页
        ├── login.html   登录
        ├── register.html 注册
        ├── dnfs.html    树状笔记（调用 dnfs:8006）
        ├── __view.html  文件浏览器（系统文件，不在列表中显示）
        ├── allcolor.html 调色板
        ├── css/         theme.css（统一主题）+ view.css
        ├── js/          dnfs.js / login.js / register.js / validation.js / view.js
        ├── games/       HTML 小游戏合集
        └── favicon.ico
```

## 路由

| 路径 | 说明 |
|------|------|
| `GET /` | 社区首页（index.html），不存在时回退 `__view.html` |
| `GET /<path>` | 静态文件服务；目录返回 `__view.html` 由前端拉取列表 |
| `GET /api/filelist?path=` | 文件列表 API，过滤 `__` 前缀系统文件 |
| `GET /video/<path>` | 视频/音频流媒体（HTTP Range 206 分段） |
| `GET /<path>?download=true` | 强制下载 |
| `GET /health` | 健康检查 |

## 功能

- 文件浏览：目录导航、面包屑、实时搜索（`/` 或 `Ctrl+F`）、文件统计
- 流媒体：视频（MP4/WebM/OGG/AVI/MOV/MKV）与音频（MP3/WAV/OGG/FLAC/AAC/M4A）在线播放，支持进度拖动
- 深色模式：`css/theme.css` 统一主题，自动适配系统偏好
- 路径遍历防护：`safe_path_join` 限制访问范围在 `directory.root` 内
- Nuitka 编译：可打包为独立二进制 `fsys.bin`（`sys.frozen` 检测 + BASE_DIR 锚定）

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
```

## 依赖

```
flask>=2.3.0,<3.0.0
flask-cors>=4.0.0,<5.0.0
nuitka          # 可选，编译为独立二进制
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
