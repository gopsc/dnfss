# smtp3 — C++20 邮箱验证码服务

smtp 的 C++20 重实现，接口与 Python 版完全兼容（pymsg 路由 send_email/verify_email/health），修复审计发现的安全项（弱随机、日志明文验证码、now 遮蔽等）。基于 libmsg + boost::json + OpenSSL。

## 架构

```
ca :8443 (注册流程经 pymsg 调用)
   │  pymsg TCP
   ▼
smtp3 :8008 (RpcServer)
   │  SMTP_SSL (OpenSSL)
   ▼
外部 SMTP 服务器 (163/QQ 等)
```

- **服务框架**: libmsg `RpcServer`（pymsg 协议，4B 大端长度前缀 + UTF-8 JSON）
- **SMTP 会话**: 直接控制 OpenSSL 会话发送，更精细的超时/错误处理
- **安全**: `std::random_device` 安全随机、常数时间比较、日志脱敏、冷却/限流/熔断
- **异步日志**: libmsg `AsyncQueue` → db2 Logs 表

## 接口

| pymsg 路由 | 说明 |
|-----------|------|
| `send_email` | 生成 6 位验证码并经 SMTP_SSL 发送，10 分钟有效；同一邮箱 60s 冷却，发送失败不计入冷却 |
| `verify_email` | 校验，通过即一次性消费防重放；连续失败 5 次自动作废并返回 `invalidated` |
| `health` | 健康检查 |

请求/响应 JSON 格式与 Python smtp 完全一致，ca 零改动。

## 配置

纯 CLI + 环境变量 + 默认值，不依赖 confs。

| 配置项 | CLI 参数 | 默认值 |
|--------|---------|--------|
| 绑定地址 | `--addr` / `-a` | 127.0.0.1 |
| 监听端口 | `--port` / `-p` | 8008 |
| SMTP 服务器端口 | `--smtp-port` | 465（SSL） |
| 验证码长度 | `--code-len` | 6（4~10） |
| 验证码有效期 | `--code-ttl` | 600s |
| 发送冷却 | `--send-interval` | 60s |
| 最大校验失败次数 | `--max-verify-attempts` | 5 |
| 单邮箱限流 | `--email-hourly-limit` / `--email-daily-limit` | 3 / 10 |
| 全局熔断 | `--smtp-hourly-limit` | 100 |
| 邮件标题/正文上限 | `--max-subject-len` / `--max-content-len` | 256 / 4096 |
| 实例名 / 日志级别 | `--instance` / `--log-level` | smtp / info |

SMTP 账号凭据经 `.env` 注入（`SMTP_SERVER` / `SMTP_USER` / `__PASSWORD`）。优先级: CLI > 环境变量 > 默认值。

## 构建与运行

```bash
./_set.sh    # 编译
./_run.sh    # 运行
```

依赖: libmsg, boost::json, OpenSSL（libssl-dev）

## 状态

- **待上线**：代码就绪并通过安全审计，`core/services.yaml` 仍拉起 Python 版 `smtp`，尚未平替
- 开发文档见 [docs/reports/smtp_cpp_rewrite_dev_doc.md](../docs/reports/smtp_cpp_rewrite_dev_doc.md)
- 安全审计见 [docs/security/smtp3_SECURITY_AUDIT_20260809.md](../docs/security/smtp3_SECURITY_AUDIT_20260809.md)

## 许可证

MIT License
