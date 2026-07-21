# 文件托管服务器

一个基于 Flask 的轻量级文件托管与浏览服务器，支持视频/音频流媒体播放和文件下载。

## ✨ 功能特性

- **文件浏览** - 现代化 Web 界面，支持文件列表查看和目录导航
- **流媒体支持** - 支持视频和音频文件的在线播放及拖动进度条
- **文件下载** - 一键下载任意文件
- **文件搜索** - 支持实时搜索过滤（快捷键：`/` 或 `Ctrl+F`）
- **响应式设计** - 同时支持桌面端和移动端访问
- **深色模式** - 自动适配系统深色/浅色主题
- **面包屑导航** - 快速定位当前目录位置
- **文件统计** - 实时显示目录中文件和子目录数量

## 🚀 快速开始

### 安装依赖

```bash
pip install flask flask-cors
```

### 配置文件

创建 `config.ini` 配置文件（可选，不创建则使用默认配置）：

```ini
[server]
host = 0.0.0.0
port = 5000

[directory]
root = ./public

[ssl]
enabled = false
cert_file = cert.pem
key_file = key.pem
```

### 启动服务器

```bash
python file_server.py
```

或指定配置文件：

```bash
python file_server.py --config my_config.ini
```

服务器启动后，访问 `http://localhost:5000` 即可浏览文件。

## 📁 目录结构

将需要托管的文件放入配置的目录（默认为 `./public`），服务器会自动创建该目录。

```
project/
├── file_server.py      # 主程序
├── config.ini          # 配置文件（可选）
└── public/             # 托管文件目录（自动创建）
    ├── videos/
    ├── music/
    └── documents/
```

## 🎬 支持的媒体格式

| 类型 | 支持的格式 |
|------|-----------|
| 视频 | MP4, WebM, OGG, AVI, MOV, MKV |
| 音频 | MP3, WAV, OGG, FLAC, AAC, M4A |

## 📡 API 接口

### 获取文件列表
```
GET /api/filelist?path=subdir
```

### 流媒体播放
```
GET /video/{filepath}
```

### 健康检查
```
GET /health
```

## 🔒 安全特性

- 路径遍历防护，防止访问托管目录外的文件
- 完善的错误处理和日志记录
- 支持 HTTPS/SSL 加密传输

## 📋 依赖

- Python 3.6+
- Flask
- Flask-CORS

## 📄 许可证

MIT License