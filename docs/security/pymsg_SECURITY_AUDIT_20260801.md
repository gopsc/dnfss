# pymsg 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `pymsg/__init__.py`、`pymsg/rpc.py`、`pymsg/async_queue.py`  
**代码版本**: dev_qing 分支

---

## 代码验证记录

**验证日期**: 2026-08-03  
**验证方式**: 逐条对照审计报告与当前代码（`__init__.py`、`rpc.py`、`async_queue.py`）

### 验证结果汇总

| 等级 | 总数 | 已修复 | 部分修复 | 未修复 | 修复率 |
|------|------|--------|----------|--------|--------|
| P0 | 3 | 1 | 0 | 2 | 33% |
| P1 | 5 | 0 | 0 | 5 | 0% |
| P2 | 4 | 0 | 0 | 4 | 0% |
| P3 | 4 | 0 | 0 | 4 | 0% |
| **合计** | **16** | **1** | **0** | **15** | **6.25%** |

### 逐条验证

| 编号 | 状态 | 代码位置 | 验证说明 |
|------|------|----------|----------|
| P0-1 | ❌ 未修复 | `__init__.py` L103, L140-156 | `_recv_exact` 仍使用 `sock.settimeout()` 单次系统调用超时，非 deadline 模式；攻击者可逐字节发送维持连接 |
| P0-2 | ✅ 已修复 | `__init__.py` L113-114, L462-463 | 双重防护：`recv()` 将 `UnicodeDecodeError` 包装为 `ConnectionError`；`_handle_client` 防御性捕获 `UnicodeDecodeError` |
| P0-3 | ❌ 未修复 | `rpc.py` L80-171 | 无认证机制，仅依赖 127.0.0.1 网络层隔离 |
| P1-1 | ❌ 未修复 | `__init__.py` L359 | `listen(10)` 仅限制 SYN 队列，不限制已建立连接数 |
| P1-2 | ❌ 未修复 | `__init__.py` L362-365 | `ThreadPoolExecutor` 使用默认无界队列 |
| P1-3 | ❌ 未修复 | `rpc.py` L160, L178 | `_dispatch` 将 `str(e)` 和 `f"JSON 解析失败: {e}"` 直接返回客户端 |
| P1-4 | ❌ 未修复 | 全模块 | 明文 TCP，无 TLS 支持 |
| P1-5 | ❌ 未修复 | `rpc.py` L158 | `json.loads(text)` 无深度限制 |
| P2-1 | ❌ 未修复 | `__init__.py` L106-111 | length=0 空消息无特殊处理 |
| P2-2 | ❌ 未修复 | `__init__.py` L395-418 | `broadcast()` 无频率/权限控制 |
| P2-3 | ❌ 未修复 | `__init__.py` L377-378 | `except (ValueError, OSError): break` 仍捕获所有 OSError（含 EINTR） |
| P2-4 | ❌ 未修复 | `__init__.py` L196-203 | `MsgClient.send()` 短连接模式，高频调用导致 TIME_WAIT 堆积 |
| P3-1 | ❌ 未修复 | - | 安全事件（超大消息、超时、JSON 解析失败）无日志 |
| P3-2 | ❌ 未修复 | `__init__.py` L182 | `name` 参数未过滤控制字符 |
| P3-3 | ❌ 未修复 | `__init__.py` L359 | `listen(10)` backlog 在高并发下偏小 |
| P3-4 | ❌ 未修复 | `__init__.py` L520-527 | `_shutdown` 直接 `close()` 所有 socket，可能发送 RST |

---

## 问题严重等级

| 等级 | 说明 | 响应时间 |
|------|------|----------|
| P0 🔴 | 可导致服务崩溃、DoS 或远程接管 | 立即修复 |
| P1 🟠 | 可导致资源耗尽、信息泄露 | 72小时内 |
| P2 🟡 | 健壮性/配置问题 | 一周内 |
| P3 🟢 | 优化建议 | 版本迭代 |

---

## 修复进度

| 优先级 | 总数 | 已修复 | 待修复 |
|--------|------|--------|--------|
| P0 | 3 | 1 | 2 |
| P1 | 5 | 0 | 5 |
| P2 | 4 | 0 | 4 |
| P3 | 4 | 0 | 4 |

---

## 🔴 P0 — 严重问题

### P0-1: recv 超时可被绕过（Slowloris 变种 DoS）
- **位置**: `__init__.py:102-153` (`recv` / `_recv_exact`)
- **问题**: `sock.settimeout()` 设置的是**单次系统调用**超时，不是整个消息接收总超时。攻击者可每次在快超时时发送 1 字节：
  - 4 字节长度头最长可占用 `4 * recv_timeout = 40秒`
  - 载荷部分按字节发送可使连接存活任意长时间
- **影响**: 少量攻击者即可耗尽连接/线程资源，造成所有依赖 pymsg 的服务拒绝服务
- **修复建议**: 使用 deadline 模式：
  ```python
  deadline = time.monotonic() + self._recv_timeout
  while got < n:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
          raise TimeoutError("Message receive timeout")
      sock.settimeout(remaining)
      chunk = sock.recv_into(view[got:], n - got)
  ```

---

### P0-2: UnicodeDecodeError 可导致同步模式服务崩溃 ✅ 已修复
- **位置**: `__init__.py:113-114`、`__init__.py:462-463`
- **原问题**: `payload.decode("utf-8")` 抛出的 `UnicodeDecodeError`（`ValueError` 子类）不在 `_handle_client` 的异常捕获列表中，同步模式下收到无效 UTF-8 字节会直接导致整个服务进程崩溃
- **修复方案（双重防护）**:
  1. `recv()` 函数将 `UnicodeDecodeError` 包装为 `ConnectionError`（第一层）
  2. `_handle_client` 异常捕获列表添加 `UnicodeDecodeError`（第二层，防御性兜底）
- **影响范围**: 已消除，db/confs/smtp/captcha 等所有 pymsg 服务均受保护

---

### P0-3: 无认证/鉴权机制（已部分缓解：绑定 127.0.0.1）
- **位置**: `rpc.py:80-171` 整个 RpcServer
- **问题**: RPC 服务完全没有身份验证，任何能连接到端口的客户端都能调用所有路由
- **当前缓解**: 所有服务均绑定 127.0.0.1，依赖网络层隔离
- **修复建议**:
  1. 添加基于预共享 token 的认证（如 `_auth` 字段）
  2. 支持路由级权限控制
  3. 文档明确警告不要暴露到公网

---

## 🟠 P1 — 高危问题

| # | 问题 | 位置 |
|---|------|------|
| P1-1 | 无连接数限制，连接耗尽攻击 | `__init__.py:334` `listen(10)` 不限制已建立连接数 |
| P1-2 | 线程池无队列限制，慢 handler 耗尽内存 | `__init__.py:337-340` ThreadPoolExecutor 默认无界队列 |
| P1-3 | 异常信息直接返回客户端，泄露内部细节 | `rpc.py:144,162` 直接将 `str(e)` 返回调用方 |
| P1-4 | 无 TLS/加密传输，内网流量可被窃听 | 全模块明文 TCP |
| P1-5 | JSON 解析无深度限制，深度嵌套导致栈溢出/CPU 100% | `recv()` 后 `json.loads()` 无限制 |

---

## 🟡 P2 — 中危问题

| # | 问题 |
|---|------|
| P2-1 | length=0 空消息未特殊处理，可用于心跳攻击 |
| P2-2 | broadcast() 无频率/权限控制，可被用于放大攻击 |
| P2-3 | select 异常处理过于宽泛，EINTR 临时错误直接退出循环 |
| P2-4 | MsgClient 短连接模式导致 TIME_WAIT 堆积 |

---

## 🟢 P3 — 低危/建议

| # | 建议 |
|---|------|
| P3-1 | 安全事件（超大消息、超时、JSON解析失败）无日志 |
| P3-2 | `name` 参数未过滤控制字符，可导致日志注入 |
| P3-3 | listen backlog=10 在高并发下偏小 |
| P3-4 | 无优雅关闭（直接 close() 可能发送 RST） |

---

## ✅ 已有安全机制

1. ✅ 64 MiB 消息大小上限（`_MAX_MSG_SIZE`），防止 OOM
2. ✅ send/recv 双端都检查消息大小
3. ✅ recv_timeout 防 Slowloris（基础实现，需修复 P0-1）
4. ✅ 线程池模式下同一连接串行处理，避免帧交错
5. ✅ 使用 `sendmsg` 散射-聚集 I/O 减少内存拷贝
6. ✅ 使用 `recv_into` 预分配 buffer
7. ✅ 线程安全的发送锁
8. ✅ 回调异常隔离（线程池模式）
9. ✅ 单元测试覆盖消息大小边界

## 新增安全机制（审计后添加，审计报告未覆盖）

| 机制 | 代码位置 | 说明 |
|------|----------|------|
| UnicodeDecodeError 双重防护 | `__init__.py` L113-114, L462-463 | `recv()` 包装为 `ConnectionError` + `_handle_client` 防御性捕获，防止无效 UTF-8 导致服务崩溃 |
| AsyncRpcQueue 有界队列 | `async_queue.py` L47, L130-132 | `max_queue_size` 默认 10000，超限丢弃最旧数据，防止内存无限增长 |
| AsyncRpcQueue 错误隔离 | `async_queue.py` L49, L189-194 | `on_error` 回调 + 异常捕获，后台线程失败不影响主业务 |
| RpcClient 响应格式校验 | `rpc.py` L89-90 | 校验响应必须是含 `success` 字段的 dict，非法响应抛出 `RpcError` |
| MsgClient.call 节制日志 | `__init__.py` L209, L248-254 | 重试日志首次立即打印，后续每 5 秒一次，避免刷屏 |

---

## 修复路线图

### 立即（24小时）
- [ ] 修复 P0-1: recv 总超时（deadline 模式）
- [x] 修复 P0-2: 捕获 UnicodeDecodeError/JSONDecodeError ✅ 已完成

### 短期（72小时）
- [ ] 添加最大连接数限制
- [ ] 线程池改用有界队列
- [ ] 异常信息脱敏
- [ ] JSON 解析深度限制

### 中期
- [ ] 统一认证机制
- [ ] TLS 支持选项
- [ ] 安全事件日志
