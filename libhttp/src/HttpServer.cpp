#include "us/detail/HttpServerImpl.hpp"

namespace pmc {
namespace net {

// ===================== HttpServer 核心生命周期 =====================

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
    std::cout << "  WebSocket-Proxy: " << ws_proxy_handlers_.size() << " route(s)" << std::endl;
    for (const auto& entry : ws_proxy_handlers_)
        std::cout << "    WS-PROXY " << entry.path << std::endl;
    std::cout << "  WebSocket clients: " << getWsClientCount() << std::endl;
    std::cout << "Middlewares: " << middlewares_.size() << std::endl;
    std::cout << "==========================" << std::endl;
}

} // namespace net
} // namespace pmc
