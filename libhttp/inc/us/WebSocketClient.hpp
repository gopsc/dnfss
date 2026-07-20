#ifndef PMC_NET_WEBSOCKET_CLIENT_HPP
#define PMC_NET_WEBSOCKET_CLIENT_HPP

#include "WebSocketClientBase.hpp"

namespace pmc {
namespace net {

/**
 * @brief Asynchronous WebSocket client based on Boost.Beast (non-SSL version)
 */
class WebSocketClient : public WebSocketClientBase {
public:
    /**
     * @brief Constructor
     * @param host Server hostname or IP address
     * @param port Server port number
     * @param path WebSocket path (default "/")
     */
    WebSocketClient(const std::string& host, const std::string& port, const std::string& path = "/");
    
    /**
     * @brief Destructor
     */
    ~WebSocketClient();

    // Implement WebSocketClientBase interface
    void setMessageCallback(MessageCallback callback) override;
    void setErrorCallback(ErrorCallback callback) override;
    void setConnectCallback(ConnectCallback callback) override;
    void setCloseCallback(CloseCallback callback) override;

    bool connect(int timeout_ms = 5000) override;
    void disconnect() override;
    bool isConnected() const override;

    bool send(const std::string& message) override;
    bool sendBinary(const void* data, size_t size) override;

    void setAutoReconnect(bool enable, int interval_ms = 3000) override;
    void setHeartbeatInterval(int interval_ms = 30000) override;

    std::string getHost() const override;
    std::string getPort() const override;
    std::string getPath() const override;

    // SSL/TLS related (non-SSL version returns default values)
    bool isSecure() const override { return false; }
    void setVerifyCertificate(bool verify) override { (void)verify;/* Ignored */ }
    void setCertificateFile(const std::string& cert_file) override { (void)cert_file; /* Ignored */ }
    void setPrivateKeyFile(const std::string& key_file) override { (void)key_file;/* Ignored */ }
    void setCertificateAuthorityFile(const std::string& ca_file) override { (void)ca_file;/* Ignored */ }

private:
    // Forward declaration of implementation class
    class Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace net
} // namespace pmc

#endif // PMC_NET_WEBSOCKET_CLIENT_HPP