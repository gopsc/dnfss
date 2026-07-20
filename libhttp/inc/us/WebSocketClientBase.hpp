#ifndef PMC_NET_WEBSOCKET_CLIENT_BASE_HPP
#define PMC_NET_WEBSOCKET_CLIENT_BASE_HPP

#include <string>
#include <functional>
#include <memory>

namespace pmc {
namespace net {

/**
 * @brief WebSocket client base class
 * 
 * Defines the common interface for all WebSocket clients
 */
class WebSocketClientBase {
public:
    // Callback function types
    using MessageCallback = std::function<void(const std::string&)>;
    using ErrorCallback = std::function<void(const std::string&)>;
    using ConnectCallback = std::function<void(bool)>;
    using CloseCallback = std::function<void()>;

    virtual ~WebSocketClientBase() = default;

    // Callback setters
    virtual void setMessageCallback(MessageCallback callback) = 0;
    virtual void setErrorCallback(ErrorCallback callback) = 0;
    virtual void setConnectCallback(ConnectCallback callback) = 0;
    virtual void setCloseCallback(CloseCallback callback) = 0;

    // Connection management
    virtual bool connect(int timeout_ms = 5000) = 0;
    virtual void disconnect() = 0;
    virtual bool isConnected() const = 0;

    // Message sending
    virtual bool send(const std::string& message) = 0;
    virtual bool sendBinary(const void* data, size_t size) = 0;

    // Configuration options
    virtual void setAutoReconnect(bool enable, int interval_ms = 3000) = 0;
    virtual void setHeartbeatInterval(int interval_ms = 30000) = 0;

    // Information retrieval
    virtual std::string getHost() const = 0;
    virtual std::string getPort() const = 0;
    virtual std::string getPath() const = 0;

    // SSL/TLS related
    virtual bool isSecure() const = 0;
    virtual void setVerifyCertificate(bool verify) = 0;
    virtual void setCertificateFile(const std::string& cert_file) = 0;
    virtual void setPrivateKeyFile(const std::string& key_file) = 0;
    virtual void setCertificateAuthorityFile(const std::string& ca_file) = 0;
};

} // namespace net
} // namespace pmc

#endif // PMC_NET_WEBSOCKET_CLIENT_BASE_HPP