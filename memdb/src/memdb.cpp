#include <us/RpcServer.hpp>
#include <us/memdb.hpp>

#include <boost/program_options.hpp>

#include <atomic>
#include <csignal>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

namespace json = boost::json;
namespace po   = boost::program_options;

struct ServerConfig {
    std::string listen_host = "127.0.0.1";
    int         listen_port = 8011;
    int         workers     = 4;
    int         max_time    = 600;   // 默认 TTL 10 分钟
    int         backlog     = 128;   // 短连接并发突刺时防丢连接（libmsg 默认仅 10）
};

static std::atomic<bool> g_running{true};

static void signal_handler(int) {
    g_running = false;
}

class MemdbServer {
private:
    ServerConfig __cfg;
    Memdb<std::string, std::string> __db;
    pmc::net::RpcServer __rpc;
    std::mutex __mtx;

    static std::string _require_string(const json::object& req, const std::string& field) {
        auto it = req.find(field);
        if (it == req.end()) {
            throw std::runtime_error("missing required field: " + field);
        }
        if (!it->value().is_string()) {
            throw std::runtime_error("field '" + field + "' must be a string");
        }
        const auto& s = it->value().as_string();
        return std::string(s.data(), s.size());
    }

public:
    explicit MemdbServer(const ServerConfig& cfg)
        : __cfg(cfg)
        , __db(cfg.max_time)
        , __rpc(cfg.listen_host, cfg.listen_port,
                {.workers = cfg.workers, .listen_backlog = cfg.backlog})
    {
        _register_routes();
    }

    void start() { __rpc.start(); }
    void stop()  { __rpc.stop(); }

private:
    void _register_routes() {
        // ---- 健康检查 ----
        __rpc.add_route("ping", [](const json::object&) -> json::value {
            return "pong";
        });

        // ---- set: 插入键值对 ----
        __rpc.add_route("set", [this](const json::object& req) -> json::value {
            const auto& key = _require_string(req, "key");
            const auto& val = _require_string(req, "value");
            std::lock_guard<std::mutex> lock(__mtx);
            return __db.insert(key, val);
        });

        // ---- get: 查询键 ----
        __rpc.add_route("get", [this](const json::object& req) -> json::value {
            const auto& key = _require_string(req, "key");
            std::lock_guard<std::mutex> lock(__mtx);
            auto result = __db.search(key);
            if (result.has_value()) {
                return json::string(*result);
            }
            return nullptr;
        });

        // ---- del: 删除键 ----
        __rpc.add_route("del", [this](const json::object& req) -> json::value {
            const auto& key = _require_string(req, "key");
            std::lock_guard<std::mutex> lock(__mtx);
            return __db.remove(key);
        });

        // ---- update: 更新键值（可选刷新 TTL）----
        __rpc.add_route("update", [this](const json::object& req) -> json::value {
            const auto& key = _require_string(req, "key");
            const auto& val = _require_string(req, "value");
            bool refresh_ttl = false;
            if (auto it = req.find("refresh_ttl"); it != req.end() && it->value().is_bool()) {
                refresh_ttl = it->value().as_bool();
            }
            std::lock_guard<std::mutex> lock(__mtx);
            return __db.update(key, val, refresh_ttl);
        });

        // ---- exists: 检查键是否存在 ----
        __rpc.add_route("exists", [this](const json::object& req) -> json::value {
            const auto& key = _require_string(req, "key");
            std::lock_guard<std::mutex> lock(__mtx);
            return __db.search(key).has_value();
        });

        // ---- size: 当前条目数 ----
        __rpc.add_route("size", [this](const json::object&) -> json::value {
            std::lock_guard<std::mutex> lock(__mtx);
            return static_cast<int64_t>(__db.size());
        });

        // ---- show: 导出所有条目 ----
        __rpc.add_route("show", [this](const json::object&) -> json::value {
            std::lock_guard<std::mutex> lock(__mtx);
            json::array result;
            for (const auto& col : __db.get_db()) {
                json::object entry;
                entry["key"]   = json::string(col.key);
                entry["value"] = json::string(col.value);
                result.push_back(std::move(entry));
            }
            return result;
        });

        // ---- expire: 更新值并重置 TTL（仅已有键；等价于 update + refresh_ttl）----
        __rpc.add_route("expire", [this](const json::object& req) -> json::value {
            const auto& key = _require_string(req, "key");
            const auto& val = _require_string(req, "value");
            std::lock_guard<std::mutex> lock(__mtx);
            return __db.update(key, val, true);
        });
    }
};


int main(int argc, char** argv) {
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    ServerConfig cfg;

    po::options_description desc("memdb options");
    desc.add_options()
        ("help,h",                                  "display help")
        ("instance",  po::value<std::string>()->default_value("memdb"), "instance name")
        ("log-level", po::value<std::string>()->default_value("info"),  "log level")
        ("host",      po::value<std::string>()->default_value("127.0.0.1"), "listen host")
        ("port",      po::value<int>()->default_value(8011), "listen port")
        ("workers",   po::value<int>()->default_value(4),    "worker threads")
        ("max-time",  po::value<int>()->default_value(600),  "default TTL (seconds)")
        ("backlog",   po::value<int>()->default_value(128),  "listen() backlog");

    po::variables_map vm;
    po::store(po::parse_command_line(argc, argv, desc), vm);
    po::notify(vm);

    if (vm.count("help")) {
        std::cout << desc << std::endl;
        return 0;
    }

    cfg.listen_host = vm["host"].as<std::string>();
    cfg.listen_port = vm["port"].as<int>();
    cfg.workers     = vm["workers"].as<int>();
    cfg.max_time    = vm["max-time"].as<int>();
    cfg.backlog     = vm["backlog"].as<int>();

    auto instance  = vm["instance"].as<std::string>();
    auto log_level = vm["log-level"].as<std::string>();

    MemdbServer server(cfg);

    std::cout << "[memdb:" << instance << "] log-level=" << log_level
              << " listening on " << cfg.listen_host
              << ":" << cfg.listen_port << std::endl;

    std::thread server_thread([&server]() {
        server.start();
    });

    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    std::cout << "\n[memdb:" << instance << "] shutting down..." << std::endl;
    server.stop();
    if (server_thread.joinable()) {
        server_thread.join();
    }

    return 0;
}