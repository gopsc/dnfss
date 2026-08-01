#include "us/detail/HttpServerImpl.hpp"

namespace pmc {
namespace net {

// ---- WebSocket 路由注册 ----
void HttpServer::ws(const std::string& path, WebSocketMessageHandler handler) {
    WsRouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    ws_handlers_.push_back(entry);
}

void HttpServer::ws_proxy(const std::string& path, WsProxyHandshakeHandler handshake) {
    WsProxyRouteEntry entry;
    entry.path = path;
    entry.handshake = handshake;
    ws_proxy_handlers_.push_back(entry);
}

void HttpServer::onWsConnect(WebSocketConnectHandler handler) {
    ws_connect_handler_ = handler;
}

void HttpServer::onWsDisconnect(WebSocketDisconnectHandler handler) {
    ws_disconnect_handler_ = handler;
}

WebSocketMessageHandler HttpServer::findWsRoute(const std::string& path) {
    for (const auto& e : ws_handlers_) {
        if (e.path == path) return e.handler;
    }
    return nullptr;
}

WsProxyHandshakeHandler HttpServer::findWsProxyRoute(const std::string& path) {
    for (const auto& e : ws_proxy_handlers_) {
        if (e.path == path) return e.handshake;
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

} // namespace net
} // namespace pmc
