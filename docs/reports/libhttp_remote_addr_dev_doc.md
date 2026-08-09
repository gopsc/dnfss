# libhttp 对端地址透传 · 开发文档

**日期**: 2026-08-09
**背景**: gateway 审计 A4（[gateway_SECURITY_AUDIT_20260809.md](../security/gateway_SECURITY_AUDIT_20260809.md)）——独立部署模式 B 下 RateLimiter 以客户端可控的 `X-Forwarded-For` 为限流键，伪造 XFF 即绕过全部 IP 级限流。根治需要 libhttp 向 handler 透传 TCP 对端地址。
**范围**: `libhttp`（注入实现）、`gateway`（消费方配套）；其余消费方（core/dnfs2/fsys）零改动。

---

## 0. 摘要

libhttp 的 Session 层**已经持有**对端地址（accept 时 `setRemoteInfo()`），但未向请求处理链路透传：`HttpRequestHandler` 只收到 `(req, params)`。本方案在 Session 派发前向请求注入**可信私有头** `X-Remote-Addr`（先剥离客户端伪造值再写入对端 IP），实现非破坏性透传；gateway 侧新增 `trust_xff` 配置选择限流键，闭环 A4。

---

## 1. 现状分析

### 1.1 调用链与缺口

```
Listener::onAccept          ← socket.remote_endpoint() 在此可用
  └─ session->setRemoteInfo(ip, port)          （HttpServer_Listener.cpp L50）
       └─ Session::handleRead                  （解析完成，WS/HTTP 分流）
            ├─ WS 路径：findWsProxyRoute(path)(request_) / findWsRoute
            └─ HTTP 路径：processRequest(req)
                 └─ server_->handleRequest(req)          （HttpServer_Request.cpp L8）
                      └─ handler(req, params)            ← 对端地址在此丢失
```

**缺口**: `remote_ip_`/`remote_port_` 是 `Session` 私有成员（`HttpServerImpl.hpp` L122-123），仅用于 SSL 握手与读写错误日志；`handleRequest`/`handler` 拿不到。

### 1.2 已有基础设施（无需新建）

| 设施 | 位置 | 说明 |
|------|------|------|
| `Session::setRemoteInfo(ip, port)` | `HttpServerImpl.hpp` L60 | accept 时已被调用 |
| `Session::getRemoteAddress()` | `HttpServerImpl.hpp` L63 | `"ip:port"` 形式 |
| `remote_ip_` / `remote_port_` 成员 | `HttpServerImpl.hpp` L122-123 | 默认 `"unknown"` / 0 |
| WS 侧对端地址 | `WebSocketConnectHandler` | WS 路径已有 `remoteAddress` 回调参数，HTTP 路径独缺 |

### 1.3 消费方清单（`us_http` 链接者）

| 模块 | 受影响 | 说明 |
|------|--------|------|
| gateway | 配套改造 | 限流键切换 + 代理剥离私有头（A4 闭环） |
| core / dnfs2 / fsys | 零改动 | 非破坏方案下无感知 |

---

## 2. 方案对比

| 方案 | 改动 | 破坏性 | 评价 |
|------|------|--------|------|
| **A. handler 签名扩展** | `HttpRequestHandler` 加第三参 `remote_addr` | 🔴 破坏：4 模块全部路由注册 lambda（`[](const auto& req, const auto& params)`）编译失败，gateway 约 15 处 | 类型安全但爆炸半径大；std::function 签名变更无渐进路径 |
| **B. 可信私有头注入** | Session 派发前 erase+set `X-Remote-Addr` | 🟢 非破坏：handler 签名不变，消费方 opt-in 读取 | **推荐**。与 WAF 覆写 XFF、gateway 注入可信 XFF 的既有模式同构 |
| **C. 新增 Ex 注册 API** | `HttpRequestHandlerEx` + `getEx/postEx/...` | 🟢 非破坏 | 长期双 API 并存，路由查找分叉，不值得 |

**结论：选 B**。若未来确需类型安全（如更多连接元信息：TLS 指纹、证书序列号），可在 B 稳定后再评估 A，B 作为便利层保留。

---

## 3. 方案 B 详细设计

### 3.1 注入点与语义

**注入点**: `Session::handleRead()` 解析成功分支（`HttpServer_Request.cpp` L266 `if (!ec)` 内、WS 分流判断**之前**）。单点覆盖 HTTP 与 WS-upgrade 两条路径，且 keep-alive 连接复用时每个请求独立注入（对端地址在同一连接内不变，重复注入无害）。

**语义（strip-then-set，防伪造）**:

```cpp
// handleRead() if (!ec) 分支最前面：
// 注入可信对端地址：先剥离客户端伪造值，再写入 socket 对端 IP（A4 根治）
request_.erase("X-Remote-Addr");                 // Beast fields 大小写不敏感
request_.set("X-Remote-Addr", remote_ip_);
```

- Beast 的 `fields::erase/set` 对头名大小写不敏感，`set` 本身即覆盖全部同名字段；显式 `erase` 先行使意图自解释。
- 头名 `X-Remote-Addr`：与 `X-Forwarded-For`/`X-Real-IP` 区分语义——**它不是转发链信息，而是本跳的 socket 事实**，任何代理都不得原样转发（见 §4.2 剥离配套）。

### 3.2 边缘情况

| 场景 | 行为 |
|------|------|
| 客户端伪造 `X-Remote-Addr`（任意大小写） | 被 erase，无效 |
| keep-alive 同连接第 N 个请求 | 每次解析后重新注入，值不变 |
| WS-upgrade 请求 | 同样注入（注入点在分流前），WS 握手回调可读取 |
| 请求体超限（413）/解析失败 | 不进 handleRead 成功分支，无注入，也无 handler 调用 |
| `remote_ip_` 为 `"unknown"` | 理论上 accept 后必被 setRemoteInfo 覆盖；兜底值仍可注入，不影响正确性 |

### 3.3 性能

每请求一次 `erase` + `set`，复杂度 O(头字段数)（Beast fields 为线性表），实测开销可忽略；无堆分配之外的引入（`remote_ip_` 已存在）。

---

## 4. gateway 配套设计（A4 闭环）

### 4.1 配置项

`gateway.ini` 新增（confs 下发，与现有配置两层加载一致）：

```ini
[Security]
# 是否信任 X-Forwarded-For 作为限流/审计的客户端 IP 键。
# on  = 模式 A（WAF 协作）：WAF 覆写 XFF，可信（默认，保持现状）
# off = 模式 B（独立部署）：XFF 客户端可伪造，改用 libhttp 注入的 X-Remote-Addr
trust_xff = on
```

环境变量覆盖：`TRUST_XFF=off`（与 `LISTEN_*`/`DB_*` 覆盖机制同路径）。**默认 on**，不改变现有生产（模式 A）行为。

### 4.2 代码改动（`gateway/src/main.cpp`，约 30 行）

1. **代理剥离**: `is_special_header()` 增加 `x-remote-addr`（小写比较已就绪）——防止两件事：(a) 模式 A 下把"WAF 的 IP"泄露给下游；(b) 客户端伪造值随转发到达下游。
2. **限流键抽取**: 新增 helper，替换 5 处限流点的 XFF 直读（login/register/send_code/reset_password/captcha_image）：

```cpp
/* 限流键：模式 A 取 WAF 注入的 XFF；模式 B（trust_xff=off）取 libhttp 注入的
 * 对端地址，客户端伪造 XFF 不再影响限流（审计 A4） */
static std::string rate_limit_key(const http::request<http::string_body>& req) {
    auto it = req.base().find(_TRUST_XFF ? "X-Forwarded-For" : "X-Remote-Addr");
    if (it != req.base().end()) return std::string(it->value());
    return "direct";
}
```

3. **配置加载**: `main()` 读取 `[Security] trust_xff`（默认 true）→ `_TRUST_XFF` 全局 bool；启动日志打印当前模式。
4. **README**: 模式 B 的 A4 降级提示翻转为已根治（`trust_xff=off` 时按对端地址限流），并补充模式 B 配置示例。

### 4.3 行为矩阵

| 部署 | trust_xff | 限流键 | 伪造 XFF 效果 |
|------|-----------|--------|---------------|
| 模式 A（WAF） | on（默认） | XFF（WAF 覆写） | 无效（现状） |
| 模式 B（独立） | off | `X-Remote-Addr`（socket 对端） | **无效（新增）** |
| 模式 B（独立） | on | XFF（客户端控制） | 可绕过限流（现状，文档已警示） |

---

## 5. 开发计划

| 步骤 | 文件 | 内容 |
|------|------|------|
| 1 | `libhttp/src/HttpServer_Request.cpp` | `handleRead` 成功分支注入 `X-Remote-Addr`（2 行 + 注释） |
| 2 | `libhttp/test_server.cpp` | 用例：handler 断言收到注入头；客户端自带伪造头被覆盖 |
| 3 | `libhttp/README.md` | 接口契约补充：`X-Remote-Addr` 由服务端注入、语义、禁止转发约定 |
| 4 | `gateway/src/main.cpp` | `is_special_header` 加 `x-remote-addr`；`rate_limit_key()` helper；5 处限流点替换；`_TRUST_XFF` 加载 |
| 5 | `confs/configs/gateway.ini` | `[Security] trust_xff = on`（含注释） |
| 6 | `gateway/README.md` | 模式 B 提示翻转 + 配置项文档 |
| 7 | `tests/blackbox/test_gateway.py` | E 段限流用例参数化：`trust_xff=off` 重启后伪造 XFF 不再重置限流桶 |

**编译依赖**: gateway 需重链新版 `us_http`；core/dnfs2/fsys 无需重编（头注入对它们透明），但建议随版本统一重建。

## 6. 测试计划

| 测试 | 预期 |
|------|------|
| libhttp 单测：handler 读取 `X-Remote-Addr` | 等于连接对端 IP（127.0.0.1） |
| libhttp 单测：客户端发送 `x-remote-addr: 6.6.6.6` | handler 读到的是对端 IP，伪造值被覆盖 |
| gateway 回归（trust_xff 默认 on） | 现有 34 断言全绿（32 passed / 2 env-skipped） |
| gateway 模式 B 实测（trust_xff=off） | 同 XFF 连发 6 次 /login → 全部按对端 IP 计数；换伪造 XFF **不再**获得新桶（第 6 次起稳定 429） |
| 代理转发检查 | 下游收到的请求不含 `X-Remote-Addr`（用 static echo 或抓包验证） |

## 7. 风险与兼容

| 风险 | 评估 | 缓解 |
|------|------|------|
| 下游泄露对端地址 | gateway 转发前剥离（§4.2-1）；core/dnfs2/fsys 不做转发，无泄露面 | 剥离进 `is_special_header`，与 XFF 同机制 |
| 客户端伪造 | erase-then-set，任意大小写变体均被覆盖 | Beast fields 大小写不敏感保证 |
| 既有消费方兼容 | handler 签名不变，不读该头即无感 | 非破坏设计本身 |
| 模式 A 误配 trust_xff=off | 限流键变为 WAF 的 IP（127.0.0.1），全站共享一个桶 → 限流形同全局熔断 | 启动日志打印模式；README 配置矩阵警示 |

## 8. 工作量估算

| 部分 | 规模 |
|------|------|
| libhttp（实现 + 单测 + README） | ~40 行 |
| gateway（helper + 5 处替换 + 配置 + 剥离） | ~35 行 |
| 配置/文档/黑盒用例 | ~50 行 |
| **合计** | 半天内（含构建与实测） |
