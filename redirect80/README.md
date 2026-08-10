# redirect80 — HTTP→HTTPS 重定向服务

基于 Python 内置 `http.server` 的极简 HTTP 重定向服务，监听 80 端口，将所有 HTTP 请求重定向到 HTTPS (443)，实现浏览器和搜索引擎友好的 HTTP→HTTPS 无缝升级。

## 设计

- **零 Web 框架依赖**：纯 Python 标准库 `http.server` + `ThreadingMixIn` 多线程并发
- **智能重定向**：GET/HEAD → 301（搜索引擎友好永久重定向），POST/PUT/DELETE/PATCH → 308（保留方法和 body）
- **Host 保留**：读取请求 `Host` 头构造重定向 URL（剥离端口），回退用配置的监听地址
- **日志集成**：通过 `log_client.py` 异步批量推送运行日志到 `db:8005` 的 Logs 表（fail-safe）
- **配置分层**：CLI 参数（由 core/services.yaml 传入） > 环境变量覆盖（`REDIRECT80_*`） > 代码默认值

## 配置

配置通过三层加载（CLI > 环境变量 > 默认值），全部配置项由 core/services.yaml 经 CLI 参数传入，不依赖 confs。

| 配置项 | CLI 参数 | 环境变量 | 默认值 | 说明 |
|--------|----------|----------|--------|------|
| `host` | `--host` | `REDIRECT80_HOST` | `0.0.0.0` | 监听地址 |
| `port` | `--port` | `REDIRECT80_PORT` | `80` | 监听端口 |
| `redirect_port` | `--redirect-port` | `REDIRECT80_REDIRECT_PORT` | `443` | 重定向目标 HTTPS 端口 |
| `redirect_code` | `--redirect-code` | `REDIRECT80_REDIRECT_CODE` | `301` | GET/HEAD 重定向状态码 |

## 安装 & 运行

```bash
cd redirect80
bash _set.sh   # 创建 venv 并安装依赖
bash _run.sh   # 运行（尝试自动设置 cap_net_bind_service）
```

## 端口权限

80 端口是 Linux 特权端口（<1024），需要 `CAP_NET_BIND_SERVICE` 能力或 root 权限。`_run.sh` 会自动尝试：

```bash
sudo setcap 'cap_net_bind_service=+ep' venv/bin/python3
```

如果 `setcap` 不可用或失败，建议使用 systemd 的 `AmbientCapabilities=CAP_NET_BIND_SERVICE`：

```ini
[Service]
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=/opt/micro_servers/redirect80/_run.sh
```

## 在 core 的 services.yaml 中

redirect80 作为最后一个服务启动，配置全部经 CLI 传入：

```yaml
- name: redirect80
  cmd: bash /opt/micro_servers/redirect80/_run.sh
  args:
    - "--instance"
    - "redirect80"
    - "--log-level"
    - "info"
    - "--host"
    - "0.0.0.0"
    - "--port"
    - "80"
    - "--redirect-port"
    - "443"
    - "--redirect-code"
    - "301"
  depends_on: [waf]
  sleep_before: 0
```

## 端口一览

| 端口 | 绑定地址 | 说明 |
|------|----------|------|
| 80 | 0.0.0.0 | HTTP 入口，301/308 → HTTPS :443 |

## 许可证

MIT License
