#include "us/detail/HttpServerImpl.hpp"

namespace pmc {
namespace net {

// ===================== Listener 实现 =====================

void HttpServer::Listener::run() {
    if (acceptor_.is_open()) doAccept();
}

void HttpServer::Listener::close() {
    beast::error_code ec;
    if (acceptor_.is_open()) {
        acceptor_.close(ec);
        if (ec) {
            std::cerr << "[Listener] Close acceptor error: " << ec.message() << std::endl;
        }
    }
}

void HttpServer::Listener::initAcceptor(tcp::endpoint ep) {
    beast::error_code ec;
    acceptor_.open(ep.protocol(), ec);
    if (!ec) {
        acceptor_.set_option(asio::socket_base::reuse_address(true), ec);
        acceptor_.bind(ep, ec);
        if (!ec) acceptor_.listen(asio::socket_base::max_listen_connections, ec);
    }
    if (ec) throw HttpListenerException("Listener error: " + ec.message());
}

void HttpServer::Listener::doAccept() {
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
                // 继续接受下一个连接
                if (self->acceptor_.is_open()) {
                    self->doAccept();
                }
            } else {
                // acceptor 关闭或操作取消时不再递归（正常关闭流程）
                if (ec != asio::error::operation_aborted && self->acceptor_.is_open()) {
                    std::cerr << "[Listener] Accept error: " << ec.message() << std::endl;
                    self->doAccept();
                }
            }
        });
}

} // namespace net
} // namespace pmc
