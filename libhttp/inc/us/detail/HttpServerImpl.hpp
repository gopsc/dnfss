#ifndef HTTPSERVER_IMPL_HPP
#define HTTPSERVER_IMPL_HPP

#include "us/HttpServer.hpp"
#include <boost/algorithm/string.hpp>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <optional>
#include <openssl/err.h>
#include <openssl/ssl.h>
#include <boost/beast/websocket/ssl.hpp>

namespace pmc {
namespace net {

// ===================== 自定义异常 =====================
class HttpServerException : public std::runtime_error {
public:
    explicit HttpServerException(const std::string& msg) : runtime_error(msg) {}
};
class HttpListenerException : public HttpServerException {
public:
    explicit HttpListenerException(const std::string& msg) : HttpServerException(msg) {}
};
class SSLInitException : public HttpServerException {
public:
    explicit SSLInitException(const std::string& msg) : HttpServerException(msg) {}
};
class CreateObjectFailed : public HttpServerException {
public:
    explicit CreateObjectFailed(const std::string& msg) : HttpServerException(msg) {}
};

// ===================== Session (完整定义) =====================
class HttpServer::Session : public std::enable_shared_from_this<Session> {
    using tcp_stream = beast::tcp_stream;
    using ssl_stream = asio::ssl::stream<tcp_stream&>;
    using ws_stream = websocket::stream<tcp_stream&>;
    using wss_stream = websocket::stream<ssl_stream&>;

    // Proxy outbound types
    using proxy_ws_stream = websocket::stream<tcp::socket&>;
    using proxy_wss_stream = websocket::stream<asio::ssl::stream<tcp::socket&>&>;

public:
    Session(tcp::socket&& socket, HttpServer* server)
        : stream_(std::move(socket)), server_(server), ssl_enabled_(false),
          is_websocket_(false), is_ws_proxy_(false), client_id_("") {}

    Session(tcp::socket&& socket, asio::ssl::context& ssl_ctx, HttpServer* server)
        : stream_(std::move(socket)), server_(server), ssl_enabled_(true),
          is_websocket_(false), is_ws_proxy_(false), client_id_("") {
        ssl_stream_ = std::make_unique<ssl_stream>(stream_, ssl_ctx);
    }

    ~Session() { close(); }

    void run();
    void setRemoteInfo(const std::string& ip, unsigned short port);
    void wsSend(const std::string& message);
    std::string getClientId() const { return client_id_; }
    std::string getRemoteAddress() const { return remote_ip_ + ":" + std::to_string(remote_port_); }
    bool isWebSocket() const { return is_websocket_; }

private:
    void close();
    void doSSLHandshake();
    void doRead();
    void handleRead(beast::error_code ec);
    bool isWebSocketUpgrade(const http::request<http::string_body>& req);
    void doWsUpgrade();
    void doWssUpgrade();
    void onWsAccept(beast::error_code ec);

    // Regular WebSocket
    void doWsRead();
    void onWsRead(beast::error_code ec, std::size_t bytes_transferred);
    void doWsWrite();
    void onWsClose();

    // WebSocket Proxy
    static void parseProxyUrl(const std::string& url, bool& ssl, std::string& host,
                               std::string& port, std::string& path);
    void doProxyConnect(const std::string& target_url);
    void onProxyResolve(beast::error_code ec, tcp::resolver::results_type results);
    void onProxyConnect(beast::error_code ec, tcp::endpoint ep);
    void onProxySslHandshake(beast::error_code ec);
    void onProxyHandshake(beast::error_code ec);

    // Client → Backend
    void doProxyReadClient();
    void onProxyReadClient(beast::error_code ec, std::size_t bytes_transferred);
    void doProxyWriteBackend();

    // Backend → Client
    void doProxyReadBackend();
    void onProxyReadBackend(beast::error_code ec, std::size_t bytes_transferred);
    void doProxyWriteClient();

    void closeProxy(websocket::close_code code);

    // Regular HTTP
    void processRequest(http::request<http::string_body> req);
    void doWrite(std::shared_ptr<http::response<http::string_body>> response);

    // Client stream (inbound)
    tcp_stream stream_;
    std::unique_ptr<ssl_stream> ssl_stream_;
    std::unique_ptr<ws_stream>  ws_stream_;
    std::unique_ptr<wss_stream> wss_stream_;

    beast::flat_buffer buffer_;
    beast::flat_buffer ws_buffer_;
    http::request<http::string_body> request_;
    std::optional<http::request_parser<http::string_body>> parser_;

    HttpServer* server_;
    bool ssl_enabled_;

    std::string remote_ip_ = "unknown";
    unsigned short remote_port_ = 0;

    bool is_websocket_;
    bool is_ws_proxy_;
    std::string client_id_;
    std::string ws_path_;
    WebSocketMessageHandler ws_handler_;
    std::deque<std::string> ws_send_queue_;

    // Proxy state (outbound to backend)
    std::string ws_proxy_target_;
    bool proxy_ssl_ = false;
    bool proxy_connected_ = false;
    bool proxy_closed_ = false;
    std::string proxy_host_, proxy_port_, proxy_path_;

    std::unique_ptr<tcp::resolver> proxy_resolver_;
    std::unique_ptr<tcp::socket> proxy_socket_;
    std::unique_ptr<beast::tcp_stream> proxy_tcp_stream_;
    std::unique_ptr<asio::ssl::context> proxy_ssl_ctx_;
    std::unique_ptr<asio::ssl::stream<tcp::socket&>> proxy_ssl_stream_;
    std::unique_ptr<proxy_ws_stream>  proxy_ws_stream_;
    std::unique_ptr<proxy_wss_stream> proxy_wss_stream_;

    beast::flat_buffer proxy_client_buffer_;
    beast::flat_buffer proxy_backend_buffer_;
    std::deque<std::pair<std::string, bool>> proxy_to_backend_queue_;
    std::deque<std::pair<std::string, bool>> proxy_to_client_queue_;
};

// ===================== Listener (完整定义) =====================
class HttpServer::Listener : public std::enable_shared_from_this<Listener> {
public:
    Listener(asio::io_context& ioc, tcp::endpoint endpoint, HttpServer* server)
        : ioc_(ioc), acceptor_(ioc), server_(server), ssl_enabled_(false) {
        initAcceptor(endpoint);
    }

    Listener(asio::io_context& ioc, tcp::endpoint endpoint,
             asio::ssl::context& ssl_ctx, HttpServer* server)
        : ioc_(ioc), acceptor_(ioc), ssl_ctx_(&ssl_ctx), server_(server), ssl_enabled_(true) {
        initAcceptor(endpoint);
    }

    void run();

private:
    void initAcceptor(tcp::endpoint ep);
    void doAccept();

    asio::io_context& ioc_;
    tcp::acceptor acceptor_;
    asio::ssl::context* ssl_ctx_ = nullptr;
    HttpServer* server_;
    bool ssl_enabled_;
};

} // namespace net
} // namespace pmc

#endif // HTTPSERVER_IMPL_HPP
