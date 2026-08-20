# ca

UniServe Framework 的中央认证服务（Central Auth），C++20 实现，v1.0.0 起承接原 gateway v0.8.0 的全部认证职责。

## 拓扑定位（waf → ca → gateway 前置拓扑）

```
客户端 ──HTTPS──▶ waf:443 (终止 TLS + coraza 规则) ──HTTP──▶ ca:8443 (认证+前置转发) ──HTTP──▶ gateway:8445 (纯转发) ──▶ fsys:9200 / dnfs2:8006 / core:5200
```

- CA 接管原 gateway 的 **8443** 端口，WAF upstream 配置不变（`waf/config.yaml` 仍指向 8443）。
- gateway 退居 **8445**（仅 loopback），退化为纯转发服务，不再验签。
- 验签在 CA 本地完成（RSA 密钥同进程），对受保护请求零网络跳。
- 下游 core / dnfs2 / 前端 / WAF 全部零改动。

## 职责

- **账号鉴权**：`/register` 注册（需邮箱验证码，经 pymsg 调 smtp:8008 校验）、`/login` 登录，签发 RSA 签名的 Token。
- **Token 生命周期**：`/logout`（jti 吊销）、`/refresh_token`（宽限期刷新 + 次数上限）、`/whoami`、`/admin/verify`（供 core 等内部服务调用）。
- **前置转发层**：通配 `GET/POST *` 把请求转发到唯一上游 gateway:8445：
  - 无 `Target-Url` 头 = 公开请求：剥离伪造身份头（`X-Auth-User`/`X-Auth-Admin`）后直转。
  - 有 `Target-Url` 头 = 受保护请求：本地验签（RSA + 过期 + jti 黑名单 + 用户版本号），通过后注入 `X-Auth-User`（管理员追加 `X-Auth-Admin: 1`）与 `Authorization: Bearer` 再转发。
  - 上游不可达/超时统一返回 `502 Bad Gateway`（区别于 CA 自身故障的 500）。
- **WebSocket 反向代理（仅管理员）**：`--ws-proxy-routes` 配置的路径在 HTTP Upgrade 握手时校验 Token（`is_admin=true`），通过后透明桥接到后端 ws:// 或 wss:// 服务。
- **CAPTCHA 代理**：`GET /captcha/image` 代理 captcha2 签发图形验证码（PNG + `X-Captcha-Token` 响应头，10 次/分钟/IP，禁缓存）。
- **邮箱验证码**：`/register/send_code`、`/reset/send_code` 发送 6 位验证码邮件（10 分钟有效）；`/reset_password` 重置密码（防枚举恒成功 + 改密后版本号吊销全端 Token）。
- **速率限制**：登录 5 次/5 分钟、注册 3 次/10 分钟、发验证码 5 次/10 分钟（IP 级）+ 3 次/小时/10 次/天（邮箱级），按 `X-Forwarded-For` 客户端 IP 计数（由 WAF 注入），超限一律返回 429。
- **OPTIONS 预检**：由 libhttp 处理，`Access-Control-Allow-Origin` 反射请求的 Origin 头（见 [`ca_SECURITY_AUDIT.md`](../docs/security/ca_SECURITY_AUDIT.md) P2-3）。

## 身份头契约（strip-then-set）

| 头 | 方向 | 语义 |
|----|------|------|
| `X-Auth-User` | CA → gateway → 下游 | 已认证用户名（邮箱）。客户端携带的同名单被 CA 剥离，仅 CA 注入的值可信 |
| `X-Auth-Admin: 1` | CA → gateway → 下游 | 仅当 Token `is_admin=true` 时注入 |
| `Authorization: Bearer <token>` | CA → gateway → 下游 | Token 来自 Cookie 时由 CA 补齐；来自客户端 Authorization 头时透传 |

gateway 侧仅做**存在性不变量**检查（显式 `Target-Url` 请求必须携带 `X-Auth-User`，否则 401），不复验签名——信任边界是 loopback。

## 安全特性

| 特性 | 实现 |
|------|------|
| 密码哈希 | PBKDF2-HMAC-SHA256，100000 轮，16 字节随机盐，32 字节哈希 |
| 密码强度校验 | 至少 8 字符；大写/小写/数字/特殊字符中至少 3 类 |
| Token 机制 | `Base64(payload) + "_" + RSA-Sign(payload)`，验签 + 过期校验（默认 1 小时） |
| Token 吊销 | 双轨纯内存：jti 黑名单（5000 条阈值清理）+ 用户版本号（改密全端吊销） |
| 常数时间比较 | PBKDF2 验证用 `diff |= hash[i] ^ expected[i]` 防 timing attack |
| 防用户枚举 | 登录对不存在用户执行 dummy PBKDF2 拉平时序；重置密码恒返回成功 |
| CAPTCHA | 统一走 captcha2 远端校验（Token 一次性消费）；`/login` 强制 |
| TLS 可选 | 缺省（不传 `--tls`）纯 HTTP（配合 WAF），`--tls` 时用 `full_chain.crt` / `server.key` |
| 下游容错 | pymsg 客户端 `retry_on_refused`：db 未就绪时阻塞等待；日志客户端 fail-safe 静默丢弃 |

## 接口

### `GET /captcha/image`

代理 captcha2 签发图形验证码：响应体为 PNG，答案 Token 在 `X-Captcha-Token` 响应头（64 位 hex，一次性消费），`Cache-Control: no-store`。限流 10 次/分钟/IP（429）。

### `POST /login`

```json
{
  "username": "user@example.com",
  "password": "P@ssw0rd",
  "captcha_token": "a1b2c3d4...",
  "captcha_answer": "Ab3xy"
}
```

成功：`{"success": true, "data": {"token": "<base64_payload>_<rsa_sign>"}}`，同时签发 `HttpOnly + Secure + SameSite=Lax` 的 `q4_token` Cookie。
错误码：`400`（参数/CAPTCHA 错误）、`401`（账号/密码错）、`429`（限流）。

### `POST /register` / `POST /register/send_code` / `POST /reset/send_code` / `POST /reset_password`

与 gateway v0.8.0 契约完全一致（详见黑盒 tests/blackbox/test_gateway.py）。

### `POST /logout` / `POST /refresh_token` / `POST /whoami` / `POST /admin/verify`

- `/logout`：吊销当前 Token 的 jti 并清除 Cookie。
- `/refresh_token`：宽限期（iat + keep_time × refresh_grace_multiplier）内换发新 Token，旧 jti 吊销，刷新次数超 `max_refresh` 后需重新登录。
- `/whoami`：恒 200，未登录返回 `{"login": false}`。
- `/admin/verify`：fail-closed，非管理员/无效 Token 一律 401（供 core:5200 管理员接口鉴权）。

### `GET/POST *`（前置转发）

```
Authorization: Bearer <base64_payload>_<rsa_sign>   // 或 Cookie: q4_token=...
Target-Url: http://127.0.0.1:8006                   // 可选；携带即视为受保护请求
```

- 无 `Target-Url`：公开请求，剥身份头直转 gateway（由其 `Proxy.default` 路由）。
- 有 `Target-Url`：验签通过后注入身份头转发；`Target-Url` 白名单由 gateway 侧 `Proxy.opened` 裁决（403）。
- 上游故障：`502 {"success": false, "message": "Bad Gateway"}`。

### WebSocket 代理（`--ws-proxy-routes` 配置的路径）

握手阶段从 `Authorization: Bearer` / `Cookie: q4_token` 提取 Token，验签且 `is_admin=true` 才升级连接并桥接后端；否则拒绝。路由精确匹配，优先级高于 HTTP 通配 `*`。

## 配置

v1.1.0 起配置**全部经 CLI 传递**，不再依赖 confs 统一配置服务（与 dnfs2 同模式）。三层优先级：**CLI 参数 > 环境变量 > 代码默认值**；部署侧由 [`core/services.yaml`](../core/services.yaml) 的 `args` 声明完整传入。

| CLI 选项 | 环境变量（CA_ 前缀优先，兼容旧名） | 默认值 |
|----------|-----------------------------------|--------|
| `--host` | `CA_HOST` / `LISTEN_ADDR` | `127.0.0.1` |
| `--port` | `CA_PORT` / `LISTEN_PORT` | `8443` |
| `--tls` | `CA_TLS=on` | 关（纯 HTTP） |
| `--upstream-gateway` | `CA_UPSTREAM_GATEWAY` / `UPSTREAM_GATEWAY` | `http://127.0.0.1:8445` |
| `--token-keep-time` | `CA_TOKEN_KEEP_TIME` | `3600` |
| `--token-refresh-grace-multiplier` | `CA_TOKEN_REFRESH_GRACE_MULTIPLIER` | `2` |
| `--token-max-refresh` | `CA_TOKEN_MAX_REFRESH` | `10` |
| `--cors-origins` | `CA_CORS_ORIGINS` | 空 |
| `--admin-users` | `CA_ADMIN_USERS` | 空 |
| `--ws-proxy-routes` | `CA_WS_PROXY_ROUTES` | 空 |
| `--db-host` / `--db-port` | `CA_DB_HOST`/`DB_HOST` / `CA_DB_PORT`/`DB_PORT` | `127.0.0.1:8005` |
| `--captcha-host` / `--captcha-port` | `CA_CAPTCHA_HOST`/`CAPTCHA_HOST` / `CA_CAPTCHA_PORT`/`CAPTCHA_PORT` | `127.0.0.1:8009` |
| `--smtp-host` / `--smtp-port` | `CA_SMTP_HOST`/`SMTP_HOST` / `CA_SMTP_PORT`/`SMTP_PORT` | `127.0.0.1:8008` |
| `--instance` | — | `ca` |
| `--log-level` | — | `info` |

- `--admin-users`：管理员白名单（分号分隔邮箱），登录命中时 Token payload 写入 `is_admin=true`。
- `--cors-origins`：已读取但尚未用于预检响应（见 [`ca_SECURITY_AUDIT.md`](../docs/security/ca_SECURITY_AUDIT.md) P2-3）。
- `--ws-proxy-routes`：`/path=ws://host:port/target`，分号分隔多条。
- 旧环境变量名（`LISTEN_ADDR` 等）保留兼容，供 Docker 部署用服务名覆盖 `127.0.0.1`。
- 启动时做配置完整性校验：监听地址/端口、上游 gateway、db 地址非法则启动失败。

## 编译与运行

```bash
bash _set.sh   # make clean && make
bash _run.sh   # 纯 CLI 配置启动（"$@" 透传，可临时覆盖任意配置项）
```

命令行参数：完整选项见 `--help`（见上表）；`-h/--help`、`-v/--version`。日志经 LogC 异步批量推送 db:8005 `Logs` 表（service 名 `ca`）。

## 目录结构

```
ca/
├── src/
│   └── main.cpp          # 全部实现：认证 handler、Token 机制、限流、前置转发层、WS 代理
├── inc/
│   ├── DbC.hpp           # db 服务客户端（pymsg，retry_on_refused=true；client 名 "ca"）
│   ├── LogC.hpp          # 统一日志客户端（异步批量，fail-safe；client 名 "ca-log"）
│   ├── ExpBase.hpp       # 异常基类（带 source_location）
│   └── LocalTime.hpp     # 本地时间工具
├── Makefile
├── _set.sh / _run.sh
└── README.md
```

## 版本

`1.1.0`：配置体系重构——彻底移除 confs 依赖（删 `Conf.hpp` 与 `confs/configs/ca.ini`），配置全部经 CLI 传递（CLI > 环境变量 > 默认值三层），原 ca.ini 配置值迁入 `core/services.yaml` 的 ca 条目 args；修复伪造 Token 触发 500（RSA 验签异常未捕获，现统一返回 401，符合黑盒测试 B3/B4 契约）。

`1.0.0`：认证独立化首发——自 gateway v0.8.0 整体搬迁全部认证资产（逐字保留实现），新增前置转发层（固定上游 + strip-then-set 身份头契约 + 上游 502 语义）。原 gateway 同步瘦身为 v0.9.0 纯转发服务。
