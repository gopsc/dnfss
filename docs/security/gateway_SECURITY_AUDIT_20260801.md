# gateway 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `src/main.cpp`、`src/shared/`（libth、libhttp、libcrypt、libsqlite3）  
**代码版本**: dev_qing 分支

---

## 修复进度

| 优先级 | 总数 | 已修复 | 部分修复 | 未修复 |
|--------|------|--------|----------|--------|
| P0 | 1 | 0 | 1 | 0 |
| P1 | 6 | 0 | 1 | 5 |
| P2 | 4 | 1 | 0 | 3 |
| **合计** | **11** | **1** | **2** | **8** |

---

## 🔴 P0 — 严重问题

### P0-1: RSA 私钥存储在仓库中/硬编码
- **位置**: `main.cpp` L681 `_KEYGEN = std::make_unique<MyRSA::Generator>();`
- **当前状态**: ⚠️ 部分修复
  - 密钥由 `MyRSA::Generator` 在内存中运行时生成，**未硬编码在源码中**，**未随仓库分发**
  - 每个进程实例独立生成密钥对，满足"每个部署独立密钥"要求
  - **遗留问题**: 密钥为内存生成，进程重启后丢失，所有已签发 Token 立即失效
- **修复建议**:
  1. ~~私钥从文件路径加载（启动参数或环境变量），不硬编码~~ ✅ 已不在源码中硬编码
  2. ~~每个部署实例生成独立密钥对~~ ✅ 已满足
  3. 私钥文件权限 0600，仅服务运行用户可读 — 需增加持久化机制
  4. 公钥分发机制：dnfs、aibox 等需要验签的服务可访问公钥 — 待实现

---

## 🟠 P1 — 高危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P1-1 | Token 吊销机制缺失 | ❌ 未修复 | `handleLogout` (L1184) 仅清除 Cookie，无服务端黑名单/吊销列表；修改密码/注销后旧 Token 仍有效直到过期 |
| P1-2 | 登录失败次数限制 | ⚠️ 部分修复 | IP 级限流已实现 (`_LOGIN_RATE` L371，`check_and_record(ip,5,300)` L1022)，但**无账户级锁定**（同一邮箱多次失败不触发账户锁定） |
| P1-3 | 上游代理超时配置 | ❌ 未修复 | `handleProxy` (L1947) 中 `HttpClient` 未显式设置超时，依赖 libhttp 默认值 |
| P1-4 | C++ HTTP 解析器缓冲区溢出审计 | ❌ 未修复 | libhttp 库（`src/shared/`）需全面 fuzz 测试 |
| P1-5 | 请求体大小限制 | ❌ 未修复 | 未配置显式请求体大小上限（如 10MB），依赖 Boost.Beast 默认行为 |
| P1-6 | 反向代理响应无大小限制 | ❌ 未修复 | `handleProxy` 回调 L2069-2074 拼接下游响应 body 无上限（`res.body() += ret`，带 `FIXME: 增加长度限制`），下游（static/dnfs）返回超大响应可导致 OOM（全局审计 P1-1） |

---

## 🟡 P2 — 中危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P2-1 | 错误页面泄露版本号/堆栈信息 | ✅ 已修复 | `wrapperHttp` (L909-940) 异常捕获返回统一 `"Internal Server Error"`；`ExceptionBase::build_message()` 中文件/行号/函数名已注释（`ExpBase.hpp` L173-197），`what()` 仅含业务消息，不向客户端暴露源码位置 |
| P2-2 | HTTPS TLS 版本和密码套件安全 | ❌ 未修复 | `main.cpp` L677 TLS 模式下仅指定证书文件，未显式限定 TLS 版本和 CipherSuites |
| P2-3 | CORS 通配（反射任意 Origin） | ❌ 未修复 | **根因**：`_CORS_ORIGINS` 白名单 (L593) 已读取但**从未用于响应头**——OPTIONS 预检由 libhttp 库处理（`HttpServer_Request.cpp` L73-74），直接反射请求 Origin，libhttp 不感知 gateway 的 `_CORS_ORIGINS` 状态，等于允许任意来源（全局审计 P2）。修复需在 libhttp 层接入白名单或由 gateway 注册显式 OPTIONS 路由 |
| P2-4 | HTTP 服务器无超时配置 | ❌ 未修复 | 未设置 Read/Write/Idle 超时，`HttpServer(addr,port,threads)` 构造函数无超时参数，依赖 libhttp 默认行为，存在 Slowloris/连接耗尽风险（全局审计 P2） |

---

## ✅ 已有安全机制

1. ✅ **PBKDF2 密码哈希**: 慢哈希算法防彩虹表，使用唯一盐值 (`main.cpp` L58-143)
2. ✅ **RSA Token 签名**: Token 使用 RSA 私钥签名，防伪造 (`main.cpp` L1112-1130)
3. ✅ **反向代理架构**: 统一入口，隐藏后端服务拓扑
4. ✅ **速率限制**: 多层防护（IP 级 + 邮箱级双窗口限流、60秒冷却、失败锁定）
5. ✅ **防用户名枚举**: 用户不存在时执行 dummy PBKDF2 校验拉平响应时序 (`main.cpp` L1102-1108)
6. ✅ **WAF 前置防护**: Go WAF 处理 SQLi/XSS/CC/协议解析
7. ✅ **C++ 高性能实现**: 避免 Python GIL 瓶颈，抗并发能力强
8. ✅ **依赖审计**: 使用项目自带的 libcrypt 加密库（PBKDF2、RSA）
9. ✅ **路径遍历防护**: `is_safe_target()` 校验请求路径，拒绝 `..`、空字节、URL 编码遍历 (`main.cpp` L2161-2186)
10. ✅ **X-Forwarded-For 可信注入**: 剥离客户端伪造的 IP 头后由 gateway 主动设置 (`main.cpp` L2088-2098, `is_special_header` L2126-2138)
11. ✅ **优雅关闭**: SIGINT/SIGTERM 信号处理 (`main.cpp` L484-490)
12. ✅ **Token 刷新机制**: 支持宽限期刷新 + 次数上限 + 限流 (`main.cpp` L1339-1450)
13. ✅ **CAPTCHA 强制校验**: 登录/注册/发送验证码均需 CAPTCHA，对接 captcha 服务 (`main.cpp` L438, 调用点 L1044/1671/1784/1905)
14. ✅ **异步日志**: 批量推送 + fail-safe，db 不可用时静默丢弃 (`LogC.hpp`)

---

## 修复路线图

### 立即（24小时）
- [ ] 增加请求体大小限制（参考 waf 10MB 配置）
- [ ] 反向代理响应体大小限制（P1-6，防 OOM）
- [ ] 代理上游 HttpClient 增加显式超时配置

### 短期（72小时）
- [x] 确认 RSA 私钥未硬编码 → ✅ 已确认：运行时内存生成，非硬编码
- [ ] 实现 Token 吊销列表（密码修改时注销旧 Token）
- [ ] 增加账户级登录失败锁定
- [ ] libhttp 解析器 fuzz 测试

### 中期
- [x] 错误页面脱敏 → ✅ 已完成
- [ ] TLS 安全配置（限定 TLS 版本和 CipherSuites）
- [ ] CORS 白名单生效（`_CORS_ORIGINS` 接入响应头，替代反射任意 Origin）
- [ ] HTTP 服务器超时配置（Read/Write/Idle）
- [ ] 管理操作审计日志
- [ ] RSA 密钥持久化（避免重启后 Token 全部失效）

---

## 代码验证记录

**验证日期**: 2026-08-03  
**验证方式**: 逐条对照审计报告与当前代码（`main.cpp`、`Conf.hpp`、`DbC.hpp`、`LogC.hpp`、`Msg.hpp`）

### 验证结果汇总

| 等级 | 总数 | 已修复 | 部分修复 | 未修复 | 修复率 |
|------|------|--------|----------|--------|--------|
| P0 | 1 | 0 | 1 | 0 | 50% (部分) |
| P1 | 6 | 0 | 1 | 5 | 8% (部分) |
| P2 | 4 | 1 | 0 | 3 | 25% |
| **合计** | **11** | **1** | **2** | **8** | **18%** |

### 逐条验证

| 编号 | 状态 | 代码位置 | 验证说明 |
|------|------|----------|----------|
| P0-1 | ⚠️ 部分 | `main.cpp` L681 | `MyRSA::Generator()` 运行时生成，非硬编码；但无持久化，重启后 Token 全部失效 |
| P1-1 | ❌ 未修复 | `main.cpp` L1184-1198 | `handleLogout` 仅清除 Cookie，无 Token 黑名单/吊销机制 |
| P1-2 | ⚠️ 部分 | `main.cpp` L371, L1022 | IP 限流已实现 (5次/300s)，无账户级锁定 |
| P1-3 | ❌ 未修复 | `main.cpp` L1947-2124 | `handleProxy` 中 `HttpClient` 未显式设置超时 |
| P1-4 | ❌ 未修复 | `src/shared/` | libhttp 库未进行 fuzz 测试 |
| P1-5 | ❌ 未修复 | `main.cpp` | 未设置请求体大小上限 |
| P1-6 | ❌ 未修复 | `main.cpp` L2069-2074 | `handleProxy` 回调 `res.body() += ret` 拼接下游响应 body 无上限，超大响应可 OOM |
| P2-1 | ✅ 已修复 | `main.cpp` L909-940, `ExpBase.hpp` L173-197 | 异常统一返回 `"Internal Server Error"`；`build_message()` 已注释文件/行号/函数名，不泄露源码位置 |
| P2-2 | ❌ 未修复 | `main.cpp` L677 | TLS 模式仅指定证书，未限定版本和密码套件 |
| P2-3 | ❌ 未修复 | `main.cpp` L593, libhttp `HttpServer_Request.cpp` L73-74 | `_CORS_ORIGINS` 白名单读取但未用于响应头（OPTIONS 由 libhttp 处理，不感知 gateway 状态）；OPTIONS 预检反射任意 Origin |
| P2-4 | ❌ 未修复 | `main.cpp` L677-679, libhttp `HttpServer` | `HttpServer(addr,port,threads)` 无超时参数，未设置 HTTP 服务器 Read/Write/Idle 超时 |

### 新增安全机制（审计报告未覆盖）

| 机制 | 代码位置 | 说明 |
|------|----------|------|
| 路径遍历防护 | `main.cpp` L2161-2186 | `is_safe_target()` 拒绝 `..`、空字节、URL 编码遍历 |
| XFF 可信注入 | `main.cpp` L2088-2098, L2126-2138 | `is_special_header` 剥离客户端伪造 IP 头后主动设置可信 XFF |
| 优雅关闭 | `main.cpp` L484-490 | SIGINT/SIGTERM 信号处理 |
| 防用户枚举 | `main.cpp` L1102-1108 | 用户不存在时执行 dummy PBKDF2 拉平响应时序 |
| 密码强度校验 | `main.cpp` L146-176 | 8-128字符，至少3种字符类别 |
| 邮箱双窗口限流 | `main.cpp` L1677-1681 | 3次/小时 + 10次/天/邮箱 |
| 异步日志 fail-safe | `LogC.hpp` L210-213 | db 不可用时静默丢弃，绝不影响业务 |
