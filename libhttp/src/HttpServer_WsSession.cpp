#include "us/detail/HttpServerImpl.hpp"

namespace pmc {
namespace net {

// ===================== Session WebSocket 处理 =====================

void HttpServer::Session::wsSend(const std::string& message) {
    auto exec = stream_.get_executor();
    asio::post(exec, [self = shared_from_this(), message]() {
        // P1-6：超过队列上限，判定慢消费者，关闭连接防内存耗尽
        if (self->ws_send_queue_.size() >= self->server_->getMaxWsQueueSize()) {
            std::cerr << "[WS] ws_send_queue_ overflow (" << self->ws_send_queue_.size()
                      << " >= " << self->server_->getMaxWsQueueSize()
                      << ") from " << self->getRemoteAddress() << ", closing" << std::endl;
            self->close();
            return;
        }
        self->ws_send_queue_.push_back(message);
        if (self->ws_send_queue_.size() == 1) {
            self->doWsWrite();
        }
    });
}

void HttpServer::Session::doWsUpgrade() {
    auto self = shared_from_this();
    ws_stream_ = std::make_unique<ws_stream>(stream_);
    ws_stream_->text(true);
    // P1-3：限制单条 WS 消息大小，超限由 Beast 拒绝并关闭连接（防 OOM）
    ws_stream_->read_message_max(server_->getMaxWsMessageSize());

    ws_stream_->async_accept(
        request_,
        [self](beast::error_code ec) {
            self->onWsAccept(ec);
        });
}

void HttpServer::Session::doWssUpgrade() {
    auto self = shared_from_this();
    wss_stream_ = std::make_unique<wss_stream>(*ssl_stream_);
    wss_stream_->text(true);
    // P1-3：限制单条 WS 消息大小，超限由 Beast 拒绝并关闭连接（防 OOM）
    wss_stream_->read_message_max(server_->getMaxWsMessageSize());

    wss_stream_->async_accept(
        request_,
        [self](beast::error_code ec) {
            self->onWsAccept(ec);
        });
}

void HttpServer::Session::onWsAccept(beast::error_code ec) {
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

    if (is_ws_proxy_) {
        std::cout << "[WS-Proxy] Proxying " << ws_path_ << " -> " << ws_proxy_target_ << std::endl;
        doProxyConnect(ws_proxy_target_);
        return;
    }

    if (server_->ws_connect_handler_) {
        server_->ws_connect_handler_(client_id_, getRemoteAddress());
    }

    doWsRead();
}

void HttpServer::Session::doWsRead() {
    auto self = shared_from_this();
    ws_buffer_.consume(ws_buffer_.size());

    // P1-4：WS 读取设置空闲超时，清理长期不发消息的连接
    stream_.expires_after(server_->getReadTimeout());

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

void HttpServer::Session::onWsRead(beast::error_code ec, std::size_t bytes_transferred) {
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

void HttpServer::Session::doWsWrite() {
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

void HttpServer::Session::onWsClose() {
    if (client_id_.empty()) return;
    std::cout << "[WS] Client " << client_id_ << " disconnected" << std::endl;

    closeProxy(websocket::close_code::normal);
    server_->removeWsClient(client_id_);

    if (server_->ws_disconnect_handler_) {
        server_->ws_disconnect_handler_(client_id_);
    }
    client_id_.clear();
    is_websocket_ = false;
    is_ws_proxy_ = false;
}

// ===================== WebSocket Proxy (双向桥接) =====================

void HttpServer::Session::parseProxyUrl(const std::string& url, bool& ssl, std::string& host,
                                         std::string& port, std::string& path) {
    std::string rest;
    if (url.size() >= 6 && url.substr(0, 6) == "wss://") {
        ssl = true;
        rest = url.substr(6);
    } else if (url.size() >= 5 && url.substr(0, 5) == "ws://") {
        ssl = false;
        rest = url.substr(5);
    } else {
        ssl = false;
        rest = url;
    }

    size_t path_pos = rest.find('/');
    std::string host_port;
    if (path_pos != std::string::npos) {
        host_port = rest.substr(0, path_pos);
        path = rest.substr(path_pos);
    } else {
        host_port = rest;
        path = "/";
    }

    size_t port_pos = host_port.find(':');
    if (port_pos != std::string::npos) {
        host = host_port.substr(0, port_pos);
        port = host_port.substr(port_pos + 1);
    } else {
        host = host_port;
        port = ssl ? "443" : "80";
    }
}

void HttpServer::Session::doProxyConnect(const std::string& target_url) {
    auto self = shared_from_this();
    bool ssl;
    std::string host, port, path;
    parseProxyUrl(target_url, ssl, host, port, path);
    proxy_ssl_ = ssl;
    proxy_host_ = host;
    proxy_port_ = port;
    proxy_path_ = path;

    auto exec = stream_.get_executor();
    proxy_resolver_ = std::make_unique<tcp::resolver>(asio::make_strand(exec));

    proxy_resolver_->async_resolve(host, port,
        asio::bind_executor(exec,
            [self](beast::error_code ec, tcp::resolver::results_type results) {
                self->onProxyResolve(ec, results);
            }));
}

void HttpServer::Session::onProxyResolve(beast::error_code ec, tcp::resolver::results_type results) {
    if (ec) {
        std::cerr << "[WS-Proxy] Resolve error: " << ec.message() << std::endl;
        close();
        return;
    }

    auto self = shared_from_this();
    auto exec = stream_.get_executor();

    proxy_socket_ = std::make_unique<tcp::socket>(exec);
    proxy_tcp_stream_ = std::make_unique<beast::tcp_stream>(std::move(*proxy_socket_));
    proxy_socket_.reset();

    proxy_tcp_stream_->expires_after(std::chrono::seconds(10));
    asio::async_connect(proxy_tcp_stream_->socket(), results,
        asio::bind_executor(exec,
            [self](beast::error_code ec, tcp::endpoint ep) {
                self->onProxyConnect(ec, ep);
            }));
}

void HttpServer::Session::onProxyConnect(beast::error_code ec, tcp::endpoint /*ep*/) {
    if (ec) {
        std::cerr << "[WS-Proxy] Connect error: " << ec.message() << std::endl;
        close();
        return;
    }

    auto self = shared_from_this();
    if (proxy_ssl_) {
        proxy_ssl_ctx_ = std::make_unique<asio::ssl::context>(asio::ssl::context::tlsv12_client);
        proxy_ssl_ctx_->set_verify_mode(asio::ssl::verify_none);
        proxy_ssl_stream_ = std::make_unique<asio::ssl::stream<tcp::socket&>>(
            proxy_tcp_stream_->socket(), *proxy_ssl_ctx_);

        if (!SSL_set_tlsext_host_name(proxy_ssl_stream_->native_handle(), proxy_host_.c_str())) {
            std::cerr << "[WS-Proxy] Failed to set SNI" << std::endl;
            close();
            return;
        }

        proxy_tcp_stream_->expires_after(std::chrono::seconds(10));
        auto exec = stream_.get_executor();
        proxy_ssl_stream_->async_handshake(asio::ssl::stream_base::client,
            asio::bind_executor(exec,
                [self](beast::error_code ec) {
                    self->onProxySslHandshake(ec);
                }));
    } else {
        proxy_ws_stream_ = std::make_unique<proxy_ws_stream>(proxy_tcp_stream_->socket());
        proxy_tcp_stream_->expires_never();

        // P1-3：限制代理方向单条 WS 消息大小（防 OOM）
        proxy_ws_stream_->read_message_max(server_->getMaxWsMessageSize());

        proxy_ws_stream_->set_option(websocket::stream_base::decorator(
            [](websocket::request_type& req) {
                req.set(http::field::user_agent, "UniServe-WsProxy");
            }));

        auto exec = self->stream_.get_executor();
        proxy_ws_stream_->async_handshake(proxy_host_, proxy_path_,
            asio::bind_executor(exec,
                [self](beast::error_code ec) {
                    self->onProxyHandshake(ec);
                }));
    }
}

void HttpServer::Session::onProxySslHandshake(beast::error_code ec) {
    if (ec) {
        std::cerr << "[WS-Proxy] SSL handshake error: " << ec.message() << std::endl;
        close();
        return;
    }

    auto self = shared_from_this();
    proxy_wss_stream_ = std::make_unique<proxy_wss_stream>(*proxy_ssl_stream_);
    proxy_tcp_stream_->expires_never();

    // P1-3：限制代理方向单条 WS 消息大小（防 OOM）
    proxy_wss_stream_->read_message_max(server_->getMaxWsMessageSize());

    proxy_wss_stream_->set_option(websocket::stream_base::decorator(
        [](websocket::request_type& req) {
            req.set(http::field::user_agent, "UniServe-WsProxy");
        }));

    auto exec = stream_.get_executor();
    proxy_wss_stream_->async_handshake(proxy_host_, proxy_path_,
        asio::bind_executor(exec,
            [self](beast::error_code ec) {
                self->onProxyHandshake(ec);
            }));
}

void HttpServer::Session::onProxyHandshake(beast::error_code ec) {
    if (ec) {
        std::cerr << "[WS-Proxy] Backend handshake error: " << ec.message() << std::endl;
        close();
        return;
    }

    proxy_connected_ = true;
    proxy_closed_ = false;
    std::cout << "[WS-Proxy] Connected to backend " << proxy_host_ << ":" << proxy_port_
              << proxy_path_ << std::endl;

    if (server_->ws_connect_handler_) {
        server_->ws_connect_handler_(client_id_, getRemoteAddress());
    }

    // Start bidirectional bridging
    doProxyReadClient();
    doProxyReadBackend();
}

// ---- Client → Backend direction ----

void HttpServer::Session::doProxyReadClient() {
    if (proxy_closed_) return;
    auto self = shared_from_this();
    proxy_client_buffer_.consume(proxy_client_buffer_.size());

    auto read_handler = [self](beast::error_code ec, std::size_t bytes_transferred) {
        self->onProxyReadClient(ec, bytes_transferred);
    };

    if (ssl_enabled_ && wss_stream_) {
        wss_stream_->async_read(proxy_client_buffer_, read_handler);
    } else if (ws_stream_) {
        ws_stream_->async_read(proxy_client_buffer_, read_handler);
    }
}

void HttpServer::Session::onProxyReadClient(beast::error_code ec, std::size_t bytes_transferred) {
    if (ec) {
        if (ec != websocket::error::closed) {
            std::cerr << "[WS-Proxy] Client read error: " << ec.message() << std::endl;
        }
        closeProxy(websocket::close_code::normal);
        return;
    }

    bool is_binary = false;
    if (ssl_enabled_ && wss_stream_) {
        is_binary = wss_stream_->got_binary();
    } else if (ws_stream_) {
        is_binary = ws_stream_->got_binary();
    }

    std::string data = beast::buffers_to_string(proxy_client_buffer_.data());
    proxy_client_buffer_.consume(bytes_transferred);

    // Queue to backend
    auto exec = stream_.get_executor();
    asio::post(exec, [self = shared_from_this(), data = std::move(data), is_binary]() {
        // P1-6：超过队列上限，判定慢后端，关闭代理连接防内存耗尽
        if (self->proxy_to_backend_queue_.size() >= self->server_->getMaxWsQueueSize()) {
            std::cerr << "[WS-Proxy] proxy_to_backend_queue_ overflow ("
                      << self->proxy_to_backend_queue_.size() << ") closing" << std::endl;
            self->closeProxy(websocket::close_code::internal_error);
            return;
        }
        self->proxy_to_backend_queue_.emplace_back(std::move(data), is_binary);
        if (self->proxy_to_backend_queue_.size() == 1) {
            self->doProxyWriteBackend();
        }
    });

    doProxyReadClient();
}

void HttpServer::Session::doProxyWriteBackend() {
    if (proxy_to_backend_queue_.empty() || !proxy_connected_ || proxy_closed_) return;

    auto self = shared_from_this();
    auto& [msg, is_binary] = proxy_to_backend_queue_.front();

    auto write_handler = [self](beast::error_code ec, std::size_t) {
        if (ec) {
            std::cerr << "[WS-Proxy] Backend write error: " << ec.message() << std::endl;
            self->closeProxy(websocket::close_code::internal_error);
            return;
        }
        self->proxy_to_backend_queue_.pop_front();
        if (!self->proxy_to_backend_queue_.empty()) {
            self->doProxyWriteBackend();
        }
    };

    if (proxy_ssl_ && proxy_wss_stream_) {
        proxy_wss_stream_->binary(is_binary);
        proxy_wss_stream_->async_write(asio::buffer(msg), write_handler);
    } else if (proxy_ws_stream_) {
        proxy_ws_stream_->binary(is_binary);
        proxy_ws_stream_->async_write(asio::buffer(msg), write_handler);
    }
}

// ---- Backend → Client direction ----

void HttpServer::Session::doProxyReadBackend() {
    if (proxy_closed_) return;
    auto self = shared_from_this();
    proxy_backend_buffer_.consume(proxy_backend_buffer_.size());

    auto read_handler = [self](beast::error_code ec, std::size_t bytes_transferred) {
        self->onProxyReadBackend(ec, bytes_transferred);
    };

    if (proxy_ssl_ && proxy_wss_stream_) {
        proxy_wss_stream_->async_read(proxy_backend_buffer_, read_handler);
    } else if (proxy_ws_stream_) {
        proxy_ws_stream_->async_read(proxy_backend_buffer_, read_handler);
    }
}

void HttpServer::Session::onProxyReadBackend(beast::error_code ec, std::size_t bytes_transferred) {
    if (ec) {
        if (ec != websocket::error::closed) {
            std::cerr << "[WS-Proxy] Backend read error: " << ec.message() << std::endl;
        }
        closeProxy(websocket::close_code::normal);
        return;
    }

    bool is_binary = false;
    if (proxy_ssl_ && proxy_wss_stream_) {
        is_binary = proxy_wss_stream_->got_binary();
    } else if (proxy_ws_stream_) {
        is_binary = proxy_ws_stream_->got_binary();
    }

    std::string data = beast::buffers_to_string(proxy_backend_buffer_.data());
    proxy_backend_buffer_.consume(bytes_transferred);

    // Queue to client
    auto exec = stream_.get_executor();
    asio::post(exec, [self = shared_from_this(), data = std::move(data), is_binary]() {
        // P1-6：超过队列上限，判定慢客户端，关闭代理连接防内存耗尽
        if (self->proxy_to_client_queue_.size() >= self->server_->getMaxWsQueueSize()) {
            std::cerr << "[WS-Proxy] proxy_to_client_queue_ overflow ("
                      << self->proxy_to_client_queue_.size() << ") closing" << std::endl;
            self->closeProxy(websocket::close_code::internal_error);
            return;
        }
        self->proxy_to_client_queue_.emplace_back(std::move(data), is_binary);
        if (self->proxy_to_client_queue_.size() == 1) {
            self->doProxyWriteClient();
        }
    });

    doProxyReadBackend();
}

void HttpServer::Session::doProxyWriteClient() {
    if (proxy_to_client_queue_.empty() || !proxy_connected_ || proxy_closed_) return;

    auto self = shared_from_this();
    auto& [msg, is_binary] = proxy_to_client_queue_.front();

    auto write_handler = [self](beast::error_code ec, std::size_t) {
        if (ec) {
            std::cerr << "[WS-Proxy] Client write error: " << ec.message() << std::endl;
            self->closeProxy(websocket::close_code::internal_error);
            return;
        }
        self->proxy_to_client_queue_.pop_front();
        if (!self->proxy_to_client_queue_.empty()) {
            self->doProxyWriteClient();
        }
    };

    if (ssl_enabled_ && wss_stream_) {
        wss_stream_->binary(is_binary);
        wss_stream_->async_write(asio::buffer(msg), write_handler);
    } else if (ws_stream_) {
        ws_stream_->binary(is_binary);
        ws_stream_->async_write(asio::buffer(msg), write_handler);
    }
}

void HttpServer::Session::closeProxy(websocket::close_code code) {
    if (proxy_closed_) return;
    proxy_closed_ = true;
    proxy_connected_ = false;

    beast::error_code ec;
    // Close backend connection
    if (proxy_ssl_ && proxy_wss_stream_) {
        proxy_wss_stream_->close(code, ec);
    } else if (proxy_ws_stream_) {
        proxy_ws_stream_->close(code, ec);
    }
    if (proxy_ssl_stream_) {
        proxy_ssl_stream_->shutdown(ec);
    }
    if (proxy_tcp_stream_) {
        proxy_tcp_stream_->socket().shutdown(tcp::socket::shutdown_send, ec);
        proxy_tcp_stream_->close();
    }

    proxy_to_backend_queue_.clear();
    proxy_to_client_queue_.clear();
    proxy_ws_stream_.reset();
    proxy_wss_stream_.reset();
    proxy_ssl_stream_.reset();
    proxy_ssl_ctx_.reset();
    proxy_tcp_stream_.reset();
    proxy_resolver_.reset();
}

} // namespace net
} // namespace pmc
