// WebSocketServer.cpp - WebSocket server implementation
// Based on Boost.Beast and Boost.Asio

#include "us/WebSocketServer.hpp"
#include <iostream>
#include <memory>
#include <string>
#include <unordered_map>
#include <mutex>
#include <deque>
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <boost/asio/strand.hpp>
#include <boost/asio/ip/tcp.hpp>

namespace beast = boost::beast;
namespace websocket = beast::websocket;
namespace asio = boost::asio;
using tcp = asio::ip::tcp;

namespace pmc::net {

// Internal Session class implementation
class WebSocketServer::Session : public std::enable_shared_from_this<Session> {
public:
    Session(tcp::socket socket, WebSocketServer* server)
        : ws_(std::move(socket))
        , server_(server) {
        
        // Get remote address
        auto endpoint = ws_.next_layer().socket().remote_endpoint();
        remoteAddress_ = endpoint.address().to_string() + ":" + std::to_string(endpoint.port());
    }

    void start() {
        // Generate client ID
        if (server_) {
            clientId_ = server_->generateClientId();
        } else {
            // If server is null, use default ID
            static std::atomic<uint64_t> counter{0};
            clientId_ = "client_" + std::to_string(++counter);
        }
        
        // Asynchronously accept WebSocket handshake
        ws_.async_accept(
            beast::bind_front_handler(
                &Session::onAccept,
                shared_from_this()));
    }

    void send(const std::string& message) {
        // Add message to send queue
        bool write_in_progress = !sendQueue_.empty();
        sendQueue_.push_back(message);
        
        if (!write_in_progress) {
            // Start asynchronous write
            doWrite();
        }
    }

    const std::string& getClientId() const { return clientId_; }
    const std::string& getRemoteAddress() const { return remoteAddress_; }

private:
    websocket::stream<beast::tcp_stream> ws_;
    beast::flat_buffer buffer_;
    std::deque<std::string> sendQueue_;
    WebSocketServer* server_;
    std::string clientId_;
    std::string remoteAddress_;

    void onAccept(beast::error_code ec) {
        if (ec) {
            std::cerr << "WebSocket accept error: " << ec.message() << std::endl;
            return;
        }
        
        // Add to server client list
        if (server_) {
            server_->addClient(clientId_, shared_from_this());
            
            // Call connection handler
            if (server_->connectHandler_) {
                server_->connectHandler_(clientId_, remoteAddress_);
            }
        }
        
        // Start reading messages
        doRead();
    }

    void doRead() {
        // Asynchronously read message
        ws_.async_read(
            buffer_,
            beast::bind_front_handler(
                &Session::onRead,
                shared_from_this()));
    }

    void onRead(beast::error_code ec, std::size_t bytes_transferred) {
        if (ec == websocket::error::closed) {
            // Normal close
            if (server_) {
                server_->removeClient(clientId_);
                if (server_->disconnectHandler_) {
                    server_->disconnectHandler_(clientId_);
                }
            }
            return;
        }
        
        if (ec) {
            std::cerr << "WebSocket read error: " << ec.message() << std::endl;
            if (server_) {
                server_->removeClient(clientId_);
                if (server_->disconnectHandler_) {
                    server_->disconnectHandler_(clientId_);
                }
            }
            return;
        }
        
        // Process received message
        std::string message = beast::buffers_to_string(buffer_.data());
        buffer_.consume(bytes_transferred);
        
        if (server_) {
            // Find message handler
            for (const auto& handler : server_->messageHandlers_) {
                // Path matching logic can be added here
                handler.second(clientId_, message, [this](const std::string& response) {
                    send(response);
                });
            }
        }
        
        // Continue reading
        doRead();
    }

    void doWrite() {
        if (sendQueue_.empty()) {
            return;
        }
        
        // Get first message from queue
        auto message = sendQueue_.front();
        
        // Asynchronously write message
        ws_.async_write(
            asio::buffer(message),
            beast::bind_front_handler(
                &Session::onWrite,
                shared_from_this()));
    }

    void onWrite(beast::error_code ec, std::size_t bytes_transferred) {
        (void)bytes_transferred;
        if (ec) {
            std::cerr << "WebSocket write error: " << ec.message() << std::endl;
            if (server_) {
                server_->removeClient(clientId_);
                if (server_->disconnectHandler_) {
                    server_->disconnectHandler_(clientId_);
                }
            }
            return;
        }
        
        // Remove sent message
        sendQueue_.pop_front();
        
        // Continue sending next message in queue
        if (!sendQueue_.empty()) {
            doWrite();
        }
    }
};

// Internal Listener class implementation
class WebSocketServer::Listener : public std::enable_shared_from_this<Listener> {
public:
    static std::shared_ptr<Listener> create(asio::io_context& ioc, tcp::endpoint endpoint, WebSocketServer* server) {
        return std::shared_ptr<Listener>(new Listener(ioc, endpoint, server));
    }
    
    void run() {
        if (!acceptor_.is_open()) {
            throw std::runtime_error("Acceptor is not open");
        }
        
        doAccept();
    }

private:
    Listener(asio::io_context& ioc, tcp::endpoint endpoint, WebSocketServer* server)
        : ioc_(ioc)
        , acceptor_(asio::make_strand(ioc))
        , server_(server) {
        
        beast::error_code ec;
        
        // Open acceptor
        acceptor_.open(endpoint.protocol(), ec);
        if (ec) {
            std::cerr << "Failed to open acceptor: " << ec.message() << std::endl;
            throw std::runtime_error("Failed to open acceptor: " + ec.message());
        }
        
        // Set address reuse
        acceptor_.set_option(asio::socket_base::reuse_address(true), ec);
        if (ec) {
            std::cerr << "Failed to set reuse address: " << ec.message() << std::endl;
            throw std::runtime_error("Failed to set reuse address: " + ec.message());
        }
        
        // Bind to endpoint
        acceptor_.bind(endpoint, ec);
        if (ec) {
            std::cerr << "Failed to bind to endpoint: " << ec.message() << std::endl;
            throw std::runtime_error("Failed to bind to endpoint: " + ec.message());
        }
        
        // Start listening
        acceptor_.listen(asio::socket_base::max_listen_connections, ec);
        if (ec) {
            std::cerr << "Failed to listen: " << ec.message() << std::endl;
            throw std::runtime_error("Failed to listen: " + ec.message());
        }
    }
    
    void doAccept() {
        acceptor_.async_accept(
            asio::make_strand(ioc_),
            beast::bind_front_handler(
                &Listener::onAccept,
                shared_from_this()));
    }
    
    void onAccept(beast::error_code ec, tcp::socket socket) {
        if (ec) {
            std::cerr << "Accept error: " << ec.message() << std::endl;
        } else {
            // Create new Session to handle connection
            std::make_shared<Session>(std::move(socket), server_)->start();
        }
        
        // Continue accepting new connections
        doAccept();
    }

    asio::io_context& ioc_;
    tcp::acceptor acceptor_;
    WebSocketServer* server_;
};

WebSocketServer::WebSocketServer(unsigned short port, unsigned int threads)
    : port_(port)
    , threads_(threads)
    , impl_(std::make_unique<Impl>()) {}

WebSocketServer::~WebSocketServer() {
    stop();
}

void WebSocketServer::start() {
    if (running_) {
        return;
    }
    
    // Create listener
    tcp::endpoint endpoint(tcp::v4(), port_);
    impl_->listener_ = Listener::create(impl_->ioc_, endpoint, this);
    
    // Start listener
    impl_->listener_->run();
    
    // Start worker threads
    for (unsigned int i = 0; i < threads_; ++i) {
        impl_->threads_.emplace_back([this]() {
            impl_->ioc_.run();
        });
    }
    
    running_ = true;
    std::cout << "WebSocketServer started on port " << port_ << " with " << threads_ << " threads" << std::endl;
}

void WebSocketServer::stop() {
    if (!running_) {
        return;
    }
    
    // Stop io_context
    impl_->ioc_.stop();
    
    // Wait for all threads to finish
    for (auto& thread : impl_->threads_) {
        if (thread.joinable()) {
            thread.join();
        }
    }
    
    impl_->threads_.clear();
    running_ = false;
    std::cout << "WebSocketServer stopped" << std::endl;
}

void WebSocketServer::onMessage(const std::string& path, WebSocketMessageHandler handler) {
    messageHandlers_[path] = std::move(handler);
}

void WebSocketServer::onConnect(WebSocketConnectHandler handler) {
    connectHandler_ = std::move(handler);
}

void WebSocketServer::onDisconnect(WebSocketDisconnectHandler handler) {
    disconnectHandler_ = std::move(handler);
}

bool WebSocketServer::sendToClient(const std::string& clientId, const std::string& message) {
    std::lock_guard<std::mutex> lock(clientsMutex_);
    auto it = clients_.find(clientId);
    if (it != clients_.end()) {
        it->second->send(message);
        return true;
    }
    return false;
}

size_t WebSocketServer::broadcast(const std::string& message) {
    std::lock_guard<std::mutex> lock(clientsMutex_);
    size_t count = 0;
    for (auto& pair : clients_) {
        pair.second->send(message);
        count++;
    }
    return count;
}

void WebSocketServer::disconnectClient(const std::string& clientId) {
    std::lock_guard<std::mutex> lock(clientsMutex_);
    auto it = clients_.find(clientId);
    if (it != clients_.end()) {
        // Should close WebSocket connection here
        // Currently only removing from list
        clients_.erase(it);
    }
}

size_t WebSocketServer::getClientCount() const {
    std::lock_guard<std::mutex> lock(clientsMutex_);
    return clients_.size();
}

std::string WebSocketServer::generateClientId() const {
    static std::atomic<uint64_t> counter{0};
    return "client_" + std::to_string(++counter);
}

void WebSocketServer::addClient(const std::string& clientId, std::shared_ptr<Session> session) {
    std::lock_guard<std::mutex> lock(clientsMutex_);
    clients_[clientId] = session;
}

void WebSocketServer::removeClient(const std::string& clientId) {
    std::lock_guard<std::mutex> lock(clientsMutex_);
    clients_.erase(clientId);
}

std::shared_ptr<WebSocketServer::Session> WebSocketServer::getClient(const std::string& clientId) {
    std::lock_guard<std::mutex> lock(clientsMutex_);
    auto it = clients_.find(clientId);
    if (it != clients_.end()) {
        return it->second;
    }
    return nullptr;
}

} // namespace pmc::net