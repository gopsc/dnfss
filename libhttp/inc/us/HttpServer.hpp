#ifndef HTTPSERVER_HPP
#define HTTPSERVER_HPP

#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <boost/beast/http.hpp>
#include <boost/beast/websocket.hpp>
#include <boost/beast/version.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/ssl.hpp>
#include <boost/asio/strand.hpp>
#include <boost/config.hpp>
#include <memory>
#include <string>
#include <functional>
#include <unordered_map>
#include <vector>
#include <iostream>
#include <thread>
#include <atomic>
#include <mutex>
#include <regex>
#include <deque>

namespace pmc {
namespace net {

namespace beast = boost::beast;
namespace http  = beast::http;
namespace websocket = beast::websocket;
namespace asio  = boost::asio;
using tcp = boost::asio::ip::tcp;

using HttpRequestHandler = std::function<http::response<http::string_body>(
    const http::request<http::string_body>&,
    const std::unordered_map<std::string, std::string>&)>;

using MiddlewareHandler = std::function<bool(
    const http::request<http::string_body>&,
    http::response<http::string_body>&,
    const std::unordered_map<std::string, std::string>&)>;

// WebSocket 回调类型
using WebSocketMessageHandler = std::function<void(
    const std::string& clientId,
    const std::string& message,
    std::function<void(const std::string&)> sendCallback)>;

using WebSocketConnectHandler = std::function<void(
    const std::string& clientId,
    const std::string& remoteAddress)>;

using WebSocketDisconnectHandler = std::function<void(
    const std::string& clientId)>;

// WebSocket 代理：握手回调接收 HTTP upgrade 请求，返回目标 ws:// 或 wss:// URL
// 返回空字符串表示拒绝（返回 403）
using WsProxyHandshakeHandler = std::function<std::string(
    const http::request<http::string_body>& upgradeReq)>;

struct RouteEntry {
    std::string path;
    std::regex pattern;
    HttpRequestHandler handler;
    bool is_wildcard;
};

struct WsRouteEntry {
    std::string path;
    WebSocketMessageHandler handler;
};

struct WsProxyRouteEntry {
    std::string path;
    WsProxyHandshakeHandler handshake;
};

class HttpServer {
public:
    explicit HttpServer(const std::string& address, unsigned short port,
                        unsigned int threads = 1);
    explicit HttpServer(const std::string& address, unsigned short port,
                        const std::string& cert_file, const std::string& key_file,
                        unsigned int threads = 1);
    ~HttpServer();

    void start();
    void stop();

    void get(const std::string& path, HttpRequestHandler handler);
    void post(const std::string& path, HttpRequestHandler handler);
    void put(const std::string& path, HttpRequestHandler handler);
    void del(const std::string& path, HttpRequestHandler handler);
    void options(const std::string& path, HttpRequestHandler handler);
    void use(MiddlewareHandler middleware);
    void setStaticDirectory(const std::string& path);

    // WebSocket 接口
    void ws(const std::string& path, WebSocketMessageHandler handler);
    void ws_proxy(const std::string& path, WsProxyHandshakeHandler handshake);
    void onWsConnect(WebSocketConnectHandler handler);
    void onWsDisconnect(WebSocketDisconnectHandler handler);
    bool sendToWsClient(const std::string& clientId, const std::string& message);
    size_t broadcastWs(const std::string& message);
    void disconnectWsClient(const std::string& clientId);
    size_t getWsClientCount() const;

    bool isRunning() const;
    unsigned short getPort() const;
    std::string getAddress() const;
    bool isSSLEnabled() const;

    void run();
    void showStatus() const;

    void setPrivateKeyPassword(const std::string& password);

    // 请求体大小上限（字节）。默认 10MB，与 WAF 对齐；超限返回 413。
    void setMaxBodySize(std::size_t bytes) { max_body_size_ = bytes; }
    std::size_t getMaxBodySize() const { return max_body_size_; }

    class debug {
    public:
        static void print_all_header_fields(const http::request<http::string_body>& req);
    };

private:
    class Session;
    class Listener;

    std::string address_;
    unsigned short port_;
    unsigned int threads_;
    std::atomic<bool> running_{false};

    std::size_t max_body_size_ = 10 * 1024 * 1024; // 默认 10MB,与 WAF 对齐

    bool ssl_enabled_{false};
    std::string cert_file_;
    std::string key_file_;
    std::string private_key_password_;

    std::unique_ptr<asio::ssl::context> ssl_ctx_;
    std::unique_ptr<asio::io_context> ioc_;

    std::shared_ptr<Listener> listener_;
    std::vector<std::thread> worker_threads_;

    std::vector<RouteEntry> get_handlers_;
    std::vector<RouteEntry> post_handlers_;
    std::vector<RouteEntry> put_handlers_;
    std::vector<RouteEntry> delete_handlers_;
    std::vector<RouteEntry> options_handlers_;
    std::vector<MiddlewareHandler> middlewares_;
    std::string static_directory_;

    // WebSocket 路由和处理
    std::vector<WsRouteEntry> ws_handlers_;
    std::vector<WsProxyRouteEntry> ws_proxy_handlers_;
    WebSocketConnectHandler ws_connect_handler_;
    WebSocketDisconnectHandler ws_disconnect_handler_;
    std::unordered_map<std::string, std::shared_ptr<Session>> ws_clients_;
    mutable std::mutex ws_clients_mutex_;
    std::atomic<uint64_t> ws_client_counter_{0};

    std::vector<std::weak_ptr<Session>> sessions_;
    std::mutex sessions_mutex_;

    std::unordered_map<std::string, std::string> parseQueryParams(const std::string& query);
    std::regex wildcardToRegex(const std::string& pattern);
    bool matchPath(const std::string& path, const std::regex& pattern);
    http::response<http::string_body> handleRequest(const http::request<http::string_body>& req);
    HttpRequestHandler findRoute(const std::string& method, const std::string& path);
    bool routeExists(const std::string& method, const std::string& path);
    WebSocketMessageHandler findWsRoute(const std::string& path);
    WsProxyHandshakeHandler findWsProxyRoute(const std::string& path);

    // WebSocket 客户端管理
    std::string generateWsClientId();
    void addWsClient(const std::string& clientId, std::shared_ptr<Session> session);
    void removeWsClient(const std::string& clientId);
    std::shared_ptr<Session> getWsClient(const std::string& clientId);

    void addSession(const std::shared_ptr<Session>& session);
    void removeSession(const Session* session);
    void closeAllSessions();
    void initSSLContext();

    friend class Session;
};

} // namespace net
} // namespace pmc

#endif // HTTPSERVER_HPP
