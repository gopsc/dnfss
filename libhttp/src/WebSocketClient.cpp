#include "us/WebSocketClient.hpp"
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <boost/asio/strand.hpp>
#include <boost/asio/dispatch.hpp>
#include <boost/asio/post.hpp>
#include <boost/asio/connect.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <iostream>
#include <thread>
#include <chrono>
#include <mutex>
#include <queue>
#include <condition_variable>
#include <atomic>

namespace pmc {
namespace net {

namespace beast = boost::beast;
namespace asio = boost::asio;
using tcp = asio::ip::tcp;

// Internal implementation structure
struct WebSocketClient::Impl {
    // Configuration parameters
    std::string host;
    std::string port;
    std::string path;
    
    // Boost objects
    asio::io_context ioc;
    tcp::resolver resolver;
    beast::websocket::stream<beast::tcp_stream> ws;
    
    // Thread management
    std::thread io_thread;
    std::atomic<bool> running{false};
    
    // Callback functions
    WebSocketClientBase::MessageCallback message_callback;
    WebSocketClientBase::ErrorCallback error_callback;
    WebSocketClientBase::ConnectCallback connect_callback;
    WebSocketClientBase::CloseCallback close_callback;
    
    // Message queue
    std::queue<std::string> send_queue;
    std::mutex queue_mutex;
    std::condition_variable queue_cv;
    std::atomic<bool> sending{false};
    
    // Status flags
    std::atomic<bool> connected{false};
    std::atomic<bool> connecting{false};
    std::atomic<bool> stopping{false};
    
    // Configuration options
    bool auto_reconnect{false};
    int reconnect_interval_ms{3000};
    int heartbeat_interval_ms{30000};
    
    // Constructor
    Impl(const std::string& host, const std::string& port, const std::string& path)
        : host(host)
        , port(port)
        , path(path)
        , resolver(asio::make_strand(ioc))
        , ws(asio::make_strand(ioc))
    {
    }
    
    // Destructor
    ~Impl() {
        stop();
    }
    
    // Start IO thread
    void start() {
        if (!running) {
            running = true;
            io_thread = std::thread([this]() {
                while (running) {
                    try {
                        ioc.run();
                        break;
                    } catch (const std::exception& e) {
                        std::cerr << "IO context exception: " << e.what() << std::endl;
                    }
                }
            });
        }
    }
    
    // Stop IO thread
    void stop() {
        running = false;
        stopping = true;
        
        if (io_thread.joinable()) {
            ioc.stop();
            io_thread.join();
        }
    }
    
    // Perform connection
    void doConnect() {
        if (connecting || connected) {
            return;
        }
        
        connecting = true;
        connected = false;
        
        // Resolve hostname
        resolver.async_resolve(host, port,
            [this](beast::error_code ec, tcp::resolver::results_type results) {
                onResolve(ec, results);
            });
    }
    
    // Resolve completion callback
    void onResolve(beast::error_code ec, tcp::resolver::results_type results) {
        if (ec) {
            onError("Resolve failed: " + ec.message());
            return;
        }
        
        // Connect to resolved endpoints
        beast::get_lowest_layer(ws).async_connect(results,
            [this](beast::error_code ec, tcp::resolver::results_type::endpoint_type) {
                onConnect(ec);
            });
    }
    
    // Connection completion callback
    void onConnect(beast::error_code ec) {
        if (ec) {
            onError("Connection failed: " + ec.message());
            return;
        }
        
        // Set WebSocket options
        ws.set_option(beast::websocket::stream_base::timeout::suggested(
            beast::role_type::client));
        
        // Perform WebSocket handshake
        ws.async_handshake(host, path,
            [this](beast::error_code ec) {
                onHandshake(ec);
            });
    }
    
    // Handshake completion callback
    void onHandshake(beast::error_code ec) {
        connecting = false;
        
        if (ec) {
            onError("Handshake failed: " + ec.message());
            return;
        }
        
        connected = true;
        
        // Call connection success callback
        if (connect_callback) {
            connect_callback(true);
        }
        
        // Start reading messages
        doRead();
        
        // Start sending queued messages
        doSend();
    }
    
    // Read messages
    void doRead() {
        if (!connected) {
            return;
        }
        
        ws.async_read(buffer,
            [this](beast::error_code ec, std::size_t bytes_transferred) {
                onRead(ec, bytes_transferred);
            });
    }
    
    // Read completion callback
    void onRead(beast::error_code ec, std::size_t bytes_transferred) {
        if (ec) {
            if (ec == beast::websocket::error::closed) {
                // Normal close
                onClose();
            } else {
                onError("Read failed: " + ec.message());
            }
            return;
        }
        
        // Process received message
        std::string message = beast::buffers_to_string(buffer.data());
        buffer.consume(bytes_transferred);
        
        if (message_callback) {
            message_callback(message);
        }
        
        // Continue reading
        doRead();
    }
    
    // Send messages
    void doSend() {
        if (!connected || sending) {
            return;
        }
        
        std::unique_lock<std::mutex> lock(queue_mutex);
        if (send_queue.empty()) {
            return;
        }
        
        sending = true;
        std::string message = std::move(send_queue.front());
        send_queue.pop();
        lock.unlock();
        
        ws.async_write(asio::buffer(message),
            [this](beast::error_code ec, std::size_t bytes_transferred) {
                onWrite(ec, bytes_transferred);
            });
    }
    
    // Write completion callback
    void onWrite(beast::error_code ec, std::size_t bytes_transferred) {
        sending = false;
        
        if (ec) {
            onError("Write failed: " + ec.message());
            return;
        }
        
        // Continue sending queued messages
        doSend();
    }
    
    // Error handling
    void onError(const std::string& error) {
        connecting = false;
        connected = false;
        
        std::cerr << "WebSocket error: " << error << std::endl;
        
        if (error_callback) {
            error_callback(error);
        }
        
        // Auto reconnect
        if (auto_reconnect && !stopping) {
            std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_interval_ms));
            asio::post(ioc, [this]() { doConnect(); });
        }
    }
    
    // Connection close
    void onClose() {
        connected = false;
        
        if (close_callback) {
            close_callback();
        }
        
        // Auto reconnect
        if (auto_reconnect && !stopping) {
            std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_interval_ms));
            asio::post(ioc, [this]() { doConnect(); });
        }
    }
    
private:
    beast::flat_buffer buffer;
};

// WebSocketClient implementation
WebSocketClient::WebSocketClient(const std::string& host, const std::string& port, const std::string& path)
    : impl_(std::make_unique<Impl>(host, port, path))
{
    impl_->start();
}

WebSocketClient::~WebSocketClient() {
    disconnect();
}

void WebSocketClient::setMessageCallback(MessageCallback callback) {
    impl_->message_callback = callback;
}

void WebSocketClient::setErrorCallback(ErrorCallback callback) {
    impl_->error_callback = callback;
}

void WebSocketClient::setConnectCallback(ConnectCallback callback) {
    impl_->connect_callback = callback;
}

void WebSocketClient::setCloseCallback(CloseCallback callback) {
    impl_->close_callback = callback;
}

bool WebSocketClient::connect(int timeout_ms) {
    if (impl_->connecting || impl_->connected) {
        return false;
    }
    
    impl_->doConnect();
    return true;
}

void WebSocketClient::disconnect() {
    if (!impl_->connected && !impl_->connecting) {
        return;
    }
    
    impl_->stopping = true;
    
    asio::post(impl_->ioc, [this]() {
        beast::error_code ec;
        impl_->ws.close(beast::websocket::close_code::normal, ec);
        impl_->connected = false;
        impl_->connecting = false;
    });
}

bool WebSocketClient::isConnected() const {
    return impl_->connected;
}

bool WebSocketClient::send(const std::string& message) {
    if (!impl_->connected) {
        return false;
    }
    
    {
        std::lock_guard<std::mutex> lock(impl_->queue_mutex);
        impl_->send_queue.push(message);
    }
    
    impl_->queue_cv.notify_one();
    asio::post(impl_->ioc, [this]() { impl_->doSend(); });
    
    return true;
}

bool WebSocketClient::sendBinary(const void* data, size_t size) {
    if (!impl_->connected) {
        return false;
    }
    
    std::string message(static_cast<const char*>(data), size);
    return send(message);
}

void WebSocketClient::setAutoReconnect(bool enable, int interval_ms) {
    impl_->auto_reconnect = enable;
    impl_->reconnect_interval_ms = interval_ms;
}

void WebSocketClient::setHeartbeatInterval(int interval_ms) {
    impl_->heartbeat_interval_ms = interval_ms;
}

std::string WebSocketClient::getHost() const {
    return impl_->host;
}

std::string WebSocketClient::getPort() const {
    return impl_->port;
}

std::string WebSocketClient::getPath() const {
    return impl_->path;
}

} // namespace net
} // namespace pmc