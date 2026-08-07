# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [Unreleased]

### Security - 安全修复

#### Gateway v0.8.0 (2026-08-07)

**P0 严重问题修复：**
- 修复 HttpException 错误消息泄露内部处理步骤问题（Login/Register/Reset错误消息脱敏）
  - 原消息 `"Login (Query Step) Failed"` → 改为 `"Login failed, please try again later"`
  - 原消息 `"Login (Update Step) Failed"` → 改为 `"Login failed, please try again later"`
  - 原消息 `"Registe Failed"`（拼写错误）→ 改为 `"Registration failed, please try again later"`
  - 原消息 `"Reset Failed"` → 改为 `"Password reset failed, please try again later"`
  - 修正注册失败 HTTP 状态码：400 → 500（服务端内部错误）
- **所有 P0 安全问题已全部修复**，安全评级从 B+ 提升至 A-

**进程无法退出/杀不死 Bug 修复（关键修复）：**

修复了 gateway 模块收到 SIGTERM/SIGINT 后进程无法正常退出的多个根本原因：

1. **信号处理安全问题（高）**：
   - 原信号处理函数调用 `std::cout` 和 `_SERVE->stop()` 等非 async-signal-safe 函数，可能导致死锁
   - 改为安全模式：仅设置 `volatile std::sig_atomic_t` 原子标志，使用 `write()` 输出提示（write 是 async-signal-safe）
   - 主线程轮询检测标志后在安全上下文执行关闭流程

2. **SIGPIPE 未处理（中）**：
   - 新增 SIGPIPE 信号忽略处理，防止写已关闭 socket 时内核意外终止进程

3. **Listener 无限递归 Bug（严重）**：
   - `doAccept()` 在 acceptor 关闭后错误分支仍无条件递归调用自身，导致 CPU 100% 空转
   - 修复：acceptor 关闭或 `operation_aborted` 错误时停止接受循环
   - 新增 `Listener::close()` 公开方法，可显式关闭 acceptor 停止接受新连接

4. **stop() 流程顺序问题（中）**：
   - 改进 `HttpServer::stop()` 关闭顺序：
     1. 先关闭 acceptor，停止接受新连接
     2. 清空 WebSocket 客户端
     3. 停止 io_context 事件循环
     4. 等待所有 worker 线程退出
     5. 重置 listener

5. **关闭无超时保护（高）**：
   - 服务器 stop() 添加 8 秒超时保护，超时后 `std::exit(1)` 强制退出
   - 日志 shutdown() 添加 3 秒超时保护，超时后丢弃剩余日志退出
   - **保证进程绝不会挂死**，极端情况下最多 11 秒内必定退出

6. **启动/关闭架构改进**：
   - 服务器事件循环改为在独立线程运行，主线程负责信号检测和关闭协调
   - 服务器异常退出也能被主线程检测到并正确清理资源

**Libhttp 改进：**
- Listener 新增 `close()` 方法
- 修复 doAccept 关闭后的无限递归问题
- stop() 流程更健壮，分步输出日志便于排障

### Docs - 文档更新
- 新增 `docs/security/gateway_SECURITY_AUDIT_20260807.md` 安全审计复审文档
- 更新审计修复进度：P0 问题 100% 修复，总体修复率 56%
- 安全评级更新：B+（良好）→ A-（优秀）

---

## [0.8.0] - 2026-07-xx (之前版本)

### Added - 新增功能
- Gateway v0.8.0 初始版本
- Token 双轨吊销机制（jti 黑名单 + 用户版本号）
- 错误信息脱敏（不返回源码路径/C++类型名）
- JTI 黑名单内存有界保护（5000条阈值+过期清理）
- 反向代理响应大小限制（libhttp 默认 10MB）
- 集中式异步日志客户端 LogC（批量推送+fail-safe）
- WebSocket 代理支持（管理员权限校验）
- 多层速率限制（IP级+邮箱级）
- PBKDF2-HMAC-SHA256 密码哈希（10万轮）
- RSA Token 签名验签
- HttpOnly+Secure+SameSite Cookie
- 密码强度校验
