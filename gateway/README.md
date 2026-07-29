# gateway

UniServe Framework 的应用层网关，C++20 实现。

在引入 [waf](../waf/) 模块后，gateway 退化为内部 HTTP 服务：TLS 由 WAF 终止，gateway 专注做账号鉴权与反向代理。通过 `Server.tls` 配置可一键切换独立部署（HTTPS）或与 WAF 协作（HTTP）两种模式。

## 职责

- **账号鉴权**：`/register` 注册（需邮箱验证码，经 pymsg 调 smtp:8008 校验）、`/login` 登录，签发 RSA 签名的 Token。
- **反向代理**：携带 Token 的请求校验后转发到 `Proxy.opened` 中放开的内部服务。
- **CAPTCHA 接口**：`/captcha` 生成验证码 token + 文本（内存存储，5 分钟过期，一次性使用）。
- **邮箱验证码**：`/register/send_code` 发送 6 位验证码邮件（10 分钟有效）；注册时校验，一次性消费。
- **速率限制**：登录 5 次/5 分钟、注册 3 次/10 分钟、发验证码 5 次/10 分钟，按 `X-Forwarded-For` 客户端 IP 计数（由 WAF 注入），超限一律返回 429。
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

```json
{ "email": "user@example.com" }
```

限流 5 次/10 分钟/IP；同一邮箱 60 秒发送冷却（由 smtp 服务执行，冷却期拒绝时返回 `retry_after` 剩余秒数与提示文案，gateway 按 429 透传；已发出且在有效期内的验证码不受影响，发送失败不计入冷却）。错误码：`400`（邮箱格式错误）、`429`（IP 速率超限或同邮箱发送冷却）、`502`（smtp 服务不可用）、`500`（发送失败）。

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

[example.ini](example.ini)：

```ini
[Server]
# tls = off  HTTP 模式（配合 waf 模块）
# tls = on   HTTPS 模式（独立部署，证书为 full_chain.crt / server.key）
address = 127.0.0.1
port = 8443
tls = off

[Proxy]
default = http://127.0.0.1:9200
closed  = http://127.0.0.1:8005;http://127.0.0.1:8007;
opened  = http://127.0.0.1:9200;http://127.0.0.1:8006;

[Token]
# Token 有效期（秒），默认 3600 = 1 小时
keep_time = 3600

[CORS]
# 允许跨域的 Origin 白名单（分号分隔）
# 仅命中的 Origin 才会回填 Access-Control-Allow-Origin；缺省为空（不回填）
allowed_origins = https://qsont.me;https://www.qsont.me;http://127.0.0.1:8443;http://localhost:8443

[Database]
path = sqlite3.db
```

- `Proxy.default`：未带 `Target-Url` header 时的默认转发地址（不需 Token，主要服务前端静态资源）。
- `Proxy.opened`：允许外部带 Token 访问的内部服务白名单（分号分隔）。
- `Proxy.closed`：仅记录用途，实际靠"不在 opened 中即拒绝"实现隔离。
- `Token.keep_time`：登录签发 Token 的有效期（秒），缺省 3600。
- `CORS.allowed_origins`：OPTIONS 预检允许跨域的 Origin 白名单（分号分隔），缺省为空（不回填 ACAO）。

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
bash _set.sh   # make clear && make
bash _run.sh   # ./build/gateway.out -i example.ini
```

`gateway.out` 需以 `-i <ini>` 传配置文件路径。其他参数：`-h/--help`、`-v/--version`。

## 目录结构

```
gateway/
├── src/
│   └── main.cpp          # 全部实现：PBKDF2 / PasswordValidator / RateLimiter / 路由 / handler
├── inc/
│   ├── Conf.hpp          # ConfIni：基于 boost::property_tree 的 ini 解析
│   ├── ExpBase.hpp       # 异常基类（带 source_location）
│   └── LocalTime.hpp     # 本地时间工具（getCurrent / parse）
├── Makefile
├── example.ini
├── _set.sh / _run.sh
└── README.md
```

## 版本

`0.5.3`（见 [src/main.cpp:30](src/main.cpp#L30)）。启动时打印：

```
Gateway 0.5.3 started
```

近期变更：

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
