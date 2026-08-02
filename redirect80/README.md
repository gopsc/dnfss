# redirect80 — HTTP→HTTPS 重定向服务

基于 Python 内置 `http.server` 的极简 HTTP 重定向服务，监听 80 端口，将所有 HTTP 请求重定向到 HTTPS (443)，实现浏览器和搜索引擎友好的 HTTP→HTTPS 无缝升级。

## 设计

- **零 Web 框架依赖**：纯 Python 标准库 `http.server` + `ThreadingMixIn` 多线程并发
- **智能重定向**：GET/HEAD → 301（搜索引擎友好永久重定向），POST/PUT/DELETE/PATCH → 308（保留方法和 body）
- **Host 保留**：读取请求 `Host` 头构造重定向 URL（剥离端口），回退用配置的监听地址
- **日志集成**：通过 `log_client.py` 异步批量推送运行日志到 `db:8005` 的 Logs 表（fail-safe）
- **配置分层**：confs 强依赖 → 环境变量覆盖（`REDIRECT80_*`），confs 不可用时直接退出不做本地回退

## 配置

配置通过 confs 统一配置服务分发（`confs/configs/redirect80.ini`），启动时经 pymsg 阻塞等待拉取（retry_on_refused），confs 不可用时进程直接退出。环境变量优先级最高。

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| `host` | `REDIRECT80_HOST` | `0.0.0.0` | 监听地址 |
| `port` | `REDIRECT80_PORT` | `80` | 监听端口 |
| `redirect_port` | `REDIRECT80_REDIRECT_PORT` | `443` | 重定向目标 HTTPS 端口 |
| `redirect_code` | `REDIRECT80_REDIRECT_CODE` | `301` | 非 GET/HEAD 重定向状态码（308 vs 307） |

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

## 在 core 的 init.txt 中

```
bash /opt/micro_servers/redirect80/_run.sh
```

建议放在 waf 之前：

```
bash /opt/micro_servers/db/_run.sh
bash /opt/micro_servers/confs/_run.sh
bash /opt/micro_servers/redirect80/_run.sh   # ← 80→443 重定向
bash /opt/micro_servers/waf/_run.sh
bash /opt/micro_servers/gateway/_run.sh
...
```

## 端口一览

| 端口 | 绑定地址 | 说明 |
|------|----------|------|
| 80 | 0.0.0.0 | HTTP 入口，301/308 → HTTPS :443 |

## 许可证

MIT License
