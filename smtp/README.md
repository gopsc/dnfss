# smtp — 邮箱验证码服务

基于自研 [pymsg](../pymsg/) 协议（TCP 长度前缀帧 + JSON）的邮箱验证码服务，通过 SMTP_SSL 发送 6 位数字验证码，支持发送冷却和防暴力枚举。

服务由 `core`（PMC）的 `init.txt` 拉起，监听 `127.0.0.1:8008`（TCP），仅允许本机调用。`gateway` 在 `/register` 流程中经 pymsg 调用本服务完成邮箱验证。

## 架构

```
gateway :8443 (注册流程)
    │  pymsg TCP
    ▼
smtp :8008 (MsgServer)
    │  SMTP_SSL
    ▼
外部 SMTP 服务器 (163/QQ 等)
    │
    ▼
用户邮箱（收验证码）
```

## 路由

服务基于 pymsg 协议，每条消息为完整 JSON，`_route` 字段指定路由。

| _route | 说明 |
|--------|------|
| `send_email` | 生成 6 位数字验证码，通过 SMTP_SSL 发送至指定邮箱 |
| `verify_email` | 校验用户输入的验证码，一次性消费 |
| `health` | 健康检查 |

响应格式：

```json
{"success": true,  "data": {...}}
{"success": false, "error": "..."}
```

### 1. send_email — 发送验证码

**请求**：

```json
{"_route": "send_email", "email": "user@example.com"}
```

可选字段 `subject` / `content` 覆盖默认标题与正文，`content` 中 `{code}` 会被替换为验证码。

**响应**：

| 响应 data | 触发条件 |
|-----------|----------|
| `{"sent": true, "ttl": 600}` | 发送成功，TTL 为有效期（秒） |
| `{"sent": false, "error": "invalid email"}` | 邮箱格式无效 |
| `{"sent": false, "error": "SMTP 服务未配置"}` | .env 缺少 SMTP 配置 |
| `{"sent": false, "retry_after": N, "error": "发送过于频繁，请 N 秒后重试"}` | 同一邮箱在冷却期内 |

**安全机制**：
- 发送冷却：同一邮箱两次成功发送的最小间隔 `SEND_INTERVAL`（60 秒）。冷却期内请求直接拒绝，不覆盖已有验证码。
- 发送失败回滚：SMTP 发送失败时立即删除刚写入的验证码，避免"码已存但邮件没到"的不可用状态。

### 2. verify_email — 校验验证码

**请求**：

```json
{"_route": "verify_email", "email": "user@example.com", "code": "123456"}
```

兼容旧字段名 `input_captcha`。

**响应**（均为 `success: true`，业务结果在 `data` 中区分）：

| 响应 data | 触发条件 |
|-----------|----------|
| `{"valid": true}` | 验证码正确（已消费） |
| `{"valid": false, "error": "email and code required"}` | 缺少字段 |
| `{"valid": false, "error": "invalid or expired code"}` | 验证码错误、过期或不存在 |
| `{"valid": false, "invalidated": true, "error": "验证码连续错误 5 次，已失效，请重新获取"}` | 连续校验失败达上限 |

> 验证码一经正确校验即从内存中删除（一次性消费），防重放。同一验证码连续校验失败达 `MAX_VERIFY_ATTEMPTS`（5 次）即作废，防止对 6 位数字码的在线枚举。

### 3. health — 健康检查

**请求**：`{"_route": "health"}`
**响应**：`{"success": true, "data": {"status": "ok", "service": "smtp"}}`

## 配置

通过 `.env` 文件配置 SMTP 连接信息，支持 4 种格式：

```bash
# KEY=VALUE（推荐）
SMTP_SERVER=smtp.163.com
SMTP_PORT=465
SMTP_USER=your@163.com
__PASSWORD=your_smtp_password

# 也支持 KEY: VALUE / KEY VALUE / export KEY=...
MAIL_FROM=dnfs@qsont.me    # 可选，发件人身份；默认跟随 SMTP_USER
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `SMTP_SERVER` | 是 | SMTP 服务器地址 |
| `SMTP_PORT` | 否 | SMTP 端口，默认 465 |
| `SMTP_USER` | 是 | 邮箱登录账号 |
| `__PASSWORD` | 是 | 邮箱密码/授权码（兼容别名 `SMTP_PASSWORD`） |
| `MAIL_FROM` | 否 | 发件人身份，默认同 SMTP_USER。多数服务商强制 From 头与登录账号一致 |

服务启动时检查必填配置项，缺失时打印警告日志但不阻止启动（`send_email` 路由会返回配置错误）。

## 数据模型与验证码生命周期

- **存储**：进程内存字典 `code_store`，键为 email，值为 `{code, expires_at, attempts}`。
- **冷却记录**：`send_history` 字典，键为 email，值为上次成功发送的时间戳。
- **生成**：`send_email` 成功时写入 `code_store`，记录 `expires_at = now + CODE_TTL`（600 秒）。
- **消费**：`verify_email` 校验通过时 `del code_store[email]`，不可再次使用。
- **过期**：超时后 `verify_email` 自动删除并返回 `invalid or expired code`。`send_email` 时惰性清理过期项。
- **作废**：连续校验失败达 5 次后删除验证码，返回 `invalidated: true`。
- **发送失败回滚**：SMTP 异常时立即 `pop` 刚写入的 `code_store` 条目。

## 运行

```bash
# 初始化（创建 venv + 安装依赖）
bash _set.sh

# 启动
bash _run.sh

# 或直接运行
python3 smtp_v2.py --addr 127.0.0.1 --port 8008
```

## 集成关系

- 由 `core` 通过 `init.txt` 拉起的 `bash _run.sh` 守护运行。
- `gateway` 在 `/register` 时经 pymsg 调 `send_email` 发送验证码，调 `verify_email` 校验。需在 gateway 的 `example.ini` 中启用邮箱验证功能。
- 日志通过 `log_client.py` 异步批量推送至 db:8005 的 Logs 表（缓冲 50 条 / 2 秒）。

## 项目结构

```
smtp/
├── smtp_v2.py        # 服务主程序（pymsg 协议，当前有效版本）
├── smtp.py           # 旧版 Flask HTTP 实现（已废弃，仅作参考）
├── log_client.py     # 集中日志推送 SDK
├── _set.sh           # 初始化脚本（创建 venv + 安装依赖）
├── _run.sh           # 启动脚本（exec python3 smtp_v2.py）
├── requirements.txt  # Python 依赖（pymsg、nuitka）
└── .gitignore        # 忽略 __pycache__ / .env / venv
```

## 注意事项

- 验证码存储在进程内存中，服务重启后全部失效；不支持多实例水平扩展。
- 默认仅监听 `127.0.0.1`，仅供本机调用；浏览器侧不直接访问本服务。
- 旧版 `smtp.py`（Flask HTTP 实现）已废弃，`_run.sh` 当前启动的是 `smtp_v2.py`。旧版存在无冷却、无失败回滚、无防枚举等安全问题，且 `verify_email` 在 email 不存在时有 KeyError 风险，不应在生产环境使用。
- 多数邮箱服务商（163/QQ 等）强制 From 头与登录账号一致，`MAIL_FROM` 仅当服务商允许自定义发件身份时才起作用。
- 验证码为纯 6 位数字，有效期 600 秒。

## 许可证

MIT License
