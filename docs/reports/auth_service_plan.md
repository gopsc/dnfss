# 认证独立化（CA 模块）开发文档

**日期**: 2026-08-09（v2 重写）
**背景**: gateway v0.8.0 单文件 [main.cpp](../../gateway/src/main.cpp)（2470 行），认证逻辑（约 65%）与反向代理耦合；core/dnfs2 通过 HTTP 回调 gateway 验签，gateway 既是流量入口又是认证权威，形成环形依赖
**范围**: `ca`（新模块）、`gateway`（瘦身为纯代理）；**WAF / core / dnfs2 / static / 前端零改动**
**前版**: 2026-08-08 初版计划书（auth 双栈方案）。本版按已选定拓扑重写：**数据流 `waf → ca → gateway`，gateway 不再验签**。原双栈/后置方案见 §2.2 决策记录

---

## 0. 摘要

新建 CA（Central Auth）模块：C++20 服务，接管 gateway 当前监听的 `127.0.0.1:8443`（WAF upstream 不变），承载**全部认证面**（登录/注册/Token 生命周期/CAPTCHA 代理/限流/WS 代理），并作为**认证前置层**：对业务请求本地验签后注入可信身份头，转发给退居 `127.0.0.1:8445` 的 gateway。gateway 删除全部认证代码，不再验签，仅保留白名单反代。

核心收益：验签在 CA 本地完成（RSA 密钥同进程，**零新增网络跳**）；core/dnfs2 仍调 `8443` 的 `/admin/verify`、`/whoami`，**无感知切换**；前端 URL 不变；现有黑盒 34 断言原样充当验收门。

---

## 1. 现状分析

### 1.1 当前调用链

```
客户端 ──HTTPS──▶ WAF:443 ──HTTP──▶ gateway:8443 ──HTTP──▶ static:9200 / dnfs2:8006 / core:5200
                                     │  ├─ 本地验签（RSA 内存密钥）+ Cookie 回退 + Authorization 注入
                                     │  ├─ /login /register /logout /refresh_token /whoami /admin/verify
                                     │  ├─ /register/send_code /reset/send_code /reset_password
                                     │  ├─ GET /captcha/image（pymsg 代理 captcha2:8009）
                                     │  └─ WS 代理 /ws/aibox（握手验签 is_admin）
                                     └── pymsg: db:8005 / captcha2:8009 / smtp3:8008 / confs:8010

下游验签回调（HTTP 回环）:
  core:5200  ──HTTP──▶ gateway:8443/admin/verify   （HttpTask.cpp，GATEWAY_HOST/GATEWAY_PORT 可配，fail-closed）
  dnfs2:8006 ──HTTP──▶ gateway:8443/whoami          （gateway_url 配置项，fail-closed）
```

### 1.2 gateway 职责盘点与归属

| 职责域 | 函数/组件 | 归属 |
|--------|-----------|------|
| 用户认证 | `handleLogin` / `handleRegister` / `handleLogout` / `handleResetPassword` / `handleSendEmailCode` / `handleResetSendCode` | → CA |
| Token 管理 | `verify_token` / `generate_jti` / `revoke_jti` / `revoke_all_user_tokens` / `handleRefreshToken` / `handleWhoami` / `handleAdminVerify` / jti 黑名单 / 用户版本号表 / RSA 密钥对 | → CA |
| 密码体系 | `PBKDF2Hasher` / `PasswordValidator` / 防枚举 dummy 校验 | → CA |
| 验证码/邮箱 | `handleCaptchaImage` / `verify_captcha_remote` / `verify_email_code_remote` | → CA |
| 速率限制 | 全部 RateLimiter 实例（登录 5/5min、注册 3/10min、发码 5/10min + 邮箱级 3/h+10/d、验证码 10/min 等） | → CA |
| WS 代理 | `verifyWsAdminToken` + `_WS_PROXY_ROUTES` 注册循环 | → CA（验签+桥接一体） |
| Cookie | `q4_token` 签发/清除、`extract_token_from_request`（Cookie 回退）、代理时 `Authorization: Bearer` 注入 | → CA |
| HTTP 反代 | `handleProxy`（白名单/路径安全/头转发/XFF） | 留 gateway（去验签化） |
| 头卫生 | `is_special_header` / `is_safe_target` | 留 gateway |
| 基础设施 | `wrapperHttp` / `ConfIni` / `DbC` / `LogC` / 信号处理 / 优雅关闭 | 两侧各持一份（模板见 smtp3） |

### 1.3 核心问题

1. **职责耦合**：认证与代理同进程，任一域改动爆炸半径为全文件
2. **环形依赖**：gateway 是流量入口又是验签权威，core/dnfs2 须 HTTP 回调入口
3. **敏感面混杂**：RSA 私钥、密码哈希校验、jti 黑名单与转发逻辑共用内存空间
4. **不可独立扩展**：认证密集与代理密集负载无法分别扩容；认证状态纯内存，绑定单实例

---

## 2. 目标架构

### 2.1 拓扑

```
客户端 ──HTTPS──▶ WAF:443 ──HTTP──▶ CA:8443 ──HTTP──▶ gateway:8445 ──▶ static:9200 / dnfs2:8006 / core:5200
                                     │
                                     ├─ 本地处理：/login /register /logout /refresh_token /whoami /admin/verify
                                     │            /register/send_code /reset/send_code /reset_password
                                     │            GET /captcha/image
                                     ├─ WS：/ws/aibox 握手验签(is_admin) → 直接桥接 ws://127.0.0.1:8765
                                     ├─ 其余路径：本地验签 → 注入 X-Auth-User/Authorization → 转发 gateway:8445
                                     └─ pymsg: db:8005 / captcha2:8009 / smtp3:8008 / confs:8010

端口安排：CA 接管 8443（WAF upstream 配置不变）；gateway 迁 8445（仅 loopback，仅 CA 可达）。
```

职责划分：**CA = 认证权威 + 认证前置代理**；**gateway = 纯路由/转发策略**（opened 白名单、路径安全、XFF 透传）。

### 2.2 决策记录

| 方案 | 拓扑 | 结论 |
|------|------|------|
| **选定：CA 前置** | `waf → ca → gateway` | ✅ 验签在 CA 本地（零网络跳）；core/dnfs2 调 8443 不变；WAF/前端不变；gateway 无需新增路径路由机制 |
| 否决 A：CA 后置双栈 | `waf → gateway → ca`（HTTP+pymsg 双栈） | gateway 需新增路径级路由表；CORS/Set-Cookie/错误映射下沉 CA；两服务改动面都大 |
| 否决 B：CA 后置纯 pymsg | `waf → gateway →(pymsg) ca` | gateway 保留 HTTP↔pymsg 翻译层与 Cookie 职责，认证面仍在入口进程内，瘦身不彻底；且每请求多一跳 pymsg 验签或引入吊销延迟的缓存 |
| 否决 C：验签缓存 | gateway 缓存验签结果 60s | logout/改密后 Token 在窗口内仍可用，吊销语义弱化（随拓扑选定自然消失） |

### 2.3 信任模型（重要）

1. **loopback 信任基线不变**：本架构 loopback 内部服务本就不互鉴权（db:8005 execute 无认证即先例）。gateway:8445 仅绑 `127.0.0.1`，与本基线一致。
2. **身份头契约（strip-then-set）**：CA 在边缘剥离客户端伪造的 `X-Auth-User` / `X-Auth-Admin`（Beast fields 大小写不敏感，`erase` 后 `set`），验签通过才注入。模式 A 下 CA 的唯一上游是 WAF（loopback）。
3. **gateway 存在性不变量**：显式 `Target-Url` 请求必须携带 `X-Auth-User`，否则 401（非验签，仅防线编程，防本地误直连/SSR​​F 兜底）。
4. **模式 B 警示迁移**：CA 直连公网（无 WAF）时 XFF 可被伪造、限流可绕过——原 gateway README 的 A4 降级提示随限流一并迁入 CA README；根治方案 [libhttp_remote_addr_dev_doc.md](libhttp_remote_addr_dev_doc.md) 中的 `trust_xff`/`rate_limit_key()` 配套改在 CA 落地。

---

## 3. CA 模块设计

### 3.1 目录与构建（沿用 smtp3/captcha2 约定）

```
ca/
├── src/main.cpp        # 入口 + HTTP 路由 + 认证逻辑 + 前置转发（迁移代码为主）
├── inc/
│   ├── Conf.hpp        # ConfIni（confs 拉取 + env 覆盖 + 完整性校验）
│   ├── DbC.hpp         # 自 gateway 随迁（已基于 libmsg us/Msg.hpp；client 名 "gateway"→"ca"）
│   ├── LogC.hpp        # 异步日志（smtp3 长连接版）
│   ├── ExpBase.hpp     # 异常基类
│   └── LocalTime.hpp   # 时间工具
├── Makefile            # 链接 us_http / us_msg / us_crypt / ssl / crypto / boost_json / curl
├── _set.sh / _run.sh
└── README.md
```

- 二进制 `ca.bin`；版本从 `1.0.0` 起；日志服务名 `ca`
- 依赖：libhttp（HttpServer/HttpClient/ws_proxy）、libmsg（MsgClient/RpcClient）、libcrypt（MyRSA/Base64）、OpenSSL（PBKDF2/RAND_bytes）、boost::json/program_options/property_tree、libcurl
- **不依赖 libsqlite3**：Users 表访问全部经 db:8005（现状即如此）

### 3.2 配置（`confs/configs/ca.ini` 新增）

```ini
[Server]
address = 127.0.0.1
port = 8443                 # 接管 gateway 现端口，WAF upstream 不变

[Upstream]
gateway = http://127.0.0.1:8445   # 业务请求唯一上游

[Token]                     # 自 gateway.ini 原样迁入
keep_time = 3600
refresh_grace_multiplier = 2
max_refresh = 10

[Admin]                     # 原样迁入
users = 1776177662@qq.com;277665039@qq.com;

[WsProxy]                   # 自 gateway.ini 迁入
routes = /ws/aibox=ws://127.0.0.1:8765;

[CORS]                      # 原样迁入（P2-3：预检反射由 libhttp 处理，白名单暂仍不生效）
allowed_origins = https://qsont.me;https://www.qsont.me;http://127.0.0.1:8443;http://localhost:8443

[Downstream]                # 原样迁入
db_host = 127.0.0.1
db_port = 8005
captcha_host = 127.0.0.1
captcha_port = 8009
smtp_host = 127.0.0.1
smtp_port = 8008
```

环境变量覆盖（与 gateway 现机制一致）：`LISTEN_ADDR`/`LISTEN_PORT`、`UPSTREAM_GATEWAY`、`DB_*`、`CAPTCHA_*`、`SMTP_*`、`CONFS_HOST`/`CONFS_PORT`。配置加载：confs `get` 拉取（retry_on_refused 阻塞）→ env 覆盖 → 完整性校验。

### 3.3 HTTP 路由表（对外契约与原 gateway 完全一致）

| 路由 | 处理 | 说明 |
|------|------|------|
| POST `/login` | 本地 | 强制远端 CAPTCHA；dummy PBKDF2 防枚举；签发 Token + `q4_token` Cookie |
| POST `/register` | 本地 | 密码强度→邮箱格式→CAPTCHA→邮箱验证码（smtp fail-closed）→写 Users |
| POST `/register/send_code`、`/reset/send_code` | 本地 | IP+邮箱级限流、强制 CAPTCHA、smtp 60s 冷却 429 透传 |
| POST `/reset_password` | 本地 | 防枚举恒成功文案；改密后版本号递增全设备吊销 |
| POST `/logout` | 本地 | jti 吊销 + 清 Cookie |
| POST `/refresh_token` | 本地 | 宽限期刷新、旧 jti 吊销、max_refresh 上限 |
| POST `/whoami` | 本地 | 恒 200，`{login:false}` 不泄露细节；core/dnfs2 依赖此契约 |
| POST `/admin/verify` | 本地 | fail-closed 401；core 依赖此契约 |
| GET `/captcha/image` | 本地 | pymsg 调 captcha2，PNG + `X-Captcha-Token` + `no-store`，10/min/IP |
| WS `[WsProxy]` 路径 | 本地 | 握手验签 `is_admin=true` → 桥接后端（ws/wss），双向透传 |
| GET/POST `*`（通配） | **转发** | 见 §3.4 |

### 3.4 前置转发层（通配路由 → gateway:8445）

迁移 `handleProxy` 的转发骨架，认证语义前置：

1. **路径安全**：`is_safe_target()` 校验（`..`/`%2e`/`%2f`/`%5c`/空字节 → 400），CA/gateway 双层各执行一次（防线叠加，gateway 侧保留原校验）。
2. **公开 vs 受保护**：无 `Target-Url` 头 → 公开目标（Proxy.default 语义在 gateway），**剥离**客户端 `X-Auth-User`/`X-Auth-Admin` 后直接转发，不注入任何凭证。
3. **受保护**：有 `Target-Url` → 提取 Token（`Authorization` 优先，回退 `q4_token` Cookie）→ `verify_token` 本地验签（RSA→过期→jti→版本号）→ 失败按现契约 400/401，**不转发**；成功则注入：
   - `Authorization: Bearer <token>`（Cookie 来源也统一补齐，dnfs2 等下游零改动）
   - `X-Auth-User: <email>`、`X-Auth-Admin: true`（仅管理员；供 gateway 不变量与下游未来免回调使用）
4. **头卫生**：剥离客户端自带的 `X-Auth-User`/`X-Auth-Admin`（strip-then-set）；XFF 透传 WAF 值（缺省 `unknown`）；`Host`/`connection`/`Accept-Encoding` 等逐跳头处理沿用现 `is_special_header` 集合 + `x-auth-user`/`x-auth-admin` 新增。
5. **转发失败语义**：gateway:8445 不可达 → 502（与现 captcha/smtp fail-closed 风格一致），记 warning 日志。
6. **响应**：状态码与响应头原样回传（现 `handleProxy` 行为），`Connection: close`。

### 3.5 迁移资产清单（gateway `main.cpp` → CA `main.cpp`）

代码以**整体搬迁**为主，保持实现逐字不变（降低回归面），仅做两处适配：日志服务名 `gateway`→`ca`；DbC client 名同上。

| 资产 | 备注 |
|------|------|
| PBKDF2Hasher / PasswordValidator / 常数时间比较 | 含 SHA256 旧哈希迁移注释块（保持注释状态） |
| TokenInfo / verify_token / generate_jti / revoke_jti / revoke_all_user_tokens / jti 黑名单（5000 上限清理）/ 用户版本号表 | RSA 密钥对启动内存生成（重启即全吊销，语义不变） |
| 8 个 RateLimiter 实例 + 邮箱级双窗口限流 | 限流键仍取 XFF（模式 A 可信；模式 B 警示见 §2.3-4） |
| handleLogin ~ handleResetPassword 全部 handler | 错误文案/状态码契约逐字保留（黑盒断言依赖） |
| handleWhoami / handleAdminVerify / handleRefreshToken / handleLogout | 同上 |
| handleCaptchaImage / verify_captcha_remote / verify_email_code_remote | fail-closed 语义保留 |
| verifyWsAdminToken + ws_proxy 注册 | 桥接目标直接为后端（不再经 gateway） |
| handleProxy 转发骨架 / is_safe_target / is_special_header / get_cookie_value / extract_token_from_request | 按 §3.4 改造 |
| wrapperHttp / 信号处理 / 优雅关闭（8s+3s 超时）/ ExpBase / ConfIni / LocalTime / DbC / LogC | 基础设施随迁 |

### 3.6 不变量自检（启动时）

- `[Upstream] gateway` 非空且为 `http://127.0.0.1:*`（防误配到公网）
- 模式 B 检测：`Server.address` 非 loopback 时启动日志打印 A4 安全降级警告

---

## 4. gateway 瘦身设计（v0.9.0）

### 4.1 删除清单（预计 -1700 行，2470 → ~750 行）

- 全部认证 handler 与 Token/PBKDF2/RSA/jti/版本号/限流/CAPTCHA/邮箱验证码代码
- `q4_token` Cookie 读写、`extract_token_from_request`、代理内验签与 Authorization 注入
- `[Token]`/`[Admin]`/`[WsProxy]`/`[Database]` 配置读取；下游 captcha/smtp 配置（db 保留供 LogC）
- `/login` 等 9 条认证路由 + `/captcha/image` 路由 + WS 代理注册循环

### 4.2 保留清单

- `handleProxy`（去验签化）+ `is_safe_target` + `is_special_header` + `wrapperHttp`
- 存在性不变量：显式 `Target-Url` 且无 `X-Auth-User` → 401（§2.3-3）
- 公开目标规则：无 `Target-Url` → `Proxy.default`，不校验身份头
- XFF 处理：透传 CA 值（缺省 `unknown`）
- 配置加载、信号处理、优雅关闭、LogC

### 4.3 精简后 `confs/configs/gateway.ini`

```ini
[Server]
address = 127.0.0.1
port = 8445                # 迁出 8443；仅 CA 可达
tls = off

[Proxy]
default = http://127.0.0.1:9200
closed  = http://127.0.0.1:8005;http://127.0.0.1:8007;
opened  = http://127.0.0.1:9200;http://127.0.0.1:8006;http://127.0.0.1:5200;

[Downstream]               # 仅供 LogC 日志落库
db_host = 127.0.0.1
db_port = 8005
```

### 4.4 启动自检

- `Server.address` 必须为 loopback（`127.0.0.1`/`::1`）：gateway 不再验签后，绑定公网等于裸奔，**非 loopback 直接拒绝启动**并打印致命错误。

---

## 5. 接口契约

### 5.1 对外 HTTP（不变量）

`https://<host>/login`、`/register`、`/whoami` 等全部 URL、请求/响应 JSON 结构、状态码（400/401/403/429/500/502）、`q4_token` Cookie 属性、`X-Captcha-Token` 头——**逐字不变**。前端、WAF、core、dnfs2 零改动的依据即此。

### 5.2 内部头契约（CA ↔ gateway ↔ 下游）

| 头 | 方向 | 语义 |
|----|------|------|
| `X-Auth-User` | CA→gateway→下游 | 已验签身份；CA strip-then-set；gateway 对显式 Target-Url 要求其存在 |
| `X-Auth-Admin` | CA→gateway→下游 | `true` 表示管理员；供下游免回调豁免（可选消费） |
| `Authorization` | CA→gateway→下游 | Cookie 来源统一补齐为 Bearer；下游解析不变 |
| `X-Forwarded-For` | WAF→CA→gateway→下游 | 逐层透传，缺省 `unknown` |
| `Target-Url` | 客户端→CA→gateway | 路由策略头，语义不变 |

### 5.3 Token 格式（不变）

`Base64(payload)_RSA-Sign(payload)`；payload 字段（user/jti/ver/iat/created_at/keep_time/is_admin/refresh_count/max_refresh）不变。切换瞬间旧 Token 全部失效（CA 新密钥），用户重新登录——一次性事件，cutover 通告中说明。

### 5.4 下游零改动验证点

| 消费方 | 现状调用 | 切换后 |
|--------|----------|--------|
| core `verify_admin_token` | `http://127.0.0.1:8443/admin/verify` | 同 URL 由 CA 服务，契约不变 |
| dnfs2 Token 验签 | `http://127.0.0.1:8443/whoami` | 同上 |
| static `/api/check_user` | 直连 db 查 Users（账号旁路） | 不受影响；遗留项另行收口 |

---

## 6. 上线步骤（cutover 与回滚）

| 步骤 | 内容 |
|------|------|
| 1 | CA 开发完成，`LISTEN_PORT=18443` 并行实例联调（不影响现网 8443） |
| 2 | gateway 瘦身版（v0.9.0）构建，`gateway.ini` 改 8445 |
| 3 | cutover（秒级窗口）：停 gateway:8443 → 启 CA:8443 → 启 gateway:8445 → 黑盒全量验证 |
| 4 | `core/init.txt` 增加 `ca/_run.sh`（gateway 之前）；README/INDEX 文档同步 |
| 5 | 观察 24h：CA/gateway 日志、db Logs 表 4xx/5xx 基线对比 |

**回滚**：停 CA → 旧 gateway 二进制 + 旧配置回 8443 → 重启。Token 全部重签（用户重新登录），无数据迁移。

---

## 7. 测试计划

### 7.1 验收门（零改动复用）

- `tests/blackbox/test_gateway.py` 34 断言**原样**对 8443 执行：认证路径打到 CA、代理路径经 CA→gateway，外部契约不变则全绿（当前基线 32 passed / 2 env-skipped）
- 修复期两个红测试（A3 缺字段 400）已在 v0.8.0 转绿，迁移后必须保持

### 7.2 新增探针（test_ca.py，黑盒补充）

| 探针 | 预期 |
|------|------|
| 客户端伪造 `X-Auth-User: admin@x` + 显式 Target-Url（无 Token） | CA 剥离后 401，不到下游 |
| 伪造 `X-Auth-User` + 有效非管理员 Token 访问 WS 路由 | 403（以验签结果为准，不以头为准） |
| 直连 gateway:8445，显式 Target-Url、无 `X-Auth-User` | 401（存在性不变量） |
| 经 CA 全链路（8443）带 Token 访问 dnfs2 | 下游收到 `X-Auth-User` 与 `Authorization` |
| CA 存活、gateway:8445 停止 | 业务路径 502（不静默放行），认证路径正常 |
| gateway 存活、CA 停止 | WAF 502（入口不可达，符合预期 fail-closed） |
| 公开目标（无 Target-Url）带伪造身份头 | 下游收不到 `X-Auth-User`/`Authorization` |

### 7.3 集成回归

- core HTTP 管理接口（`/admin/verify` 链路）实测；dnfs2 笔记 CRUD（`/whoami` 链路 + AI 审核）实测
- WS 代理端到端：管理员 Token 升级 `/ws/aibox` 双向帧；非管理员 403
- 限流全谱在 CA 复测（随机 XFF 技巧沿用）；cutover/回滚演练各一次

### 7.4 测试资产演进（后续）

- `test_gateway.py` 按进程边界拆分为 `test_ca.py`（A/B/C/E 段）+ 精简 `test_gateway.py`（D 段代理矩阵 + 8445 直连探针），README/INDEX 同步
- 认证 happy path 仍依赖 captcha2/smtp 测试钩子（既有遗留，CA 落地后可仅在 CA 层加 `[Security] captcha_required` 开关，单点解锁）

---

## 8. 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| CA 不可用 → 全站入口中断 | 高 | CA 职责内聚后可用性等同于现 gateway；core 守护 + 黑盒监控；回滚方案秒级 |
| 迁移遗漏导致认证行为漂移 | 中 | 代码逐字搬迁 + 34 断言验收门 + §7.2 探针矩阵 |
| 身份头伪造/泄露 | 中 | strip-then-set + gateway 存在性不变量 + 公开目标不注入 + 直连探针回归 |
| gateway:8445 被公网暴露 | 高 | 启动强制 loopback 自检（§4.4）；部署审计 checklist 增加端口扫描项 |
| cutover 窗口 Token 全失效 | 低 | 预期内（RSA 换钥），通告用户重新登录 |
| 双重转发延迟 | 低 | 本机 HTTP 转发 <1ms；CA→gateway 短连接可后续优化为 keep-alive |
| 模式 B 限流绕过（A4 迁移） | 中 | CA README 降级警示；根治按 libhttp_remote_addr_dev_doc（配套改落 CA） |

---

## 9. 工作量估算

| 部分 | 规模 | 估时 |
|------|------|------|
| CA 脚手架（目录/Makefile/_set.sh/_run.sh/LogC 等） | 模板复制 + 适配 | 0.5 天 |
| 认证资产搬迁 + 前置转发层改造 | ~1700 行搬迁 + ~150 行改造 | 1.5 天 |
| gateway 瘦身 + 启动自检 | -1700 行 + ~20 行 | 0.5 天 |
| 配置/init.txt/文档（README×2、INDEX、confs） | — | 0.5 天 |
| 黑盒验收 + §7.2 探针 + 集成回归 + cutover 演练 | — | 1 天 |
| **合计** | | **4 天**（含缓冲） |

---

## 10. 与既有文档的关系

- 本文档**替代** 2026-08-08 初版计划书中的双栈拓扑与阶段划分（初版的职责盘点/迁移清单仍然有效，已并入 §1.2/§3.5）
- [libhttp_remote_addr_dev_doc.md](libhttp_remote_addr_dev_doc.md)（A4 根治）：实施时 `trust_xff`/`rate_limit_key()` 配套由 gateway 改为落 CA；`is_special_header` 两侧同步加 `x-remote-addr`
- [gateway_SECURITY_AUDIT_20260809.md](../security/gateway_SECURITY_AUDIT_20260809.md)：A2（白名单 403）断言、P2-3（CORS 白名单）随转发层/配置迁入 CA；P1-7（CSRF Token）在 CA 层评估
- gateway README / 项目 README 的架构图、端口表、请求链路在上线步骤第 4 步同步更新
