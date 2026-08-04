# libhttp 安全审计报告

**审计日期**: 2026-08-03  
**审计范围**: `libhttp/inc/us/`、`libhttp/src/`（HttpServer 6 个拆分 TU + HttpClient + 遗留 WebSocketServer/WebSocketClient）  
**代码版本**: dev_qing 分支

---

## 修复进度

| 优先级 | 总数 | 已修复 | 部分修复 | 未修复 |
|--------|------|--------|----------|--------|
| P0 | 0 | 0 | 0 | 0 |
| P1 | 6 | 3 | 0 | 3 |
| P2 | 4 | 0 | 0 | 4 |
| **合计** | **10** | **5** | **0** | **5** |

---

## 🟠 P1 — 高危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P1-1 | 请求体大小限制不可配置 + 超限无 413 | ✅ 已修复 | `Session::doRead`（`HttpServer_Request.cpp` L167-186）改用 `http::request_parser` + `body_limit()`（默认 10MB，与 WAF 对齐）；`HttpServer` 新增 `setMaxBodySize()`/`getMaxBodySize()`（`HttpServer.hpp` L118-120）。超限时 `handleRead` 返回 **413 Payload Too Large** 并关闭连接（L256-265），而非静默断连 |
| P1-2 | HttpClient 响应无大小限制 | ✅ 已修复 | `HttpClient.cpp` 新增 `WriteCtx`（流式回调 + 累计字节 + 上限），`write_callback` 超限返回 0 触发 `CURLE_WRITE_ERROR`，`request_stream_sync` 抛异常；`request_stream_sync` 改为**真流式**（callback 非空时每块即回调，不二次累积），`HttpClient` 新增 `set_max_response_size()`/`get_max_response_size()`（默认 10MB，对齐 WAF，0 表示不限制） |
| P1-3 | WebSocket 消息无大小限制 | ❌ 未修复 | `Session::onWsRead`（`HttpServer_WsSession.cpp` L88-111）把整条 WS 消息读入内存，依赖 Beast 默认 `read_message_max`（16MB）不可配置；超大消息可被恶意客户端推送导致 OOM |
| P1-4 | 服务器无读写超时（Slowloris） | ❌ 未修复 | 入站 `stream_`（`beast::tcp_stream`）**从未设置 `expires_after`**（仅代理后端设了超时）。攻击者建立连接后缓慢发送 HTTP 头，可无限占用连接与线程，Slowloris/连接耗尽（对应全局审计 P2-4） |
| P1-5 | 静态文件目录路径遍历 | ✅ 已修复 | 新增 `isSafeStaticPath()`（`HttpServer_Request.cpp` L139-156），静态文件服务前校验 target_path（拒绝 `..`、空字节、非 `/` 开头、URL 编码 `%2e`/`%2f`/`%5c`），逻辑与 gateway `is_safe_target()` 对齐（`HttpServer.cpp` L94） |
| P1-6 | 发送队列无界（慢消费者 OOM） | ❌ 未修复 | `ws_send_queue_`、`proxy_to_backend_queue_`、`proxy_to_client_queue_`（`HttpServer_WsSession.cpp` L8-16/L362-367/L438-443）均为无界 `std::deque`。若客户端消费慢，服务端持续入队可导致内存耗尽 |

---

## 🟡 P2 — 中危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P2-1 | CORS 反射任意 Origin | ❌ 未修复 | OPTIONS 预检（`HttpServer_Request.cpp` L73-74）把 `Access-Control-Allow-Origin` 直接设为请求的 `Origin` 值，任意来源均被放行，无白名单校验（对应全局审计 P2-3 根因） |
| P2-2 | TLS 客户端一律不校验证书 | ❌ 未修复 | `HttpClient.cpp` L131 与 WS 代理 `HttpServer_WsSession.cpp` L243 均 `set_verify_mode(asio::ssl::verify_none)`。对内部后端可接受，但库内不留逃生口，无法在需要校验时启用 |
| P2-3 | WebSocket 握手无 Origin/CSWSH 校验 | ❌ 未修复 | libhttp 对任意 `ws`/`ws_proxy` upgrade 请求直接握手，不校验 `Origin`/`Sec-WebSocket-Protocol`。若后端依赖 Cookie 鉴权，存在跨站 WebSocket 劫持（CSWSH）风险（gateway 的 `ws_proxy` 依赖握手回调鉴权，属应用层缓解） |
| P2-4 | HttpClient `ws_recv` 忽略 timeout 参数 | ❌ 未修复 | `HttpClient.cpp` L220-221 `(void)timeout_ms;` 丢弃超时参数，`ws_recv` 会**无限阻塞**。后端挂起时调用线程永久卡死 |
| P2-5 | 反向代理后端连接建立后无超时 | ❌ 未修复 | 代理后端握手成功后 `proxy_tcp_stream_->expires_never()`（`HttpServer_WsSession.cpp` L262/L287），双向读写无超时，慢后端可长期占用连接 |
| P2-6 | 无连接数限制 | ❌ 未修复 | `Listener::doAccept`（`HttpServer_Listener.cpp` L23-48）无限接受连接，每连接一个 Session，无最大连接数上限，可被连接耗尽 |

---

## 🔵 P3 — 低危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P3-1 | 遗留 WebSocketServer/WebSocketClient 未构建 | ✅ 已修复 | 已删除 `WebSocketServer.cpp`/`WebSocketClient.cpp`/`WebSocketServer.hpp`/`WebSocketClient.hpp`/`WebSocketClientBase.hpp`（未构建、与 `HttpClient` 别名同名冲突、有缺陷），并清理 Makefile/README 引用。WebSocket 能力由 `HttpServer` + `HttpClient` 提供 |
| P3-2 | CMakeLists.txt 过时 | ✅ 已修复 | CMakeLists 已重写对齐 Makefile：`libus_http.so`（6 个服务端 TU）+ `libus_httpclient.so`（HttpClient），移除遗留文件引用与错误库名 |

---

## ✅ 已有安全机制

1. ✅ **Beast 默认头/体限制**: `http::async_read` 使用 Beast 默认 `header_limit`(8KB) + `body_limit`(1MB)，超限拒绝暴力请求
2. ✅ **TLS 服务端密码套件受限**: `initSSLContext`（`HttpServer.cpp` L30-62）禁用 `!aNULL:!MD5:!DSS`，仅允许强套件
3. ✅ **TLS 服务端禁用 SSLv2/v3**: `no_sslv2 | no_sslv3`（`HttpServer.cpp` L36-37）
4. ✅ **会话生命周期管理**: `enable_shared_from_this` + `close()` 析构，连接关闭时清理 WS 代理队列
5. ✅ **代理后端连接超时**: 建立连接阶段设 `expires_after(10s)`（`HttpServer_WsSession.cpp` L225/L253）
6. ✅ **WS 消息顺序保持**: 写队列串行化，避免并发写交错
7. ✅ **WS 代理握手鉴权回调**: `ws_proxy` 支持 `WsProxyHandshakeHandler` 在 upgrade 阶段授权（gateway 用于 admin-only 控制）

---

## 修复路线图

### 立即（24小时）
- [x] P1-1 请求体大小限制：`doRead` 改用 `http::request_parser` + `body_limit()`，`HttpServer` 增加 `setMaxBodySize()`（对齐 WAF 10MB），超限返回 413 而非断开 → ✅ 已实现（默认 10MB，`setMaxBodySize()` 可覆盖）
- [x] P1-2 HttpClient 响应上限：`write_callback` 累计超过上限即失败，返回错误 → ✅ 已实现（`WriteCtx` 流式回调 + 上限，默认 10MB，`set_max_response_size()` 可覆盖）
- [ ] P1-4 服务器读超时：入站 `stream_` 设置 `expires_after`（首字节/头+体超时），防 Slowloris

### 短期（72小时）
- [ ] P1-3 WS 消息大小限制：设置 `read_message_max`（可配置）
- [x] P1-5 静态文件路径遍历：新增 `isSafeStaticPath()` 拼接前校验 `target_path`，拒绝 `..`/空字节/编码遍历 → ✅ 已实现（`HttpServer_Request.cpp` L139-157）
- [ ] P1-6 发送队列上限：`ws_send_queue_`/代理队列设最大值，超限断开慢消费者
- [ ] P2-1 CORS 白名单：origin 白名单校验，替代反射任意 Origin

### 中期
- [ ] P2-3 WS 握手 Origin 校验（防 CSWSH）
- [ ] P2-4 `ws_recv` 实现超时（用 `io_context` + timer 或 `async_read` + 私有超时）
- [ ] P2-5 代理后端读写超时 + 空闲保活
- [ ] P2-6 最大连接数限制
- [ ] P2-2 TLS 校验可配置（至少提供 `verify_peer` 选项）
- [x] P3-1 删除或维护遗留 WebSocketServer/WebSocketClient → ✅ 已删除
- [x] P3-2 同步 CMakeLists.txt 与 Makefile → ✅ 已重写对齐

---

## 代码位置索引

| 功能 | 文件 | 行号 |
|------|------|------|
| 请求读入（body_limit 可配置） | `src/HttpServer_Request.cpp` | L167-186 |
| 请求体超限返回 413 | `src/HttpServer_Request.cpp` | L256-265 |
| setMaxBodySize/getMaxBodySize | `inc/us/HttpServer.hpp` | L118-120 |
| 请求处理/静态文件 | `src/HttpServer_Request.cpp` | L7-137, L94-119 |
| CORS 反射 Origin | `src/HttpServer_Request.cpp` | L73-74 |
| WS 读消息（无大小限制） | `src/HttpServer_WsSession.cpp` | L88-111 |
| WS 发送队列/代理队列 | `src/HttpServer_WsSession.cpp` | L8-16, L362-367, L438-443 |
| 代理后端超时 | `src/HttpServer_WsSession.cpp` | L225, L253, L262, L287 |
| 代理 TLS verify_none | `src/HttpServer_WsSession.cpp` | L243 |
| 连接接受（无限制） | `src/HttpServer_Listener.cpp` | L23-48 |
| TLS 服务端配置 | `src/HttpServer.cpp` | L30-62 |
| HttpClient 响应无上限 | `src/HttpClient.cpp` | L63-81, L329-334 |
| HttpClient TLS verify_none | `src/HttpClient.cpp` | L131 |
| HttpClient ws_recv 忽略超时 | `src/HttpClient.cpp` | L220-221 |
| 遗留 WS 实现（未构建） | `src/WebSocketServer.cpp`、`src/WebSocketClient.cpp` | 全篇 |