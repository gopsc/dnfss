# libhttp — C++20 HTTP / WebSocket 网络库

## 1. 模块概览

`libhttp` 是 `micro_servers` 项目的基础网络通信层，封装了 HTTP/HTTPS 服务器、HTTP 客户端（基于 libcurl）、WebSocket 服务器和 WebSocket 客户端四大功能。
以 C++20 标准编写，编译为动态库 `libus_http.so` 和静态库 `libus_http_static.a`，命名空间 `pmc::net`。

| 属性     | 值                          |
|----------|-----------------------------|
| 版本     | 0.0.1                       |
| 语言标准 | C++20                       |
| 许可证   | MIT                         |
| 框架名   | `us`                        |
| 库名     | `libus_http`                |
| 命名空间 | `pmc::net`                  |

**项目角色**：`libhttp` 被 `gateway` 模块和 `core` 模块直接依赖，承担所有入站请求的接收、路由、SSL 卸载和 WebSocket 长连接管理。

---

## 2. 文件清单

```
libhttp/
├── .gitignore                  # 构建产物忽略规则
├── CMakeLists.txt              # CMake 构建 (v3.12+)
├── LICENSE                     # MIT License
├── Makefile                    # GNU Make 构建
├── _set.sh                     # 一键编译安装脚本
├── inc/us/
│   ├── HttpClient.hpp          # HTTP 客户端 (header-only)
│   ├── HttpServer.hpp          # HTTP/HTTPS 服务器声明
│   ├── WebSocketClient.hpp     # WebSocket 客户端声明
│   ├── WebSocketClientBase.hpp # WebSocket 客户端抽象基类
│   └── WebSocketServer.hpp     # WebSocket 服务器声明
└── src/
    ├── HttpServer.cpp          # HTTP/HTTPS 服务器实现
    ├── WebSocketClient.cpp     # WebSocket 客户端实现
    └── WebSocketServer.cpp     # WebSocket 服务器实现
```

---

## 3. 外部依赖

| 依赖             | 用途                              | 备注                      |
|------------------|-----------------------------------|---------------------------|
| Boost 1.91+      | Asio (I/O)、Beast (HTTP/WS)、Algorithms | 默认 `BOOST_ROOT=C:/local/boost_1_91_0`，可通过环境变量覆盖 |
| OpenSSL          | TLS/SSL 加密传输                  | 通过 Boost.Asio.SSL 调用  |
| libcurl          | HTTP 客户端请求                   | 仅 `HttpClient.hpp` 依赖  |
| pthread (Unix)   | 多线程                            | CMake 自动链接            |
| ws2_32 (Windows) | Winsock                           | CMake 自动链接            |

---

## 4. 构建

### 4.1 GNU Make

```bash
# 编译 (仅 HttpServer.o → libus_http.so)
make

# 清理
make clear

# 安装到 /lib 和 /usr/include/us/
make install

# 卸载
make uninstall
```

> **注意**：Makefile 第 17 行默认只链接 `HttpServer.o`。如需 WebSocket 支持，需取消注释 `WebSocketServer.o` 和 `WebSocketClient.o`。

### 4.2 CMake

```bash
mkdir build && cd build
cmake .. -DBOOST_ROOT=/path/to/boost
cmake --build .
cmake --install . --prefix /usr/local
```

CMake 会同时产出 `libus_http.so` (动态) 和 `libus_http_static.a` (静态)。

### 4.3 一键脚本

```bash
bash _set.sh   # make clear → make → make install
```

---

## 5. API 参考

### 5.1 HttpServer — HTTP/HTTPS 服务器

**头文件**: `<us/HttpServer.hpp>`  
**实现**: `src/HttpServer.cpp`

#### 5.1.1 类型别名

```cpp
using HttpRequestHandler = std::function<http::response<http::string_body>(
    const http::request<http::string_body>& req,
    const std::unordered_map<std::string, std::string>& params)>;

using MiddlewareHandler = std::function<bool(
    const http::request<http::string_body>& req,
    http::response<http::string_body>& res,
    const std::unordered_map<std::string, std::string>& params)>;
```

- **`HttpRequestHandler`**: 路由处理函数，接收 Boost.Beast 请求对象和已解析的查询参数，返回 HTTP 响应。
- **`MiddlewareHandler`**: 中间件函数，返回 `false` 表示拦截（短路后续处理），`true` 表示放行。

#### 5.1.2 构造函数

| 签名                                                                                   | 说明                   |
|----------------------------------------------------------------------------------------|------------------------|
| `HttpServer(address, port, threads = 1)`                                               | 纯 HTTP 模式           |
| `HttpServer(address, port, cert_file, key_file, threads = 1)`                          | HTTPS 模式（自动启用 SSL） |

- `address`: 监听地址，如 `"0.0.0.0"`
- `port`: 监听端口
- `threads`: 工作线程数（即 `io_context` 线程池大小）
- `cert_file` / `key_file`: PEM 格式证书和私钥路径

#### 5.1.3 路由注册

```cpp
void get(const std::string& path, HttpRequestHandler handler);
void post(const std::string& path, HttpRequestHandler handler);
void put(const std::string& path, HttpRequestHandler handler);
void del(const std::string& path, HttpRequestHandler handler);
void options(const std::string& path, HttpRequestHandler handler);
void use(MiddlewareHandler middleware);
```

- 路径支持通配符：`*` (任意字符) 和 `?` (单个字符)，自动编译为 `std::regex`（大小写不敏感）。
- 匹配优先级：精确匹配 > 通配符匹配。
- 请求方法字符串对应：`"GET"`, `"POST"`, `"PUT"`, `"DELETE"`, `"OPTIONS"`。

#### 5.1.4 中间件

```cpp
void use(MiddlewareHandler middleware);
```

中间件在路由匹配之前执行，按注册顺序依次调用。任意中间件返回 `false` 时，请求处理结束，响应直接返回客户端。

#### 5.1.5 静态文件服务

```cpp
void setStaticDirectory(const std::string& path);
```

设置静态资源根目录。当请求路径未匹配任何路由时，自动尝试从该目录读取文件。支持 MIME 类型自动识别：`.html`, `.css`, `.js`, `.json`, `.png`, `.jpg/.jpeg`。

#### 5.1.6 OPTIONS 预检请求

当收到 `OPTIONS` 请求时：
1. 优先查找自定义 `OPTIONS` 路由处理器
2. 若未注册自定义处理器且请求包含 `Origin` 头，自动生成 CORS 响应
   - 自动检测该路径下已注册的方法（GET/POST/PUT/DELETE/OPTIONS）
   - 返回 `Access-Control-Allow-Origin`, `Access-Control-Allow-Methods`, `Access-Control-Allow-Headers`, `Access-Control-Max-Age`

#### 5.1.7 SSL 配置

```cpp
void setPrivateKeyPassword(const std::string& password);
```

- SSL 上下文使用 TLS 协议，禁用 SSLv2/SSLv3
- 仅允许 ECDHE/DHE 密钥交换的强密码套件：`ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS`
- 支持 PEM 加密私钥密码

#### 5.1.8 生命周期

```cpp
void start();              // 启动服务器（非阻塞，线程池跑 io_context）
void stop();               // 停止服务器（关闭所有会话，join 线程）
void run();                // 启动并在当前线程运行 io_context（阻塞）
bool isRunning() const;
```

#### 5.1.9 调试

```cpp
void showStatus() const;   // 打印服务器状态和已注册路由
```

内嵌类 `HttpServer::debug` 提供 `print_all_header_fields()` 静态方法，打印请求的全部头字段。

#### 5.1.10 内部架构

```
HttpServer
├── Listener (内部类)
│   └── acceptor_ (tcp::acceptor)
│       └── async_accept → Session 实例
└── Session (内部类，enable_shared_from_this)
    ├── HTTP: socket_ (tcp::socket)
    ├── HTTPS: ssl_stream_ (asio::ssl::stream<tcp::socket>)
    ├── strand_ 保证线程安全
    └── 读取 → handleRequest() → 写入 → [keep-alive 则继续读取]
```

---

### 5.2 HttpClient — HTTP 客户端

**头文件**: `<us/HttpClient.hpp>` (header-only，无独立 .cpp)  
**依赖**: libcurl

#### 5.2.1 枚举 `HttpMethod`

```cpp
enum class HttpMethod { GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS };
```

#### 5.2.2 公共方法

```cpp
HttpClient();             // 初始化 CURL 句柄，失败抛 runtime_error
~HttpClient();            // 清理 CURL 和 header 链表

void set_header(const std::string& header);
void clear_headers();

const std::map<std::string, std::string>& get_response_headers() const;
long get_response_code() const;
```

流式请求（通过回调函数接收响应体，保留原始字节）：

```cpp
void request_stream_sync(HttpMethod method, const std::string& url,
                         const std::string& data,
                         std::function<void(const std::string&)> callback);

// 便捷方法
void get_stream_sync(const std::string& url, std::function<void(const std::string&)> callback);
void post_stream_sync(const std::string& url, const std::string& data,
                      std::function<void(const std::string&)> callback);
void put_stream_sync(const std::string& url, const std::string& data,
                     std::function<void(const std::string&)> callback);
void delete_stream_sync(const std::string& url, std::function<void(const std::string&)> callback);
```

**特点**：
- 同步阻塞调用
- 响应体通过 `CURLOPT_WRITEFUNCTION` 直接追加到 `std::string`，**保留所有换行符和特殊字符**
- 超时设置：连接超时 10 秒，总超时 30 秒
- 每次请求调用 `curl_easy_reset` 重置状态

---

### 5.3 WebSocketServer — WebSocket 服务器

**头文件**: `<us/WebSocketServer.hpp>`  
**实现**: `src/WebSocketServer.cpp`

#### 5.3.1 回调类型

```cpp
using WebSocketMessageHandler = std::function<void(
    const std::string& clientId,
    const std::string& message,
    std::function<void(const std::string&)> sendCallback)>;

using WebSocketConnectHandler = std::function<void(
    const std::string& clientId, const std::string& remoteAddress)>;

using WebSocketDisconnectHandler = std::function<void(
    const std::string& clientId)>;
```

#### 5.3.2 公共接口

```cpp
WebSocketServer(unsigned short port, unsigned int threads = 2);
~WebSocketServer();

void start();
void stop();
bool isRunning() const;
unsigned short getPort() const;

// 事件注册
void onMessage(const std::string& path, WebSocketMessageHandler handler);
void onConnect(WebSocketConnectHandler handler);
void onDisconnect(WebSocketDisconnectHandler handler);

// 消息/客户端管理
bool sendToClient(const std::string& clientId, const std::string& message);
size_t broadcast(const std::string& message);
void disconnectClient(const std::string& clientId);
size_t getClientCount() const;
```

**特点**：
- PIMPL 模式：内部实现封装于 `Impl` 结构体
- 每个 `Session` 包含一个发送队列 (`std::deque<std::string>`)，保证消息顺序
- 客户端 ID 由全局原子计数器自动生成，格式 `"client_N"`
- 线程安全：客户端列表受 `clientsMutex_` 保护
- 消息处理器支持路径路由（按 `path` 键索引）

**内部架构**：

```
WebSocketServer
├── Impl
│   ├── io_context
│   ├── Listener (内部类) → tcp::acceptor
│   └── threads_
└── Session (内部类，enable_shared_from_this)
    ├── websocket::stream<beast::tcp_stream>
    ├── sendQueue_ (std::deque)
    └── 读取循环 → onMessage 回调 → sendCallback 写回
```

---

### 5.4 WebSocketClient — WebSocket 客户端

**头文件**: `<us/WebSocketClient.hpp>`, `<us/WebSocketClientBase.hpp>`  
**实现**: `src/WebSocketClient.cpp`

#### 5.4.1 抽象基类 `WebSocketClientBase`

定义统一接口：

```cpp
using MessageCallback = std::function<void(const std::string&)>;
using ErrorCallback   = std::function<void(const std::string&)>;
using ConnectCallback = std::function<void(bool)>;
using CloseCallback   = std::function<void()>;

// 回调设置
virtual void setMessageCallback(MessageCallback) = 0;
virtual void setErrorCallback(ErrorCallback) = 0;
virtual void setConnectCallback(ConnectCallback) = 0;
virtual void setCloseCallback(CloseCallback) = 0;

// 连接管理
virtual bool connect(int timeout_ms = 5000) = 0;
virtual void disconnect() = 0;
virtual bool isConnected() const = 0;

// 消息发送
virtual bool send(const std::string& message) = 0;
virtual bool sendBinary(const void* data, size_t size) = 0;

// 配置
virtual void setAutoReconnect(bool enable, int interval_ms = 3000) = 0;
virtual void setHeartbeatInterval(int interval_ms = 30000) = 0;

// SSL 相关（纯虚函数，非 SSL 版本忽略参数）
virtual bool isSecure() const = 0;
virtual void setVerifyCertificate(bool verify) = 0;
virtual void setCertificateFile(const std::string& cert_file) = 0;
virtual void setPrivateKeyFile(const std::string& key_file) = 0;
virtual void setCertificateAuthorityFile(const std::string& ca_file) = 0;
```

#### 5.4.2 公共接口

```cpp
WebSocketClient(const std::string& host, const std::string& port,
                const std::string& path = "/");
~WebSocketClient();
```

**特点**：
- PIMPL 模式：`Impl` 结构体封装所有 Boost 对象和状态
- 构造时自动启动 IO 线程
- 异步连接流程：`resolve → connect → WebSocket handshake`
- 发送队列受 `std::mutex` 和 `std::condition_variable` 保护
- 自动重连：`onError` 或 `onClose` 中若 `auto_reconnect=true` 则自动发起重连
- 非 SSL 版本：`isSecure()` 返回 `false`，SSL 配置方法为空实现
- `sendBinary` 将二进制数据包装为 `std::string` 后调用 `send`

---

## 6. 使用示例

### 6.1 基本 HTTP 服务器

```cpp
#include <us/HttpServer.hpp>
#include <iostream>

int main() {
    pmc::net::HttpServer server("0.0.0.0", 8080, 4);

    server.get("/hello", [](const auto& req, const auto& params) {
        http::response<http::string_body> res{http::status::ok, req.version()};
        res.set(http::field::content_type, "text/plain");
        res.body() = "Hello, world!";
        res.prepare_payload();
        return res;
    });

    server.post("/echo", [](const auto& req, const auto& params) {
        http::response<http::string_body> res{http::status::ok, req.version()};
        res.set(http::field::content_type, "application/json");
        res.body() = req.body();
        res.prepare_payload();
        return res;
    });

    server.start();  // 非阻塞启动
    std::cout << "Server running on port " << server.getPort() << std::endl;
    server.isRunning(); // 阻塞直到手动 stop()
}
```

### 6.2 HTTPS 服务器

```cpp
pmc::net::HttpServer server("0.0.0.0", 443,
    "/etc/ssl/certs/server.crt",
    "/etc/ssl/private/server.key",
    4);
server.setPrivateKeyPassword("mysecret");
server.start();
```

### 6.3 中间件 + 静态文件

```cpp
pmc::net::HttpServer server("0.0.0.0", 8080);

// 日志中间件
server.use([](const auto& req, auto&, const auto&) {
    std::cout << "[" << req.method_string() << "] " << req.target() << std::endl;
    return true;  // 继续处理
});

// 认证中间件
server.use([](const auto& req, auto& res, const auto&) {
    auto auth = req.find(http::field::authorization);
    if (auth == req.end()) {
        res.result(http::status::unauthorized);
        res.body() = "Unauthorized";
        res.prepare_payload();
        return false;  // 拦截
    }
    return true;
});

server.setStaticDirectory("/var/www/html");
server.start();
```

### 6.4 HTTP 客户端

```cpp
pmc::net::HttpClient client;

client.set_header("Authorization: Bearer token123");
client.set_header("Content-Type: application/json");

client.post_stream_sync("https://api.example.com/data",
    R"({"key":"value"})",
    [](const std::string& body) {
        std::cout << "Response: " << body << std::endl;
    });

long status = client.get_response_code();
auto& headers = client.get_response_headers();
```

### 6.5 WebSocket 服务器

```cpp
pmc::net::WebSocketServer ws_server(9000, 2);

ws_server.onConnect([](const std::string& clientId, const std::string& addr) {
    std::cout << "Client connected: " << clientId << " from " << addr << std::endl;
});

ws_server.onMessage("/chat", [&ws_server](const std::string& clientId,
                                           const std::string& message,
                                           auto sendCallback) {
    std::cout << "[" << clientId << "]: " << message << std::endl;
    ws_server.broadcast(clientId + ": " + message);
});

ws_server.onDisconnect([](const std::string& clientId) {
    std::cout << "Client disconnected: " << clientId << std::endl;
});

ws_server.start();
```

### 6.6 WebSocket 客户端

```cpp
pmc::net::WebSocketClient ws_client("localhost", "9000", "/chat");

ws_client.setConnectCallback([](bool success) {
    std::cout << (success ? "Connected!" : "Failed!") << std::endl;
});

ws_client.setMessageCallback([](const std::string& msg) {
    std::cout << "Received: " << msg << std::endl;
});

ws_client.setAutoReconnect(true, 3000);
ws_client.connect();

// 阻塞主线程保持连接
while (ws_client.isConnected()) {
    std::this_thread::sleep_for(std::chrono::seconds(1));
}
```

---

## 7. 请求处理流程

```
客户端 → TCP 连接
  ├─ HTTP:  Session::doRead()
  └─ HTTPS: Session::doSSLHandshake() → Session::doRead()
                ↓
      HttpServer::handleRequest(req)
         ├─ 解析 target_path + query params
         ├─ 执行中间件链 (MiddlewareHandler[])
         │    └─ 任意中间件返回 false → 短路返回
         ├─ OPTIONS 请求?
         │    ├─ 有自定义处理器 → 执行
         │    └─ 有 Origin → 自动生成 CORS 响应
         ├─ 路由匹配 (精确匹配 → 通配符匹配)
         │    └─ 命中 → 执行 HttpRequestHandler
         ├─ 路由未命中 + static_directory_ 非空
         │    └─ 尝试读取静态文件 → 自动设 Content-Type
         └─ 全部未命中 → 404
```

---

## 8. 设计要点

### 8.1 线程模型

- **HttpServer**: 每个工作线程独立运行 `io_context::run()`，所有异步操作通过 `strand` 序列化，同一 Session 的读写不会并发冲突
- **WebSocketServer**: 同 HttpServer，工作线程池跑 `io_context`
- **WebSocketClient**: 单 IO 线程，所有异步回调在该线程中执行
- **HttpClient**: 同步阻塞，无额外线程

### 8.2 PIMPL 模式

`WebSocketClient` 和 `WebSocketServer` 使用 PIMPL 隐藏 Boost 依赖，保持头文件轻量。

### 8.3 会话管理

- `HttpServer` 通过 `weak_ptr<Session>` 跟踪所有活跃会话，`stop()` 时遍历清理
- `WebSocketServer` 通过 `unordered_map<string, shared_ptr<Session>>` + mutex 管理

### 8.4 发送队列

`WebSocketServer::Session` 和 `WebSocketClient::Impl` 均采用 `std::deque` 消息队列，确保消息按序发送且不会因并发写入冲突。

---

## 9. 安全特性

| 特性                      | 实现位置                     |
|---------------------------|------------------------------|
| SSL/TLS 加密              | HttpServer SSL 构造函数      |
| 禁止 SSLv2/SSLv3          | `initSSLContext()`           |
| 仅强密码套件 (ECDHE/DHE)  | `initSSLContext()` 密码列表  |
| 禁用 NULL/MD5/DSS         | `initSSLContext()` 密码列表  |
| 加密私钥密码支持          | `setPrivateKeyPassword()`    |
| CORS 自动处理             | `handleRequest()` OPTIONS 分支 |

---

## 10. 与其他模块的关系

```
micro_servers/
├── gateway/  ──依赖──→ libhttp (HttpServer 接收外部请求)
├── core/     ──依赖──→ libhttp (HTTP 服务基础能力)
└── libhttp/  ──依赖──→ Boost, OpenSSL, libcurl
```

- **gateway** 使用 `pmc::net::HttpServer` 作为 API 网关入口，处理 JWT 认证、令牌刷新、请求转发
- **core** 模块使用 libhttp 提供的 HTTP 服务能力

---

## 11. 已知限制

1. **Makefile 默认不编译 WebSocket**：第 17 行仅链接 `HttpServer.o`，需手动启用
2. **WebSocketClient 无 SSL 实现**：当前仅有非加密版本；SSL 接口已声明但为空实现
3. **无 HTTP/2 支持**：仅 HTTP/1.1
4. **静态文件 MIME 有限**：仅支持 6 种常见类型
5. **无请求体大小限制**：依赖 Boost.Beast 默认配置
6. **HttpClient 仅同步**：无异步 HTTP 客户端实现
```

