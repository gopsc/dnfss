# gateway

UniServe Framework 的应用层网关，C++20 实现。

在引入 [waf](../waf/) 模块后，gateway 退化为内部 HTTP 服务：TLS 由 WAF 终止，gateway 专注做账号鉴权与反向代理。通过 `Server.tls` 配置可一键切换独立部署（HTTPS）或与 WAF 协作（HTTP）两种模式。

## 职责

- **账号鉴权**：`/register` 注册（需邮箱验证码，经 pymsg 调 smtp:8008 校验）、`/login` 登录，签发 RSA 签名的 Token。
- **HTTP 反向代理**：携带 Token 的请求校验后转发到 `Proxy.opened` 中放开的内部服务。
- **WebSocket 反向代理（仅管理员）**：`[WsProxy]` 段配置的路径在 HTTP Upgrade 握手时校验 Token（`is_admin=true`），通过后透明桥接到后端 ws:// 或 wss:// 服务，支持文本/二进制帧双向转发。
- **CAPTCHA 接口**：`/captcha` 生成验证码 token + 文本（内存存储，5 分钟过期，一次性使用）。
- **邮箱验证码**：`/register/send_code` 发送 6 位验证码邮件（10 分钟有效）；注册时校验，一次性消费。
- **速率限制**：登录 5 次/5 分钟、注册 3 次/10 分钟、发验证码 5 次/10 分钟（IP 级）+ 3 次/小时/10 次/天（邮箱级），按 `X-Forwarded-For` 客户端 IP 计数（由 WAF 注入），超限一律返回 429。
- **邮件轰炸防护**：`/register/send_code` 强制 CAPTCHA 校验 + 邮箱级双窗口限流（3 次/小时 + 10 次/天），smtp 层兜底同样限流 + 全局熔断（默认 100 封/小时，可配）。
- **OPTIONS 预检**：通配 `*` 返回 CORS 头，允许跨域。

## 安全特性

| 特性 | 实现 |
|------|------|
| 密码哈希 | PBKDF2-HMAC-SHA256，100000 轮，16 字节随机盐，32 字节哈希 |
| 密码强度校验 | 至少 8 字符；大写/小写/数字/特殊字符中至少 3 类 |
| 邮箱格式校验 | 正则匹配 `^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$` |
| 旧哈希自动迁移 | 登录时若发现 SHA256 旧哈希，验证通过后自动迁移为 PBKDF2 |
| Token 机制 | `Base64(payload) + "_" + RSA-Sign(payload)`，验签 + 过期校验（默认 1 小时） |
| 常数时间比较 | PBKDF2 验证用 `diff |= hash[i] ^ expected[i]` 防 timing attack |
| CAPTCHA | 一次性、5 分钟过期，登录与注册均可选启用 |
| 速率限制 | 内存计数器，自动清理超过 1 万条的老条目 |
| TLS 可选 | `Server.tls = off` 时纯 HTTP（配合 WAF），`on` 时用 `full_chain.crt` / `server.key` |
| 下游服务容错 | pymsg 客户端 `retry_on_refused` 机制：db 等强依赖服务未启动时自动阻塞重试，避免启动顺序问题；日志客户端 fail-safe 静默丢弃 |

## pymsg 内部通信协议

gateway 通过项目内的 [pymsg](../pymsg/)（长度前缀帧 TCP 协议）与内部微服务通信，C++ 实现在 `inc/Msg.hpp`，与 Python 端字节级兼容。

**线协议**：`[4字节大端uint32长度][UTF-8载荷]`，最大 64 MiB。

**JSON RPC 约定**：请求形如 `{"_route": "<路由名>", ...}`，响应形如 `{"success": true, "data": ...}` 或 `{"success": false, "error": "..."}`。

**下游服务调用方式**：

| 下游 | 默认端口 | `_route` | 调用位置 | 重试策略 |
|------|---------|----------|---------|---------|
| db | 8005 | `execute` | `DbC::call_msg()`（所有数据库 CRUD） | `retry_on_refused=true`，db 未就绪时阻塞等待 |
| db | 8005 | `execute` | `LogC::_send()`（日志异步批量推送） | `retry_on_refused=false`，fail-safe 静默丢弃 |
| captcha | 8009 | `verify` / `image` | `verify_captcha_remote()`、`/captcha/image` 代理 | 默认不重试，连接失败 fail-closed 返回 502/false |
| smtp | 8008 | `verify_email` / `send_email` | `verify_email_code_remote()`、`/register/send_code` | 默认不重试，连接失败 fail-closed 返回 502/false |
| confs | 8010 | `get` | 启动时拉取 `gateway.ini` 配置 | `retry_on_refused=true`，confs 未就绪时阻塞等待 |

**retry_on_refused 机制**（`MsgClient::call()`，对应 Python 端 `MsgClient.call(retry_on_refused=True)`）：
- 仅对 `ECONNREFUSED`（目标端口无服务监听）重试，超时、连接重置、业务错误立即抛出
- 固定 1 秒间隔重试；首次失败立即打印 stderr 提示，之后每 5 秒打印一次等待日志（节制日志，避免刷屏）
- 用于解决微服务启动顺序不确定问题：gateway 启动时 db 未就绪，首次 db 调用自动阻塞等待直到 db 上线

## 接口

### `POST /captcha`

生成验证码。返回：

```json
{
  "token": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "text": "Ab3xy"
}
```

### `POST /login`

请求体：

```json
{
  "username": "user@example.com",
  "password": "P@ssw0rd",
  "captcha_token": "a1b2c3d4...",   // 可选
  "captcha_answer": "Ab3xy"          // 可选
}
```

成功响应：

```json
{
  "success": true,
  "data": { "token": "<base64_payload>_<rsa_sign>" }
}
```

失败响应：

```json
{ "success": false, "message": "Invalid Username or Password" }
```

错误码：`400`（参数/CAPTCHA 错误）、`401`（账号/密码错或 Token 过期）、`429`（速率超限）。

### `POST /register`

请求体在 `/login` 基础上增加 `email_code`（必填，6 位数字邮箱验证码）：

```json
{ "username": "user@example.com", "password": "...", "email_code": "123456" }
```

校验顺序：密码强度 → 邮箱格式 → CAPTCHA（可选）→ **邮箱验证码**（经 pymsg 调 smtp:8008 `verify_email`，一次性消费，连续失败 5 次自动作废，fail-closed），全部通过后写入 `Users` 表。

错误码：`400`（密码/邮箱/CAPTCHA/邮箱验证码不合规）、`429`（速率超限）、`500`（数据库异常）。

### `POST /register/send_code`

向目标邮箱发送注册验证码（经 pymsg 调 smtp:8008 `send_email`，10 分钟有效）。

**请求体**（CAPTCHA 必填，防邮件轰炸）：

```json
{
  "email": "user@example.com",
  "captcha_token": "a1b2c3d4...",
  "captcha_answer": "Ab3xy"
}
```

**防护层次**：
1. IP 限流：5 次/10 分钟（`X-Forwarded-For`）
2. CAPTCHA 强制校验：经 captcha:8009 pymsg 校验，Token 一次性消费
3. 邮箱级双窗口限流：3 次/小时 + 10 次/天
4. smtp 同邮箱 60 秒发送冷却（冷却期拒绝时返回 `retry_after` 剩余秒数与提示文案，gateway 按 429 透传；已发出且在有效期内的验证码不受影响，发送失败不计入冷却）
5. smtp 邮箱级长期限流兜底 + 全局熔断（默认 100 封/小时，`SMTP_HOURLY_LIMIT` 可配）

错误码：`400`（邮箱格式错误/CAPTCHA 缺失或错误）、`429`（IP 速率超限/邮箱级限流超限/同邮箱发送冷却）、`502`（smtp 服务不可用）、`500`（发送失败）。

### `GET/POST *`（反向代理）

携带 header：

```
Authorization: Bearer <base64_payload>_<rsa_sign>
Target-Url: http://127.0.0.1:9200   // 可选，缺省取 Proxy.default
```

- `Target-Url` 缺省时走 `Proxy.default`（不需 Token）。
- 自定义 `Target-Url` 必须在 `Proxy.opened` 白名单中，否则返回 403。
- 验签通过且未过期（默认 1 小时）才转发，否则返回 401。
- `Proxy.closed`（如 db:8005、ai_review:8007）不在 `opened` 中，外部无法直访。

### `OPTIONS *`

返回 CORS 预检响应：`Access-Control-Allow-Origin: *`、`Allow-Methods: GET, POST, OPTIONS`、`Max-Age: 86400`。

### WebSocket 代理（`[WsProxy]` 配置的路径）

握手阶段鉴权规则：

1. 从 `Authorization: Bearer <token>` 头读取 Token；若缺失则回退到 `Cookie: q4_token=<token>`。
2. RSA 验签 + 过期校验（与 HTTP 代理同样的 Token 机制）。
3. Token payload 中必须 `is_admin=true`（即登录账号在 `Admin.users` 白名单内）。
4. 鉴权失败 → 返回 HTTP 403 Forbidden，连接被拒绝。
5. 鉴权通过 → 升级为 WebSocket 并透明桥接到配置的后端 ws:// 或 wss:// 地址，双向文本/二进制帧原样转发。

客户端使用方式与普通 WebSocket 一致，只需携带有效管理员 Token：

```javascript
// 浏览器（同源会自动带 q4_token Cookie）
const ws = new WebSocket("wss://qsont.me/ws/aibox");

// 或显式带 Authorization（构造子协议或连接后首条消息均可，这里由网关在 Upgrade 头校验）
```

注意：WS 代理路由必须精确匹配（与 `ws()` 一致，非通配），且优先级高于 HTTP 通配 `*` 路由。

## 数据库

用户数据存于 db 服务（pymsg/TCP :8005）的 SQLite 库，表 `Users`（由 [db/create_sqlite3.sql](../db/create_sqlite3.sql) 建立，gateway 不再自动建表）：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK AUTOINCREMENT | |
| username | TEXT UNIQUE NOT NULL | 登录账号（邮箱格式） |
| password_hash | TEXT NOT NULL | `pbkdf2:100000:<salt_hex>:<hash_hex>` |
| create_at | DATETIME DEFAULT CURRENT_TIMESTAMP | |
| last_login | DATETIME | 每次登录更新 |
| is_active | BOOLEAN DEFAULT 1 | |

gateway 不直连数据库（早期直连本地 sqlite 的代码已注释保留），所有读写经 `DbC::call_msg` 调 db 的 `execute` 路由。无默认管理员账号——首位注册者即为管理员。

## 配置

gateway 配置采用**两层加载模式**，与其他 Python 服务（dnfs/db/static）保持一致：

1. **confs 统一配置服务**（默认模式）：启动时从 confs（`127.0.0.1:8010`，可通过 `CONFS_HOST`/`CONFS_PORT` 环境变量覆盖）通过 pymsg `get` 路由拉取 `gateway.ini`，`retry_on_refused=true` 阻塞等待 confs 就绪。
2. **本地文件覆盖**（开发/调试模式）：启动时传 `-i <path>` 则跳过 confs，直接读取本地 ini 文件。
3. **环境变量覆盖**（最高优先级，Docker 兼容）：`LISTEN_ADDR`/`LISTEN_PORT`、`DB_HOST`/`DB_PORT`、`CAPTCHA_HOST`/`CAPTCHA_PORT`、`SMTP_HOST`/`SMTP_PORT` 覆盖 ini 中对应值。
4. **完整性校验**：必需配置项缺失则启动失败。

配置文件存放于 [confs/configs/gateway.ini](../confs/configs/gateway.ini)（[example.ini](example.ini) 为本地开发参考副本，内容一致，新增 `[Downstream]` 段）。

配置项说明：
- `Server.address`/`port`/`tls`：监听地址、端口、TLS 开关；`address`/`port` 可被 `LISTEN_ADDR`/`LISTEN_PORT` 环境变量覆盖。
- `Proxy.default`：未带 `Target-Url` header 时的默认转发地址（不需 Token）。
- `Proxy.opened`：允许外部带 Token 访问的内部服务白名单（分号分隔）。
- `Proxy.closed`：仅文档记录用途，实际靠"不在 opened 中即拒绝"实现隔离。
- `Token.*`：Token 有效期（`keep_time`）、刷新宽限期倍数（`refresh_grace_multiplier`）、最大刷新次数（`max_refresh`）。
- `Admin.users`：管理员白名单（分号分隔邮箱），登录命中时 Token payload 写入 `is_admin=true`。
- `CORS.allowed_origins`：允许跨域的 Origin 白名单（分号分隔）。
- `WsProxy.routes`：WebSocket 代理路由，格式 `/path=ws://host:port/target`，分号分隔。
- `Downstream.*`：下游 pymsg 服务地址（db/captcha/smtp），可被 `DB_HOST`/`DB_PORT`、`CAPTCHA_HOST`/`CAPTCHA_PORT`、`SMTP_HOST`/`SMTP_PORT` 环境变量覆盖。

## 部署模式

### 模式 A：与 WAF 协作（推荐）

```
客户端 ──HTTPS──▶ waf:443 (终止 TLS + coraza 规则) ──HTTP──▶ gateway:8443
```

```ini
address = 127.0.0.1
port = 8443
tls = off
```

证书放在 `waf/certs/`。RateLimiter 从 WAF 注入的 `X-Forwarded-For` 取真实客户端 IP。

### 模式 B：独立部署

```
客户端 ──HTTPS──▶ gateway:443 (终止 TLS + 鉴权 + 反代)
```

```ini
address = 0.0.0.0
port = 443
tls = on
```

证书 `full_chain.crt` / `server.key` 放在 gateway 目录。RateLimiter 取不到 `X-Forwarded-For`，所有请求记为 `"direct"`。

## 编译与运行

依赖系统库（由根目录 [_setup.deb.sh](../_setup.deb.sh) 或 [_setup.rhel.sh](../_setup.rhel.sh) 安装）：

- `libboost_json`、`libboost_program_options`
- `libcurl`
- `libssl` / `libcrypto`（OpenSSL，PBKDF2 + RSA + RAND_bytes）
- `libsqlite3`
- 项目内库：`libus_http`、`libus_crypt`（由 [libhttp](../libhttp/)、[libcrypt](../libcrypt/) 编译安装）

```bash
bash _set.sh   # make clean && make
bash _run.sh   # 默认从 confs 拉取配置；开发模式可用 _run.sh -i example.ini
```

默认无需传参（从 confs 拉取配置）；`-i <ini>` 为本地文件模式（开发/调试用）。其他参数：`-h/--help`、`-v/--version`。

## 目录结构

```
gateway/
├── src/
│   └── main.cpp          # 全部实现：配置加载（confs/本地文件/环境变量）、PBKDF2、RateLimiter、路由/handler
├── inc/
│   ├── Msg.hpp           # pymsg C++ 实现：长度前缀帧协议、MsgClient（含 retry_on_refused）、MsgException 族
│   ├── DbC.hpp           # db 服务客户端：通过 pymsg 调 dbv2（地址可配置，retry_on_refused=true）
│   ├── LogC.hpp          # 统一日志客户端：异步批量队列 + pymsg INSERT（fail-safe）
│   ├── Conf.hpp          # ConfIni：boost::property_tree ini 解析，支持 fromString/env/env_int
│   ├── ExpBase.hpp       # 异常基类（带 source_location）
│   └── LocalTime.hpp     # 本地时间工具（getCurrent / parse）
├── example.ini           # 本地开发配置参考副本（与 confs/configs/gateway.ini 一致）
├── Makefile
├── _set.sh / _run.sh
└── README.md
```

## 版本

`0.7.0`（见 [src/main.cpp:31](src/main.cpp#L31)）。启动时打印：

```
Gateway 0.7.0 started
```

近期变更：

- `0.7.0`：配置体系迁移到 confs 统一配置服务——新增 `confs/configs/gateway.ini`（原 example.ini 内容 + `[Downstream]` 段配置下游服务地址）；启动时默认从 confs（`CONFS_HOST`/`CONFS_PORT`，默认 `127.0.0.1:8010`）通过 pymsg `get` 路由拉取配置（`retry_on_refused=true`），`-i <path>` 保留为本地文件开发模式；`ConfIni` 增加 `fromString()`（从 ini 文本解析）、`env()`/`env_int()`（环境变量读取）、公开默认构造函数；环境变量覆盖支持 `LISTEN_ADDR`/`LISTEN_PORT`/`DB_HOST`/`DB_PORT`/`CAPTCHA_HOST`/`CAPTCHA_PORT`/`SMTP_HOST`/`SMTP_PORT`；配置完整性校验（`Server.address`/`port`、`Proxy.default`、`Downstream.db_host`/`db_port` 缺失则启动失败）；`DbC` 增加 `init(host, port)` 支持可配置 db 地址（默认 127.0.0.1:8005），main.cpp 中所有硬编码 captcha/smtp 地址改为配置变量，`logs().set_endpoint()` 在启动时初始化；`_run.sh` 默认不传 `-i`，`--version/-v` 选项补全实现。
- `0.6.1`：pymsg 客户端增加 `retry_on_refused` 重试机制——新增 `MsgConnectionRefusedException`；`MsgClient::call()` 新增 `retry_on_refused`/`retry_interval` 参数；`DbC::call_msg()` 启用重试等待 db 就绪；`LogC::_send()` 显式不重试（fail-safe）。
- `0.6.0`：WebSocket 反向代理（仅管理员）——新增 `[WsProxy]` 配置段，支持 `routes = /path=ws://host:port/target;...` 精确路径映射；握手阶段校验 Token 中 `is_admin=true`（基于 `Admin.users` 白名单），通过后双向透明桥接文本/二进制帧到后端，支持 `ws://` 和 `wss://`；后端连接错误/鉴权失败均优雅关闭。依赖 libhttp v3.1+ 的 `ws_proxy()` 接口。顺带修复 `LocalTime.hpp` 在 Apple clang 下 C++20 tzdb 不可用的编译问题（改用 `strftime`/`get_time`/`mktime`）。
- `0.5.4`：修复邮件轰炸漏洞（审计报告 P0-1）——`/register/send_code` 强制 CAPTCHA 校验（经 captcha:8009，Token 一次性消费），新增邮箱级双窗口限流（3 次/小时 + 10 次/天）；smtp 层同步增加邮箱级长期限流兜底 + 全局熔断（`SMTP_HOURLY_LIMIT` 环境变量，默认 100 封/小时）；前端 `register.html`/`register.js` 同步接入图形验证码（复用 `/captcha/image` 代理路由，方案 A：仅在「获取验证码」时要求 CAPTCHA，注册提交不加）。
- `0.5.3`：配合 smtp 同邮箱 60 秒发送冷却——`/register/send_code` 在 smtp 返回 `retry_after` 时按 429 透传其提示文案（其余发送失败仍用通用 500 文案，SMTP 内部错误细节不向外暴露）。
- `0.5.2`：限流错误码统一为 429（登录原 400、注册/发码原 403，并修正 "attampts" 拼写）；登录时用户不存在不再兜底 500——空结果统一走 401，并执行 dummy PBKDF2 校验拉平时序防用户名枚举；db 查询失败消息由误名的 "Login (Update Step) Failed" 改为 "Login (Query Step) Failed"；pymsg 下游调用超时常量化（`DbC::DEFAULT_TIMEOUT`、`_TIMEOUT_CAPTCHA/_SMTP_VERIFY/_SMTP_SEND`）。
- `0.5.1`：登录成功后通过 db 更新 `Users.last_login`（`"$now"` 哨兵，db 日期适配层转为服务端当前 UTC 时间；更新失败经 THROW_EX_CUSTOM 抛 500，与其他步骤一致）。

## 依赖

- C++20（`g++`）
- Boost：`boost::json`、`boost::program_options`、`boost::property_tree`
- OpenSSL：PBKDF2-HMAC-SHA256、RSA 签名/验签、`RAND_bytes`
- libcurl（反向代理出站请求）
- libsqlite3（用户表存储）
- 项目库：[libhttp](../libhttp/)（HttpServer/HttpClient）、[libcrypt](../libcrypt/)（MyRSA/Base64/SHA256）、[libsqlite3](../libsqlite3/)
