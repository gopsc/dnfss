#include "us/HttpServer.hpp"
#include <boost/algorithm/string.hpp>
#include <fstream>
#include <sstream>
#include <iomanip>
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

// ===================== Session =====================
class HttpServer::Session : public std::enable_shared_from_this<Session> {
    using tcp_stream = beast::tcp_stream;
    using ssl_stream = asio::ssl::stream<tcp_stream&>;
    using ws_stream = websocket::stream<tcp_stream&>;
    using wss_stream = websocket::stream<ssl_stream&>;

public:
    Session(tcp::socket&& socket, HttpServer* server)
        : stream_(std::move(socket)), server_(server), ssl_enabled_(false),
          is_websocket_(false), client_id_("") {}

    Session(tcp::socket&& socket, asio::ssl::context& ssl_ctx, HttpServer* server)
        : stream_(std::move(socket)), server_(server), ssl_enabled_(true),
          is_websocket_(false), client_id_("") {
        ssl_stream_ = std::make_unique<ssl_stream>(stream_, ssl_ctx);
    }

    ~Session() { close(); }

    void run() {
        if (ssl_enabled_) {
            doSSLHandshake();
        } else {
            doRead();
        }
    }

    void setRemoteInfo(const std::string& ip, unsigned short port) {
        remote_ip_ = ip;
        remote_port_ = port;
    }

    void wsSend(const std::string& message) {
        auto exec = stream_.get_executor();
        asio::post(exec, [self = shared_from_this(), message]() {
            self->ws_send_queue_.push_back(message);
            if (self->ws_send_queue_.size() == 1) {
                self->doWsWrite();
            }
        });
    }

    std::string getClientId() const { return client_id_; }
    std::string getRemoteAddress() const { return remote_ip_ + ":" + std::to_string(remote_port_); }
    bool isWebSocket() const { return is_websocket_; }

private:
    void close() {
        beast::error_code ec;
        if (is_websocket_) {
            if (ssl_enabled_ && wss_stream_) {
                wss_stream_->close(websocket::close_code::normal, ec);
            } else if (ws_stream_) {
                ws_stream_->close(websocket::close_code::normal, ec);
            }
        }
        if (ssl_stream_) {
            ssl_stream_->shutdown(ec);
        }
        stream_.socket().shutdown(tcp::socket::shutdown_send, ec);
        stream_.close();
    }

    void doSSLHandshake() {
        auto self = shared_from_this();
        std::cout << "[SSL] Starting handshake for "
                  << remote_ip_ << ":" << remote_port_ << std::endl;

        ssl_stream_->async_handshake(
            asio::ssl::stream_base::server,
            [self](beast::error_code ec) {
                if (ec) {
                    std::cerr << "[SSL] Handshake error: "
                              << self->remote_ip_ << ":" << self->remote_port_
                              << " - " << ec.message()
                              << " (code=" << ec.value() << ")" << std::endl;
                    ERR_print_errors_fp(stderr);
                    return;
                }
                std::cout << "[SSL] Handshake succeeded: "
                          << self->remote_ip_ << ":" << self->remote_port_ << std::endl;
                self->doRead();
            });
    }

    void doRead() {
        auto self = shared_from_this();
        request_ = {};
        buffer_.consume(buffer_.size());

        if (ssl_enabled_ && ssl_stream_) {
            http::async_read(*ssl_stream_, buffer_, request_,
                [self](beast::error_code ec, std::size_t) {
                    self->handleRead(ec);
                });
        } else {
            http::async_read(stream_, buffer_, request_,
                [self](beast::error_code ec, std::size_t) {
                    self->handleRead(ec);
                });
        }
    }

    void handleRead(beast::error_code ec) {
        if (!ec) {
            if (isWebSocketUpgrade(request_)) {
                std::string path = std::string(request_.target());
                auto qpos = path.find('?');
                if (qpos != std::string::npos) path = path.substr(0, qpos);

                auto ws_handler = server_->findWsRoute(path);
                if (ws_handler) {
                    ws_handler_ = ws_handler;
                    ws_path_ = path;
                    if (ssl_enabled_) {
                        doWssUpgrade();
                    } else {
                        doWsUpgrade();
                    }
                    return;
                }
            }

            auto req_copy = std::move(request_);
            processRequest(std::move(req_copy));
        } else if (ec != http::error::end_of_stream) {
            std::cerr << "[Read] Error from " << remote_ip_ << ":" << remote_port_
                      << ": " << ec.message() << std::endl;
        }
    }

    bool isWebSocketUpgrade(const http::request<http::string_body>& req) {
        if (req.method() != http::verb::get) return false;

        auto upgrade = req.find(http::field::upgrade);
        if (upgrade == req.end()) return false;

        std::string upgrade_val(upgrade->value());
        boost::to_lower(upgrade_val);
        boost::trim(upgrade_val);
        if (upgrade_val != "websocket") return false;

        auto connection = req.find(http::field::connection);
        if (connection == req.end()) return false;

        std::string conn_val(connection->value());
        boost::to_lower(conn_val);
        return conn_val.find("upgrade") != std::string::npos;
    }

    void doWsUpgrade() {
        auto self = shared_from_this();
        ws_stream_ = std::make_unique<ws_stream>(stream_);
        ws_stream_->text(true);

        ws_stream_->async_accept(
            request_,
            [self](beast::error_code ec) {
                self->onWsAccept(ec);
            });
    }

    void doWssUpgrade() {
        auto self = shared_from_this();
        wss_stream_ = std::make_unique<wss_stream>(*ssl_stream_);
        wss_stream_->text(true);

        wss_stream_->async_accept(
            request_,
            [self](beast::error_code ec) {
                self->onWsAccept(ec);
            });
    }

    void onWsAccept(beast::error_code ec) {
        if (ec) {
            std::cerr << "[WS] Accept error from " << remote_ip_ << ":" << remote_port_
                      << ": " << ec.message() << std::endl;
            return;
        }

        is_websocket_ = true;
        client_id_ = server_->generateWsClientId();
        server_->addWsClient(client_id_, shared_from_this());

        std::cout << "[WS] Client " << client_id_ << " connected from "
                  << remote_ip_ << ":" << remote_port_ << " at " << ws_path_ << std::endl;

        if (server_->ws_connect_handler_) {
            server_->ws_connect_handler_(client_id_, getRemoteAddress());
        }

        doWsRead();
    }

    void doWsRead() {
        auto self = shared_from_this();
        ws_buffer_.consume(ws_buffer_.size());

        if (ssl_enabled_ && wss_stream_) {
            wss_stream_->async_read(
                ws_buffer_,
                [self](beast::error_code ec, std::size_t bytes_transferred) {
                    self->onWsRead(ec, bytes_transferred);
                });
        } else if (ws_stream_) {
            ws_stream_->async_read(
                ws_buffer_,
                [self](beast::error_code ec, std::size_t bytes_transferred) {
                    self->onWsRead(ec, bytes_transferred);
                });
        }
    }

    void onWsRead(beast::error_code ec, std::size_t bytes_transferred) {
        if (ec == websocket::error::closed) {
            onWsClose();
            return;
        }
        if (ec) {
            std::cerr << "[WS] Read error from " << client_id_ << ": " << ec.message() << std::endl;
            onWsClose();
            return;
        }

        std::string message = beast::buffers_to_string(ws_buffer_.data());
        ws_buffer_.consume(bytes_transferred);

        if (ws_handler_) {
            auto self = shared_from_this();
            ws_handler_(client_id_, message,
                [self](const std::string& response) {
                    self->wsSend(response);
                });
        }

        doWsRead();
    }

    void doWsWrite() {
        if (ws_send_queue_.empty()) return;

        auto self = shared_from_this();
        std::string& message = ws_send_queue_.front();

        auto write_handler = [self](beast::error_code ec, std::size_t) {
            if (ec) {
                std::cerr << "[WS] Write error to " << self->client_id_ << ": " << ec.message() << std::endl;
                self->onWsClose();
                return;
            }
            self->ws_send_queue_.pop_front();
            if (!self->ws_send_queue_.empty()) {
                self->doWsWrite();
            }
        };

        if (ssl_enabled_ && wss_stream_) {
            wss_stream_->text(true);
            wss_stream_->async_write(asio::buffer(message), write_handler);
        } else if (ws_stream_) {
            ws_stream_->text(true);
            ws_stream_->async_write(asio::buffer(message), write_handler);
        }
    }

    void onWsClose() {
        if (client_id_.empty()) return;
        std::cout << "[WS] Client " << client_id_ << " disconnected" << std::endl;

        server_->removeWsClient(client_id_);

        if (server_->ws_disconnect_handler_) {
            server_->ws_disconnect_handler_(client_id_);
        }
        client_id_.clear();
        is_websocket_ = false;
    }

    void processRequest(http::request<http::string_body> req) {
        try {
            auto resp = std::make_shared<http::response<http::string_body>>(
                server_->handleRequest(req));
            doWrite(resp);
        } catch (const std::exception& e) {
            std::cerr << "[Request] Handler exception: " << e.what() << std::endl;
            auto err_resp = std::make_shared<http::response<http::string_body>>();
            err_resp->version(req.version());
            err_resp->result(http::status::internal_server_error);
            err_resp->set(http::field::content_type, "text/plain");
            err_resp->body() = "500 Internal Server Error\n";
            err_resp->prepare_payload();
            doWrite(err_resp);
        }
    }

    void doWrite(std::shared_ptr<http::response<http::string_body>> response) {
        auto self = shared_from_this();
        if (ssl_enabled_ && ssl_stream_) {
            http::async_write(*ssl_stream_, *response,
                [self, response](beast::error_code ec, std::size_t) {
                    if (ec) {
                        std::cerr << "[Write] Error to " << self->remote_ip_ << ":" << self->remote_port_
                                  << ": " << ec.message() << std::endl;
                        return;
                    }
                    if (!response->keep_alive()) self->close();
                    else                         self->doRead();
                });
        } else {
            http::async_write(stream_, *response,
                [self, response](beast::error_code ec, std::size_t) {
                    if (ec) {
                        std::cerr << "[Write] Error to " << self->remote_ip_ << ":" << self->remote_port_
                                  << ": " << ec.message() << std::endl;
                        return;
                    }
                    if (!response->keep_alive()) self->close();
                    else                         self->doRead();
                });
        }
    }

    tcp_stream stream_;
    std::unique_ptr<ssl_stream> ssl_stream_;
    std::unique_ptr<ws_stream>  ws_stream_;
    std::unique_ptr<wss_stream> wss_stream_;

    beast::flat_buffer buffer_;
    beast::flat_buffer ws_buffer_;
    http::request<http::string_body> request_;

    HttpServer* server_;
    bool ssl_enabled_;

    std::string remote_ip_ = "unknown";
    unsigned short remote_port_ = 0;

    bool is_websocket_;
    std::string client_id_;
    std::string ws_path_;
    WebSocketMessageHandler ws_handler_;
    std::deque<std::string> ws_send_queue_;
};

// ===================== Listener =====================
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

    void run() {
        if (acceptor_.is_open()) doAccept();
    }

private:
    void initAcceptor(tcp::endpoint ep) {
        beast::error_code ec;
        acceptor_.open(ep.protocol(), ec);
        if (!ec) {
            acceptor_.set_option(asio::socket_base::reuse_address(true), ec);
            acceptor_.bind(ep, ec);
            if (!ec) acceptor_.listen(asio::socket_base::max_listen_connections, ec);
        }
        if (ec) throw HttpListenerException("Listener error: " + ec.message());
    }

    void doAccept() {
        auto self = shared_from_this();
        acceptor_.async_accept(
            asio::make_strand(ioc_),
            [self](beast::error_code ec, tcp::socket socket) {
                if (!ec) {
                    auto ep = socket.remote_endpoint(ec);
                    std::string ip = ec ? "unknown" : ep.address().to_string();
                    unsigned short port = ec ? 0 : ep.port();
                    std::cout << "[Listener] New connection from " << ip << ":" << port << std::endl;

                    std::shared_ptr<Session> session;
                    if (self->ssl_enabled_ && self->ssl_ctx_) {
                        session = std::make_shared<Session>(std::move(socket), *self->ssl_ctx_, self->server_);
                    } else {
                        session = std::make_shared<Session>(std::move(socket), self->server_);
                    }
                    session->setRemoteInfo(ip, port);
                    self->server_->addSession(session);
                    session->run();
                } else {
                    std::cerr << "[Listener] Accept error: " << ec.message() << std::endl;
                }
                self->doAccept();
            });
    }

    asio::io_context& ioc_;
    tcp::acceptor acceptor_;
    asio::ssl::context* ssl_ctx_ = nullptr;
    HttpServer* server_;
    bool ssl_enabled_;
};

// ===================== HttpServer 实现 =====================

HttpServer::HttpServer(const std::string& address, unsigned short port, unsigned int threads)
    : address_(address), port_(port), threads_(threads), ssl_enabled_(false),
      ioc_(std::make_unique<asio::io_context>(threads)) {
    if (!ioc_) throw CreateObjectFailed("Failed to create io_context");
}

HttpServer::HttpServer(const std::string& address, unsigned short port,
                       const std::string& cert_file, const std::string& key_file,
                       unsigned int threads)
    : address_(address), port_(port), threads_(threads), ssl_enabled_(true),
      cert_file_(cert_file), key_file_(key_file),
      ioc_(std::make_unique<asio::io_context>(threads)) {
    if (!ioc_) throw CreateObjectFailed("Failed to create io_context");
    initSSLContext();
}

HttpServer::~HttpServer() { stop(); }

void HttpServer::setPrivateKeyPassword(const std::string& password) {
    private_key_password_ = password;
}

void HttpServer::initSSLContext() {
    if (!ssl_enabled_) return;
    try {
        ssl_ctx_ = std::make_unique<asio::ssl::context>(asio::ssl::context::tls);
        ssl_ctx_->set_options(
            asio::ssl::context::default_workarounds |
            asio::ssl::context::no_sslv2 |
            asio::ssl::context::no_sslv3 |
            asio::ssl::context::single_dh_use);

        SSL_CTX_set_cipher_list(ssl_ctx_->native_handle(),
            "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS");

        if (!private_key_password_.empty()) {
            ssl_ctx_->set_password_callback(
                [this](std::size_t, asio::ssl::context::password_purpose) {
                    return private_key_password_;
                });
        }

        std::cout << "[SSL] Loading certificate chain: " << cert_file_ << std::endl;
        ssl_ctx_->use_certificate_chain_file(cert_file_);

        std::cout << "[SSL] Loading private key: " << key_file_ << std::endl;
        ssl_ctx_->use_private_key_file(key_file_, asio::ssl::context::pem);

        std::cout << "[SSL] SSL context ready." << std::endl;
    } catch (const std::exception& e) {
        std::cerr << "[SSL] Init failed: " << e.what() << std::endl;
        ERR_print_errors_fp(stderr);
        throw SSLInitException("Failed to initialize SSL context");
    }
}

void HttpServer::start() {
    if (running_) return;
    try {
        auto addr = asio::ip::make_address(address_);
        tcp::endpoint ep(addr, port_);
        if (ssl_enabled_ && ssl_ctx_) {
            listener_ = std::make_shared<Listener>(*ioc_, ep, *ssl_ctx_, this);
        } else {
            listener_ = std::make_shared<Listener>(*ioc_, ep, this);
        }
        listener_->run();
        running_ = true;

        for (unsigned i = 0; i < threads_; ++i) {
            worker_threads_.emplace_back([this]() {
                try { ioc_->run(); }
                catch (const std::exception& e) {
                    std::cerr << "[Thread] Exception: " << e.what() << std::endl;
                }
            });
        }
        std::cout << (ssl_enabled_ ? "HTTPS" : "HTTP")
                  << " Server started on " << address_ << ":" << port_
                  << " with " << threads_ << " threads";
        if (!ws_handlers_.empty()) {
            std::cout << " (WebSocket enabled, " << ws_handlers_.size() << " WS route(s))";
        }
        std::cout << "\n";
    } catch (const std::exception& e) {
        std::cerr << "[Start] Failed: " << e.what() << std::endl;
        running_ = false;
    }
}

void HttpServer::stop() {
    if (!running_) return;
    running_ = false;

    {
        std::lock_guard<std::mutex> lock(ws_clients_mutex_);
        ws_clients_.clear();
    }

    closeAllSessions();
    if (ioc_) ioc_->stop();
    for (auto& t : worker_threads_) if (t.joinable()) t.join();
    worker_threads_.clear();
    listener_.reset();
    std::cout << (ssl_enabled_ ? "HTTPS" : "HTTP") << " Server stopped\n";
}

void HttpServer::run() {
    start();
    if (ioc_) ioc_->run();
}

bool HttpServer::isRunning() const { return running_; }
unsigned short HttpServer::getPort() const { return port_; }
std::string HttpServer::getAddress() const { return address_; }
bool HttpServer::isSSLEnabled() const { return ssl_enabled_; }

// ---- HTTP 路由注册 ----
void HttpServer::get(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    get_handlers_.push_back(entry);
}

void HttpServer::post(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    post_handlers_.push_back(entry);
}

void HttpServer::put(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    put_handlers_.push_back(entry);
}

void HttpServer::del(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    delete_handlers_.push_back(entry);
}

void HttpServer::options(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    options_handlers_.push_back(entry);
}

void HttpServer::use(MiddlewareHandler m) {
    middlewares_.push_back(m);
}

void HttpServer::setStaticDirectory(const std::string& p) {
    static_directory_ = p;
}

// ---- WebSocket 路由注册 ----
void HttpServer::ws(const std::string& path, WebSocketMessageHandler handler) {
    WsRouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    ws_handlers_.push_back(entry);
}

void HttpServer::onWsConnect(WebSocketConnectHandler handler) {
    ws_connect_handler_ = handler;
}

void HttpServer::onWsDisconnect(WebSocketDisconnectHandler handler) {
    ws_disconnect_handler_ = handler;
}

bool HttpServer::sendToWsClient(const std::string& clientId, const std::string& message) {
    std::shared_ptr<Session> session;
    {
        std::lock_guard<std::mutex> lock(ws_clients_mutex_);
        auto it = ws_clients_.find(clientId);
        if (it != ws_clients_.end()) {
            session = it->second;
        }
    }
    if (session) {
        session->wsSend(message);
        return true;
    }
    return false;
}

size_t HttpServer::broadcastWs(const std::string& message) {
    std::vector<std::shared_ptr<Session>> clients;
    {
        std::lock_guard<std::mutex> lock(ws_clients_mutex_);
        for (auto& pair : ws_clients_) {
            clients.push_back(pair.second);
        }
    }
    for (auto& session : clients) {
        session->wsSend(message);
    }
    return clients.size();
}

void HttpServer::disconnectWsClient(const std::string& clientId) {
    std::lock_guard<std::mutex> lock(ws_clients_mutex_);
    ws_clients_.erase(clientId);
}

size_t HttpServer::getWsClientCount() const {
    std::lock_guard<std::mutex> lock(ws_clients_mutex_);
    return ws_clients_.size();
}

// ---- 通配符转换 ----
std::regex HttpServer::wildcardToRegex(const std::string& pattern) {
    std::string r;
    r.reserve(pattern.size() * 2);
    for (char c : pattern) {
        if (c == '*') r += ".*";
        else if (c == '?') r += ".";
        else if (c == '.' || c == '+' || c == '(' || c == ')' || c == '[' || c == ']' ||
                 c == '{' || c == '}' || c == '^' || c == '$' || c == '|' || c == '\\') {
            r += '\\';
            r += c;
        } else r += c;
    }
    return std::regex(r, std::regex::icase);
}

bool HttpServer::matchPath(const std::string& path, const std::regex& pattern) {
    return std::regex_match(path, pattern);
}

// ---- 查询参数解析 ----
std::unordered_map<std::string, std::string> HttpServer::parseQueryParams(const std::string& query) {
    std::unordered_map<std::string, std::string> params;
    if (query.empty()) return params;

    std::vector<std::string> pairs;
    boost::split(pairs, query, boost::is_any_of("&"));
    for (const auto& pair : pairs) {
        std::vector<std::string> kv;
        boost::split(kv, pair, boost::is_any_of("="));
        if (kv.size() == 2) params[kv[0]] = kv[1];
        else if (kv.size() == 1) params[kv[0]] = "";
    }
    return params;
}

// ---- 路由查找 ----
HttpRequestHandler HttpServer::findRoute(const std::string& method, const std::string& path) {
    const std::vector<RouteEntry>* handlers = nullptr;
    if (method == "GET") handlers = &get_handlers_;
    else if (method == "POST") handlers = &post_handlers_;
    else if (method == "PUT") handlers = &put_handlers_;
    else if (method == "DELETE") handlers = &delete_handlers_;
    else if (method == "OPTIONS") handlers = &options_handlers_;
    else return nullptr;

    for (const auto& e : *handlers) {
        if (!e.is_wildcard && e.path == path) return e.handler;
    }
    for (const auto& e : *handlers) {
        if (e.is_wildcard && matchPath(path, e.pattern)) return e.handler;
    }
    return nullptr;
}

bool HttpServer::routeExists(const std::string& method, const std::string& path) {
    return findRoute(method, path) != nullptr;
}

WebSocketMessageHandler HttpServer::findWsRoute(const std::string& path) {
    for (const auto& e : ws_handlers_) {
        if (e.path == path) return e.handler;
    }
    return nullptr;
}

// ---- WebSocket 客户端管理 ----
std::string HttpServer::generateWsClientId() {
    return "ws_client_" + std::to_string(++ws_client_counter_);
}

void HttpServer::addWsClient(const std::string& clientId, std::shared_ptr<Session> session) {
    std::lock_guard<std::mutex> lock(ws_clients_mutex_);
    ws_clients_[clientId] = session;
}

void HttpServer::removeWsClient(const std::string& clientId) {
    std::lock_guard<std::mutex> lock(ws_clients_mutex_);
    ws_clients_.erase(clientId);
}

std::shared_ptr<HttpServer::Session> HttpServer::getWsClient(const std::string& clientId) {
    std::lock_guard<std::mutex> lock(ws_clients_mutex_);
    auto it = ws_clients_.find(clientId);
    if (it != ws_clients_.end()) return it->second;
    return nullptr;
}

// ---- 请求处理核心 ----
http::response<http::string_body> HttpServer::handleRequest(const http::request<http::string_body>& req) {
    std::string target_path(req.target().data(), req.target().size());
    std::string query_string;
    auto pos = target_path.find('?');
    if (pos != std::string::npos) {
        query_string = target_path.substr(pos + 1);
        target_path = target_path.substr(0, pos);
    }
    auto params = parseQueryParams(query_string);

    http::response<http::string_body> res;
    res.version(req.version());
    res.set(http::field::server, "Boost.Beast HttpServer");
    res.set(http::field::content_type, "text/plain");
    res.keep_alive(req.keep_alive());

    bool middleware_handled = false;
    for (auto& m : middlewares_) {
        if (!m(req, res, params)) {
            middleware_handled = true;
            break;
        }
    }
    if (middleware_handled) {
        res.prepare_payload();
        return res;
    }

    std::string method = http::to_string(req.method());

    if (method == "OPTIONS") {
        auto handler = findRoute("OPTIONS", target_path);
        if (handler) {
            try {
                return handler(req, params);
            } catch (const std::exception& e) {
                std::cerr << "[Handler] Exception: " << e.what() << std::endl;
                res.result(http::status::internal_server_error);
                res.body() = "500 Internal Server Error\n";
                res.prepare_payload();
                return res;
            }
        }

        auto origin = req.find(http::field::origin);
        if (origin != req.end()) {
            std::vector<std::string> all_methods = {"GET", "POST", "PUT", "DELETE", "OPTIONS"};
            std::vector<std::string> allowed_methods;

            for (const auto& m : all_methods) {
                if (m == "OPTIONS" || routeExists(m, target_path)) {
                    allowed_methods.push_back(m);
                }
            }

            if (!allowed_methods.empty() || findWsRoute(target_path)) {
                if (allowed_methods.empty()) allowed_methods.push_back("GET");

                std::string allow_methods;
                for (size_t i = 0; i < allowed_methods.size(); ++i) {
                    if (i > 0) allow_methods += ", ";
                    allow_methods += allowed_methods[i];
                }

                res.result(http::status::ok);
                res.set(http::field::allow, allow_methods);
                res.set(http::field::access_control_allow_origin,
                       std::string(origin->value()));
                res.set(http::field::access_control_allow_methods, allow_methods);
                res.set(http::field::access_control_allow_headers,
                       "Content-Type, Authorization, X-Requested-With, Accept, Origin");
                res.set(http::field::access_control_max_age, "86400");
                res.body() = "";
                res.content_length(0);
                res.prepare_payload();
                return res;
            }
        }

        res.result(http::status::not_found);
        res.body() = "404 Not Found\n";
        res.prepare_payload();
        return res;
    }

    auto handler = findRoute(method, target_path);

    if (!handler && !static_directory_.empty()) {
        std::string file_path = static_directory_ + target_path;
        std::ifstream file(file_path, std::ios::binary);
        if (file.is_open()) {
            std::stringstream buffer;
            buffer << file.rdbuf();
            res.result(http::status::ok);
            res.body() = buffer.str();

            if (boost::algorithm::ends_with(file_path, ".html"))
                res.set(http::field::content_type, "text/html");
            else if (boost::algorithm::ends_with(file_path, ".css"))
                res.set(http::field::content_type, "text/css");
            else if (boost::algorithm::ends_with(file_path, ".js"))
                res.set(http::field::content_type, "application/javascript");
            else if (boost::algorithm::ends_with(file_path, ".json"))
                res.set(http::field::content_type, "application/json");
            else if (boost::algorithm::ends_with(file_path, ".png"))
                res.set(http::field::content_type, "image/png");
            else if (boost::algorithm::ends_with(file_path, ".jpg") ||
                     boost::algorithm::ends_with(file_path, ".jpeg"))
                res.set(http::field::content_type, "image/jpeg");
            res.prepare_payload();
            return res;
        }
    }

    if (handler) {
        try {
            return handler(req, params);
        } catch (const std::exception& e) {
            std::cerr << "[Handler] Exception: " << e.what() << std::endl;
            res.result(http::status::internal_server_error);
            res.body() = "500 Internal Server Error\n";
            res.prepare_payload();
            return res;
        }
    }

    res.result(http::status::not_found);
    res.body() = "404 Not Found\n";
    res.prepare_payload();
    return res;
}

void HttpServer::showStatus() const {
    std::cout << "=== " << (ssl_enabled_ ? "HTTPS" : "HTTP") << " Server Status ===" << std::endl;
    std::cout << "Protocol: " << (ssl_enabled_ ? "HTTPS (SSL/TLS)" : "HTTP") << std::endl;
    std::cout << "Address: " << address_ << std::endl;
    std::cout << "Port: " << port_ << std::endl;
    std::cout << "Threads: " << threads_ << std::endl;
    std::cout << "Running: " << (running_ ? "Yes" : "No") << std::endl;
    if (ssl_enabled_) {
        std::cout << "Certificate: " << cert_file_ << std::endl;
        std::cout << "Private Key: " << key_file_ << std::endl;
    }
    std::cout << "Static Directory: " << (static_directory_.empty() ? "Not set" : static_directory_) << std::endl;
    std::cout << "Registered Routes:" << std::endl;
    std::cout << "  GET: " << get_handlers_.size() << " handler(s)" << std::endl;
    for (const auto& entry : get_handlers_)
        std::cout << "    GET " << entry.path << (entry.is_wildcard ? " (wildcard)" : "") << std::endl;
    std::cout << "  POST: " << post_handlers_.size() << " handler(s)" << std::endl;
    for (const auto& entry : post_handlers_)
        std::cout << "    POST " << entry.path << (entry.is_wildcard ? " (wildcard)" : "") << std::endl;
    std::cout << "  PUT: " << put_handlers_.size() << " handler(s)" << std::endl;
    for (const auto& entry : put_handlers_)
        std::cout << "    PUT " << entry.path << (entry.is_wildcard ? " (wildcard)" : "") << std::endl;
    std::cout << "  DELETE: " << delete_handlers_.size() << " handler(s)" << std::endl;
    for (const auto& entry : delete_handlers_)
        std::cout << "    DELETE " << entry.path << (entry.is_wildcard ? " (wildcard)" : "") << std::endl;
    std::cout << "  OPTIONS: " << options_handlers_.size() << " handler(s)" << std::endl;
    for (const auto& entry : options_handlers_)
        std::cout << "    OPTIONS " << entry.path << (entry.is_wildcard ? " (wildcard)" : "") << std::endl;
    std::cout << "  WebSocket: " << ws_handlers_.size() << " route(s)" << std::endl;
    for (const auto& entry : ws_handlers_)
        std::cout << "    WS " << entry.path << std::endl;
    std::cout << "  WebSocket clients: " << getWsClientCount() << std::endl;
    std::cout << "Middlewares: " << middlewares_.size() << std::endl;
    std::cout << "==========================" << std::endl;
}

// ---- 会话管理 ----
void HttpServer::addSession(const std::shared_ptr<Session>& session) {
    std::lock_guard<std::mutex> lock(sessions_mutex_);
    sessions_.erase(std::remove_if(sessions_.begin(), sessions_.end(),
        [](const std::weak_ptr<Session>& wp) { return wp.expired(); }), sessions_.end());
    sessions_.push_back(session);
}

void HttpServer::removeSession(const Session* session) {
    std::lock_guard<std::mutex> lock(sessions_mutex_);
    sessions_.erase(std::remove_if(sessions_.begin(), sessions_.end(),
        [session](const std::weak_ptr<Session>& wp) {
            auto sp = wp.lock();
            return !sp || sp.get() == session;
        }), sessions_.end());
}

void HttpServer::closeAllSessions() {
    std::lock_guard<std::mutex> lock(sessions_mutex_);
    sessions_.clear();
}

// ---- 调试工具 ----
void HttpServer::debug::print_all_header_fields(const http::request<http::string_body>& req) {
    for (auto const& field : req) {
        std::cout << "Name: " << field.name_string()
                  << ", Value: " << field.value() << std::endl;
    }
}

} // namespace net
} // namespace pmc
