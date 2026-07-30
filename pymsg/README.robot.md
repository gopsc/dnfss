# pymsg 模块技术手册

> 文档版本：2026-07-30
> 代码版本：0.2.0
> 语言：Python 3.9+（核心）、C++（gateway 集成）、Go（waf 集成）
> 定位：跨语言 TCP 消息帧协议库

---

## 一、模块概述

### 1.1 定位

**pymsg** 是一个极简跨语言 TCP 消息帧协议库，基于长度前缀（4 字节大端 uint32）+ UTF-8 载荷。为微服务体系提供轻量级 RPC 通信层。

### 1.2 设计哲学

| 原则 | 说明 |
|------|------|
| 零依赖 | 仅使用 Python stdlib，无需任何第三方包 |
| 极简协议 | 4 字节头 + 载荷，实现只需 20 行代码 |
| 跨语言 | Python/C++/Go 三端实现完全兼容 |
| 二进制安全 | 长度前缀帧，JSON 中的换行符无需转义 |
| fail-safe | 日志推送在 db 不可用时静默丢弃，不影响业务 |

### 1.3 与微服务体系的关系

pymsg 是微服务的**核心通信层**：

```
┌──────────────┐  pymsg  ┌──────────────┐
│   dnfs:8006  │ ───────→│   db:8005    │
│  (Python)    │  短连接 │  (Python)    │
└──────────────┘  RPC    └──────────────┘
┌──────────────┐  pymsg  ┌──────────────┐
│ gateway:8443 │ ───────→│   db:8005    │
│  (C++20)     │  短连接 │              │
└──────────────┘  RPC    └──────────────┘
┌──────────────┐  pymsg  ┌──────────────┐
│   waf:443    │ ───────→│   db:8005    │
│   (Go)       │  短连接 │              │
└──────────────┘  RPC    └──────────────┘
```

---

## 二、技术架构

### 2.1 协议格式

```
┌────────────────────────┬──────────────────────────┐
│  4 字节（大端序）        │  N 字节（UTF-8）          │
│  uint32 载荷字节长度     │  JSON / 文本载荷          │
└────────────────────────┴──────────────────────────┘
```

### 2.2 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        微服务调用方                         │
│  (dnfs / gateway / waf / static / smtp / captcha)           │
└─────────────────────────┬───────────────────────────────────┘
                          │ MsgClient.call() / send()
┌─────────────────────────▼───────────────────────────────────┐
│                   pymsg 协议层                              │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                    底层协议函数                         │ │
│  │  send(sock, data)  →  [4字节长度头][UTF-8载荷]           │ │
│  │  recv(sock)       →  读4字节→解析长度→读对应字节→返回    │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                              │                                │
│  ┌──────────────────────────▼──────────────────────────────┐ │
│  │                   MsgClient 客户端                       │ │
│  │  send(msg)     — 短连接，发送即忘（带 name 前缀）        │ │
│  │  call(msg)     — 短连接，RPC 请求-应答                  │ │
│  │  listen(cb)    — 长连接，回调模式                       │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                              │                                │
│  ┌──────────────────────────▼──────────────────────────────┐ │
│  │                   MsgServer 服务端                       │ │
│  │  start(on_message)  — 启动监听                         │ │
│  │  reply(sock, msg)   — 回复客户端                        │ │
│  │  broadcast(msg)     — 广播到所有客户端                  │ │
│  │  stop()             — 停止服务                         │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                          │ TCP
┌─────────────────────────▼───────────────────────────────────┐
│                     db:8005 服务端                          │
│                   MsgServer + 路由分发                      │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 消息流

#### RPC 请求-应答

```
调用方                              服务端 (db)
──────                              ──────────
MsgClient.call(json_str)
    │
    ├── socket.connect(127.0.0.1:8005)
    ├── send(sock, json_str)
    │   ├── payload = json_str.encode("utf-8")
    │   ├── header = struct.pack("!I", len(payload))
    │   └── sock.sendall(header + payload)
    │
    ├── recv(sock)                    ←── on_message(sock, addr, text)
    │   ├── sock.recv(4)                     ├── json.loads(text)
    │   ├── length = struct.unpack("!I", header)[0]
    │   ├── sock.recv(length)               ├── handler(req)
    │   └── payload.decode("utf-8")         └── server.reply(sock, response)
    │                                         ├── send(sock, response)
    ├── close(sock)                           │   └── sock.sendall(header + payload)
    └── return response                   ←── recv(sock) 返回 response
```

#### 日志推送

```
各服务 (gateway/static/smtp)          db:8005
─────────────────────────────         ──────────
log_client.push(entry)
    │
    ├── 入内存队列 (ch, 容量 256)
    │
    ├── 后台 goroutine 批量 flush
    │   ├── 满 50 条 或 2 秒超时
    │   └── 逐条 MsgClient.call(json)
    │
    └── (同 RPC 流程，fail-safe：失败静默丢弃)
```

---

## 三、代码结构

### 3.1 文件结构

```
pymsg/
├── pymsg/
│   └── __init__.py     # 核心实现（~460 行）
├── tests/
│   └── test_threadpool.py  # 线程池/同步模式单元测试（纯 stdlib）
├── README.md            # 用户文档
├── pyproject.toml       # 项目配置
└── .gitignore
```

### 3.2 跨语言实现位置

| 语言 | 文件 | 说明 |
|------|------|------|
| Python | [pymsg/__init__.py](file:///Users/qingsong/micro_servers/pymsg/pymsg/__init__.py) | 主实现 |
| C++ | [gateway/inc/Msg.hpp](file:///Users/qingsong/micro_servers/gateway/inc/Msg.hpp) | gateway 使用 |
| Go | [waf/internal/logclient/logclient.go](file:///Users/qingsong/micro_servers/waf/internal/logclient/logclient.go) | waf 使用 |
| Python (黑盒) | [tests/blackbox/helpers.py](file:///Users/qingsong/micro_servers/tests/blackbox/helpers.py) | 纯 stdlib 测试实现 |

### 3.3 代码索引

#### Python 实现（__init__.py）

| 函数/类 | 行号 | 功能 |
|---------|------|------|
| `send(sock, data, *, timeout=None)` | L51 | 底层发送 |
| `recv(sock, *, timeout=None)` | L75 | 底层接收 |
| `_recv_exact(sock, n)` | L106 | 精确读 n 字节 |
| `_timeout(sock, seconds)` | L38 | 超时 context manager |
| `TimeoutError` | L33 | 超时异常 |
| `MsgClient` | L123 | TCP 客户端 |
| `MsgClient.__init__` | L139 | 构造器 |
| `MsgClient.connect()` | L147 | 建立长连接 |
| `MsgClient.send(msg)` | L153 | 短连接发送（带 name 前缀） |
| `MsgClient.call(msg, *, timeout=None)` | L166 | 短连接 RPC |
| `MsgClient.listen(on_message)` | L196 | 长连接监听 |
| `MsgClient.stop()` | L223 | 停止监听 |
| `MsgServer` | L237 | TCP 服务端 |
| `MsgServer.__init__(addr, port, *, workers=0)` | L262 | 构造器（workers>0 启用线程池） |
| `MsgServer.start(on_message)` | L276 | 启动服务 |
| `MsgServer.broadcast(msg, except_sock)` | L329 | 广播（每连接锁串行化） |
| `MsgServer.reply(sock, msg)` | L355 | 回复（每连接锁串行化） |
| `MsgServer._locked_send(sock, msg)` | L363 | 带每连接锁的发送 |
| `MsgServer.stop()` | L373 | 停止服务 |
| `MsgServer._run_task(...)` | L413 | 工作线程：执行回调、失败打标记 |
| `MsgServer._reap_failed(socketlist)` | L434 | 主循环统一移除失败连接 |

#### C++ 实现（Msg.hpp）

| 函数/类 | 行号 | 功能 |
|---------|------|------|
| `MsgException` | L47 | 基础异常 |
| `MsgTimeoutException` | L52 | 超时异常 |
| `MsgConnectionException` | L57 | 连接异常 |
| `writeUint32BE(buf, val)` | L72 | 4 字节大端编码 |
| `readUint32BE(buf)` | L79 | 4 字节大端解码 |
| `setSockTimeout(sockfd, seconds)` | L88 | 设置 socket 超时 |
| `recvExact(sockfd, buf, n)` | L99 | 精确读 n 字节 |
| `sendAll(sockfd, data, len)` | L121 | 完整发送 |
| `openConnection(addr, port, timeout)` | L139 | 打开短连接 |
| `pmsg_send(sockfd, data)` | L174 | 协议发送 |
| `pmsg_recv(sockfd)` | L188 | 协议接收 |
| `MsgClient` | L210 | C++ 客户端 |
| `MsgClient.connect()` | L220 | 长连接 |
| `MsgClient.send(msg)` | L228 | 短连接发送 |
| `MsgClient.call(msg, timeout)` | L248 | 短连接 RPC |
| `MsgClient.listen(on_message)` | L269 | 长连接监听 |
| `MsgClient.stop()` | L311 | 停止 |

#### Go 实现（logclient.go）

| 函数 | 行号 | 功能 |
|------|------|------|
| `pymsgCall(payload)` | L226 | 发送并接收一条 pymsg 消息 |
| `Client.sendOne(entry, ts)` | L179 | 发送单条日志 |
| `Client.send(batch)` | L171 | 批量发送 |
| `Client.flushLoop()` | L125 | 后台 flush goroutine |
| `Push(service, level, message, extra)` | L82 | 全局推送 |

---

## 四、API 参考

### 4.1 底层函数

#### send(sock, data, *, timeout=None)

```python
def send(sock: socket.socket, data: str, *, timeout: float | None = None) -> None
```

**功能**：在已连接的 socket 上发送一条长度前缀帧消息
- 将字符串编码为 UTF-8
- 前面加上 4 字节大端长度头
- 调用 `sock.sendall()` 发送

**参数**：
- `sock`：已连接的 socket 对象
- `data`：要发送的字符串
- `timeout`：可选超时（秒），超时抛出 `TimeoutError`

#### recv(sock, *, timeout=None)

```python
def recv(sock: socket.socket, *, timeout: float | None = None) -> str
```

**功能**：从已连接的 socket 接收一条长度前缀帧消息
- 读取 4 字节头解出长度
- 读取指定字节数
- 返回解码后的 UTF-8 字符串

**参数**：
- `sock`：已连接的 socket 对象
- `timeout`：可选超时（秒）

**返回值**：解码后的字符串

**异常**：
- `ConnectionError`：连接在完整消息接收前断开
- `TimeoutError`：操作超时

#### TimeoutError

```python
class TimeoutError(Exception)
```

`send` 或 `recv` 超过指定超时时抛出的异常。

---

### 4.2 MsgClient（客户端）

#### 构造

```python
client = MsgClient(addr: str, port: int, name: str)
```

**参数**：
- `addr`：服务端地址或 IP
- `port`：服务端端口
- `name`：客户端标识（`send()` 模式下以 `"name: message"` 格式发送）

#### 方法

| 方法 | 模式 | 说明 |
|------|------|------|
| `connect()` | 长连接 | 创建持久 TCP 连接（供 `listen()` 使用） |
| `send(msg)` | 短连接 | 发送即忘，消息带 `name: msg` 前缀 |
| `call(msg, timeout=10.0)` | 短连接 | RPC 模式，阻塞等待回复 |
| `listen(on_message)` | 长连接 | 阻塞当前线程，持续接收消息 |
| `stop()` | — | 中断 `listen()`，可从另一线程调用 |

#### 使用示例

```python
from pymsg import MsgClient

# RPC 调用（短连接）
client = MsgClient("127.0.0.1", 8005, "my-service")
resp = client.call('{"_route":"execute","action":"select","table":"Users"}', timeout=10.0)
print(resp)  # {"success": true, "data": [...]}

# 发送即忘
client.send("hello")

# 长连接监听
def on_message(text):
    print(f"收到: {text}")

client.connect()
client.listen(on_message)  # 阻塞，直到 stop()
```

---

### 4.3 MsgServer（服务端）

#### 构造

```python
server = MsgServer(addr: str, port: int, *, workers: int = 0)
```

**参数**：
- `addr`：监听地址（`0.0.0.0` 表示所有网卡）
- `port`：监听端口
- `workers`：工作线程数（v0.2.0+）。默认 `0` 为同步模式（回调在 select 循环线程内串行执行）；`>0` 时回调提交到 `ThreadPoolExecutor` 并发执行

#### 并发模型（v0.2.0）

```
客户端 A ──┐
客户端 B ──┼──► select 主循环（单线程）
客户端 C ──┘      │
                  ├── accept / recv（主线程，快速 I/O）
                  │
                  ├── workers=0：on_message 就地同步执行（串行）
                  │
                  └── workers>0：标记 busy → submit 到 ThreadPoolExecutor
                                      │
                                      ▼
                            worker 线程执行 on_message
                            ├── reply() 经该连接发送锁串行化
                            ├── 异常 → 标记 _failed（主循环下轮统一移除）
                            └── 完成 → 清除 busy，连接恢复 select 监听
```

保证：
- **同连接串行**：busy 标记使回调完成前该连接暂停 select 监听，同一连接的消息不会并发执行（恢复粒度 ≤100ms，短连接 RPC 无感）
- **发送原子性**：`reply()`/`broadcast()` 经每连接 `Lock` 串行化，多 worker 向同一 socket 写入不会帧交错
- **故障隔离**：回调抛非连接异常时打印 traceback 并移除该连接，服务整体存活（同步模式下异常会使 `start()` 崩溃，两模式语义不同）
- **fd 安全**：worker 线程不直接 close socket，连接移除统一由主循环执行，避免与 accept/select 产生 fd 复用竞态

#### 方法

| 方法 | 说明 |
|------|------|
| `start(on_message)` | 启动服务器（阻塞），回调签名：`on_message(sock, addr, text)` |
| `reply(sock, msg)` | 向指定客户端回复 |
| `broadcast(msg, except_sock=None)` | 广播到所有客户端（可排除某个 socket） |
| `stop()` | 停止服务（可从另一线程调用） |

#### 回调签名

```python
def on_message(sock: socket.socket, addr: tuple, text: str):
    """
    Args:
        sock:  发送消息的客户端套接字（用于 reply）
        addr:  (ip, port) 元组
        text:  解码后的字符串消息
    """
    server.reply(sock, "ack")
```

#### 使用示例

```python
from pymsg import MsgServer

def on_message(sock, addr, text):
    print(f"[{addr}] {text}")
    server.reply(sock, "ack")

server = MsgServer("0.0.0.0", 8005)
server.start(on_message)  # 阻塞
```

---

### 4.4 异常速查

| 异常 | 触发条件 |
|------|----------|
| `pymsg.TimeoutError` | `send()` 或 `recv()` 超过 `timeout` |
| `ConnectionError`（内置） | 完整消息接收前 socket 断开 |
| `ConnectionRefusedError`（内置） | `MsgClient.call()` 或 `send()` 无法连接目标 |
| `RuntimeError` | `MsgClient.listen()` 在 `connect()` 之前调用 |

---

## 五、使用场景

### 5.1 场景一：数据库服务（db:8005）

db 服务通过 pymsg 暴露通用 CRUD 接口：

```python
import json
from pymsg import MsgServer

class DBServer:
    def __init__(self):
        self._server = MsgServer("127.0.0.1", 8005)
        self._routes = {"execute": self._handle_execute}

    def start(self):
        self._server.start(self._dispatch)

    def _dispatch(self, sock, addr, text):
        req = json.loads(text)
        route = req.pop("_route", "")
        handler = self._routes.get(route)
        if handler is None:
            self._server.reply(sock,
                json.dumps({"success": False, "error": "未知路由"}))
            return
        try:
            result = handler(req)
            self._server.reply(sock,
                json.dumps({"success": True, "data": result}))
        except Exception as e:
            self._server.reply(sock,
                json.dumps({"success": False, "error": str(e)}))

    def _handle_execute(self, data):
        # 通过 SQLAlchemy 执行 SQL，返回 dict 列表
        pass
```

### 5.2 场景二：RPC 调用（dnfs → db）

```python
import json
from pymsg import MsgClient

def _call_dbv2(payload):
    client = MsgClient("127.0.0.1", 8005, "dnfs")
    resp = client.call(json.dumps(payload), timeout=10.0)
    return json.loads(resp)

# 创建笔记
result = _call_dbv2({
    "_route": "execute",
    "action": "insert",
    "table": "Notes",
    "values": {"title": "会议记录", "content": "..."}
})
```

### 5.3 场景三：异步日志推送（log_client）

```python
import json
from pymsg import MsgClient

def _send(batch):
    for entry in batch:
        client = MsgClient("127.0.0.1", 8005, "gateway-log")
        client.call(json.dumps({
            "_route": "execute",
            "action": "insert",
            "table": "Logs",
            "values": {
                "ts": now,
                "service": entry["service"],
                "level": entry["level"],
                "message": entry["message"],
            }
        }), timeout=5)
```

### 5.4 场景四：日志推送（Go 实现）

```go
// waf/internal/logclient/logclient.go
func (c *Client) sendOne(entry Entry, ts string) {
    req := dbV2Request{
        Route:  "execute",
        Action: "insert",
        Table:  "Logs",
        Values: values,
    }
    data, _ := json.Marshal(req)
    resp, err := pymsgCall(data)  // 短连接 RPC
    if err != nil {
        return  // fail-safe
    }
}

func pymsgCall(payload []byte) ([]byte, error) {
    conn, err := net.DialTimeout("tcp", "127.0.0.1:8005", 5*time.Second)
    // ... 写 4 字节头 + 载荷，读回复
}
```

---

## 六、跨语言实现对照

### 6.1 协议层

| 功能 | Python | C++ | Go |
|------|--------|-----|-----|
| 4 字节编码 | `struct.Struct("!I")` | `writeUint32BE` (手动位移) | `binary.BigEndian.PutUint32` |
| 4 字节解码 | `struct.unpack("!I", header)` | `readUint32BE` (手动位移) | `binary.BigEndian.Uint32` |
| 超时设置 | `_timeout` context manager | `setSockTimeout` (setsockopt) | `net.DialTimeout` + `SetDeadline` |
| 安全上限 | 无 | 64 MiB | 无 |

### 6.2 客户端对照

| 操作 | Python | C++ | Go |
|------|--------|-----|-----|
| 构造 | `MsgClient(addr, port, name)` | `MsgClient(addr, port, name)` | — |
| 长连接 | `connect()` | `connect()` | — |
| 发送即忘 | `send(msg)` 短连接 + `name:` 前缀 | `send(msg)` 短连接 + `name:` 前缀 | — |
| RPC 调用 | `call(msg, timeout)` 短连接 | `call(msg, timeout)` 短连接 | `pymsgCall(payload)` 短连接 |
| 长监听 | `listen(on_message)` | `listen(on_message)` | — |
| 停止 | `stop()` | `stop()` | — |

### 6.3 服务端对照

| 操作 | Python | C++ | Go |
|------|--------|-----|-----|
| 启动 | `start(on_message)` | — | — |
| 回复 | `reply(sock, msg)` | `pmsg_send(fd, msg)` | — |
| 广播 | `broadcast(msg, except_sock)` | — | — |
| 停止 | `stop()` | — | — |

### 6.4 从头实现协议

仅需四步：

```
1. 将字符串编码为 UTF-8 字节
2. 将字节长度打包为 4 字节大端无符号整数
3. 写入 头 + 载荷 到 socket
4. 接收端先读 4 字节解出长度，再读对应字节数，解码为 UTF-8
```

---

## 七、线程安全

| 场景 | 安全性 | 说明 |
|------|--------|------|
| `MsgServer.stop()` 跨线程调用 | ✅ 安全 | 设置原子标志并关闭监听 socket |
| `MsgServer.reply()` 在回调内调用 | ✅ 安全 | 经每连接发送锁串行化，worker 线程内同样安全 |
| `MsgServer.broadcast()` 在回调内调用 | ✅ 安全 | 发送经每连接锁；但会遍历客户端列表，仅限回调线程，勿从无关第三方线程并发调用 |
| `MsgClient.stop()` 跨线程调用 | ✅ 安全 | 可中断 `listen()` |
| `send()`/`recv()` 多线程共享 socket | ⚠️ 不安全 | 底层函数无锁，调用方需自行串行化（MsgServer 内部已用每连接锁保护） |
| 同一连接消息并发执行回调 | ✅ 不会 | busy 标记暂停 select 监听，保证同连接串行 |

---

## 八、性能特性

### 8.1 性能特点

| 特性 | 说明 |
|------|------|
| 零拷贝 | 长度前缀帧避免数据复制 |
| 短连接复用 | `call()` 每次新建连接，但调用完成立即释放 |
| fail-safe | 日志推送失败静默丢弃，不阻塞业务 |
| 批量缓冲 | 日志推送：50 条或 2 秒自动 flush |

### 8.2 连接模型

| 模式 | 连接数 | 延迟 | 适用场景 |
|------|--------|------|---------|
| `send()` | 每次 1 个 | 中 | 低频通知 |
| `call()` | 每次 1 个 | 中 | RPC 调用 |
| `listen()` | 持久 1 个 | 低 | 高频消息流 |

---

## 九、已知限制

| ID | 描述 | 严重等级 | 影响范围 |
|----|------|---------|---------|
| L1 | **无消息校验**：协议无 CRC/哈希，传输损坏无法检测 | 🟡 Medium | 所有消息 |
| L2 | **无认证**：任何人可连接 8005 端口，无鉴权机制 | 🔴 High | db 服务 |
| L3 | **无加密**：明文传输，无 TLS/加密保护 | 🔴 High | 所有服务 |
| L4 | **Python 版无安全上限**：C++ 版有 64 MiB 上限，Python 版未检查 | 🟡 Medium | Python 客户端 |
| L5 | **无重试机制**：连接失败直接抛异常，无自动重试 | 🟢 Low | 所有调用 |
| L6 | **无版本号**：协议升级无兼容路径 | 🟢 Low | 长期维护 |
| L7 | **无心跳**：长连接无 keep-alive，可能被防火墙切断 | 🟡 Medium | listen 模式 |
| L8 | **全局阻塞**：`MsgServer.start()` 阻塞主线程，需另开线程 | 🟢 Low | 服务端 |

### 建议改进

1. **L2/L3**：在防火墙层面限制 8005 端口仅允许本机连接（当前部署已这样做）
2. **L4**：在 Python `recv()` 中添加消息大小上限检查
3. **L7**：在 `listen()` 循环中添加 ping/pong 机制
4. **L5**：在 `call()` 中添加简单重试（1-2 次）

---

## 十、设计考量

### 10.1 为什么用长度前缀帧？

| 方案 | 优点 | 缺点 |
|------|------|------|
| **长度前缀帧** ✅ | 二进制安全、实现简单、无需流式解析 | 无 |
| 换行分隔 | 简单 | JSON 中换行需转义、缓冲区管理复杂 |
| HTTP | 成熟、有工具 | 头部开销大、需额外依赖 |
| Protobuf | 高效、类型安全 | 需额外依赖、跨语言实现复杂 |

### 10.2 为什么用短连接？

| 考虑 | 说明 |
|------|------|
| 简单性 | 短连接实现最简洁，无连接管理开销 |
| 可靠性 | 每次调用都是独立连接，单个请求失败不影响其他 |
| 性能 | 本地回环（`127.0.0.1`）下 TCP 连接建立开销很小 |
| 可测试性 | 易于模拟各种网络故障 |

### 10.3 协议设计取舍

| 取舍 | 选择 | 原因 |
|------|------|------|
| 头大小 | 4 字节 | 支持最大 4 GiB 消息，足够使用 |
| 字节序 | 大端 | 网络标准序，跨语言一致 |
| 编码 | UTF-8 | 支持中文/多语言，与 JSON 兼容 |
| 版本号 | 无 | 保持最简，升级时可扩展 |

---

## 十一、开发指南

### 11.1 安装

```bash
# 开发模式
cd pymsg
pip install -e .

# 作为本地依赖
# 在其他项目的 requirements.txt 中：
# ../pymsg
```

### 11.2 添加新功能

#### 添加消息校验（CRC）

```python
import zlib

def send_with_crc(sock, data):
    payload = data.encode("utf-8")
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    # [4字节长度][4字节CRC][UTF-8载荷]
    header = _HEADER.pack(len(payload) + 4)
    crc_bytes = struct.pack("!I", crc)
    sock.sendall(header + crc_bytes + payload)
```

#### 添加心跳机制

```python
import time

def listen_with_heartbeat(self, on_message, interval=30):
    last_recv = time.time()
    while self._running:
        # 发送心跳
        if time.time() - last_recv > interval:
            send(self._sock, "PING")
        # 接收消息
        text = recv(self._sock, timeout=1.0)
        if text == "PONG":
            continue
        last_recv = time.time()
        on_message(text)
```

### 11.3 扩展协议版本

```python
# 在头中添加版本号
_VERSION = 1  # 1 字节版本号

def send_v2(sock, data):
    payload = data.encode("utf-8")
    header = struct.pack("!BI", _VERSION, len(payload))
    sock.sendall(header + payload)

def recv_v2(sock):
    header = _recv_exact(sock, 5)
    version, length = struct.unpack("!BI", header)
    if version != _VERSION:
        raise ValueError(f"Unsupported protocol version: {version}")
    payload = _recv_exact(sock, length)
    return payload.decode("utf-8")
```

### 11.4 构建与测试

```bash
# Python 测试
cd pymsg
python -c "
from pymsg import MsgClient, MsgServer, send, recv
# ... 单元测试
"

# 黑盒测试
cd tests/blackbox
python test_runner.py
```

---

## 十二、版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| 0.1.0 | — | 初始版本：核心协议、MsgClient、MsgServer、跨语言实现 |
| 0.2.0 | 2026-07-30 | `MsgServer` 新增 `workers` 参数：回调可提交线程池并发执行；同连接串行（busy 标记）、每连接发送锁防帧交错、回调异常故障隔离；`tests/test_threadpool.py` 单元测试 |

---

## 附录

### A. 协议规范（RFC 风格）

```
Protocol: pymsg v0

Frame Format:
┌──────────┬──────────┬──────────────────┐
│ 4 bytes  │  N bytes │                  │
├──────────┼──────────┼──────────────────┤
│ uint32   │ UTF-8    │                  │
│ big-end  │ string   │                  │
│ payload  │ payload  │                  │
│ length   │          │                  │
└──────────┴──────────┴──────────────────┘

Encoding: UTF-8
Byte Order: Big-Endian (network byte order)
Max Message Size: 2^32 - 1 bytes (theoretical)
Timeout: Optional, specified per operation

Modes:
  - Short Connection (send/call): connect → send → recv → close
  - Long Connection (listen): connect → loop(recv → callback)
```

### B. 错误码映射

| 异常 | 来源 | 场景 |
|------|------|------|
| `TimeoutError` | pymsg | 操作超过指定超时 |
| `ConnectionError` | Python | 连接在接收前断开 |
| `ConnectionRefusedError` | Python | 目标服务不可达 |
| `RuntimeError` | Python | 在 `connect()` 前调用 `listen()` |
| `MsgTimeoutException` | C++ | C++ 版超时 |
| `MsgConnectionException` | C++ | C++ 版连接异常 |

### C. 参考实现清单

| 语言 | 文件 | 类型 |
|------|------|------|
| Python | [pymsg/__init__.py](file:///Users/qingsong/micro_servers/pymsg/pymsg/__init__.py) | 主实现 |
| Python | [tests/blackbox/helpers.py](file:///Users/qingsong/micro_servers/tests/blackbox/helpers.py) | 测试客户端 |
| C++ | [gateway/inc/Msg.hpp](file:///Users/qingsong/micro_servers/gateway/inc/Msg.hpp) | gateway 客户端 |
| Go | [waf/internal/logclient/logclient.go](file:///Users/qingsong/micro_servers/waf/internal/logclient/logclient.go) | waf 客户端 |

### D. 参考资源

- [Beej's Guide to Network Programming](https://beej.us/guide/bgnet/) — Socket 编程经典
- [The SO_REUSEADDR socket option](https://man7.org/linux/man-pages/man2/socket.2.html)
- [Python socket module](https://docs.python.org/3/library/socket.html)

---

*本文档基于 pymsg 源码分析生成，供开发团队参考。*
