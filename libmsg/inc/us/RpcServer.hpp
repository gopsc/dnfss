#pragma once

/** RpcServer — pymsg 线协议之上的 JSON RPC 服务端（C++ 版，对应 Python 版 pymsg.rpc.RpcServer）
 *
 * 约定（与 Python/Go 客户端字节级兼容）：
 *   请求:  {"_route": "<路由名>", ...业务字段}
 *   响应:  {"success": true,  "data": ...}
 *         {"success": false, "error": "..."}
 *
 * 基于 MsgServer 提供路由分发、JSON 解析、响应自动包装、异常脱敏等功能。
 * handler 签名为 ``json::value(const json::object& req)``：
 *   - 返回值自动包装为 {"success": true, "data": ...}
 *   - 抛出的异常自动包装为 {"success": false, "error": "<脱敏后错误信息>"}
 *   - 服务不会因 handler 异常而崩溃
 *
 * 注意：此头文件依赖 boost::json，核心 libmsg 库保持零外部依赖不变。
 * 使用 RpcServer 时需要链接 -lboost_json。
 *
 * Usage:
 *   #include <us/RpcServer.hpp>
 *
 *   pmc::net::RpcServer rpc("127.0.0.1", 8005, {.workers=4});
 *
 *   rpc.add_route("ping", [](const json::object&) -> json::value {
 *       return "pong";
 *   });
 *
 *   rpc.add_route("execute", [](const json::object& req) -> json::value {
 *       // 处理业务逻辑...
 *       return json::object{{"rows", json::array{}}};
 *   });
 *
 *   rpc.start();   // 阻塞当前线程
 */

#include <us/MsgServer.hpp>

#include <boost/system/error_code.hpp>

#include <cctype>
#include <cstddef>
#include <functional>
#include <regex>
#include <string>
#include <unordered_map>

#include <boost/json.hpp>

namespace pmc {
namespace net {

namespace json = boost::json;

// ============================================================================
// RpcServer
// ============================================================================

/// JSON RPC 服务端：基于 MsgServer，自带路由分发与响应包装。
class RpcServer {
public:
    /// 路由处理函数签名：接收请求参数 object，返回 data 值（任意 JSON 类型）。
    using Handler = std::function<json::value(const json::object& req)>;

    /// 错误回调：void(route_name, exception)，handler 抛异常时调用（用于记日志）。
    using ErrorCallback = std::function<void(const std::string& route, const std::exception& e)>;

    /** 构造 RPC 服务端。
     *
     * @param addr       监听地址
     * @param port       监听端口
     * @param config     MsgServer 配置（线程池/超时/连接数限制等）
     * @param on_error   可选错误回调，handler 抛异常时被调用
     */
    explicit RpcServer(const std::string& addr, int port,
                       MsgServerConfig config = {},
                       ErrorCallback on_error = {})
        : _server(addr, port, config), _on_error(std::move(on_error)) {}

    // ---- 路由注册 ----------------------------------------------------------

    /** 注册路由处理函数。
     *
     * @param name     路由名（对应请求中的 _route 字段）
     * @param handler  处理函数，签名 json::value(const json::object& req)
     */
    void add_route(const std::string& name, Handler handler) {
        _routes[name] = std::move(handler);
    }

    // ---- 启动 / 停止 -------------------------------------------------------

    /// 启动服务（阻塞当前线程）。
    void start() {
        _server.start([this](int fd, const std::string& addr, const std::string& msg) {
            this->_dispatch(fd, addr, msg);
        });
    }

    /// 从另一线程停止服务。
    void stop() {
        _server.stop();
    }

    /// 获取底层 MsgServer 引用（可用于 broadcast 等高级操作）。
    MsgServer& server() noexcept { return _server; }

private:
    // ---- 分发与响应包装 ----------------------------------------------------

    void _dispatch(int fd, const std::string& /*addr*/, const std::string& text) {
        // 解析 JSON（限制嵌套深度防止栈溢出）
        json::parse_options opts;
        opts.max_depth = 32;  // 与 Python 版一致

        boost::system::error_code ec;
        json::value req_val = json::parse(text, ec, {}, opts);
        if (ec || !req_val.is_object()) {
            _reply_error(fd, "invalid JSON request");
            return;
        }

        json::object req = req_val.as_object();

        // 提取 _route
        std::string route;
        if (auto it = req.find("_route"); it != req.end() && it->value().is_string()) {
            route = it->value().as_string().c_str();
            req.erase(it);  // 从业务参数中移除 _route
        } else {
            _reply_error(fd, "missing _route");
            return;
        }

        // 查找 handler
        auto handler_it = _routes.find(route);
        if (handler_it == _routes.end()) {
            _reply_error(fd, "unknown route: " + route);
            return;
        }

        // 执行 handler
        try {
            json::value result = handler_it->second(req);
            _reply_success(fd, result);
        } catch (const std::exception& e) {
            if (_on_error) {
                try {
                    _on_error(route, e);
                } catch (...) {}
            }
            // 脱敏后返回：保留业务错误语义，过滤绝对路径等内部细节
            try {
                _reply_error(fd, _sanitize_error(e.what()));
            } catch (...) {
                // _sanitize_error 或序列化失败时，使用安全的固定文案
                try { _reply_error(fd, "internal error"); } catch (...) {}
            }
        }
    }

    void _reply_success(int fd, const json::value& data) {
        json::object resp;
        resp["success"] = true;
        resp["data"] = data;
        _server.reply(fd, json::serialize(resp));
    }

    void _reply_error(int fd, const std::string& error) {
        json::object resp;
        resp["success"] = false;
        resp["error"] = error;
        _server.reply(fd, json::serialize(resp));
    }

    // ---- 错误信息脱敏 ------------------------------------------------------
    // 匹配绝对路径（如 /opt/micro_servers/db/xxx.py 或 /usr/lib/...），替换为 <redacted>
    // 不返回函数堆栈、SQL、文件路径等内部细节给客户端；完整异常由 on_error 钩子记录到服务端日志。

    static std::string _sanitize_error(const std::string& msg) {
        if (msg.empty()) return msg;
        try {
            // 匹配以 / 开头的绝对路径段，替换为 <redacted>
            // 不返回函数堆栈、SQL、文件路径等内部细节给客户端
            static const std::regex path_re(R"(/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*)");
            return std::regex_replace(msg, path_re, "<redacted>");
        } catch (...) {
            return msg;  // 正则异常时返回原文
        }
    }

    // ---- 成员 --------------------------------------------------------------

    MsgServer _server;
    std::unordered_map<std::string, Handler> _routes;
    ErrorCallback _on_error;
};

} // namespace net
} // namespace pmc
