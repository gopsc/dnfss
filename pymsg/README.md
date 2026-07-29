# pymsg

基于长度前缀的可变长 socket 消息帧协议，Python 实现。  
零依赖、极简的 TCP 消息层 —— 没有 HTTP，没有 protobuf，只有 `[4字节头][UTF-8载荷]`。

## 协议

```
┌────────────────────────┬──────────────────────────┐
│  4 字节（大端序）        │  N 字节（UTF-8）          │
│  uint32 载荷字节长度     │  JSON / 文本载荷          │
└────────────────────────┴──────────────────────────┘
```

消息头为 4 字节大端无符号整数，表示后续 UTF-8 载荷的字节数。接收方先读 4 字节解出长度，再精确读取对应字节数，即完成一条消息的接收。

## 安装

```bash
cd pymsg
pip install -e .
```

或在其他项目中作为本地依赖引用：

```
# requirements.txt
../pymsg
```

要求 Python 3.9+，无外部依赖。

## 快速上手

```python
import socket
from pymsg import send, recv

# --- 发送方 ---
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("127.0.0.1", 8005))
send(sock, '{"action": "ping"}')
resp = recv(sock, timeout=5.0)
print(resp)  # {"success": true, "data": "pong"}
sock.close()

# --- 接收方 ---
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", 8005))
server.listen(1)
conn, addr = server.accept()
msg = recv(conn)
send(conn, '{"success": true, "data": "pong"}')
conn.close()
```

## API 参考

### 底层函数

**`send(sock, data, *, timeout=None)`**

在已连接的 socket 上发送一条长度前缀帧消息。将字符串编码为 UTF-8，前面加上 4 字节大端长度头，调用 `sock.sendall()` 发送。

若指定 `timeout`，会临时调整 socket 超时，超时后抛出 `TimeoutError`，原始超时设置会被恢复。

**`recv(sock, *, timeout=None)`**

从已连接的 socket 接收一条长度前缀帧消息。读取 4 字节头解出长度，再读取指定字节数，返回解码后的 UTF-8 字符串。

若连接在完整消息接收前断开，抛出 `ConnectionError`。若指定了 `timeout` 且超时，抛出 `TimeoutError`。

**`TimeoutError`**

`send` 或 `recv` 操作超过指定超时时间时抛出的异常，继承自 `Exception`。

### `MsgClient` — TCP 客户端

封装了 socket 创建、连接和三种通信模式。

```python
from pymsg import MsgClient

client = MsgClient("127.0.0.1", 8005, "my-service")
```

| 参数 | 含义 |
|------|------|
| `addr` | 服务端地址或 IP |
| `port` | 服务端端口 |
| `name` | 客户端标识，在 `send()` 模式下会以 `"name: message"` 格式发送 |

三种使用模式：

**1. 发送即忘（短连接）**

```python
client.send("hello")
```

每次调用新建 TCP 连接，发送带 name 前缀的消息 `"my-service: hello"` 后立即关闭，不等待回复。适用于通知、日志推送等无需响应的场景。

**2. 请求-应答 / RPC（短连接）**

```python
resp = client.call('{"action": "select", "table": "Users"}', timeout=10.0)
```

新建连接，发送消息（不带 name 前缀），阻塞等待一条回复后关闭。这是 RPC 风格服务调用的主要模式。默认超时 10 秒。

目标不可达时抛出 `ConnectionRefusedError`，超时无回复时抛出 `TimeoutError`。

**3. 长连接监听（回调模式）**

```python
def on_msg(text):
    print(f"收到: {text}")

client.connect()          # 建立持久 TCP 连接
client.listen(on_msg)     # 阻塞当前线程，每收到一条消息回调一次
```

先调用 `connect()` 建立长连接，再调用 `listen()` 阻塞当前线程，每收到一条消息即回调 `on_message(text)`。从另一线程调用 `client.stop()` 可退出监听循环。

### `MsgServer` — TCP 服务端

基于 pymsg 协议的多客户端 TCP 服务端。接受连接，接收帧消息，分发到用户提供的回调函数。

```python
from pymsg import MsgServer

def on_message(sock, addr, text):
    print(f"[{addr}] {text}")
    server.reply(sock, "ack")

server = MsgServer("0.0.0.0", 8005)
server.start(on_message)   # 阻塞当前线程
```

| 参数 | 含义 |
|------|------|
| `addr` | 监听地址 |
| `port` | 监听端口 |

回调签名为 `on_message(sock, addr, text)`：
- `sock` — 客户端 socket，可用 `reply()` 回复
- `addr` — `(ip, port)` 元组
- `text` — 解码后的 UTF-8 字符串消息

**主要方法：**

`start(on_message)` — 绑定端口、开始监听，进入 accept/分发循环，阻塞至 `stop()` 被调用。

`reply(sock, msg)` — 向指定客户端 socket 发送一条帧格式的回复。典型用法：服务端收到请求、处理后调用 `reply(sock, json_response)` 返回结果。

`broadcast(msg, except_sock=None)` — 向所有已连接客户端广播消息，可排除某个 socket（通常是发送者自身）。

`stop()` — 设置停止标志并关闭监听 socket，可从另一线程安全调用。

服务端内部管理客户端列表，透明处理断连和管道破裂，使用 `select` 实现非阻塞 I/O，轮询间隔 100ms。

## 线程安全

- `MsgServer.stop()` 可从任意线程安全调用：设置原子标志并关闭监听 socket，`select` 循环会干净退出。
- `MsgServer.broadcast()` 和 `reply()` 在 `on_message` 回调内（同一线程）调用是安全的。服务端运行期间从其他线程并发调用不支持。
- `MsgClient.stop()` 可从另一线程调用以中断 `listen()`。
- `send()` 和 `recv()` 内部不做同步 —— 如果多线程共享同一 socket，调用方需自行串行化。

## 实际用例

### 服务端 — 数据库服务（dbv2）

db 服务通过 pymsg 暴露通用 CRUD 接口。每条请求是带 `_route` 字段的 JSON 对象，服务端按路由分发到对应处理函数，通过同一 socket 回复结果。

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
        # ... 通过 SQLAlchemy 执行 SQL，返回 dict 列表
        pass
```

### 客户端 — RPC 调用（dnfs → db）

dnfs 笔记服务自身没有数据库，所有 CRUD 操作均通过 pymsg 调用 db。

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

### 客户端 — 异步日志推送（log_client）

各服务通过 pymsg 将日志异步推送到 db。使用 `MsgClient.call()` 逐批插入。

```python
import json
from pymsg import MsgClient

def _send(batch):
    client = MsgClient("127.0.0.1", 8005, "gateway-log")
    for entry in batch:
        client.call(json.dumps({
            "_route": "execute",
            "action": "insert",
            "table": "Logs",
            "values": {
                "ts": now, "service": entry["service"],
                "level": entry["level"], "message": entry["message"],
            }
        }), timeout=5)
```

## 跨语言互操作

pymsg 的线格式有意设计得极其简单，使其他语言的服务也能直接参与通信。项目中已有的实现：

- **C++** — `gateway/inc/Msg.hpp`（`pmc::net::MsgClient`）  
  完整复刻：`pmsg_send()`、`pmsg_recv()`，以及 `MsgClient` 类（`connect` / `send` / `call` / `listen` / `stop`）。

- **Go** — `waf/internal/logclient/logclient.go`  
  原生 `pymsgCall()` 函数：写 4 字节大端头 + 载荷，读取回复。

- **Python（纯 stdlib）** — `tests/blackbox/helpers.py`（`PymsgClient`）  
  仅使用 `socket` 和 `struct` 的纯 Python 实现，用于黑盒测试，不依赖 pymsg 包本身。

从头实现该协议只需四步：
1. 将字符串编码为 UTF-8 字节
2. 将字节长度打包为 4 字节大端无符号整数
3. 写入 头 + 载荷 到 socket
4. 接收端先读 4 字节解出长度，再读对应字节数，解码为 UTF-8

## 错误处理速查

| 异常 | 触发条件 |
|------|----------|
| `pymsg.TimeoutError` | `send()` 或 `recv()` 超过 `timeout` 参数设定的时间 |
| `ConnectionError`（内置） | 完整消息接收前 socket 断开 |
| `ConnectionRefusedError`（内置） | `MsgClient.call()` 或 `send()` 无法连接目标 |
| `RuntimeError` | `MsgClient.listen()` 在 `connect()` 之前调用 |

`MsgServer` 内部会吞掉客户端断连错误（移除该客户端，继续服务）。`broadcast()` 静默跳过已断开的 socket。

## 设计考量

为什么用长度前缀帧而不是换行分隔或 HTTP？

- **二进制安全。** JSON 载荷中可能包含换行符，长度前缀帧无需转义。
- **无需流式解析器。** 接收方读完 4 字节头后就知道还要读多少字节 —— 没有缓冲、没有行分割。
- **跨语言实现极其简单。** 整个协议用 C、Go 或 Rust 大约 20 行代码即可实现。
- **适用于任何流式传输。** TCP、Unix socket、管道、TLS 包裹的连接 —— 任何提供有序字节传输的通道都可以。
