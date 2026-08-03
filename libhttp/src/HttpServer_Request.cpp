#include "us/detail/HttpServerImpl.hpp"

namespace pmc {
namespace net {

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

// ---- Session HTTP 处理 ----
void HttpServer::Session::setRemoteInfo(const std::string& ip, unsigned short port) {
    remote_ip_ = ip;
    remote_port_ = port;
}

void HttpServer::Session::doSSLHandshake() {
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

void HttpServer::Session::doRead() {
    auto self = shared_from_this();
    request_ = {};
    buffer_.consume(buffer_.size());

    // 使用 request_parser 并设置可配置的请求体上限（默认 10MB，超限返回 413）
    parser_.emplace();
    parser_->body_limit(server_->max_body_size_);

    auto on_read = [self](beast::error_code ec, std::size_t) {
        if (!ec) self->request_ = self->parser_->release();
        self->handleRead(ec);
    };

    if (ssl_enabled_ && ssl_stream_) {
        http::async_read(*ssl_stream_, buffer_, *parser_, on_read);
    } else {
        http::async_read(stream_, buffer_, *parser_, on_read);
    }
}

bool HttpServer::Session::isWebSocketUpgrade(const http::request<http::string_body>& req) {
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

void HttpServer::Session::handleRead(beast::error_code ec) {
    if (!ec) {
        if (isWebSocketUpgrade(request_)) {
            std::string path = std::string(request_.target());
            auto qpos = path.find('?');
            if (qpos != std::string::npos) path = path.substr(0, qpos);

            // Check proxy routes first (higher priority)
            auto proxy_handshake = server_->findWsProxyRoute(path);
            if (proxy_handshake) {
                std::string target = proxy_handshake(request_);
                if (target.empty()) {
                    // Rejected by handshake callback → 403
                    auto resp = std::make_shared<http::response<http::string_body>>();
                    resp->version(request_.version());
                    resp->result(http::status::forbidden);
                    resp->set(http::field::content_type, "text/plain");
                    resp->set(http::field::connection, "close");
                    resp->body() = "403 Forbidden\n";
                    resp->prepare_payload();
                    doWrite(resp);
                    return;
                }
                ws_path_ = path;
                is_ws_proxy_ = true;
                ws_proxy_target_ = target;
                if (ssl_enabled_) {
                    doWssUpgrade();
                } else {
                    doWsUpgrade();
                }
                return;
            }

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
    } else if (ec == http::error::body_limit) {
        // 请求体超过上限 → 返回 413 并关闭连接
        auto resp = std::make_shared<http::response<http::string_body>>();
        resp->version(11); // request_ 在超限时未成功解析，固定 HTTP/1.1
        resp->result(http::status::payload_too_large);
        resp->set(http::field::content_type, "text/plain");
        resp->set(http::field::connection, "close");
        resp->body() = "413 Payload Too Large\n";
        resp->prepare_payload();
        doWrite(resp);
    } else if (ec != http::error::end_of_stream) {
        std::cerr << "[Read] Error from " << remote_ip_ << ":" << remote_port_
                  << ": " << ec.message() << std::endl;
    }
}

void HttpServer::Session::processRequest(http::request<http::string_body> req) {
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

void HttpServer::Session::doWrite(std::shared_ptr<http::response<http::string_body>> response) {
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

void HttpServer::Session::close() {
    beast::error_code ec;
    closeProxy(websocket::close_code::going_away);
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

void HttpServer::Session::run() {
    if (ssl_enabled_) {
        doSSLHandshake();
    } else {
        doRead();
    }
}

} // namespace net
} // namespace pmc
