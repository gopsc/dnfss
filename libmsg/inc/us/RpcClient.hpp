#pragma once

/** RpcClient — pymsg 线协议之上的 JSON RPC 客户端（C++ 版，对应 Python 版 pymsg.rpc.RpcClient）
 *
 * 约定（与 Python/Go 客户端字节级兼容）：
 *   请求:  {"_route": "<路由名>", ...业务字段}
 *   响应:  {"success": true,  "data": ...}
 *         {"success": false, "error": "..."}
 *
 * 基于 boost::json 进行 JSON 序列化/反序列化，依赖 MsgClient 进行 TCP 通信。
 *
 * 注意：此头文件依赖 boost::json，核心 libmsg 库（Msg.hpp/MsgServer.hpp/AsyncQueue.hpp）
 * 保持零外部依赖不变。使用 RpcClient 时需要链接 -lboost_json。
 *
 * Usage:
 *   #include <us/RpcClient.hpp>
 *
 *   pmc::net::RpcClient rpc("127.0.0.1", 8005, "gateway");
 *   json::object params;
 *   params["action"] = "select";
 *   params["table"] = "Users";
 *   json::value data = rpc.call("execute", params);  // 返回 data 字段
 *
 *   // 启动等待模式（db 未启动时阻塞重试）
 *   pmc::net::RpcClient rpc("127.0.0.1", 8005, "gateway", 10.0, true);
 */

#include <us/Msg.hpp>

#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>

#include <boost/json.hpp>

namespace pmc {
namespace net {

namespace json = boost::json;

// ============================================================================
// RpcError — RPC 业务异常
// ============================================================================

/// 远端返回 success=false 的业务错误，或响应不符合 RPC 约定（非 JSON / 缺少 success 字段）。
/// 网络层异常（超时、连接拒绝等）仍抛 MsgTimeoutException / MsgConnectionRefusedException 等。
class RpcError : public MsgException {
public:
    explicit RpcError(const std::string& msg) : MsgException(msg) {}
};

// ============================================================================
// RpcClient
// ============================================================================

/// JSON RPC 客户端（短连接，每次调用独立 TCP 连接）。
class RpcClient {
public:
    /** 构造 RPC 客户端。
     *
     * @param addr              服务地址（通常 127.0.0.1）
     * @param port              服务端口
     * @param name              客户端名称（用于调试/日志）
     * @param timeout           默认超时秒数
     * @param retry_on_refused  默认是否在 ECONNREFUSED 时阻塞重试
     * @param retry_interval    重试间隔秒数
     */
    explicit RpcClient(const std::string& addr, int port,
                       const std::string& name = "rpc",
                       double timeout = 10.0,
                       bool retry_on_refused = false,
                       double retry_interval = 1.0)
        : _addr(addr), _port(port), _name(name),
          _timeout(timeout),
          _retry_on_refused(retry_on_refused),
          _retry_interval(retry_interval) {
        if (timeout <= 0 || timeout > 300.0) {
            throw MsgException("invalid timeout: " + std::to_string(timeout));
        }
        if (retry_interval <= 0 || retry_interval > 60.0) {
            throw MsgException("invalid retry_interval: " + std::to_string(retry_interval));
        }
    }

    // ---- RPC 调用 ----------------------------------------------------------

    /** 调用指定路由，返回响应中的 data 字段。
     *
     * @param route             路由名（自动注入为 _route 字段）
     * @param params            业务参数（_route 会被覆盖）
     * @param timeout           本次调用超时（nullopt = 使用构造时默认值）
     * @param retry_on_refused  本次调用是否重试连接拒绝（nullopt = 使用构造时默认值）
     * @return                  服务端返回的 data 字段值（可能为 null）
     * @throws RpcError                   服务端返回 success=false，或响应非法
     * @throws MsgTimeoutException        网络超时
     * @throws MsgConnectionRefusedException 目标不可达且未重试
     * @throws MsgConnectionException     连接中断
     */
    json::value call(const std::string& route,
                     const json::object& params = {},
                     std::optional<double> timeout = std::nullopt,
                     std::optional<bool> retry_on_refused = std::nullopt,
                     std::optional<double> retry_interval = std::nullopt) {
        json::object req = params;
        req["_route"] = route;

        double t = timeout.value_or(_timeout);
        bool retry = retry_on_refused.value_or(_retry_on_refused);
        double interval = retry_interval.value_or(_retry_interval);

        MsgClient client(_addr, _port, _name);
        std::string resp_str = client.call(json::serialize(req), t, retry, interval);

        json::value resp;
        try {
            resp = json::parse(resp_str);
        } catch (const std::exception& e) {
            throw RpcError("响应不是合法 JSON: " + std::string(e.what()));
        }

        if (!resp.is_object()) {
            throw RpcError("非法 RPC 响应（不是 object）: " + resp_str.substr(0, 200));
        }
        const json::object& obj = resp.as_object();
        if (!obj.contains("success")) {
            throw RpcError("非法 RPC 响应（缺少 success 字段）: " + resp_str.substr(0, 200));
        }
        const json::value& success = obj.at("success");
        if (!success.is_bool()) {
            // 类型不符时不要调用 as_bool()（会抛未捕获的 boost system_error），统一抛 RpcError
            throw RpcError("非法 RPC 响应（success 字段不是 bool）");
        }
        if (!success.as_bool()) {
            std::string err = "未知错误";
            if (auto it = obj.find("error"); it != obj.end() && it->value().is_string()) {
                err = it->value().as_string().c_str();
            }
            throw RpcError(err);
        }
        if (auto it = obj.find("data"); it != obj.end()) {
            return it->value();
        }
        return nullptr;
    }

    /** 调用指定路由，返回完整响应对象（包含 success/data/error），不抛 RpcError。
     *  适用于需要自行检查 success 字段或访问额外字段的场景。
     */
    json::object call_raw(const std::string& route,
                          const json::object& params = {},
                          std::optional<double> timeout = std::nullopt,
                          std::optional<bool> retry_on_refused = std::nullopt,
                          std::optional<double> retry_interval = std::nullopt) {
        json::object req = params;
        req["_route"] = route;

        double t = timeout.value_or(_timeout);
        bool retry = retry_on_refused.value_or(_retry_on_refused);
        double interval = retry_interval.value_or(_retry_interval);

        MsgClient client(_addr, _port, _name);
        std::string resp_str = client.call(json::serialize(req), t, retry, interval);

        json::value resp;
        try {
            resp = json::parse(resp_str);
        } catch (const std::exception& e) {
            throw RpcError("响应不是合法 JSON: " + std::string(e.what()));
        }
        if (!resp.is_object()) {
            throw RpcError("非法 RPC 响应（不是 object）: " + resp_str.substr(0, 200));
        }
        return resp.as_object();
    }

    // ---- 访问器 ------------------------------------------------------------

    const std::string& addr() const noexcept { return _addr; }
    int port() const noexcept { return _port; }
    const std::string& name() const noexcept { return _name; }

private:
    std::string _addr;
    int _port;
    std::string _name;
    double _timeout;
    bool _retry_on_refused;
    double _retry_interval;
};

} // namespace net
} // namespace pmc
