# pymsg 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `pymsg/__init__.py`、`pymsg/rpc.py`  
**代码版本**: dev_qing 分支

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

### P0-2: UnicodeDecodeError 可导致同步模式服务崩溃
- **位置**: `__init__.py:111`、`__init__.py:434-448`
- **问题**: `payload.decode("utf-8")` 抛出的 `UnicodeDecodeError`（`ValueError` 子类）不在 `_handle_client` 的异常捕获列表 `(ConnectionError, ConnectionResetError, OSError, TimeoutError)` 中。**同步模式（workers=0，默认配置）下收到无效 UTF-8 字节会直接导致整个服务进程崩溃！**
- **影响范围**: db/confs/smtp/captcha 等所有默认配置的 pymsg 服务
- **修复建议**: 在异常捕获中添加 `UnicodeDecodeError` 和 `json.JSONDecodeError`：
  ```python
  except (ConnectionError, ConnectionResetError, OSError, TimeoutError,
          UnicodeDecodeError, json.JSONDecodeError):
      ...
  ```

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

---

## 修复路线图

### 立即（24小时）
- [ ] 修复 P0-1: recv 总超时（deadline 模式）
- [ ] 修复 P0-2: 捕获 UnicodeDecodeError/JSONDecodeError

### 短期（72小时）
- [ ] 添加最大连接数限制
- [ ] 线程池改用有界队列
- [ ] 异常信息脱敏
- [ ] JSON 解析深度限制

### 中期
- [ ] 统一认证机制
- [ ] TLS 支持选项
- [ ] 安全事件日志
