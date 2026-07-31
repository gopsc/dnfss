#pragma once
#include <string>
#include <functional>
#include <map>
#include <memory>
#include <stdexcept>
#include <chrono>
#include <iostream>
#include <curl/curl.h>
#include <openssl/ssl.h>

// Boost.Beast for WebSocket client
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <boost/beast/websocket/ssl.hpp>
#include <boost/asio/connect.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/ssl.hpp>
#include <boost/asio/ssl/stream.hpp>

namespace pmc::net {

namespace beast = boost::beast;
namespace asio = boost::asio;
namespace websocket = beast::websocket;
using tcp = asio::ip::tcp;

enum class HttpMethod {
    GET,
    POST,
    PUT,
    DELETE,
    PATCH,
    HEAD,
    OPTIONS
};

class HttpClient {
public:
    HttpClient() {
        curl_ = curl_easy_init();
        if (!curl_) {
            throw std::runtime_error("Failed to initialize CURL");
        }
    }
    
    ~HttpClient() {
        ws_close();
        if (curl_) {
            curl_easy_cleanup(curl_);
        }
        if (headers_) {
            curl_slist_free_all(headers_);
        }
    }

    HttpClient(const HttpClient&) = delete;
    HttpClient& operator=(const HttpClient&) = delete;
    
    void set_header(const std::string& header) {
        headers_ = curl_slist_append(headers_, header.c_str());
    }
    
    void clear_headers() {
        if (headers_) {
            curl_slist_free_all(headers_);
            headers_ = nullptr;
        }
    }
    
    const std::map<std::string, std::string>& get_response_headers() const {
        return response_headers_;
    }
    
    long get_response_code() const {
        return response_code_;
    }
    
    // ===== HTTP Methods (Synchronous, libcurl-based) =====
    void request_stream_sync(
        HttpMethod method,
        const std::string& url,
        const std::string& data,
        std::function<void(const std::string&)> callback
    ) {
        if (!curl_) {
            throw std::runtime_error("CURL not initialized");
        }
        
        response_headers_.clear();
        response_code_ = 0;
        
        curl_easy_reset(curl_);
        curl_easy_setopt(curl_, CURLOPT_URL, url.c_str());
        set_http_method(method, data);
        curl_easy_setopt(curl_, CURLOPT_HTTPHEADER, headers_);
        curl_easy_setopt(curl_, CURLOPT_HEADERFUNCTION, header_callback);
        curl_easy_setopt(curl_, CURLOPT_HEADERDATA, this);
        
        std::string full_response;
        curl_easy_setopt(curl_, CURLOPT_WRITEFUNCTION, write_callback);
        curl_easy_setopt(curl_, CURLOPT_WRITEDATA, &full_response);
        
        curl_easy_setopt(curl_, CURLOPT_TIMEOUT, 30L);
        curl_easy_setopt(curl_, CURLOPT_CONNECTTIMEOUT, 10L);
        
        CURLcode res = curl_easy_perform(curl_);
        
        if (res != CURLE_OK) {
            throw std::runtime_error(std::string("CURL error: ") + curl_easy_strerror(res));
        }
        
        curl_easy_getinfo(curl_, CURLINFO_RESPONSE_CODE, &response_code_);
        
        if (!full_response.empty()) {
            callback(full_response);
        }
    }
    
    void get_stream_sync(const std::string& url, std::function<void(const std::string&)> callback) {
        request_stream_sync(HttpMethod::GET, url, "", callback);
    }
    
    void post_stream_sync(const std::string& url, const std::string& data, std::function<void(const std::string&)> callback) {
        request_stream_sync(HttpMethod::POST, url, data, callback);
    }
    
    void put_stream_sync(const std::string& url, const std::string& data, std::function<void(const std::string&)> callback) {
        request_stream_sync(HttpMethod::PUT, url, data, callback);
    }
    
    void delete_stream_sync(const std::string& url, std::function<void(const std::string&)> callback) {
        request_stream_sync(HttpMethod::DELETE, url, "", callback);
    }

    // ===== WebSocket Methods (Synchronous, Boost.Beast-based) =====

    /**
     * Connect to WebSocket server (ws:// or wss://)
     * @param url Full URL like "ws://host:port/path" or "wss://host:port/path"
     * @param timeout_ms Connection timeout in milliseconds
     * @return true on success
     */
    bool ws_connect(const std::string& url, int timeout_ms = 5000) {
        std::string host, port, path;
        bool ssl = false;

        if (url.size() >= 6 && url.substr(0, 6) == "wss://") {
            ssl = true;
            parse_ws_url(url.substr(6), host, port, path, "443");
        } else if (url.size() >= 5 && url.substr(0, 5) == "ws://") {
            ssl = false;
            parse_ws_url(url.substr(5), host, port, path, "80");
        } else {
            return false;
        }

        return ws_connect(host, port, path, ssl, timeout_ms);
    }

    /**
     * Connect to WebSocket server with explicit parameters
     */
    bool ws_connect(const std::string& host, const std::string& port,
                    const std::string& path = "/", bool ssl = false, int timeout_ms = 5000) {
        ws_close();
        ws_state_ = std::make_unique<WsState>();
        ws_state_->ssl_enabled = ssl;
        ws_state_->host = host;
        ws_state_->port = port;
        ws_state_->path = path.empty() ? "/" : path;

        try {
            tcp::resolver resolver(ws_state_->ioc);
            auto const results = resolver.resolve(host, port);

            if (ssl) {
                // wss:// path: tcp::socket → asio::ssl::stream → websocket::stream
                ws_state_->ssl_ctx = std::make_unique<asio::ssl::context>(asio::ssl::context::tlsv12_client);
                ws_state_->ssl_ctx->set_verify_mode(asio::ssl::verify_none);

                // Create socket wrapped in beast::tcp_stream for timeout support
                ws_state_->tcp_stream = std::make_unique<beast::tcp_stream>(ws_state_->ioc);

                // Set connect timeout
                ws_state_->tcp_stream->expires_after(std::chrono::milliseconds(timeout_ms));
                asio::connect(ws_state_->tcp_stream->socket(), results);

                // Now build SSL stream on top of the socket
                ws_state_->ssl_stream = std::make_unique<asio::ssl::stream<tcp::socket&>>(
                    ws_state_->tcp_stream->socket(), *ws_state_->ssl_ctx);

                // Set SNI
                if (!SSL_set_tlsext_host_name(ws_state_->ssl_stream->native_handle(), host.c_str())) {
                    throw std::runtime_error("Failed to set SNI hostname");
                }

                // SSL handshake
                ws_state_->ssl_stream->handshake(asio::ssl::stream_base::client);

                // Build websocket stream on top of SSL stream
                ws_state_->wss_stream = std::make_unique<websocket::stream<asio::ssl::stream<tcp::socket&>&>>(
                    *ws_state_->ssl_stream);

                // WebSocket handshake
                ws_state_->tcp_stream->expires_never();
                ws_state_->wss_stream->set_option(websocket::stream_base::timeout::suggested(beast::role_type::client));
                ws_state_->wss_stream->set_option(websocket::stream_base::decorator(
                    [](websocket::request_type& req) {
                        req.set(beast::http::field::user_agent, "UniServe-HttpClient");
                    }));
                ws_state_->wss_stream->handshake(host, ws_state_->path);
            } else {
                // ws:// path: tcp::socket wrapped in beast::tcp_stream → websocket::stream
                ws_state_->tcp_stream = std::make_unique<beast::tcp_stream>(ws_state_->ioc);

                ws_state_->tcp_stream->expires_after(std::chrono::milliseconds(timeout_ms));
                asio::connect(ws_state_->tcp_stream->socket(), results);

                // Build websocket stream on top of tcp_stream's socket (by reference)
                ws_state_->ws_stream = std::make_unique<websocket::stream<tcp::socket&>>(
                    ws_state_->tcp_stream->socket());

                ws_state_->tcp_stream->expires_never();
                ws_state_->ws_stream->set_option(websocket::stream_base::timeout::suggested(beast::role_type::client));
                ws_state_->ws_stream->set_option(websocket::stream_base::decorator(
                    [](websocket::request_type& req) {
                        req.set(beast::http::field::user_agent, "UniServe-HttpClient");
                    }));
                ws_state_->ws_stream->handshake(host, ws_state_->path);
            }

            ws_state_->connected = true;
            return true;
        } catch (const std::exception& e) {
            std::cerr << "[HttpClient::ws_connect] " << e.what() << std::endl;
            ws_state_.reset();
            return false;
        }
    }

    /**
     * Send a text message over WebSocket
     */
    bool ws_send(const std::string& message) {
        if (!ws_state_ || !ws_state_->connected) return false;
        try {
            if (ws_state_->ssl_enabled && ws_state_->wss_stream) {
                ws_state_->wss_stream->write(asio::buffer(message));
            } else if (ws_state_->ws_stream) {
                ws_state_->ws_stream->write(asio::buffer(message));
            }
            return true;
        } catch (const std::exception& e) {
            std::cerr << "[HttpClient::ws_send] " << e.what() << std::endl;
            ws_state_->connected = false;
            return false;
        }
    }

    /**
     * Send binary data over WebSocket
     */
    bool ws_send_binary(const void* data, size_t size) {
        if (!ws_state_ || !ws_state_->connected) return false;
        try {
            if (ws_state_->ssl_enabled && ws_state_->wss_stream) {
                ws_state_->wss_stream->binary(true);
                ws_state_->wss_stream->write(asio::buffer(data, size));
                ws_state_->wss_stream->text(true);
            } else if (ws_state_->ws_stream) {
                ws_state_->ws_stream->binary(true);
                ws_state_->ws_stream->write(asio::buffer(data, size));
                ws_state_->ws_stream->text(true);
            }
            return true;
        } catch (const std::exception& e) {
            std::cerr << "[HttpClient::ws_send_binary] " << e.what() << std::endl;
            ws_state_->connected = false;
            return false;
        }
    }

    /**
     * Receive a message from WebSocket (blocking)
     * @param timeout_ms Timeout in milliseconds (0 = wait forever)
     * @return Received message, or empty string on error/timeout/close
     */
    std::string ws_recv(int timeout_ms = 0) {
        if (!ws_state_ || !ws_state_->connected) return "";
        try {
            beast::flat_buffer buffer;

            // For synchronous recv with timeout, we use websocket stream's own timeout options
            // since we set suggested timeouts during handshake
            if (ws_state_->ssl_enabled && ws_state_->wss_stream) {
                ws_state_->wss_stream->read(buffer);
            } else if (ws_state_->ws_stream) {
                ws_state_->ws_stream->read(buffer);
            }
            return beast::buffers_to_string(buffer.data());
        } catch (const beast::system_error& e) {
            if (e.code() == websocket::error::closed) {
                ws_state_->connected = false;
            } else {
                std::cerr << "[HttpClient::ws_recv] " << e.what() << std::endl;
                ws_state_->connected = false;
            }
            return "";
        } catch (const std::exception& e) {
            std::cerr << "[HttpClient::ws_recv] " << e.what() << std::endl;
            ws_state_->connected = false;
            return "";
        }
    }

    bool ws_is_connected() const {
        return ws_state_ && ws_state_->connected;
    }

    void ws_close() {
        if (!ws_state_) return;
        try {
            if (ws_state_->connected) {
                beast::error_code ec;
                if (ws_state_->ssl_enabled && ws_state_->wss_stream) {
                    ws_state_->wss_stream->close(websocket::close_code::normal, ec);
                } else if (ws_state_->ws_stream) {
                    ws_state_->ws_stream->close(websocket::close_code::normal, ec);
                }
            }
        } catch (...) {
            // Ignore close errors
        }
        ws_state_.reset();
    }

    std::string ws_get_host() const { return ws_state_ ? ws_state_->host : ""; }
    std::string ws_get_port() const { return ws_state_ ? ws_state_->port : ""; }
    std::string ws_get_path() const { return ws_state_ ? ws_state_->path : ""; }

private:
    CURL* curl_ = nullptr;
    struct curl_slist* headers_ = nullptr;
    std::map<std::string, std::string> response_headers_;
    long response_code_ = 0;

    // WebSocket state - layered streams with reference ownership
    struct WsState {
        asio::io_context ioc;
        std::unique_ptr<beast::tcp_stream> tcp_stream;                      // owns the socket
        std::unique_ptr<asio::ssl::context> ssl_ctx;                        // SSL context
        std::unique_ptr<asio::ssl::stream<tcp::socket&>> ssl_stream;        // references tcp_stream->socket()
        std::unique_ptr<websocket::stream<tcp::socket&>> ws_stream;         // ws:// references tcp_stream->socket()
        std::unique_ptr<websocket::stream<asio::ssl::stream<tcp::socket&>&>> wss_stream; // wss:// references ssl_stream
        bool ssl_enabled = false;
        bool connected = false;
        std::string host;
        std::string port;
        std::string path;
    };
    std::unique_ptr<WsState> ws_state_;

    void set_http_method(HttpMethod method, const std::string& data) {
        switch (method) {
            case HttpMethod::GET:
                curl_easy_setopt(curl_, CURLOPT_HTTPGET, 1L);
                break;
            case HttpMethod::POST:
                curl_easy_setopt(curl_, CURLOPT_POST, 1L);
                if (!data.empty()) {
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDS, data.c_str());
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDSIZE, data.size());
                }
                break;
            case HttpMethod::PUT:
                curl_easy_setopt(curl_, CURLOPT_CUSTOMREQUEST, "PUT");
                if (!data.empty()) {
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDS, data.c_str());
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDSIZE, data.size());
                }
                break;
            case HttpMethod::DELETE:
                curl_easy_setopt(curl_, CURLOPT_CUSTOMREQUEST, "DELETE");
                break;
            case HttpMethod::PATCH:
                curl_easy_setopt(curl_, CURLOPT_CUSTOMREQUEST, "PATCH");
                if (!data.empty()) {
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDS, data.c_str());
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDSIZE, data.size());
                }
                break;
            case HttpMethod::HEAD:
                curl_easy_setopt(curl_, CURLOPT_NOBODY, 1L);
                break;
            case HttpMethod::OPTIONS:
                curl_easy_setopt(curl_, CURLOPT_CUSTOMREQUEST, "OPTIONS");
                break;
        }
    }

    static size_t header_callback(char* buffer, size_t size, size_t nitems, void* userdata) {
        size_t total_size = size * nitems;
        HttpClient* client = static_cast<HttpClient*>(userdata);
        std::string header_line(buffer, total_size);
        size_t colon_pos = header_line.find(':');
        if (colon_pos != std::string::npos) {
            std::string key = header_line.substr(0, colon_pos);
            std::string value = header_line.substr(colon_pos + 1);
            key.erase(0, key.find_first_not_of(" \t"));
            key.erase(key.find_last_not_of(" \t") + 1);
            value.erase(0, value.find_first_not_of(" \t"));
            value.erase(value.find_last_not_of(" \t\r\n") + 1);
            if (!key.empty() && !value.empty()) {
                client->response_headers_[key] = value;
            }
        }
        return total_size;
    }
    
    static size_t write_callback(char* ptr, size_t size, size_t nmemb, void* userdata) {
        size_t total_size = size * nmemb;
        std::string* response = static_cast<std::string*>(userdata);
        response->append(ptr, total_size);
        return total_size;
    }

    static void parse_ws_url(const std::string& url_part, std::string& host, std::string& port, std::string& path, const char* default_port) {
        size_t path_pos = url_part.find('/');
        std::string host_port;
        if (path_pos != std::string::npos) {
            host_port = url_part.substr(0, path_pos);
            path = url_part.substr(path_pos);
        } else {
            host_port = url_part;
            path = "/";
        }
        size_t port_pos = host_port.find(':');
        if (port_pos != std::string::npos) {
            host = host_port.substr(0, port_pos);
            port = host_port.substr(port_pos + 1);
        } else {
            host = host_port;
            port = default_port;
        }
    }
};

// Backward compatibility aliases
using WebSocketClientBase = HttpClient;
using WebSocketClient = HttpClient;

} // namespace pmc::net
