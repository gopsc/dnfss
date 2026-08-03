# C++ 共享库安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `libth/`（线程库）、`libhttp/`（HTTP/WebSocket）、`libcrypt/`（加密）、`libsqlite3/`（SQLite封装）  
**代码版本**: dev_qing 分支

---

## 修复进度

| 优先级 | 总数 | 已修复 | 待修复 |
|--------|------|--------|--------|
| P0 | 1 | 0 | 1 |
| P1 | 2 | 0 | 2 |
| P2 | 2 | 0 | 2 |

---

## 🔴 P0 — 严重问题

### P0-1: libhttp HTTP 解析器需 fuzz 测试，潜在缓冲区溢出
- **位置**: `libhttp/src/HttpServer.cpp`
- **问题**: 自研 C++ HTTP 解析器未经过大规模 fuzz 测试：
  - 异常 HTTP 方法、超长 header、畸形 chunked 编码可能触发缓冲区溢出
  - 边界条件处理不当可导致拒绝服务或远程代码执行
- **修复建议**:
  1. 使用 AFL++ 或 libFuzzer 对 HTTP 解析器进行 fuzz 测试
  2. 重点测试：header 解析、URL 解析、chunked 编码、Content-Length 处理
  3. 考虑替换为成熟库（如 llhttp、picohttpparser）

---

## 🟠 P1 — 高危问题

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| P1-1 | libcrypt RSA 实现需审计 | `libcrypt/src/Crypto_Basic.cpp`、`inc/us/MyRSA.hpp` | 自研 RSA 实现需验证侧信道攻击防护、填充方案（PKCS#1 v1.5/OAEP）正确性 |
| P1-2 | WebSocket 帧解析需 fuzz | `libhttp/src/WebSocketServer.cpp` | 畸形帧、超大帧、分片帧处理需验证 |

---

## 🟡 P2 — 中危问题

| # | 问题 |
|---|------|
| P2-1 | libsqlite3 封装需验证 SQL 注入防护（确认使用参数化查询） |
| P2-2 | libth 线程库同步原语需验证死锁/竞态条件 |

---

## ✅ 已有安全机制

1. ✅ **RAII 智能指针**: 减少内存泄漏和 use-after-free
2. ✅ **C++ 类型安全**: 相比纯 C 减少类型混淆漏洞
3. ✅ **项目内统一库**: 代码可控，可快速修复
4. ✅ **libcrypt 已实现**: PBKDF2、SHA256、AES-CBC、RSA、Base64

---

## 修复路线图

### 短期（72小时）
- [ ] 对 libhttp HTTP/WebSocket 解析器进行 fuzz 测试
- [ ] 验证 libsqlite3 封装的参数化查询正确使用

### 中期
- [ ] libcrypt RSA 实现安全审计
- [ ] 考虑替换 HTTP 解析器为成熟第三方库
