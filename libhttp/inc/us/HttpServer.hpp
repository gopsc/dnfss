#ifndef HTTPSERVER_HPP
#define HTTPSERVER_HPP

#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <boost/beast/http.hpp>
#include <boost/beast/version.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/ssl.hpp>
#include <boost/asio/strand.hpp>
#include <boost/config.hpp>
#include <memory>
#include <string>
#include <functional>
#include <unordered_map>
#include <vector>
#include <iostream>
#include <thread>
#include <atomic>
#include <mutex>
#include <regex>

namespace pmc {
namespace net {

namespace beast = boost::beast;
namespace http  = beast::http;
namespace asio  = boost::asio;
using tcp = boost::asio::ip::tcp;

using HttpRequestHandler = std::function<http::response<http::string_body>(
    const http::request<http::string_body>&,
    const std::unordered_map<std::string, std::string>&)>;

using MiddlewareHandler = std::function<bool(
    const http::request<http::string_body>&,
    http::response<http::string_body>&,
    const std::unordered_map<std::string, std::string>&)>;

struct RouteEntry {
    std::string path;
    std::regex pattern;
    HttpRequestHandler handler;
    bool is_wildcard;
};

class HttpServer {
public:
    explicit HttpServer(const std::string& address, unsigned short port,
                        unsigned int threads = 1);
    explicit HttpServer(const std::string& address, unsigned short port,
                        const std::string& cert_file, const std::string& key_file,
                        unsigned int threads = 1);
    ~HttpServer();

    void start();
    void stop();

    void get(const std::string& path, HttpRequestHandler handler);
    void post(const std::string& path, HttpRequestHandler handler);
    void put(const std::string& path, HttpRequestHandler handler);
    void del(const std::string& path, HttpRequestHandler handler);
    void use(MiddlewareHandler middleware);
    void setStaticDirectory(const std::string& path);

    bool isRunning() const;
    unsigned short getPort() const;
    std::string getAddress() const;
    bool isSSLEnabled() const;

    void run();
    void showStatus() const;

    void setPrivateKeyPassword(const std::string& password);

    class debug {
    public:
        static void print_all_header_fields(const http::request<http::string_body>& req);
    };

private:
    class Session;
    class Listener;

    std::string address_;
    unsigned short port_;
    unsigned int threads_;
    std::atomic<bool> running_{false};

    bool ssl_enabled_{false};
    std::string cert_file_;
    std::string key_file_;
    std::string private_key_password_;

    std::unique_ptr<asio::ssl::context> ssl_ctx_;
    std::unique_ptr<asio::io_context> ioc_;

    std::shared_ptr<Listener> listener_;
    std::vector<std::thread> worker_threads_;

    std::vector<RouteEntry> get_handlers_;
    std::vector<RouteEntry> post_handlers_;
    std::vector<RouteEntry> put_handlers_;
    std::vector<RouteEntry> delete_handlers_;
    std::vector<MiddlewareHandler> middlewares_;
    std::string static_directory_;

    std::vector<std::weak_ptr<Session>> sessions_;
    std::mutex sessions_mutex_;

    std::unordered_map<std::string, std::string> parseQueryParams(const std::string& query);
    std::regex wildcardToRegex(const std::string& pattern);
    bool matchPath(const std::string& path, const std::regex& pattern);
    http::response<http::string_body> handleRequest(const http::request<http::string_body>& req);
    HttpRequestHandler findRoute(const std::string& method, const std::string& path);

    void addSession(const std::shared_ptr<Session>& session);
    void removeSession(const Session* session);
    void closeAllSessions();
    void initSSLContext();
};

} // namespace net
} // namespace pmc

#endif // HTTPSERVER_HPP
