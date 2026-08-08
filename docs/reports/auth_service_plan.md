# 权限系统独立化计划书

## 一、现状分析

### 1.1 当前架构

```
客户端 ──HTTPS──▶ WAF:443 ──HTTP──▶ gateway:8443 ──HTTP──▶ 下游服务
                                    ( auth + proxy + captcha + rate-limit + ws-proxy )
                                        │
                        ┌───────────────┼───────────────┐
                        ▼               ▼               ▼
                    db:8005        captcha:8009      smtp:8008
                   (Users表)       (图形验证码)       (邮件发送)

下游鉴权调用链:
  core:5200   ──HTTP POST──▶ gateway:/admin/verify  (管理员验签)
  dnfs2:8006  ──HTTP POST──▶ gateway:/whoami        (Token 验签)
```

### 1.2 Gateway 职责清单（main.cpp 2508 行，单文件）

| 职责域 | 函数/组件 | 行数占比 |
|--------|-----------|---------|
| **用户认证** | handleLogin, handleRegister, handleLogout, handleResetPassword, handleSendEmailCode, handleResetSendCode, PBKDF2Hasher, PasswordValidator | ~40% |
| **Token 管理** | verify_token, generate_jti, revoke_jti, revoke_all_user_tokens, handleRefreshToken, handleWhoami, handleAdminVerify, TokenInfo, JtiDenylist, UserVerMap | ~25% |
| **CAPTCHA** | handleCaptcha, handleCaptchaImage, CaptchaEntry, generate_captcha_text, verify_captcha | ~8% |
| **速率限制** | 8 个 RateLimiter 实例（login/register/email/refresh/reset 等） | ~5% |
| **HTTP 反向代理** | handleProxy, is_special_header, is_safe_target | ~12% |
| **WebSocket 代理** | verifyWsAdminToken, ws_proxy 路由注册 | ~5% |
| **配置/基础设施** | main(), registerRoutes(), ConfIni, DbC, LogC, 信号处理 | ~5% |

### 1.3 核心问题

1. **职责严重耦合**：auth（~65%代码量）与 proxy（~17%）捆绑在同一进程，任一域的重构/扩展都影响全局
2. **不可独立扩展**：认证密集型场景（批量登录/注册）和代理密集型场景（大文件传输）无法分别扩容
3. **安全隔离缺失**：密码哈希、RSA 私钥、Token 黑名单等敏感数据与代理逻辑共存于同一进程内存空间
4. **下游耦合 gateway**：core/dnfs2 通过 HTTP 回调 gateway 做验签，gateway 既是流量入口又是认证权威，形成环形依赖
5. **单文件维护瓶颈**：2508 行 C++ 单文件，所有功能交叉耦合，测试和 code review 成本高
6. **Token 状态纯内存**：jti 黑名单和版本号在 gateway 重启后丢失（虽然 RSA 密钥同步重建可兜底，但限制了多实例部署）

---

## 二、目标架构

```
客户端 ──HTTPS──▶ WAF:443 ──HTTP──▶ gateway:8443 ──HTTP──▶ 下游服务
                                    ( 纯反向代理 + Token 透传 )
                                        │
                                        ▼
                                  auth:8444 (独立权限服务)
                                   ┌────┼────┐
                                   ▼    ▼    ▼
                              db:8005  captcha  smtp
                            
下游鉴权调用链（改造后）:
  core:5200   ──pymsg──▶ auth:8444:/verify  (管理员验签)
  dnfs2:8006  ──pymsg──▶ auth:8444:/whoami  (Token 验签)
  gateway     ──pymsg──▶ auth:8444:/verify  (代理转发前的 Token 校验)
```

### 2.1 职责划分

| 模块 | 改造后职责 |
|------|-----------|
| **auth（新）** | 用户注册/登录/注销、Token 签发/验签/刷新/吊销、CAPTCHA、密码重置、邮箱验证码、速率限制、管理员验证 |
| **gateway（精简）** | HTTP/WebSocket 反向代理、Target-Url 白名单过滤、XFF 注入、CORS 预检；认证路由（/login, /register 等）作为代理目标指向 auth |
| **core / dnfs2** | 验签调用目标由 gateway 改为 auth（pymsg 协议，消除 HTTP 回调） |

---

## 三、实施阶段

### 阶段一：auth 服务搭建（核心剥离）

**目标**：将认证逻辑整体迁入独立 C++ 服务

**新建目录结构**：
```
auth/
├── src/
│   └── main.cpp            # 入口 + 路由注册
├── inc/
│   ├── AuthService.hpp     # 核心认证逻辑（PBKDF2、Token 签发/验签/吊销）
│   ├── RateLimiter.hpp     # 速率限制器（从 gateway 迁移）
│   ├── CaptchaService.hpp  # CAPTCHA 生成/验证
│   ├── DbC.hpp             # 复用 gateway 的 DbC（提取为公共库或复制）
│   ├── LogC.hpp            # 复用 LogC
│   ├── Conf.hpp            # 复用 ConfIni
│   ├── LocalTime.hpp       # 复用
│   └── ExpBase.hpp         # 复用
├── Makefile
├── _run.sh
├── _set.sh
└── README.md
```

**迁移清单**（从 gateway/src/main.cpp 提取）：

| 组件 | 源位置 | 目标文件 |
|------|--------|---------|
| PBKDF2Hasher | main.cpp L62-85 | AuthService.hpp |
| PasswordValidator | main.cpp L87-160 | AuthService.hpp |
| TokenInfo / generate_jti / verify_token / revoke_* | main.cpp L340-760 | AuthService.hpp |
| handleLogin | main.cpp L1346-1525 | main.cpp (auth) |
| handleRegister | main.cpp L1891-1981 | main.cpp (auth) |
| handleLogout | main.cpp L1527-1554 | main.cpp (auth) |
| handleWhoami | main.cpp L1556-1606 | main.cpp (auth) |
| handleAdminVerify | main.cpp L1608-1657 | main.cpp (auth) |
| handleRefreshToken | main.cpp L1672-1837 | main.cpp (auth) |
| handleSendEmailCode | main.cpp ~L1983-2080 | main.cpp (auth) |
| handleResetSendCode | main.cpp ~L2080-2170 | main.cpp (auth) |
| handleResetPassword | main.cpp ~L2170-2308 | main.cpp (auth) |
| handleCaptcha / handleCaptchaImage | main.cpp ~L1158,1845-1890 | CaptchaService.hpp |
| RateLimiter 实例（8 个） | main.cpp L417-427 | RateLimiter.hpp |
| CAPTCHA_STORE | main.cpp L438-499 | CaptchaService.hpp |
| JTI_DENYLIST / USER_VER_MAP | main.cpp L447-465 | AuthService.hpp |

**新增配置**（`confs/configs/auth.ini`）：
```ini
[Server]
address = 127.0.0.1
port = 8444

[Token]
keep_time = 3600
refresh_grace_multiplier = 2
max_refresh = 10

[Admin]
users = 1776177662@qq.com;277665039@qq.com;

[Downstream]
db_host = 127.0.0.1
db_port = 8005
captcha_host = 127.0.0.1
captcha_port = 8009
smtp_host = 127.0.0.1
smtp_port = 8008
```

**对外接口**（pymsg TCP 协议 + HTTP 双栈）：

| 路由 | 协议 | 说明 |
|------|------|------|
| POST /login | HTTP | 登录（兼容前端直访） |
| POST /register | HTTP | 注册 |
| POST /logout | HTTP | 注销 |
| POST /refresh_token | HTTP | Token 刷新 |
| POST /whoami | HTTP | 身份查询 |
| POST /register/send_code | HTTP | 发送注册验证码 |
| POST /reset/send_code | HTTP | 发送重置验证码 |
| POST /reset_password | HTTP | 重置密码 |
| POST /captcha | HTTP | 生成验证码 |
| GET /captcha/image | HTTP | 验证码图片代理 |
| verify | pymsg | 内部验签（替代 /admin/verify，供 core/dnfs2/gateway 调用） |
| whoami | pymsg | 内部身份查询（供 dnfs2 调用） |

### 阶段二：gateway 瘦身

**目标**：gateway 仅保留反向代理 + WebSocket 代理

**保留内容**：
- `handleProxy`（HTTP 反代 + Token 透传）
- WebSocket 代理路由注册 + `verifyWsAdminToken`（改为调 auth 的 pymsg verify 接口）
- `is_special_header` / `is_safe_target` / `get_cookie_value`
- 配置加载、信号处理、LogC

**删除内容**：
- 所有 auth handler（handleLogin ~ handleResetPassword）
- PBKDF2Hasher / PasswordValidator / TokenInfo / JTI_DENYLIST / USER_VER_MAP
- CAPTCHA 相关代码
- 8 个 RateLimiter 实例（迁移到 auth，auth 路由不再经过 gateway 限流）
- RSA 密钥对生成（`_KEYGEN`，迁移到 auth）

**gateway.ini 精简**：
```ini
[Server]
address = 127.0.0.1
port = 8443
tls = off

[Proxy]
default = http://127.0.0.1:9200
opened = http://127.0.0.1:9200;http://127.0.0.1:8006;http://127.0.0.1:5200;

[Auth]
host = 127.0.0.1
port = 8444

[WsProxy]
routes = /ws/aibox=ws://127.0.0.1:8765;

[CORS]
allowed_origins = https://qsont.me;https://www.qsont.me
```

**认证路由处理方案**（二选一）：

- **方案 A（推荐）：gateway 代理认证路由**
  - gateway 将 `/login`、`/register` 等路径加入代理白名单，Target-Url 自动指向 auth 服务
  - 前端请求路径不变：`POST /login` → WAF → gateway → auth
  - 优势：前端零改动，gateway 仍是唯一入口
  
- **方案 B：auth 直暴露**
  - WAF 配置分流规则，认证路径直接转发到 auth:8444
  - 需改动 WAF 配置，增加路由复杂度

### 阶段三：下游服务适配

**目标**：core 和 dnfs2 的验签调用目标从 gateway 切换为 auth

#### 3.1 core 适配

**当前**（`core/src/main/HttpTask.cpp` L155-204）：
```cpp
// HTTP POST → gateway:8443/admin/verify
std::string url = "http://" + host + ":" + std::to_string(port) + "/admin/verify";
pmc::net::HttpClient client;
client.set_header("Authorization: " + raw);
client.post_stream_sync(url, "", ...);
```

**改造后**：
```cpp
// pymsg → auth:8444 verify 路由（消除 HTTP 回调，统一内部协议）
pmc::net::MsgClient client(auth_host, auth_port, "core");
json::value req = {{"_route", "verify"}, {"token", raw_token}};
std::string resp = client.call(json::serialize(req), 5.0);
```

#### 3.2 dnfs2 适配

**当前**（`dnfs2/src/dnfs2.cpp` L446-495）：
```cpp
// HTTP POST → gateway:8443/whoami
http.post_stream_sync(_cfg.gateway_url, "{}", ...);
```

**改造后**：
```cpp
// pymsg → auth:8444 whoami 路由
RpcClient auth_client(_cfg.auth_host, _cfg.auth_port, "dnfs2", timeout);
json::value result = auth_client.call("whoami", {{"token", token}});
```

**配置文件变更**（`confs/configs/dnfs.ini`）：
```ini
[auth]          # 新增，替代 [gateway]
host = 127.0.0.1
port = 8444
timeout = 5
```

### 阶段四：启动编排与部署

**core/init.txt 更新**：
```
bash /opt/micro_servers/db2/_run.sh
bash /opt/micro_servers/confs/_run.sh
bash /opt/micro_servers/auth/_run.sh        # 新增，须在 gateway 之前启动
bash /opt/micro_servers/ai_review/_run.sh
bash /opt/micro_servers/dnfs2/_run.sh
bash /opt/micro_servers/smtp/_run.sh
bash /opt/micro_servers/static/_run.sh
bash /opt/micro_servers/waf/_run.sh
bash /opt/micro_servers/gateway/_run.sh
bash /opt/micro_servers/captcha/_run.sh
bash /opt/micro_servers/aibox/_run.sh
bash /opt/micro_servers/redirect80/_run.sh
```

**docker-compose.yml 新增 auth 服务**：
```yaml
auth:
  build:
    context: .
    dockerfile: auth/Dockerfile
  container_name: micro-auth
  ports:
    - "127.0.0.1:8444:8444"
  environment:
    - LISTEN_ADDR=0.0.0.0
    - LISTEN_PORT=8444
    - DB_HOST=db
    - DB_PORT=8005
  depends_on:
    db:
      condition: service_healthy
```

---

## 四、接口契约设计

### 4.1 auth 内部 pymsg 接口（供其他微服务调用）

#### verify 路由（替代 /admin/verify）
```json
// 请求
{"_route": "verify", "token": "base64payload_signature", "require_admin": true}

// 响应（成功）
{"success": true, "user": "admin@example.com", "is_admin": true}

// 响应（失败）
{"success": false, "error": "token expired"}
```

#### whoami 路由（替代 /whoami）
```json
// 请求
{"_route": "whoami", "token": "base64payload_signature"}

// 响应（已登录）
{"success": true, "login": true, "user": "user@example.com", "is_admin": false}

// 响应（未登录/Token 无效）
{"success": true, "login": false}
```

### 4.2 Token 格式不变

保持现有 `Base64(payload)_RSA-Sign(payload)` 格式，payload 字段不变：
```json
{
  "user": "email@example.com",
  "jti": "random_hex_32",
  "ver": 1,
  "iat": "2026-08-08 12:00:00",
  "created_at": "2026-08-08 12:00:00",
  "keep_time": 3600,
  "is_admin": true,
  "refresh_count": 0,
  "max_refresh": 10
}
```

---

## 五、风险评估与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| auth 服务不可用导致全系统鉴权失败 | 高 | auth 的 pymsg 客户端启用 `retry_on_refused`；gateway 对 auth 路由的代理失败返回 503（而非静默放行） |
| 前端请求路径变化 | 中 | 采用方案 A（gateway 代理认证路由），前端零改动 |
| Token 验签增加一跳网络延迟 | 低 | pymsg TCP 本机通信延迟 <1ms；gateway 代理层可增加 Token 缓存（TTL 60s） |
| RSA 密钥迁移 | 中 | auth 启动时自生成密钥对（与当前 gateway 行为一致）；过渡期可共享密钥文件 |
| 过渡期双系统并存 | 中 | 分阶段切换：先 auth 上线 → gateway 认证路由代理到 auth → 删除 gateway 内置 auth 代码 |
| Token 黑名单持久化需求 | 低 | 初期保持纯内存方案（与现状一致）；后续可选 Redis 持久化 |

---

## 六、时间估算

| 阶段 | 工作量 | 说明 |
|------|--------|------|
| 阶段一：auth 服务搭建 | 2-3 天 | 代码迁移 + 新配置 + 测试 |
| 阶段二：gateway 瘦身 | 1 天 | 删除代码 + 代理路由配置 + 回归测试 |
| 阶段三：下游适配 | 1 天 | core/dnfs2 验签调用改造 + 集成测试 |
| 阶段四：部署编排 | 0.5 天 | init.txt + docker-compose + 端到端测试 |
| **合计** | **4.5-5.5 天** | 含测试缓冲 |

---

## 七、验收标准

1. auth 服务独立启动，通过 pymsg 和 HTTP 双栈提供全部认证接口
2. gateway 代码量从 ~2500 行降至 ~800 行，仅含代理逻辑
3. 前端所有认证操作（登录/注册/注销/刷新/重置密码）路径不变、行为不变
4. core/dnfs2 通过 pymsg 调用 auth 验签，延迟 ≤ 5ms（本机）
5. auth 服务重启不影响 gateway 代理转发（已认证请求在 Token 过期前仍有效）
6. 所有现有安全特性（PBKDF2、RSA 签名、常数时间比较、jti 黑名单、版本号吊销）完整保留
