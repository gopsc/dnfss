# gateway 安全审计报告

- **审计日期**:2026-08-01
- **审计版本**:gateway 0.5.3(见 [src/main.cpp:31](../src/main.cpp#L31))
- **审计范围**:
  - [gateway/src/main.cpp](../src/main.cpp)(约 1700 行,全部业务实现)
  - [gateway/inc/DbC.hpp](../inc/DbC.hpp)、[LogC.hpp](../inc/LogC.hpp)、[ExpBase.hpp](../inc/ExpBase.hpp)、[LocalTime.hpp](../inc/LocalTime.hpp)、[Msg.hpp](../inc/Msg.hpp)、[Conf.hpp](../inc/Conf.hpp)
  - 依赖库 [libcrypt/inc/us/MyRSA.hpp](../../libcrypt/inc/us/MyRSA.hpp)、[Base64.hpp](../../libcrypt/inc/us/Base64.hpp)
  - 配置 [gateway/example.ini](../example.ini)、构建 [gateway/Makefile](../Makefile)
- **审计方法**:静态代码审查 + 数据流分析 + 威胁建模
- **总体评价**:架构清晰、安全意识较强(常数时间比较、dummy PBKDF2 防用户名枚举、`what()` 屏蔽源码路径、路径规范化校验、可伪造头剥离),但存在若干可被实际利用的安全缺陷。按严重程度分级如下。

---

## P0 严重(建议立即修复)

### P0-1 邮件轰炸:`/register/send_code` 缺 CAPTCHA 强制校验【已修复 v0.5.4】

- **位置**:[main.cpp:1387](../src/main.cpp#L1387) `handleSendEmailCode`
- **问题**:该接口仅靠 IP 限流(5 次/10 分钟),无任何人机校验。攻击者可:
  - 在模式 B(独立部署)下伪造 `X-Forwarded-For` 即可绕过限流(见 P0-3),向任意邮箱发送大量验证码邮件。
  - 即使模式 A,也可用代理池/IP 轮换绕过 IP 限流。
- **影响**:smtp 账号被服务商封禁、受害者邮箱被轰炸。
- **修复(v0.5.4,2026-08-01)**:实施分层防护:
  1. **gateway 层 CAPTCHA 强制校验**:经 captcha:8009 pymsg 校验,Token 一次性消费,fail-closed。
  2. **gateway 层邮箱级双窗口限流**:3 次/小时 + 10 次/天(CAPTCHA 通过后才计入配额)。
  3. **smtp 层邮箱级长期限流兜底**:同样 3 次/小时 + 10 次/天,作为 gateway 被绕过/重启时的最后防线。
  4. **smtp 层全局熔断**:`SMTP_HOURLY_LIMIT` 环境变量(默认 100 封/小时),防 smtp 账号被服务商封禁。
  - 改动文件:[gateway/src/main.cpp](../src/main.cpp)(新增 `_EMAIL_ADDR_RATE_HOUR`/`_EMAIL_ADDR_RATE_DAY` + `handleSendEmailCode` 强制 CAPTCHA + 邮箱限流)、[smtp/smtp_v2.py](../../smtp/smtp_v2.py)(新增 `send_log`/`global_send_log` + 限流检查 + 全局熔断 + 预占/回滚)。

### P0-2 注册接口 CAPTCHA 可选,可被批量注册

- **位置**:[main.cpp:1331-1338](../src/main.cpp#L1331)
- **问题**:注册流程中 CAPTCHA 仅在客户端「主动提供」时才校验:

```cpp
if (captcha_it != obj.end() && captcha_ans_it != obj.end()) {
    // 校验
}
```

自动化脚本不传 `captcha_token` 即可跳过人机验证,配合获取邮箱验证码即可批量注册垃圾账号。注意 `/login` 在 [main.cpp:800](../src/main.cpp#L800) 是强制要求 CAPTCHA 的,行为不一致。
- **建议**:CAPTCHA 改为必填(与 `/login` 行为对齐)。

### P0-3 模式 B(独立部署)下 XFF 完全可伪造,所有限流失效

- **位置**:[main.cpp:776-780](../src/main.cpp#L776)、[1298-1301](../src/main.cpp#L1298)、[1107-1110](../src/main.cpp#L1107)、[1395-1398](../src/main.cpp#L1395)
- **问题**:全部限流均信任 `X-Forwarded-For` 头:

```cpp
auto it_host = req.base().find("X-Forwarded-For");
if (it_host != req.base().end())
    client_ip = std::string(it_host->value());
else
    client_ip = "direct";
```

模式 B 下 gateway 直接面向公网,客户端每次发不同 XFF,限流计数器 key 全部不同,等于无限流。README 承认此点(「RateLimiter 取不到 X-Forwarded-For,所有请求记为 direct」),但实际代码是「客户端发啥信啥」,比文档描述更严重。
- **影响**:登录爆破、注册轰炸、Token 刷新滥用全部失防。
- **建议**:启动时根据 `_TLS` 选择 IP 来源策略——模式 B 下用 socket 真实源 IP(`Remote-Addr`),不信 XFF;或在 `is_special_header` 已剥离客户端 XFF 的基础上,模式 B 下不再回填。

### P0-4 PBKDF2 / RAND_bytes 返回值未检查

- **位置**:[main.cpp:66-75](../src/main.cpp#L66) `hash_password`、[main.cpp:97-103](../src/main.cpp#L97) `verify_password`
- **问题**:

```cpp
RAND_bytes(salt, SALT_LEN);          // 返回值未检查
PKCS5_PBKDF2_HMAC(..., hash);       // 返回值未检查
```

极端情况(如 `/dev/urandom` 不可用、内存耗尽)下,salt/hash 为未初始化栈数据,导致:
- 密码哈希可预测(若 salt 全 0)。
- 验证逻辑用未初始化数据做常数时间比较,行为未定义。

OpenSSL 文档明确要求检查返回值。
- **建议**:

```cpp
if (RAND_bytes(salt, SALT_LEN) != 1)
    throw std::runtime_error("RAND_bytes failed");
if (PKCS5_PBKDF2_HMAC(...) != 1)
    throw std::runtime_error("PBKDF2 failed");
```

---

## P1 高(应在下一版本修复)

### P1-1 Token 无撤销机制,logout 仅清 Cookie

- **位置**:[main.cpp:944](../src/main.cpp#L944) `handleLogout`
- **问题**:`handleLogout` 只设置 `Max-Age=0` 的 Cookie,Token 本身仍然有效。Token 一旦泄漏(如 XSS 抓到 Token 值、日志记录、代理缓存),在 `keep_time`(默认 1 小时)内无法撤销,攻击者可继续使用。`refresh_token` 还把可用窗口拉长到 `iat + keep_time * 2`(默认 2 小时)。
- **建议**:
  - Token payload 增加 `jti`(JWT ID)字段。
  - 维护内存黑名单(Set),logout 时把 jti 加入,代理验签后检查。
  - 黑名单按 Token 自然过期自动清理(与 CAPTCHA store 同模式)。

### P1-2 反向代理无下游超时,可被慢响应 DoS

- **位置**:[main.cpp:1633-1639](../src/main.cpp#L1633)
- **问题**:`client.get_stream_sync(url, callback)` / `post_stream_sync` 未传超时。`HttpClient` 的超时行为取决于 libhttp 实现,若下游(static:9200 / dnfs:8006)hang 住,gateway 工作线程会被永久占用。HttpServer 线程池仅 4 线程([main.cpp:540](../src/main.cpp#L540)),4 个慢请求即可让 gateway 拒绝服务。

同样问题见于 `verify_captcha_remote`、`verify_email_code_remote`、`handleCaptchaImage`——虽然传了 5s/20s 超时,但 `MsgClient::call` 短连接 + 阻塞,5 秒内 4 个慢请求即可耗尽线程池。
- **建议**:
  - 代理调用显式设置超时(如 10s)。
  - HttpServer 线程池调大或改用异步模型。
  - 关键路径下游调用加熔断(连续超时降级)。

### P1-3 代理透传 Cookie 与 Target-Url 到下游

- **位置**:[main.cpp:1605-1613](../src/main.cpp#L1605)、[main.cpp:1653](../src/main.cpp#L1653) `is_special_header`
- **问题**:`handleProxy` 复制「几乎所有」请求头到下游,`is_special_header` 只过滤了 `Host/connection/Accept-Encoding/XFF/X-Real-IP/Forwarded`,**未过滤 `Cookie` 和 `Target-Url`**。

后果:
- 用户的 `q4_token` Cookie 被透传到 static/dnfs。若下游有 access log 记录请求头,Token 泄漏。
- `Target-Url` 头透传到下游,可能被下游误解或记录(信息泄漏)。

虽然后续 `if (!cookie_token.empty())` 会用 `Authorization: Bearer` 覆盖客户端的 Authorization,但 Cookie 头不会被覆盖。
- **建议**:`is_special_header` 增加 `Cookie`、`Target-Url`(自定义头不应透传)。

### P1-4 无请求体大小限制(模式 B 下尤其严重)

- **位置**:[main.cpp:787](../src/main.cpp#L787)、[1308](../src/main.cpp#L1308)、[1405](../src/main.cpp#L1405)
- **问题**:直接 `json::parse(req.body())`,无大小检查。模式 A 下 WAF 有 `SecRequestBodyLimit 10MB` 兜底;模式 B 下无任何限制,客户端发 1GB JSON body 即可让 gateway OOM。
- **建议**:
  - 在每个 handler 入口检查 `req.body().size()`,超限返回 413。
  - 或在 libhttp 层统一限制。

### P1-5 代理鉴权先于白名单检查,泄漏白名单信息

- **位置**:[main.cpp:1538-1591](../src/main.cpp#L1538)
- **问题**:流程为:先验签 → 检查过期 → 再检查 Target-Url 是否在 `_OPEN_URL`。持有有效 Token 的攻击者可枚举 Target-Url:
  - 返回 401 → Token 无效或已过期(无法区分)
  - 返回 403 → Token 有效但 Target-Url 不在白名单
  - 返回 200/其他 → Target-Url 在白名单内

从而探测出 `Proxy.opened` 的完整内容(配置敏感信息)。
- **建议**:先检查白名单,白名单命中后再做鉴权。

### P1-6 LocalTime 用本地时间而非 UTC,跨时区/迁移出问题

- **位置**:[LocalTime.hpp:14-18](../inc/LocalTime.hpp#L14)
- **问题**:`getCurrent` 用 `current_zone()`,Token payload 的 `iat`/`created_at` 都是本地时间字符串。问题:
  - 服务器迁移时区(如 CST→UTC),旧 Token 的过期判断会错(差 8 小时)。
  - DST 切换瞬间(夏令时)有过期判断偏差。
  - `parse` 与 `getCurrent` 必须在同一时区,跨服务器部署不一致。

LogC 用 `_utc_now_iso()` UTC,与 LocalTime 不一致——同一进程两套时间表示。
- **建议**:Token payload 统一用 UTC ISO8601(可复用 LogC 的 `_utc_now_iso` 逻辑)。

---

## P2 中(建议修复)

### P2-1 RSA 密钥生成失败未在启动时检查

- **位置**:[main.cpp:542](../src/main.cpp#L542)
- **问题**:`_KEYGEN = std::make_unique<MyRSA::Generator>()` 后未验证密钥是否生成成功。`Generator` 构造函数中 `generate_rsa_key` 失败时返回 nullptr(见 [MyRSA.hpp:88-112](../../libcrypt/inc/us/MyRSA.hpp#L88)),后续 `get_private_key_pem()` 会返回空字符串,`Private_Key::from_pem("")` 抛异常,被 wrapperHttp 兜底成 500,但 gateway 已经在跑,所有登录都会 500。
- **建议**:`main()` 中启动后做一次自签自验(`_PRIV.Sign("test")` + `_PUBL.Verify`),失败直接退出。

### P2-2 is_safe_target 未覆盖双重编码与反斜杠

- **位置**:[main.cpp:1688](../src/main.cpp#L1688)
- **问题**:检查了 `..`、`%2e`、`%2f`、`%5c`,但未检查:
  - 双重编码 `%252e%252e`(下游若做二次 URL 解码会变 `..`)。
  - 反斜杠 `\`(Windows 路径分隔符,Linux 影响小但应防御)。
  - 非 ASCII Unicode 点(`。` 等,极少见)。
- **建议**:增加 `%25` 检查或白名单字符集(只允许 `[A-Za-z0-9/._~-]`)。

### P2-3 url 拼接产生双斜杠

- **位置**:[main.cpp:1518-1520](../src/main.cpp#L1518)

```cpp
url = t_url;          // "http://127.0.0.1:9200"
url += "/";
url += target_path;   // "/index.html"
// 结果: "http://127.0.0.1:9200//index.html"
```

- **问题**:双斜杠可能导致下游服务(如 Nginx `merge_slashes off`)解析异常。
- **建议**:`target_path` 已确认以 `/` 开头,无需再补 `/`。

### P2-4 refresh_token 旧 Token 仍可用

- **位置**:[main.cpp:1099](../src/main.cpp#L1099)
- **问题**:刷新后旧 Token 未作废(因无 jti 黑名单,见 P1-1)。攻击者拿到旧 Token 仍可:
  - 用旧 Token 访问代理(在 keep_time 内)。
  - 用旧 Token 再次刷新(消耗主用户的刷新次数)。

### P2-5 限流 cleanup 性能与内存

- **位置**:[main.cpp:213-219](../src/main.cpp#L213)
- **问题**:cleanup 在 `size() > 10000` 时触发,遍历全表 O(n)。若攻击者用 10001 个不同 IP 触发,每次请求都遍历 1 万条,放大 CPU 消耗。
- **建议**:改用 LRU 或定期后台线程清理。

### P2-6 signalHandler 调用非 async-signal-safe 函数

- **位置**:[main.cpp:445-449](../src/main.cpp#L445)
- **问题**:`signalHandler` 调用 `std::cout`、`_SERVE->stop()`(可能用锁),在信号上下文中是 UB。实践中常能工作,但严格来说应改用 `sigwait` + 独立线程或 `signalfd`。

### P2-7 配置项无范围校验

- **位置**:[main.cpp:507-517](../src/main.cpp#L507)
- **问题**:读取的 `_PORT`、`_TOKEN_KEEP_TIME`、`_TOKEN_REFRESH_GRACE_MULTIPLIER`、`_TOKEN_MAX_REFRESH` 均无范围检查。如 `_TOKEN_KEEP_TIME = 999999999` 会让 Token 几乎不过期;`_PORT = -1` 行为异常。
- **建议**:启动时校验合理范围,超限拒绝启动。

---

## P3 低(信息性建议)

### P3-1 Cookie 未用 `__Host-` 前缀

- **位置**:[main.cpp:928-932](../src/main.cpp#L928)
- **问题**:Cookie 名 `q4_token`,建议改 `__Host-q4_token` 绑定 hostname + Path=/ + Secure,防止子域 Cookie 注入(若未来有子域服务)。

### P3-2 handleProxy 对默认目标仍透传客户端 Authorization

- **位置**:[main.cpp:1572-1575](../src/main.cpp#L1572)
- **问题**:公开目标(flag=true)走 else 分支,只清空 `cookie_token`。但客户端发的 `Authorization` 头已在 [1605-1613](../src/main.cpp#L1605) 被复制到下游请求头中,未被覆盖(因为 `cookie_token.empty()`)。static 可能不解析,但仍是信息泄漏。

### P3-3 RSA 密钥对进程内存生成,无法跨实例共享

- **位置**:[main.cpp:542](../src/main.cpp#L542)
- **问题**:每次启动重新生成。多实例部署时,Token 在不同实例间不通用(用户登录 gateway-A 后,请求转到 gateway-B 会验签失败)。当前单实例部署,问题不大;若未来水平扩展,需改为持久化密钥(加密存储)。

### P3-4 whoami 返回 Token 内部字段

- **位置**:[main.cpp:1067-1076](../src/main.cpp#L1067)
- **问题**:返回 `iat`/`created_at`/`keep_time`,给攻击者提供精确过期时间。便于攻击者规划 Token 重放,但风险有限。

### P3-5 OPTIONS 预检未显式注册

- **位置**:[registerRoutes](../src/main.cpp#L610)
- **问题**:没有 `server.options("*", ...)`,README 说「通配 * 返回 CORS 处」,但代码依赖 `server.get("*")` 捕获 OPTIONS。若 libhttp 的 HttpServer 不自动路由 OPTIONS 到 GET,OPTIONS 会被当成 404 或走 proxy。建议显式注册 OPTIONS handler。

---

## 修复优先级建议

| 优先级 | 项目 | 工作量 | 建议时间 |
|--------|------|--------|----------|
| ✅ 已修复 | P0-1 邮件轰炸防护(v0.5.4) | 中 | 2026-08-01 完成 |
| 立即 | P0-2 CAPTCHA 强制化 | 小 | 本周 |
| 立即 | P0-3 模式 B 下 IP 来源修正 | 小 | 本周 |
| 立即 | P0-4 PBKDF2/RAND_bytes 返回值检查 | 极小 | 本周 |
| 短期 | P1-1 Token 黑名单(jti) | 中 | 2 周内 |
| 短期 | P1-2 代理下游超时 + 线程池调大 | 中(需改 libhttp) | 2 周内 |
| 短期 | P1-3 过滤 Cookie/Target-Url 头 | 极小 | 2 周内 |
| 短期 | P1-4 请求体大小限制 | 小 | 2 周内 |
| 短期 | P1-5 代理鉴权顺序调整 | 小 | 2 周内 |
| 中期 | P1-6 Token 时间统一 UTC | 小(需处理存量 Token) | 1 月内 |
| 中期 | P2-* 各项 | 小-中 | 1 月内 |

---

## 已有的良好实践(肯定)

为公正起见,记录审计中确认的已有安全设计:

| 特性 | 实现位置 | 说明 |
|------|----------|------|
| 密码哈希 | [main.cpp:58](../src/main.cpp#L58) `PBKDF2Hasher` | PBKDF2-HMAC-SHA256,10 万轮 + 16 字节随机盐,行业标准 |
| 常数时间比较 | [main.cpp:107-111](../src/main.cpp#L107) | `diff |= hash[i] ^ expected[i]` 防 timing attack |
| 防用户名枚举 | [main.cpp:862-868](../src/main.cpp#L862) | 用户不存在时执行 dummy PBKDF2 拉平响应时序 |
| 源码路径屏蔽 | [ExpBase.hpp:173-197](../inc/ExpBase.hpp#L173) | `what()` 注释了位置信息,不进入 HTTP 响应 |
| 异常兜底 | [main.cpp:703-724](../src/main.cpp#L703) | `wrapperHttp` 外层 catch 统一返回 "Internal Server Error" |
| 路径规范化 | [main.cpp:1688](../src/main.cpp#L1688) `is_safe_target` | 防路径遍历/协议注入/空字节/URL 编码绕过 |
| 可伪造头剥离 | [main.cpp:1653](../src/main.cpp#L1653) `is_special_header` | 剥离客户端 XFF/X-Real-IP/Forwarded,由 gateway 主动设置可信值 |
| RSA 填充 | [MyRSA.hpp:211](../../libcrypt/inc/us/MyRSA.hpp#L211) | 默认 PSS 填充(比 PKCS1_v1_5 更安全) |
| RSA 密钥长度 | [MyRSA.hpp:85](../../libcrypt/inc/us/MyRSA.hpp#L85) | 2048 位,符合当前推荐 |
| Cookie 安全属性 | [main.cpp:928-932](../src/main.cpp#L928) | `HttpOnly + Secure + SameSite=Lax` |
| 密码强度校验 | [main.cpp:148](../src/main.cpp#L148) `PasswordValidator` | 8+ 字符、4 类中至少 3 类 |
| 限流清理 | [main.cpp:213-219](../src/main.cpp#L213) | 超过 1 万条自动清理(虽性能可优化,见 P2-5) |
| 集中式日志 | [LogC.hpp](../inc/LogC.hpp) | 异步批量 + fail-safe,不拖慢业务 |
| Token 刷新机制 | [main.cpp:1099](../src/main.cpp#L1099) | 保留 iat 限制总生命周期,refresh_count 上限 |

---

## 附录:审计检查清单

| 类别 | 检查项 | 结果 |
|------|--------|------|
| 认证 | 密码哈希算法 | ✅ PBKDF2 |
| 认证 | 密码哈希参数(轮数/盐长度) | ✅ 10 万轮 / 16 字节 |
| 认证 | 常数时间比较 | ✅ |
| 认证 | 防用户名枚举 | ✅ dummy PBKDF2 |
| 认证 | Token 签名算法 | ✅ RSA-PSS + SHA256 |
| 认证 | Token 过期校验 | ✅ |
| 认证 | Token 撤销机制 | ❌ 见 P1-1 |
| 认证 | CAPTCHA 强制 | ✅ login + send_code 强制,register 可选(见 P0-2) |
| 授权 | 反向代理白名单 | ✅ |
| 授权 | 鉴权顺序 | ❌ 见 P1-5 |
| 授权 | is_admin 字段 | ✅ 由 gateway 统一签发,下游不各自维护 |
| 限流 | 登录/注册/发码/刷新 | ✅ 均有限流 |
| 限流 | 邮箱级限流 | ✅ 3 次/小时 + 10 次/天(gateway + smtp 双层,见 P0-1 修复) |
| 限流 | IP 来源可信 | ❌ 模式 B 下可伪造,见 P0-3 |
| 限流 | cleanup 性能 | ⚠️ 见 P2-5 |
| 输入校验 | 请求体大小 | ❌ 见 P1-4 |
| 输入校验 | JSON 解析异常处理 | ✅ |
| 输入校验 | 路径规范化 | ✅(可加强,见 P2-2) |
| 输入校验 | 邮箱格式 | ✅ 正则 |
| 输入校验 | 密码强度 | ✅ |
| 输出 | 错误信息泄漏 | ✅ what() 屏蔽 + 兜底 |
| 输出 | Token 内部字段 | ⚠️ 见 P3-4 |
| 传输 | TLS 终止位置 | ✅ WAF 模式下由 WAF 终止 |
| 传输 | Cookie 安全属性 | ✅ HttpOnly+Secure+SameSite |
| 传输 | HSTS | ✅ 由 WAF 注入 |
| 并发 | 限流器线程安全 | ✅ mutex |
| 并发 | CAPTCHA store 线程安全 | ✅ mutex |
| 并发 | 信号处理 | ⚠️ 见 P2-6 |
| 依赖 | OpenSSL 返回值检查 | ❌ 见 P0-4 |
| 依赖 | libcurl 超时 | ❌ 见 P1-2 |
| 配置 | 配置项范围校验 | ❌ 见 P2-7 |
| 配置 | RSA 密钥生成自检 | ❌ 见 P2-1 |
| 日志 | 集中式日志 | ✅ |
| 日志 | fail-safe | ✅ |
| 日志 | 敏感信息记录 | ⚠️ Token 不入日志,但 IP/用户名记录(可接受) |

---

*报告结束*
