#include "us/detail/HttpServerImpl.hpp"

namespace pmc {
namespace net {

// ---- HTTP 路由注册 ----
void HttpServer::get(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    get_handlers_.push_back(entry);
}

void HttpServer::post(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    post_handlers_.push_back(entry);
}

void HttpServer::put(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    put_handlers_.push_back(entry);
}

void HttpServer::del(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    delete_handlers_.push_back(entry);
}

void HttpServer::options(const std::string& path, HttpRequestHandler handler) {
    RouteEntry entry;
    entry.path = path;
    entry.handler = handler;
    entry.is_wildcard = (path.find('*') != std::string::npos ||
                         path.find('?') != std::string::npos);
    if (entry.is_wildcard) entry.pattern = wildcardToRegex(path);
    options_handlers_.push_back(entry);
}

void HttpServer::use(MiddlewareHandler m) {
    middlewares_.push_back(m);
}

void HttpServer::setStaticDirectory(const std::string& p) {
    static_directory_ = p;
}

// ---- 通配符转换 ----
std::regex HttpServer::wildcardToRegex(const std::string& pattern) {
    std::string r;
    r.reserve(pattern.size() * 2);
    for (char c : pattern) {
        if (c == '*') r += ".*";
        else if (c == '?') r += ".";
        else if (c == '.' || c == '+' || c == '(' || c == ')' || c == '[' || c == ']' ||
                 c == '{' || c == '}' || c == '^' || c == '$' || c == '|' || c == '\\') {
            r += '\\';
            r += c;
        } else r += c;
    }
    return std::regex(r, std::regex::icase);
}

bool HttpServer::matchPath(const std::string& path, const std::regex& pattern) {
    return std::regex_match(path, pattern);
}

// ---- 查询参数解析 ----
std::unordered_map<std::string, std::string> HttpServer::parseQueryParams(const std::string& query) {
    std::unordered_map<std::string, std::string> params;
    if (query.empty()) return params;

    std::vector<std::string> pairs;
    boost::split(pairs, query, boost::is_any_of("&"));
    for (const auto& pair : pairs) {
        std::vector<std::string> kv;
        boost::split(kv, pair, boost::is_any_of("="));
        if (kv.size() == 2) params[kv[0]] = kv[1];
        else if (kv.size() == 1) params[kv[0]] = "";
    }
    return params;
}

// ---- 路由查找 ----
HttpRequestHandler HttpServer::findRoute(const std::string& method, const std::string& path) {
    const std::vector<RouteEntry>* handlers = nullptr;
    if (method == "GET") handlers = &get_handlers_;
    else if (method == "POST") handlers = &post_handlers_;
    else if (method == "PUT") handlers = &put_handlers_;
    else if (method == "DELETE") handlers = &delete_handlers_;
    else if (method == "OPTIONS") handlers = &options_handlers_;
    else return nullptr;

    for (const auto& e : *handlers) {
        if (!e.is_wildcard && e.path == path) return e.handler;
    }
    for (const auto& e : *handlers) {
        if (e.is_wildcard && matchPath(path, e.pattern)) return e.handler;
    }
    return nullptr;
}

bool HttpServer::routeExists(const std::string& method, const std::string& path) {
    return findRoute(method, path) != nullptr;
}

} // namespace net
} // namespace pmc
