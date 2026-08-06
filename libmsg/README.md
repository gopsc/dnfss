# libmsg — UniServe 消息通信库

C++20 轻量级 TCP 消息帧通信库，实现 pymsg 协议（4字节大端长度前缀 + UTF-8载荷），与 Python 版 pymsg 字节级兼容。提供底层帧收发函数、MsgClient 客户端、MsgServer 多客户端服务端，以及通用异步批量队列 AsyncQueue。

命名空间 `pmc::net`，库文件 `libus_msg.so`，头文件安装至 `/usr/include/us/`。被 `gateway`、`db`、`dnfs`、`smtp`、`captcha`、`confs` 等所有使用 pymsg 协议的 C++ 服务依赖。

## 架构

```
pmc::net::Msg_Basic          (基类: 版本信息 / 异常层次 / MSG_CHECK 宏)
└── MsgClient                TCP 客户端（短连接 send/call + 长连接 listen）

pmc::net::MsgServer          TCP 服务端（select 事件循环 + 可选线程池 + reply/broadcast）
pmc::net::AsyncQueue<T>      通用异步批量发送队列（对应 Python AsyncRpcQueue）

pmc::net::RpcClient          JSON RPC 客户端（_route 注入 + success/error 自动解包）—— 需 boost::json
pmc::net::RpcServer          JSON RPC 服务端（路由分发 + 响应包装 + 异常脱敏）—— 需 boost::json

pmc::net 命名空间下的自由函数：
├── pmsg_send(fd, data)              底层协议发送
├── pmsg_recv(fd)                    底层协议接收（阻塞模式，依赖 SO_RCVTIMEO）
└── pmsg_recv(fd, timeout_sec)       底层协议接收（deadline 模式，总超时精确控制）

pmc::net::msg_constants      协议常量（kHeaderSize=4, kMaxMsgSize=64MiB 等）

异常层次：
├── MsgException             基础异常
│   ├── MsgTimeoutException           收发超时
│   ├── MsgConnectionException        连接异常
│   │   └── MsgConnectionRefusedException  ECONNREFUSED（用于重试）
│   └── RpcError                      JSON RPC 业务错误（success=false 或响应非法）
```

所有收发函数均为 header-only（inline 实现），`AsyncQueue<T>` 和 `MsgServer` 也是 header-only，无第三方依赖（仅 POSIX socket + 标准库线程）。`version()` 编译进 `libus_msg.so`。

`RpcClient` 和 `RpcServer` 是可选的 JSON RPC 约定层，基于 `boost::json` 提供路由分发和响应自动包装，对应 Python 版 `pymsg.rpc`。使用时需额外链接 `-lboost_json`，核心库不引入此依赖。

## 线协议

```
+--------+---------------------------+
| 4 B BE |        N B UTF-8          |
| length |          payload          |
+--------+---------------------------+
```

- 长度字段：32 位无符号整数，大端序（network byte order）
- 载荷：UTF-8 编码文本（通常为 JSON）
- 最大消息长度：64 MiB（防 OOM 攻击）
- 传输层：TCP，无 TLS（信任本机网络边界，默认绑定 127.0.0.1）

## 类与 API

### Msg_Basic — 基类

提供版本查询和异常类型，`MsgClient` 继承此类。

| 成员 | 说明 |
|------|------|
| `version()` (static protected) | 返回版本字符串（编译进 .so） |
| `MsgExp` | 异常类，继承 `std::runtime_error` |
| `MSG_CHECK(cond, msg)` | 宏，条件为 false 时抛出 `std::runtime_error` |

### 协议常量（`msg_constants` 命名空间）

| 常量 | 值 | 说明 |
|------|----|------|
| `kHeaderSize` | 4 | 帧头长度（字节） |
| `kMaxMsgSize` | 64 MiB | 消息最大长度（防 OOM） |
| `kDefaultTimeout` | 10 秒 | call()/connect() 默认超时 |
| `kSendTimeout` | 3 秒 | send() 默认超时 |
| `kListenPollMs` | 100000 微秒 | listen() select 轮询间隔 |
| `kRetryLogEvery` | 5.0 秒 | 重试等待日志打印间隔 |

### 底层协议函数

```cpp
// 发送一条消息（长度前缀 + payload）
void pmsg_send(int sockfd, const std::string& data);

// 接收一条消息（阻塞模式，依赖 socket 已设置的 SO_RCVTIMEO）
// 抛出 MsgConnectionException（连接断开）或 MsgTimeoutException（超时）
std::string pmsg_recv(int sockfd);

// 接收一条消息（deadline 模式，总超时不超过 timeout_sec）
// header+payload 共享同一个 deadline，使用 select() 实现精确超时控制
// 适用于客户端 call() 和服务端，防止总等待时间翻倍
std::string pmsg_recv(int sockfd, double timeout_sec);
```

### MsgClient — TCP 客户端

```cpp
#include <us/Msg.hpp>

// 构造：指定服务地址、端口、客户端名称（用于 send() 前缀）
pmc::net::MsgClient client("127.0.0.1", 8005, "gateway");
```

| 方法 | 说明 |
|------|------|
| `connect()` | 创建长连接（供 listen() 使用） |
| `send(msg)` | 短连接发送 ``"name: msg"`` 格式消息，不等待回复 |
| `call(msg, timeout=10, retry_on_refused=false, retry_interval=1.0)` | 短连接 RPC：发送原始消息，阻塞等待回复 |
| `listen(on_message)` | 长连接持续接收，回调签名 `void(const std::string&)` |
| `stop()` | 设置停止标志并关闭套接字，可跨线程调用 |
| `addr()` / `port()` / `name()` | 访问器 |

**call() 的 retry_on_refused 机制**：

设为 `true` 时，仅对 `ECONNREFUSED`（目标服务未启动）以 1 秒间隔阻塞重试，每 5 秒打印一次等待日志。适用于微服务启动顺序不确定的场景：服务先启动的一方会自动等待依赖方就绪，无需额外服务发现组件。其他异常（超时、连接重置等）仍立即抛出。

### MsgServer — TCP 服务端

```cpp
#include <us/MsgServer.hpp>

// 构造：绑定地址和端口，可配置线程池大小
pmc::net::MsgServer server("0.0.0.0", 8003, {.workers=4});
```

对应 Python 版 `pymsg.MsgServer`，提供基于 select 的多客户端 TCP 服务端。

**配置结构**：

```cpp
struct MsgServerConfig {
    int    workers         = 0;        // 线程池大小；0=同步模式（回调在主线程执行）
    double recv_timeout    = 10.0;     // 接收单条消息的总超时（秒），防 Slowloris
    int    max_connections = 1000;     // 最大并发连接数，超过则拒绝新连接
    int    max_queue_size  = 1000;     // 线程池任务队列上限（workers>0 时），满时断开连接
    int    listen_backlog  = 10;       // listen() backlog
    double poll_timeout    = 0.1;      // select 轮询超时（秒），影响关闭响应速度
};
```

| 方法 | 说明 |
|------|------|
| `start(on_message)` | 启动服务（阻塞当前线程）；回调签名 `void(int fd, const string& addr, const string& msg)` |
| `stop()` | 停止服务（可从任意线程调用，包括回调内部） |
| `reply(fd, msg)` | 向指定客户端回复消息（线程安全，经每连接锁串行化） |
| `broadcast(msg, except_fd=-1)` | 向所有客户端广播（线程安全），可排除指定 fd |
| `client_count()` | 当前已连接客户端数量 |

**设计特性**：

- **两种并发模式**：`workers=0` 同步模式（简单高效），`workers>0` 线程池并发模式
- **同连接串行保证**：同一连接的消息严格串行处理（回调执行期间暂停 select 监听该 fd）
- **线程安全发送**：`reply()`/`broadcast()` 经每连接 mutex 串行化，防止帧交错
- **连接数限制**：`max_connections` 防止连接耗尽攻击
- **有界任务队列**：`max_queue_size` 防止慢 handler 导致 OOM，队列满时断开连接
- **Slowloris 防护**：使用 deadline 模式 recv（select + 剩余时间），总接收时间不超过 `recv_timeout`
- **优雅关闭**：`stop()` 可从任意线程调用，事件循环退出前清理所有连接
- **故障隔离**：worker 线程异常仅标记 failed fd，主循环统一清理，避免 fd 并发操作
- **TCP_NODELAY**：新连接自动禁用 Nagle 算法，降低小包延迟

### AsyncQueue\<T\> — 通用异步批量发送队列

```cpp
#include <us/AsyncQueue.hpp>
```

对应 Python 版 `pymsg.AsyncRpcQueue`，提供内存队列 + 后台线程的异步批量处理机制，适用于日志上报、指标采集、事件通知等非关键路径场景。业务线程 `push()` 非阻塞立即返回，后台线程攒批处理（批量阈值或超时），失败时通过错误回调通知，绝不阻塞主业务流程。

**配置结构**：

```cpp
struct AsyncQueueConfig {
    size_t batch_size     = 100;    // 批量发送阈值，达到此数量立即 flush
    double flush_interval = 2.0;    // 定时 flush 间隔（秒）
    size_t max_queue_size = 10000;  // 队列最大长度，0=无上限；超过时丢最旧数据
    double poll_timeout   = 0.1;    // 后台线程轮询超时（秒）
};
```

| 方法 | 说明 |
|------|------|
| `AsyncQueue(handler, config={}, on_error={})` | 构造队列；`handler` 签名 `void(vector<T>&& batch)`，`on_error` 签名 `void(const exception&, vector<T>&&)` |
| `start()` | 启动后台发送线程（幂等） |
| `push(item)` | 非阻塞入队；返回 false 表示队列已关闭 |
| `flush()` | 在调用线程同步 flush 当前队列中的所有数据，阻塞直到处理完成 |
| `shutdown()` | 优雅关闭：flush 剩余数据后停止线程（幂等，析构自动调用） |
| `pending()` | 当前队列中待处理的数据量 |
| `dropped()` | 因队列满而丢弃的数据总数 |
| `is_running()` | 是否已 start 且未 shutdown |

**设计特性**：

- **非阻塞入队**：`push()` 立即返回，不等待网络 I/O
- **批量发送**：攒到 `batch_size` 条或超过 `flush_interval` 秒自动 flush
- **背压保护**：`max_queue_size` 限制内存队列长度，满时丢最旧数据并递增 `dropped()` 计数
- **优雅关闭**：`shutdown()` flush 所有剩余数据后停止后台线程
- **RAII**：析构自动调用 `shutdown()`
- **可插拔**：Handler 回调可自定义（RPC 调用、写文件、发消息等）
- **fail-safe**：Handler 抛异常时触发 `on_error` 回调，绝不传播到业务线程

### RpcClient — JSON RPC 客户端

```cpp
#include <us/RpcClient.hpp>   // 需链接 -lboost_json

// 构造：指定服务地址、端口、客户端名称
pmc::net::RpcClient rpc("127.0.0.1", 8005, "gateway");

// 启动等待模式（db 未启动时阻塞重试）
pmc::net::RpcClient rpc("127.0.0.1", 8005, "gateway", 10.0, true);
```

对应 Python 版 `pymsg.rpc.RpcClient`，在 `MsgClient` 之上封装 JSON RPC 约定：
- 自动注入 `_route` 字段
- 自动解析响应 `{"success":true,"data":...}` / `{"success":false,"error":"..."}`
- 业务错误抛 `RpcError`，网络错误仍抛原有网络异常

| 方法 | 说明 |
|------|------|
| `RpcClient(addr, port, name, timeout=10, retry_on_refused=false, retry_interval=1.0)` | 构造 RPC 客户端 |
| `call(route, params={}, timeout=nullopt, retry=nullopt, retry_interval=nullopt)` | 调用路由，返回 `data` 字段值；业务错误抛 `RpcError` |
| `call_raw(route, params={}, timeout=nullopt, retry=nullopt, retry_interval=nullopt)` | 调用路由，返回完整响应 `object`（含 success/data/error），不抛 `RpcError` |
| `addr()` / `port()` / `name()` | 访问器 |

**RPC 约定**：
- 请求格式：`{"_route": "<路由名>", ...业务字段}`
- 成功响应：`{"success": true, "data": ...}`
- 错误响应：`{"success": false, "error": "<错误信息>"}`
- 非 JSON 响应或缺 success 字段抛 `RpcError("响应不是合法 JSON: ...")`

### RpcServer — JSON RPC 服务端

```cpp
#include <us/RpcServer.hpp>   // 需链接 -lboost_json

// 构造：绑定地址和端口，可透传 MsgServerConfig
pmc::net::RpcServer rpc("127.0.0.1", 8005, {.workers=4});

// 注册路由
rpc.add_route("ping", [](const json::object&) -> json::value {
    return "pong";
});

rpc.start();   // 阻塞当前线程
```

对应 Python 版 `pymsg.rpc.RpcServer`，在 `MsgServer` 之上封装路由分发和响应自动包装。

| 方法 | 说明 |
|------|------|
| `RpcServer(addr, port, config={}, on_error={})` | 构造 RPC 服务端；`on_error` 签名 `void(const string& route, const exception& e)` |
| `add_route(name, handler)` | 注册路由；`handler` 签名 `json::value(const json::object& req)` |
| `start()` | 启动服务（阻塞当前线程） |
| `stop()` | 从另一线程停止服务 |
| `server()` | 获取底层 `MsgServer&` 引用（可用于 broadcast 等） |

**Handler 约定**：
- 返回值自动包装为 `{"success": true, "data": <返回值>}`
- 抛出 `std::exception` 自动包装为 `{"success": false, "error": "<脱敏后错误>"}`
- 返回 `nullptr` 表示无数据（客户端收到 `data=null`）
- 业务参数中 `_route` 字段已被移除，不会透传给 handler

**安全特性**：
- JSON 解析限制嵌套深度 32 层，防止栈溢出攻击
- 错误信息自动脱敏：绝对路径替换为 `<redacted>`，不泄露内部文件路径
- `on_error` 钩子可记录完整异常到服务端日志（脱敏仅对客户端响应生效）
- Handler 异常不导致服务崩溃

### 异常层次

| 异常类 | 含义 | 来源 |
|--------|------|------|
| `MsgException` | 所有消息库异常的基类 | — |
| `MsgTimeoutException` | send/recv 超时 | SO_RCVTIMEO/SO_SNDTIMEO 或 deadline 到期触发 |
| `MsgConnectionException` | 连接断开/重置/地址错误 | recv 返回0/错误、connect 失败 |
| `MsgConnectionRefusedException` | connect 返回 ECONNREFUSED | 仅用于 retry_on_refused 判定 |
| `RpcError` | JSON RPC 业务错误 | 远端返回 success=false、响应非 JSON、缺少 success 字段 |

## 构建与安装

**前置依赖**：g++（支持 C++20）、POSIX 系统（Linux/macOS），无第三方库依赖。

```bash
# 一键构建 + 安装（需 sudo）
bash _set.sh

# 或手动
make            # 编译 libus_msg.so
make install    # 安装头文件到 /usr/include/us/ + .so 到 /lib/
make clear      # 清理构建产物
make uninstall  # 卸载
```

使用：

```cpp
#include <us/Msg_Basic.hpp>
#include <us/Msg.hpp>
#include <us/MsgServer.hpp>   // 如需服务端
#include <us/AsyncQueue.hpp>  // 如需异步队列
#include <us/RpcClient.hpp>   // 如需 JSON RPC 客户端（需 boost::json）
#include <us/RpcServer.hpp>   // 如需 JSON RPC 服务端（需 boost::json）
```

链接：

```bash
# 仅使用核心库（Msg/MsgServer/AsyncQueue）
g++ -std=c++20 ... -lus_msg

# 使用 JSON RPC 层（RpcClient/RpcServer）
g++ -std=c++20 ... -lus_msg -lboost_json
```

> 与 `libus_crypt` 不同，`libus_msg.so` 不依赖 OpenSSL，无需额外链接 `-lssl -lcrypto`。所有通信函数、MsgServer 和 AsyncQueue 均为 header-only 内联实现，链接 `-lus_msg` 仅引入版本符号。

## 使用示例

### RPC 请求-应答（短连接）

```cpp
#include <us/Msg.hpp>
#include <iostream>

int main() {
    pmc::net::MsgClient client("127.0.0.1", 8005, "demo");

    // 简单调用（不重试）
    try {
        std::string resp = client.call(R"({"_route":"execute","sql":"SELECT 1"})", 5.0);
        std::cout << "Response: " << resp << std::endl;
    } catch (const pmc::net::MsgConnectionRefusedException& e) {
        std::cerr << "服务未启动: " << e.what() << std::endl;
    }

    // 启动等待模式（阻塞直到 db 就绪）
    std::string resp = client.call(
        R"({"_route":"execute","sql":"SELECT 1"})",
        5.0,
        /*retry_on_refused=*/true
    );
    std::cout << "Response after wait: " << resp << std::endl;

    return 0;
}
```

### 发送即忘（短连接通知）

```cpp
pmc::net::MsgClient log_client("127.0.0.1", 8005, "log");
log_client.send("application started");  // 发送 "log: application started"
```

### 长连接监听

```cpp
#include <us/Msg.hpp>
#include <iostream>

int main() {
    pmc::net::MsgClient listener("127.0.0.1", 9000, "subscriber");
    listener.connect();

    listener.listen([](const std::string& msg) {
        std::cout << "Received: " << msg << std::endl;
    });

    return 0;
}
```

### Echo 服务端（同步模式）

```cpp
#include <us/MsgServer.hpp>
#include <iostream>
#include <thread>

int main() {
    pmc::net::MsgServer server("127.0.0.1", 8003);
    std::thread t([&] {
        server.start([&](int fd, const std::string& addr, const std::string& msg) {
            server.reply(fd, "echo: " + msg);
        });
    });

    // ... 服务运行 ...
    // 另一线程调用 server.stop() 可优雅关闭

    server.stop();
    t.join();
    return 0;
}
```

### 广播聊天服务端（线程池模式）

```cpp
#include <us/MsgServer.hpp>
#include <thread>

int main() {
    pmc::net::MsgServer server("0.0.0.0", 8003, {
        .workers = 4,
        .recv_timeout = 10.0,
        .max_connections = 1000,
        .max_queue_size = 1000
    });

    server.start([&](int fd, const std::string& addr, const std::string& msg) {
        // 转发给所有人（排除发送者自己）
        server.broadcast("[" + addr + "] " + msg, /*except_fd=*/fd);
    });
    return 0;
}
```

### 异步批量队列（日志上报）

```cpp
#include <us/AsyncQueue.hpp>
#include <us/Msg.hpp>
#include <string>
#include <vector>
#include <iostream>

struct LogEntry {
    std::string service;
    std::string message;
};

int main() {
    pmc::net::AsyncQueue<LogEntry> queue(
        [](std::vector<LogEntry>&& batch) {
            pmc::net::MsgClient client("127.0.0.1", 8005, "metrics");
            for (auto& e : batch) {
                // 发送到日志服务...
            }
        },
        {50, 2.0, 10000, 0.5},
        [](const std::exception& e, std::vector<LogEntry>&&) {
            std::cerr << "[log] send failed: " << e.what() << std::endl;
        }
    );
    queue.start();

    queue.push(LogEntry{"gateway", "request handled"});
    queue.push(LogEntry{"gateway", "db connected"});

    // ... 应用运行 ...

    queue.shutdown();
    return 0;
}
```

### JSON RPC 服务端 + 客户端

```cpp
#include <us/RpcServer.hpp>
#include <us/RpcClient.hpp>
#include <thread>
#include <iostream>

int main() {
    // 服务端
    pmc::net::RpcServer rpc("127.0.0.1", 8005, {.workers=4},
        [](const std::string& route, const std::exception& e) {
            std::cerr << "[rpc][" << route << "] error: " << e.what() << std::endl;
        });

    rpc.add_route("ping", [](const json::object&) -> json::value {
        return "pong";
    });

    rpc.add_route("add", [](const json::object& req) -> json::value {
        return req.at("a").to_number<int>() + req.at("b").to_number<int>();
    });

    std::thread t([&]{ rpc.start(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // 客户端
    pmc::net::RpcClient client("127.0.0.1", 8005, "demo");

    json::object params;
    params["a"] = 3;
    params["b"] = 4;
    json::value result = client.call("add", params);
    std::cout << "3 + 4 = " << result.as_int64() << std::endl;  // 7

    try {
        client.call("nonexistent");
    } catch (const pmc::net::RpcError& e) {
        std::cout << "error: " << e.what() << std::endl;  // unknown route: nonexistent
    }

    rpc.stop();
    t.join();
    return 0;
}
```

## 项目结构

```
libmsg/
├── inc/us/
│   ├── Msg_Basic.hpp    # 基类：异常层次、常量、版本声明
│   ├── Msg.hpp          # 核心协议：pmsg_send/pmsg_recv + MsgClient（header-only）
│   ├── MsgServer.hpp    # TCP 服务端：多客户端 select 事件循环 + 线程池（header-only）
│   ├── AsyncQueue.hpp   # 通用异步批量队列（header-only 模板）
│   ├── RpcClient.hpp    # JSON RPC 客户端（header-only，需 boost::json）
│   └── RpcServer.hpp    # JSON RPC 服务端（header-only，需 boost::json）
├── src/
│   └── Msg_Basic.cpp    # version() 实现
├── Makefile
├── _set.sh
└── README.md
```

## 注意事项

- **无外部依赖**：libmsg 核心（Msg/MsgServer/AsyncQueue）仅使用 POSIX socket API（socket/connect/recv/send/select）和 C++ 标准库（thread/mutex/condition_variable/deque/functional/unordered_map），不依赖 OpenSSL、Boost 或任何第三方库。
- **JSON RPC 层依赖**：`RpcClient`/`RpcServer` 是基于 `boost::json` 的可选约定层，使用时需安装 Boost 并链接 `-lboost_json`。核心 `libus_msg.so` 不依赖 Boost。
- **线程安全**：
  - `MsgClient` 的 `stop()` 方法使用 `std::atomic` 保护标志位和 fd，可跨线程安全调用；`connect()` 会安全关闭旧连接。同一 `MsgClient` 实例的 `call()`/`send()`/`listen()` 不应并发调用（每个线程应创建独立实例）。`listen()` 中使用局部 fd 副本防止 stop() 关闭后 FD_SET(-1) 导致未定义行为。
  - `MsgServer` 的 `start()` 阻塞调用线程；`stop()`/`reply()`/`broadcast()`/`client_count()` 可在任意线程（包括消息回调内部）安全调用。消息回调在同步模式下运行于主线程，在线程池模式下运行于 worker 线程。reply()/broadcast() 经每连接 mutex 串行化防止帧交错。
  - `AsyncQueue<T>` 的 `push()`/`flush()`/`shutdown()`/`pending()`/`dropped()` 可在任意线程安全调用；Handler 回调在后台线程中执行。`push()` 使用 mutex + condition_variable 唤醒后台线程，无锁竞争热点。
  - `RpcClient` 每次 `call()` 创建独立短连接，实例可在多线程中安全使用（但建议每个线程独立实例以避免连接复用问题）。
  - `RpcServer` 的 `add_route()` 应在 `start()` 之前调用；`stop()`/`server()` 可跨线程安全调用。Handler 在 MsgServer 回调线程中执行（同步模式=主线程，线程池模式=worker线程）。
- **macOS 兼容**：自动处理 `MSG_NOSIGNAL` 缺失问题（macOS 内核不会在 send 到已关闭连接时发 SIGPIPE）。
- **超时行为**：
  - `MsgClient::call()` 使用 deadline 模式 recv（select + 剩余时间递减），header+payload 共享同一个总超时，不会出现"header 等 t 秒 + payload 再等 t 秒"的双倍超时问题。
  - `MsgClient::listen()` 使用 SO_RCVTIMEO 做 100ms 轮询超时，结合 `_running` 原子标志实现快速停止响应。
  - `MsgServer` 使用 deadline 模式（select + 剩余时间递减），单条消息总接收时间不超过 `recv_timeout`，有效防御 Slowloris 攻击。
  - `AsyncQueue::flush()` 在调用线程同步执行 `_send_batch()`，阻塞直到 handler 处理完成；`shutdown()` 永久等待后台线程结束（handler 中 socket 均有超时，不会永久阻塞）。
- **消息大小限制**：收发两端均检查 64 MiB 上限（`kMaxMsgSize`），防止超大消息导致 OOM。
- **retry_on_refused 仅对 ECONNREFUSED 重试**：连接超时、连接重置、路由不可达等错误不重试，立即抛出异常。
- **MsgServer 同步 vs 线程池**：
  - `workers=0`（默认）：回调在主线程执行，适合 CPU 轻量、无阻塞 I/O 的场景（如简单 echo、内存状态查询）。同步模式下回调异常会被捕获、记日志并断开该客户端，不会导致服务崩溃。
  - `workers>0`：回调提交到线程池，适合有 I/O 阻塞的场景；同一连接消息仍严格串行，不同连接可并行处理。`stop()` 会等待所有已排队任务处理完成（优雅关闭），不丢弃请求。
- **AsyncQueue 背压策略**：队列满时丢最旧数据（drop-oldest），而非阻塞 push 或丢最新数据，保证最新数据不丢失同时防止内存无限增长。`batch_size` 是触发阈值而非批量上限，flush 时取走队列中所有数据一次处理。
- **跨语言兼容**：C++ 实现与 Python 版 `pymsg`、Go 版（waf 内）字节级完全兼容，可无缝互操作。

## 许可证

MIT License
