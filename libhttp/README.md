# libhttp - UniServe HTTP/WebSocket Library

C++20 network library for the UniServe microservice framework, providing unified HTTP/HTTPS server and HTTP/WebSocket client capabilities built on Boost.Beast and libcurl.

## Features

### HttpServer
- **HTTP/HTTPS Server**: Asynchronous HTTP 1.1 server with TLS support
- **WebSocket Integration**: Single port can serve both HTTP(S) and WebSocket(S) endpoints
- **WebSocket Reverse Proxy**: Transparent bidirectional WebSocket proxy with handshake-time auth/callback (supports ws:// and wss:// backends)
- **Routing**: Exact path matching + wildcard (`*`) routing
- **Middleware**: `use()` for middleware chains (CORS, auth, logging, etc.)
- **Static Files**: `setStaticDirectory()` for serving static files
- **Thread Pool**: Multi-threaded I/O with `asio::strand` for thread safety
- **Client Management**: WebSocket client tracking, broadcast, and targeted send
- **WS Message Size Limit**: `setMaxWsMessageSize()` caps single WebSocket message (default 16MB, aligned with Beast; applies to server and proxy streams) to prevent OOM
- **Read Timeouts (Slowloris Protection)**: `setHeaderTimeout()` (default 10s) / `setReadTimeout()` (default 30s) enforce two-stage header/body read timeouts plus WS idle timeout, aligned with WAF
- **WS Queue Size Limit**: `setMaxWsQueueSize()` (default 1000) caps WS send / proxy queues; slow consumers get disconnected to prevent unbounded-memory OOM

### HttpClient
- **HTTP Client**: Synchronous HTTP requests via libcurl (GET/POST/PUT/DELETE/PATCH/HEAD/OPTIONS)
- **Streaming Response**: `request_stream_sync` streams response body chunk-by-chunk to the callback (no full-body buffering)
- **Response Size Limit**: `set_max_response_size()` caps downstream response body (default 10MB, aligned with WAF; 0 = unlimited) to prevent OOM on oversized responses
- **WebSocket Client**: Synchronous WebSocket client via Boost.Beast
- **Dual Protocol**: Same client instance handles both HTTP and WebSocket
- **ws:// and wss://**: Supports both plain and TLS-encrypted WebSocket connections
- **Binary Support**: Text and binary message sending
- **Backward Compatible**: Original API preserved; `WebSocketClient`/`WebSocketClientBase` are aliases for `HttpClient`

## Dependencies

- C++20 compiler
- Boost.Beast / Boost.Asio (header-only)
- OpenSSL (for TLS)
- libcurl (for HTTP client only — `libus_httpclient.so`)
- pthread

## Build

```bash
make           # builds build/libus_http.so and build/libus_httpclient.so
make install   # installs both .so to /lib and headers to /usr/include/us (requires root)
```

Two shared libraries are produced:

| Library | SONAME | Contains | Links |
|---|---|---|---|
| Server | `libus_http.so` | `HttpServer` (HTTP + WebSocket server + WS proxy) | `-lssl -lcrypto -lpthread` |
| Client | `libus_httpclient.so` | `HttpClient` (HTTP via curl + WebSocket via Beast) | `-lssl -lcrypto -lcurl -lpthread` |

`libus_http.so` does **not** depend on libcurl. Link against `libus_httpclient.so` only when you need the client, and link both when you need both server and client (e.g. a proxy/gateway).

### Source Layout

The server implementation is split into modular source files for faster incremental builds:

| Source File | Responsibility |
|---|---|
| `src/HttpServer.cpp` | Core lifecycle (construct/destruct/start/stop/run), SSL init, session management, debug utilities |
| `src/HttpServer_Listener.cpp` | TCP connection acceptor (`Listener` class) |
| `src/HttpServer_Routes.cpp` | HTTP route registration, route matching, wildcard→regex conversion, query parameter parsing |
| `src/HttpServer_Request.cpp` | Request dispatch pipeline, static file serving, CORS preflight, Session HTTP read/write |
| `src/HttpServer_Ws.cpp` | WebSocket route registration, client registry, broadcast/send APIs |
| `src/HttpServer_WsSession.cpp` | Session WebSocket handling (regular WS frames + bidirectional WS proxy bridging) |

Internal shared definitions (exception classes, full `Session`/`Listener` declarations) live in `inc/us/detail/HttpServerImpl.hpp` (installed to `/usr/include/us/detail/`).

Modifying one module only requires recompiling its corresponding `.o` file — no need to rebuild the entire 1400-line translation unit.

## Quick Start

### HTTP + WebSocket Server

```cpp
#include <us/HttpServer.hpp>
using namespace pmc::net;

int main() {
    // HTTP server on port 8080
    HttpServer server("0.0.0.0", 8080, 4); // 4 threads

    // HTTP GET route
    server.get("/hello", [](const auto& req, const auto& params) {
        http::response<http::string_body> res;
        res.result(http::status::ok);
        res.set(http::field::content_type, "text/plain");
        res.body() = "Hello, World!";
        res.prepare_payload();
        return res;
    });

    // WebSocket route
    server.ws("/ws", [](const std::string& clientId,
                         const std::string& message,
                         std::function<void(const std::string&)> send) {
        send("Echo: " + message);
    });

    server.onWsConnect([](const std::string& id, const std::string& addr) {
        std::cout << "Client " << id << " connected from " << addr << std::endl;
    });

    server.onWsDisconnect([](const std::string& id) {
        std::cout << "Client " << id << " disconnected" << std::endl;
    });

    server.start();
    // server.run(); // blocks with ioc.run()
    return 0;
}
```

### HTTP + WebSocket Client

```cpp
#include <us/HttpClient.hpp>
using namespace pmc::net;

// HTTP request
HttpClient http;
http.set_header("Content-Type: application/json");
http.post_stream_sync("http://localhost:8080/api/data", "{}",
    [](const std::string& resp) {
        std::cout << "Response: " << resp << std::endl;
    });

// WebSocket connection
HttpClient ws;
if (ws.ws_connect("ws://localhost:8080/ws")) {
    ws.ws_send("Hello WebSocket!");
    std::string reply = ws.ws_recv();
    std::cout << "Got: " << reply << std::endl;
    ws.ws_close();
}

// WSS (TLS) connection
HttpClient wss;
wss.ws_connect("wss://example.com/socket");
```

### HTTPS Server

```cpp
HttpServer secure("0.0.0.0", 443, "/path/to/cert.pem", "/path/to/key.pem", 4);
secure.get("/", handler);
secure.start();
```

## API Reference

### HttpServer

**Constructors**
```cpp
HttpServer(const std::string& address, unsigned short port, unsigned int threads = 1);
HttpServer(const std::string& address, unsigned short port,
           const std::string& cert_file, const std::string& key_file,
           unsigned int threads = 1);
```

**HTTP Routes**
```cpp
void get(const std::string& path, HttpRequestHandler handler);
void post(const std::string& path, HttpRequestHandler handler);
void put(const std::string& path, HttpRequestHandler handler);
void del(const std::string& path, HttpRequestHandler handler);
void options(const std::string& path, HttpRequestHandler handler);
void use(MiddlewareHandler middleware);
void setStaticDirectory(const std::string& path);
```

// WebSocket 单条消息大小上限（字节）。默认 16MB，与 Beast 默认一致；超限由 Beast 拒绝并关闭连接（防 OOM）。
void setMaxWsMessageSize(std::size_t bytes);
std::size_t getMaxWsMessageSize() const;

// 入站读超时（防 Slowloris）。默认 10s（头）/ 30s（体），对齐 WAF。
void setHeaderTimeout(std::chrono::seconds t);
std::chrono::seconds getHeaderTimeout() const;
void setReadTimeout(std::chrono::seconds t);
std::chrono::seconds getReadTimeout() const;

// WS 发送/代理队列上限（条数）。默认 1000；超限判定慢消费者并关闭连接（防 OOM）。
void setMaxWsQueueSize(std::size_t n);
std::size_t getMaxWsQueueSize() const;

**WebSocket Routes**
```cpp
void ws(const std::string& path, WebSocketMessageHandler handler);
void onWsConnect(WebSocketConnectHandler handler);
void onWsDisconnect(WebSocketDisconnectHandler handler);
bool sendToWsClient(const std::string& clientId, const std::string& message);
size_t broadcastWs(const std::string& message);
void disconnectWsClient(const std::string& clientId);
size_t getWsClientCount() const;
```

**WebSocket Reverse Proxy**
```cpp
void ws_proxy(const std::string& path, WsProxyHandshakeHandler handshake);
```

The `WsProxyHandshakeHandler` is called during the HTTP Upgrade handshake. It receives the upgrade request and must return the backend WebSocket URL (`ws://host:port/path` or `wss://host:port/path`) to bridge to. Returning an empty string rejects the connection (HTTP 403 is sent to the client). Proxy routes take priority over regular `ws()` routes on the same path.

Example — admin-only proxy:
```cpp
server.ws_proxy("/ws/admin", [](const http::request<http::string_body>& req) -> std::string {
    if (!isAdmin(req)) return "";           // reject non-admins
    return "ws://127.0.0.1:9000/stream";    // bridge to backend
});
```

Features:
- Full-duplex async bridging (text and binary frames preserved)
- Write queues in both directions to maintain message ordering
- Automatic SNI for wss:// backends
- Graceful close propagation between client and backend
- Errors during backend connect/handshake close the client connection cleanly

**Lifecycle**
```cpp
void start();          // non-blocking, spawns worker threads
void run();            // blocking (calls start() then ioc.run())
void stop();
bool isRunning() const;
```

### HttpClient

**HTTP Methods**
```cpp
void set_header(const std::string& header);
void clear_headers();
const std::map<std::string, std::string>& get_response_headers() const;
long get_response_code() const;

void get_stream_sync(const std::string& url, ResponseCallback cb);
void post_stream_sync(const std::string& url, const std::string& data, ResponseCallback cb);
void put_stream_sync(const std::string& url, const std::string& data, ResponseCallback cb);
void delete_stream_sync(const std::string& url, ResponseCallback cb);
void request_stream_sync(HttpMethod method, const std::string& url,
                          const std::string& data, ResponseCallback cb);

// Response size limit (防 OOM). Default 10MB (aligned with WAF); 0 = unlimited.
void set_max_response_size(size_t bytes);
size_t get_max_response_size() const;
```

**WebSocket Methods**
```cpp
bool ws_connect(const std::string& url, int timeout_ms = 5000);
bool ws_connect(const std::string& host, const std::string& port,
                const std::string& path = "/", bool ssl = false, int timeout_ms = 5000);
bool ws_send(const std::string& message);
bool ws_send_binary(const void* data, size_t size);
std::string ws_recv(int timeout_ms = 0);
bool ws_is_connected() const;
void ws_close();
std::string ws_get_host() const;
std::string ws_get_port() const;
std::string ws_get_path() const;
```

### Type Definitions

```cpp
using HttpRequestHandler = std::function<http::response<http::string_body>(
    const http::request<http::string_body>&,
    const std::unordered_map<std::string, std::string>&)>;

using MiddlewareHandler = std::function<bool(
    const http::request<http::string_body>&,
    http::response<http::string_body>&,
    const std::unordered_map<std::string, std::string>&)>;

using WebSocketMessageHandler = std::function<void(
    const std::string& clientId,
    const std::string& message,
    std::function<void(const std::string&)> sendCallback)>;

using WebSocketConnectHandler = std::function<void(
    const std::string& clientId,
    const std::string& remoteAddress)>;

using WebSocketDisconnectHandler = std::function<void(
    const std::string& clientId)>;

using WsProxyHandshakeHandler = std::function<std::string(
    const http::request<http::string_body>& upgradeReq)>;
```

## Backward Compatibility

The following type aliases are provided for existing code that used the standalone WebSocket client:

```cpp
using WebSocketClient = HttpClient;
using WebSocketClientBase = HttpClient;
```

## Version History

- **v3.4.3**: WebSocket 发送/代理队列上限（P1-6）。新增 `setMaxWsQueueSize()`（默认 1000）；`wsSend` / `onProxyReadClient` / `onProxyReadBackend` 入队前检查，超限判定慢消费者并关闭连接，防止无界队列内存耗尽。
- **v3.4.2**: 服务器读超时防 Slowloris（P1-4）。新增 `setHeaderTimeout()`（默认 10s）/`setReadTimeout()`（默认 30s）；`doRead` 两阶段读（`async_read_header` 头超时 → `async_read` 体超时），`doSSLHandshake` 覆盖握手超时，`doWsRead` 设 WS 空闲超时，对齐 WAF ReadHeaderTimeout/ReadTimeout。
- **v3.4.1**: WebSocket 单条消息大小限制（P1-3）。新增 `setMaxWsMessageSize()`/`getMaxWsMessageSize()`（默认 16MB，与 Beast 默认一致），服务端 `doWsUpgrade`/`doWssUpgrade` 与代理 `onProxyConnect`/`onProxySslHandshake` 创建流后调用 `read_message_max()`，超限消息由 Beast 在分配内存前拒绝并关闭连接，防 OOM。
- **v3.4.0**: HttpClient 响应大小限制（防 OOM）。`request_stream_sync` 改为真流式（按块回调，不二次缓冲）；新增 `set_max_response_size()`/`get_max_response_size()`（默认 10MB，对齐 WAF，0 表示不限制），超限时 `write_callback` 返回 0 中止传输并抛异常。
- **v3.3.0**: Split HttpServer into 6 modular translation units by functional area (core/lifecycle, listener, routes, request dispatch, WS management, WS session/proxy) for dramatically faster incremental builds. Added internal detail header `inc/us/detail/HttpServerImpl.hpp` for shared definitions across TU boundaries.
- **v3.2.0**: Split into two shared libraries — `libus_http.so` (server only, no curl) and `libus_httpclient.so` (HttpClient, depends on curl). HttpClient implementation moved to `src/HttpClient.cpp`; `HttpClient.hpp` is declaration-only.
- **v3.1.0**: Added WebSocket reverse proxy (`ws_proxy()`) with bidirectional async bridging, ws:// and wss:// backend support, handshake-time authorization callback.
- **v3.0.0**: Merged WebSocketServer into HttpServer and WebSocketClient into HttpClient. Single-port HTTP+WebSocket support.
- **v2.x**: Separate HttpServer, WebSocketServer, and HttpClient modules.
