#pragma once
#include <string>
#include <functional>
#include <map>
#include <memory>
#include <stdexcept>
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
    HttpClient();
    ~HttpClient();

    HttpClient(const HttpClient&) = delete;
    HttpClient& operator=(const HttpClient&) = delete;

    void set_header(const std::string& header);
    void clear_headers();

    const std::map<std::string, std::string>& get_response_headers() const;
    long get_response_code() const;

    // ===== HTTP Methods (Synchronous, libcurl-based) =====
    void request_stream_sync(
        HttpMethod method,
        const std::string& url,
        const std::string& data,
        std::function<void(const std::string&)> callback);

    void get_stream_sync(const std::string& url, std::function<void(const std::string&)> callback);
    void post_stream_sync(const std::string& url, const std::string& data, std::function<void(const std::string&)> callback);
    void put_stream_sync(const std::string& url, const std::string& data, std::function<void(const std::string&)> callback);
    void delete_stream_sync(const std::string& url, std::function<void(const std::string&)> callback);

    // ===== WebSocket Methods (Synchronous, Boost.Beast-based) =====
    bool ws_connect(const std::string& url, int timeout_ms = 5000);
    bool ws_connect(const std::string& host, const std::string& port,
                    const std::string& path = "/", bool ssl = false, int timeout_ms = 5000);
    bool ws_send(const std::string& message);
    bool ws_send_binary(const void* data, size_t size);
    std::string ws_recv(int timeout_ms = 0);
    bool ws_is_connected() const;
    void ws_close();
    std::string ws_get_host() const;
    std::string ws_get_port() const;
    std::string ws_get_path() const;

private:
    CURL* curl_ = nullptr;
    struct curl_slist* headers_ = nullptr;
    std::map<std::string, std::string> response_headers_;
    long response_code_ = 0;

    struct WsState {
        asio::io_context ioc;
        std::unique_ptr<beast::tcp_stream> tcp_stream;
        std::unique_ptr<asio::ssl::context> ssl_ctx;
        std::unique_ptr<asio::ssl::stream<tcp::socket&>> ssl_stream;
        std::unique_ptr<websocket::stream<tcp::socket&>> ws_stream;
        std::unique_ptr<websocket::stream<asio::ssl::stream<tcp::socket&>&>> wss_stream;
        bool ssl_enabled = false;
        bool connected = false;
        std::string host;
        std::string port;
        std::string path;
    };
    std::unique_ptr<WsState> ws_state_;

    void set_http_method(HttpMethod method, const std::string& data);
    static size_t header_callback(char* buffer, size_t size, size_t nitems, void* userdata);
    static size_t write_callback(char* ptr, size_t size, size_t nmemb, void* userdata);
    static void parse_ws_url(const std::string& url_part, std::string& host, std::string& port,
                             std::string& path, const char* default_port);
};

// Backward compatibility aliases
using WebSocketClientBase = HttpClient;
using WebSocketClient = HttpClient;

} // namespace pmc::net
