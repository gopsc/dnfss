// WebSocketServer.hpp - WebSocket server header file
// Based on Boost.Beast and Boost.Asio

#ifndef PMC_NET_WEBSOCKETSERVER_HPP
#define PMC_NET_WEBSOCKETSERVER_HPP

#include <functional>
#include <string>
#include <memory>
#include <unordered_map>
#include <mutex>
#include <atomic>
#include <vector>
#include <thread>
#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>

namespace pmc::net {

/**
 * @brief WebSocket connection handler type definition
 * 
 * Handles message sending and receiving after WebSocket connection is established
 */
using WebSocketMessageHandler = std::function<void(
    const std::string& clientId,
    const std::string& message,
    std::function<void(const std::string&)> sendCallback)>;

/**
 * @brief WebSocket connection callback type definition
 */
using WebSocketConnectHandler = std::function<void(
    const std::string& clientId,
    const std::string& remoteAddress)>;

/**
 * @brief WebSocket disconnection callback type definition
 */
using WebSocketDisconnectHandler = std::function<void(
    const std::string& clientId)>;

/**
 * @brief WebSocket server class
 * 
 * WebSocket server implemented based on Boost.Beast and Boost.Asio
 */
class WebSocketServer {
public:
    /**
     * @brief Constructor
     * @param port Listening port
     * @param threads Number of threads (default 2)
     */
    WebSocketServer(unsigned short port, unsigned int threads = 2);
    
    /**
     * @brief Destructor
     */
    ~WebSocketServer();
    
    /**
     * @brief Start the server
     */
    void start();
    
    /**
     * @brief Stop the server
     */
    void stop();
    
    /**
     * @brief Register message handler
     * @param path Path (for routing)
     * @param handler Message handler
     */
    void onMessage(const std::string& path, WebSocketMessageHandler handler);
    
    /**
     * @brief Register connection handler
     * @param handler Connection handler
     */
    void onConnect(WebSocketConnectHandler handler);
    
    /**
     * @brief Register disconnection handler
     * @param handler Disconnection handler
     */
    void onDisconnect(WebSocketDisconnectHandler handler);
    
    /**
     * @brief Send message to a specific client
     * @param clientId Client ID
     * @param message Message content
     * @return Whether sending was successful
     */
    bool sendToClient(const std::string& clientId, const std::string& message);
    
    /**
     * @brief Broadcast message to all clients
     * @param message Message content
     * @return Number of clients that successfully received the message
     */
    size_t broadcast(const std::string& message);
    
    /**
     * @brief Disconnect a specific client
     * @param clientId Client ID
     */
    void disconnectClient(const std::string& clientId);
    
    /**
     * @brief Get the number of currently connected clients
     * @return Number of clients
     */
    size_t getClientCount() const;
    
    /**
     * @brief Check if the server is running
     * @return Running status
     */
    bool isRunning() const { return running_; }
    
    /**
     * @brief Get the server listening port
     * @return Port number
     */
    unsigned short getPort() const { return port_; }

private:
    // Internal Session class
    class Session;
    
    // Internal Listener class
    class Listener;
    
    // Internal implementation structure
    struct Impl {
        boost::asio::io_context ioc_;
        std::shared_ptr<Listener> listener_;
        std::vector<std::thread> threads_;
    };
    
    // Server configuration
    unsigned short port_;
    unsigned int threads_;
    std::atomic<bool> running_{false};
    
    // Internal implementation
    std::unique_ptr<Impl> impl_;
    
    // Generate client ID
    std::string generateClientId() const;
    
    // Add client
    void addClient(const std::string& clientId, std::shared_ptr<Session> session);
    
    // Remove client
    void removeClient(const std::string& clientId);
    
    // Get client session
    std::shared_ptr<Session> getClient(const std::string& clientId);
    
    // Callback functions
    std::unordered_map<std::string, WebSocketMessageHandler> messageHandlers_;
    WebSocketConnectHandler connectHandler_;
    WebSocketDisconnectHandler disconnectHandler_;
    
    // Client management
    mutable std::mutex clientsMutex_;
    std::unordered_map<std::string, std::shared_ptr<Session>> clients_;
};

} // namespace pmc::net

#endif // PMC_NET_WEBSOCKETSERVER_HPP