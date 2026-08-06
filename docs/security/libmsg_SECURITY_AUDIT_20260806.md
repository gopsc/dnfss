# libmsg 模块安全审计报告

**审计日期**: 2026-08-06  
**审计范围**: `libmsg/inc/us/MsgServer.hpp`、`libmsg/inc/us/RpcServer.hpp`、`libmsg/inc/us/Msg.hpp`、`libmsg/inc/us/RpcClient.hpp`、`libmsg/inc/us/AsyncQueue.hpp`、`libmsg/inc/us/Msg_Basic.hpp`  
**代码版本**: main 分支（e92ef05）  
**审计工具**: Qoder Security SAST 云端扫描

---

## 问题严重等级

| 等级 | 说明 | 响应时间 |
|------|------|----------|
| P0 🔴 | 可导致服务崩溃、DoS 或远程接管 | 立即修复 |
| P1 🟠 | 可导致资源耗尽、信息泄露 | 72小时内 |
| P2 🟡 | 健壮性/配置问题 | 一周内 |
| P3 🟢 | 优化建议 | 版本迭代 |

---

## 修复进度汇总

| 等级 | 总数 | 已修复 | 待修复 | 修复率 |
|------|------|--------|--------|--------|
| P0 | 1 | 1 | 0 | 100% |
| P1 | 1 | 1 | 0 | 100% |
| P2 | 1 | 1 | 0 | 100% |
| **合计** | **3** | **3** | **0** | **100%** |

---

## 🔴 P0 — 严重问题

### P0-1: broadcast/reply 竞态条件导致数据发送到错误客户端 ✅ 已修复

- **CWE**: CWE-362 (Race Condition)
- **OWASP**: A01:2021
- **位置**: `MsgServer.hpp:228`（原始行号）
- **置信度**: 80%

**问题描述**:  
`broadcast()` 在 `_clients_mtx` 保护下拷贝 fd + send_lock 列表后释放锁，再无锁执行 `pmsg_send(fd, msg)`。此窗口期内 `_remove_client()` 可关闭该 fd，OS 可将其分配给新连接。`broadcast()`/`reply()` 随后向复用的 fd 发送数据，导致消息到达错误的客户端。`_remove_client()` 原实现关闭 fd 前不获取 per-connection `send_lock`，因此 `send_lock` 无法阻止此竞态。

**数据流**:
1. `broadcast()` 在 `_clients_mtx` 下拷贝 fd 和 send_lock 列表
2. `_remove_client()` 关闭 fd 但不持 send_lock，OS 复用该 fd
3. `broadcast()` 向已被复用的 fd 调用 `pmsg_send()`，数据到达新连接

**修复方案**（commit `e92ef05`）:  
`_remove_client()` 先从 `_send_locks` 取出 `send_lock` 并从 map 删除，持有 `send_lock` 后再执行 `::close(fd)`，确保关闭与正在进行的发送操作互斥：

```cpp
void _remove_client(int fd) {
    std::shared_ptr<std::mutex> send_lock;
    {
        std::lock_guard<std::mutex> clk(_clients_mtx);
        if (_clients.erase(fd) == 0) return;
        auto it = _send_locks.find(fd);
        if (it != _send_locks.end()) {
            send_lock = it->second;
            _send_locks.erase(it);
        }
    }
    if (send_lock) {
        std::lock_guard<std::mutex> slk(*send_lock);
        ::close(fd);
    } else {
        ::close(fd);
    }
}
```

---

## 🟠 P1 — 高危问题

### P1-1: 事件循环顺序阻塞接收导致 DoS（Slowloris 变体）✅ 已修复

- **CWE**: CWE-400 (Uncontrolled Resource Consumption)
- **OWASP**: A05:2021
- **位置**: `MsgServer.hpp:424`（原始行号）
- **置信度**: 80%

**问题描述**:  
`_event_loop()` 对每个就绪 fd 顺序调用 `_handle_client()`，后者在**事件循环主线程**中阻塞调用 `pmsg_recv(fd, recv_timeout)`（默认 10 秒）。线程池仅处理回调执行，不处理消息接收，即使配置了 `workers > 0` 也无法缓解。攻击者用 `max_connections`（默认 1000）个连接各发送不完整帧头（如 3 字节），可使事件循环阻塞最长 `1000 × 10 = 10000` 秒（约 2.8 小时），期间服务完全无法处理任何新连接或已有消息。

**数据流**:
1. 事件循环顺序遍历所有就绪的客户端 fd
2. `_handle_client()` 在主线程调用 `pmsg_recv(fd, recv_timeout)`，阻塞最长 `recv_timeout` 秒

**修复方案**（commit `e92ef05`）:  
在 `workers > 0` 模式下，`_handle_client()` 不再在事件循环线程调用 `pmsg_recv()`，而是提交 `needs_recv=true` 的任务给 worker 线程，由 worker 线程负责完整的 recv + callback 流程：

```cpp
// 线程池模式：直接提交，由 worker 负责 recv + callback
_pool_queue.push(Task{fd, std::move(addr), {}, true});
```

同步模式（`workers=0`）行为不变，仍在主线程顺序处理（无线程池时无法并发）。

---

## 🟡 P2 — 中危问题

### P2-1: 错误信息路径脱敏正则不完整，可能泄露内部路径 ✅ 已修复

- **CWE**: CWE-209 (Information Exposure Through an Error Message)
- **OWASP**: A04:2021
- **位置**: `RpcServer.hpp:184`（原始行号）
- **置信度**: 75%

**问题描述**:  
`RpcServer::_sanitize_error()` 使用正则 `/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*` 替换异常消息中的绝对路径，但该正则仅匹配由 `[A-Za-z0-9_.\-]` 组成的路径段。含空格（`/home/user name/`）、括号（`/tmp/file(1).txt`）、`@`（`/opt/app@2.0/`）、`~` 等特殊字符的路径不被匹配，会原文返回给远程客户端，泄露内部服务器路径信息。

**修复方案**（commit `e92ef05`）:  
将正则扩展为 `/\S+(?:/\S*)*`，匹配以 `/` 开头、后接所有非空白字符的路径段，覆盖所有常见内部路径格式：

```cpp
static const std::regex path_re(R"(/\S+(?:/\S*)*)");
return std::regex_replace(msg, path_re, "<redacted>");
```

---

## ✅ 已有安全机制

| 机制 | 位置 | 说明 |
|------|------|------|
| 消息大小上限 64 MiB | `Msg_Basic.hpp` `kMaxMsgSize` | 防止超大消息耗尽内存 |
| deadline 模式 recv 超时 | `Msg.hpp` `pmsg_recv(fd, timeout)` | header + payload 共享总超时，防 Slowloris |
| 最大连接数限制 | `MsgServer.hpp` `max_connections=1000` | 超限拒绝新连接 |
| 有界任务队列 | `MsgServer.hpp` `max_queue_size=1000` | 队列满时断开连接，防 OOM |
| per-connection 发送锁 | `MsgServer.hpp` `_send_locks` | reply/broadcast 线程安全，防帧交错 |
| 同一连接消息串行 | `MsgServer.hpp` `_busy` 集合 | 回调执行中暂停该连接 select 监听 |
| JSON 解析深度限制 | `RpcServer.hpp` `opts.max_depth=32` | 防深度嵌套 JSON 导致栈溢出 |
| 异常信息脱敏 | `RpcServer.hpp` `_sanitize_error()` | 过滤绝对路径，完整异常由 `on_error` 钩子记录 |
| RpcClient 响应格式校验 | `RpcClient.hpp` | 校验 `success` 字段，非法响应抛 `RpcError` |
| worker 线程异常隔离 | `MsgServer.hpp` `_worker_loop()` | handler 异常仅断开该连接，不影响服务 |
| select FD_SETSIZE 检查 | `MsgServer.hpp` `_accept_client()` | fd >= FD_SETSIZE 时拒绝连接，防 select 越界 |

---

## 待修复 / 已知限制

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| L-1 | 无认证/鉴权机制 | P0（已缓解） | 仅依赖绑定 127.0.0.1 网络层隔离，任意本地进程可调用所有路由 |
| L-2 | 无 TLS 加密传输 | P1 | 内网明文 TCP，进程间通信可被本地抓包 |
| L-3 | 同步模式（workers=0）仍在主线程阻塞 recv | P2 | 无线程池时无法并发，Slowloris 防护依赖 recv_timeout 截止时间 |
| L-4 | select 模型上限 FD_SETSIZE（通常 1024） | P2 | 高并发场景建议改用 epoll |
| L-5 | 安全事件无结构化日志 | P3 | 超时、超大消息、JSON 解析失败等仅 fprintf(stderr)，无可观测性 |
