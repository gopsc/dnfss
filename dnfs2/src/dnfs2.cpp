/** dnfs2 — C++20 树状网络文件系统（对齐 Python dnfs 模块）
 *
 * 基于 libhttp + libmsg + boost::json 实现，接口与 Python dnfs 完全兼容：
 *   POST /get_notes    获取子节点列表（无需鉴权）
 *   POST /add_note     添加节点（需 Token + AI 审核）
 *   POST /delete_note  删除节点（需 Token + 所有权）
 *   POST /update_note  更新节点（需 Token + 所有权 + AI 审核）
 *
 * 安全修复（相比 Python dnfs）：
 *   P0-4  统一封装 _call_db() 检查 success 后再访问 data
 *   P1-6  server.setMaxBodySize(10MB) 限制请求体大小
 *   P1-7  日志客户端复用主配置（CLI/ENV 的 db host/port）
 *   P1-8  update_note 改 pid≠0 时校验父节点存在
 *   P1-9  RpcClient 启用 retry_on_refused
 *   P1-11 note_id 显式 as_int64() + 类型校验
 *
 * 依赖: libhttp (HttpServer), libmsg (RpcClient/AsyncQueue), boost::json,
 *       OpenSSL, libcurl
 */

#include <us/HttpServer.hpp>
#include <us/HttpClient.hpp>
#include <us/RpcClient.hpp>
#include <us/AsyncQueue.hpp>

#include <boost/json.hpp>
#include <curl/curl.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace json = boost::json;
namespace http = boost::beast::http;
using namespace pmc::net;

// ============================================================================
// 常量
// ============================================================================

// 请求体大小上限（对齐 WAF，防 OOM）
static constexpr size_t kMaxBodySize = 10 * 1024 * 1024;

// 特殊节点
static constexpr size_t kSpMaxCode         = 512 * 1024;
static constexpr size_t kSpMaxData         = 3500 * 1024;
static constexpr size_t kSpReviewCodePrefix = 4000;
static constexpr size_t kSpMaxCss          = 256 * 1024;   // style 节点 CSS 上限
static constexpr size_t kSpMaxThemeVars    = 64;           // theme 节点变量条数上限
static constexpr size_t kSpMaxThemeKey     = 64;
static constexpr size_t kSpMaxThemeValue   = 256;

// 日志
static constexpr size_t kLogMaxMessage  = 8192;
static constexpr size_t kLogMaxExtra    = 4096;
static constexpr size_t kLogBatchSize   = 50;
static constexpr double kLogFlushInterval = 2.0;
static constexpr size_t kLogQueueMax    = 10000;
static constexpr double kLogRpcTimeout  = 5.0;

// HTTP 响应大小上限（对齐 WAF）
static constexpr size_t kHttpMaxResponse = 10 * 1024 * 1024;

// ============================================================================
// 工具函数
// ============================================================================

static std::string utc_now() {
    auto now = std::chrono::system_clock::now();
    std::time_t t = std::chrono::system_clock::to_time_t(now);
    struct tm tmbuf;
    gmtime_r(&t, &tmbuf);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tmbuf);
    return buf;
}

static int safe_stoi(const std::string& s, const char* field_name) {
    try {
        size_t pos = 0;
        int val = std::stoi(s, &pos);
        if (pos != s.size()) {
            throw std::runtime_error(std::string("invalid ") + field_name
                                     + ": '" + s + "' (trailing characters)");
        }
        return val;
    } catch (const std::runtime_error&) {
        throw;
    } catch (const std::exception& e) {
        throw std::runtime_error(std::string("invalid ") + field_name
                                 + ": '" + s + "' (" + e.what() + ")");
    }
}

// ============================================================================
// 配置
// ============================================================================

struct Config {
    std::string listen_host   = "127.0.0.1";
    int         listen_port   = 8006;
    int         threads       = 4;

    std::string db_host       = "127.0.0.1";
    int         db_port       = 8005;
    double      db_timeout    = 10.0;

    std::string review_url    = "http://127.0.0.1:8007/review";
    double      review_timeout = 10.0;

    std::string gateway_url   = "http://127.0.0.1:8443/whoami";
    double      gateway_timeout = 5.0;

    // CLI 标准化参数
    std::string instance      = "dnfs2";
    std::string log_level     = "info";       // debug|info|warn|error
};

static Config load_config(int argc, char* argv[]) {
    Config cfg;

    // 0. 预扫描 --help/-h（在正式解析前处理）
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            std::printf(
                "dnfs2 — C++20 树状网络文件系统\n\n"
                "用法: dnfs2 [选项]\n\n"
                "选项:\n"
                "  --host <addr>          绑定地址（别名: --addr/-a），默认 127.0.0.1\n"
                "  --port <num>           监听端口（别名: -p），默认 8006\n"
                "  --threads <num>        工作线程数（别名: -t），默认 4\n"
                "  --db-host <addr>       数据库地址，默认 127.0.0.1\n"
                "  --db-port <num>        数据库端口，默认 8005\n"
                "  --db-timeout <sec>     数据库超时秒数，默认 10\n"
                "  --review-url <url>     AI 审核服务 URL，默认 http://127.0.0.1:8007/review\n"
                "  --review-timeout <sec> AI 审核超时秒数，默认 10\n"
                "  --gateway-url <url>    网关验签 URL，默认 http://127.0.0.1:8443/whoami\n"
                "  --gateway-timeout <sec> 网关超时秒数，默认 5\n"
                "  --log-level <level>    debug|info|warn|error，默认 info\n"
                "  --instance <name>      实例名（注册中心用），默认 dnfs2\n"
                "  --help, -h             显示此帮助并退出\n\n"
                "配置优先级: CLI > 环境变量 > 默认值\n");
            std::exit(0);
        }
    }

    // 1. 环境变量覆盖（Docker 兼容）
    if (const char* v = std::getenv("LISTEN_ADDR"))  cfg.listen_host = v;
    if (const char* v = std::getenv("LISTEN_PORT"))  cfg.listen_port = safe_stoi(v, "LISTEN_PORT");
    if (const char* v = std::getenv("DB_HOST"))      cfg.db_host = v;
    if (const char* v = std::getenv("DB_PORT"))      cfg.db_port = safe_stoi(v, "DB_PORT");
    if (const char* v = std::getenv("REVIEW_URL"))   cfg.review_url = v;
    if (const char* v = std::getenv("GATEWAY_URL"))  cfg.gateway_url = v;

    // 2. 命令行参数（最高优先级）
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "--addr"    || arg == "-a" || arg == "--host") && i + 1 < argc) cfg.listen_host = argv[++i];
        else if ((arg == "--port" || arg == "-p") && i + 1 < argc) cfg.listen_port = safe_stoi(argv[++i], "--port");
        else if ((arg == "--threads" || arg == "-t") && i + 1 < argc) cfg.threads = safe_stoi(argv[++i], "--threads");
        else if (arg == "--db-host" && i + 1 < argc) cfg.db_host = argv[++i];
        else if (arg == "--db-port" && i + 1 < argc) cfg.db_port = safe_stoi(argv[++i], "--db-port");
        else if (arg == "--db-timeout" && i + 1 < argc) cfg.db_timeout = std::stod(argv[++i]);
        else if (arg == "--review-url" && i + 1 < argc) cfg.review_url = argv[++i];
        else if (arg == "--review-timeout" && i + 1 < argc) cfg.review_timeout = std::stod(argv[++i]);
        else if (arg == "--gateway-url" && i + 1 < argc) cfg.gateway_url = argv[++i];
        else if (arg == "--gateway-timeout" && i + 1 < argc) cfg.gateway_timeout = std::stod(argv[++i]);
        else if (arg == "--log-level" && i + 1 < argc) cfg.log_level = argv[++i];
        else if (arg == "--instance" && i + 1 < argc) cfg.instance = argv[++i];
    }

    // 3. 数值范围校验
    if (cfg.listen_port < 1 || cfg.listen_port > 65535) {
        std::fprintf(stderr, "[dnfs2] invalid listen_port: %d (must be 1-65535)\n", cfg.listen_port);
        std::exit(1);
    }
    if (cfg.db_port < 1 || cfg.db_port > 65535) {
        std::fprintf(stderr, "[dnfs2] invalid db_port: %d (must be 1-65535)\n", cfg.db_port);
        std::exit(1);
    }
    if (cfg.threads < 1 || cfg.threads > 256) {
        std::fprintf(stderr, "[dnfs2] invalid threads: %d (must be 1-256)\n", cfg.threads);
        std::exit(1);
    }
    if (cfg.db_timeout <= 0 || cfg.db_timeout > 60.0) {
        std::fprintf(stderr, "[dnfs2] invalid db_timeout: %f (must be in (0, 60])\n", cfg.db_timeout);
        std::exit(1);
    }
    if (cfg.review_timeout <= 0 || cfg.review_timeout > 60.0) {
        std::fprintf(stderr, "[dnfs2] invalid review_timeout: %f (must be in (0, 60])\n", cfg.review_timeout);
        std::exit(1);
    }
    if (cfg.gateway_timeout <= 0 || cfg.gateway_timeout > 60.0) {
        std::fprintf(stderr, "[dnfs2] invalid gateway_timeout: %f (must be in (0, 60])\n", cfg.gateway_timeout);
        std::exit(1);
    }

    // log_level 合法性校验
    {
        std::string lv = cfg.log_level;
        if (lv == "warning") lv = "warn";
        if (lv != "debug" && lv != "info" && lv != "warn" && lv != "error") {
            std::fprintf(stderr, "[dnfs2] invalid log_level: %s (must be debug|info|warn|error)\n",
                         cfg.log_level.c_str());
            std::exit(1);
        }
        cfg.log_level = lv;
    }

    return cfg;
}

// ============================================================================
// 异常类
// ============================================================================

class DnfsExp : public std::runtime_error {
public:
    DnfsExp(const std::string& msg, int code)
        : std::runtime_error(msg), status_(code) {}
    int status() const noexcept { return status_; }
private:
    int status_;
};

// ============================================================================
// 特殊节点校验
// ============================================================================

static const std::vector<std::string> kSpTypes = {"js", "file", "style", "theme"};

// 识别特殊节点信封：合法信封返回 object；普通内容返回 nullptr；
// 以 {"_sp" 开头但 JSON 非法视为格式错误（抛异常）。
static json::value parse_sp(const std::string& content) {
    if (content.empty()) return nullptr;

    size_t start = content.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return nullptr;
    std::string s = content.substr(start);
    if (s.size() < 6 || s.substr(0, 6) != "{\"_sp\"") return nullptr;

    try {
        json::value obj = json::parse(s);
        if (obj.is_object()) {
            const auto& o = obj.as_object();
            if (o.contains("_sp") && o.at("_sp").is_string()) {
                std::string sp_type = std::string(o.at("_sp").as_string());
                for (const auto& t : kSpTypes) {
                    if (sp_type == t) return obj;
                }
            }
        }
        return nullptr;
    } catch (const std::exception&) {
        throw DnfsExp("特殊节点格式非法（JSON 解析失败）", 400);
    }
}

static void validate_sp(const json::object& sp) {
    std::string t = std::string(sp.at("_sp").as_string());

    if (t == "js") {
        if (!sp.contains("code") || !sp.at("code").is_string())
            throw DnfsExp("前端代码不能为空", 400);
        std::string code = std::string(sp.at("code").as_string());
        if (code.empty() || code.find_first_not_of(" \t\r\n") == std::string::npos)
            throw DnfsExp("前端代码不能为空", 400);
        if (code.size() > kSpMaxCode)
            throw DnfsExp("前端代码过大（上限 512KB）", 400);
    } else if (t == "file") {
        if (!sp.contains("name") || !sp.at("name").is_string())
            throw DnfsExp("文件节点缺少文件名", 400);
        std::string name = std::string(sp.at("name").as_string());
        if (name.empty() || name.find_first_not_of(" \t\r\n") == std::string::npos)
            throw DnfsExp("文件节点缺少文件名", 400);
        if (name.size() > 255)
            throw DnfsExp("文件名过长", 400);
        if (!sp.contains("data") || !sp.at("data").is_string())
            throw DnfsExp("文件节点缺少数据", 400);
        std::string data = std::string(sp.at("data").as_string());
        if (data.empty())
            throw DnfsExp("文件节点缺少数据", 400);
        if (data.size() > kSpMaxData)
            throw DnfsExp("文件过大（上限约 2.6MB）", 400);
    } else if (t == "style") {
        if (!sp.contains("css") || !sp.at("css").is_string())
            throw DnfsExp("样式节点缺少 css", 400);
        std::string css = std::string(sp.at("css").as_string());
        if (css.empty() || css.find_first_not_of(" \t\r\n") == std::string::npos)
            throw DnfsExp("样式节点 css 不能为空", 400);
        if (css.size() > kSpMaxCss)
            throw DnfsExp("样式节点 css 过大（上限 256KB）", 400);
    } else if (t == "theme") {
        if (!sp.contains("vars") || !sp.at("vars").is_object())
            throw DnfsExp("主题节点缺少 vars 对象", 400);
        const auto& vars = sp.at("vars").as_object();
        if (vars.empty())
            throw DnfsExp("主题节点 vars 不能为空", 400);
        if (vars.size() > kSpMaxThemeVars)
            throw DnfsExp("主题变量过多（上限 64 条）", 400);
        auto is_name_char = [](char c) {
            return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                || (c >= '0' && c <= '9') || c == '-' || c == '_';
        };
        for (const auto& kv : vars) {
            std::string key(kv.key());
            // 仅允许 CSS 自定义属性名：-- 前缀 + [A-Za-z0-9_-]
            if (key.size() < 3 || key.size() > kSpMaxThemeKey
                || key[0] != '-' || key[1] != '-')
                throw DnfsExp("主题变量名必须以 -- 开头", 400);
            for (char c : key) {
                if (!is_name_char(c))
                    throw DnfsExp("主题变量名含非法字符", 400);
            }
            if (!kv.value().is_string())
                throw DnfsExp("主题变量值必须是字符串", 400);
            std::string val = std::string(kv.value().as_string());
            if (val.empty() || val.size() > kSpMaxThemeValue)
                throw DnfsExp("主题变量值为空或过长（上限 256 字符）", 400);
            // 值禁止 {}<>：防止逃逸 :root 变量上下文注入任意规则，
            // 保持 theme 相对 style 的"仅换肤"克制定位
            if (val.find_first_of("{}<>") != std::string::npos)
                throw DnfsExp("主题变量值含非法字符（{}<>）", 400);
        }
    }
}

// 特殊节点送审文本：剔除 base64 负载
static std::string sp_review_text(const std::string& title, const json::object& sp) {
    std::vector<std::string> parts;
    parts.push_back(title);

    if (sp.contains("note") && sp.at("note").is_string()) {
        std::string note = std::string(sp.at("note").as_string());
        if (!note.empty()) parts.push_back(note);
    }

    std::string t = std::string(sp.at("_sp").as_string());
    if (t == "js") {
        if (sp.contains("code") && sp.at("code").is_string()) {
            std::string code = std::string(sp.at("code").as_string());
            if (code.size() > kSpReviewCodePrefix) code = code.substr(0, kSpReviewCodePrefix);
            parts.push_back(code);
        }
    } else if (t == "style") {
        if (sp.contains("css") && sp.at("css").is_string()) {
            std::string css = std::string(sp.at("css").as_string());
            if (css.size() > kSpReviewCodePrefix) css = css.substr(0, kSpReviewCodePrefix);
            parts.push_back(css);
        }
    } else if (t == "theme") {
        if (sp.contains("vars") && sp.at("vars").is_object())
            parts.push_back(json::serialize(sp.at("vars")));
    } else {
        if (sp.contains("name") && sp.at("name").is_string())
            parts.push_back(std::string(sp.at("name").as_string()));
    }

    std::string result;
    for (size_t i = 0; i < parts.size(); ++i) {
        if (i > 0) result += "\n";
        result += parts[i];
    }
    return result;
}

// ============================================================================
// 日志
// ============================================================================

struct LogEntry {
    std::string ts;
    std::string service;
    std::string level;
    std::string message;
    std::string trace_id;
    std::string extra;
};

// ============================================================================
// HTTP 响应辅助
// ============================================================================

static http::response<http::string_body> make_response(
    http::status status,
    const std::string& body,
    const std::string& content_type,
    const http::request<http::string_body>& req)
{
    http::response<http::string_body> res;
    res.result(status);
    res.version(req.version());
    res.set(http::field::content_type, content_type);
    res.keep_alive(req.keep_alive());

    // 安全响应头
    res.set("X-Frame-Options", "SAMEORIGIN");
    res.set("X-XSS-Protection", "1; mode=block");
    res.set("X-Content-Type-Options", "nosniff");
    res.set("Referrer-Policy", "strict-origin-when-cross-origin");
    res.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
    res.set("Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self'; connect-src 'self'; frame-ancestors 'self'");

    res.body() = body;
    res.prepare_payload();
    return res;
}

static http::response<http::string_body> make_json(
    http::status status,
    const std::string& json_str,
    const http::request<http::string_body>& req)
{
    return make_response(status, json_str, "application/json", req);
}

static http::response<http::string_body> make_json_obj(
    http::status status,
    const json::object& obj,
    const http::request<http::string_body>& req)
{
    return make_json(status, json::serialize(obj), req);
}

static http::response<http::string_body> make_error(
    http::status status,
    const std::string& error_msg,
    const http::request<http::string_body>& req)
{
    json::object obj;
    obj["error"] = error_msg;
    return make_json_obj(status, obj, req);
}

// ============================================================================
// Dnfs2Server
// ============================================================================

class Dnfs2Server {
public:
    explicit Dnfs2Server(Config cfg)
        : _cfg(std::move(cfg)),
          _rpc(_cfg.db_host, _cfg.db_port, "dnfs2", _cfg.db_timeout,
               /*retry_on_refused=*/true),
          _log_rpc(_cfg.db_host, _cfg.db_port, "dnfs2-log", kLogRpcTimeout,
                   /*retry_on_refused=*/false) {

        // 初始化异步日志队列
        _log_queue = std::make_unique<AsyncQueue<LogEntry>>(
            [this](std::vector<LogEntry>&& batch) { _send_log_batch(std::move(batch)); },
            AsyncQueueConfig{kLogBatchSize, kLogFlushInterval, kLogQueueMax, 0.5},
            [](const std::exception& e, std::vector<LogEntry>&&) {
                std::fprintf(stderr, "[dnfs2] 日志队列异常: %s\n", e.what());
            }
        );
        _log_queue->start();

        std::printf("[dnfs2] 配置来源: CLI + ENV + 默认值\n");
        std::printf("[dnfs2] instance=%s log-level=%s\n",
                    _cfg.instance.c_str(), _cfg.log_level.c_str());
        std::printf("[dnfs2] Token 验签: gateway %s\n", _cfg.gateway_url.c_str());
    }

    ~Dnfs2Server() {
        if (_log_queue) _log_queue->shutdown();
    }

    void run() {
        HttpServer server(_cfg.listen_host, static_cast<unsigned short>(_cfg.listen_port),
                          static_cast<unsigned int>(_cfg.threads));

        // 请求体大小限制（P1-6 修复）
        server.setMaxBodySize(kMaxBodySize);

        // 注册路由
        server.post("/get_notes",   [this](const auto& req, const auto&) { return _handle_get_notes(req); });
        server.post("/add_note",    [this](const auto& req, const auto&) { return _handle_add_note(req); });
        server.post("/delete_note", [this](const auto& req, const auto&) { return _handle_delete_note(req); });
        server.post("/update_note", [this](const auto& req, const auto&) { return _handle_update_note(req); });

        // 信号处理（优雅关闭）
        _server = &server;
        std::signal(SIGINT,  &Dnfs2Server::_signal_handler);
        std::signal(SIGTERM, &Dnfs2Server::_signal_handler);

        std::thread stop_watcher([this]() {
            while (!_should_stop.load()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
            }
            if (_server) _server->stop();
        });

        _log("INFO", "dnfs2", "服务启动，监听 http://" + _cfg.listen_host + ":"
             + std::to_string(_cfg.listen_port)
             + "（db=" + _cfg.db_host + ":" + std::to_string(_cfg.db_port)
             + ", ai_review=" + _cfg.review_url + "）");

        server.run();

        // 确保停止标志置位（处理 server.run() 非信号退出的情况）
        _should_stop.store(true);
        if (stop_watcher.joinable()) stop_watcher.join();

        _log("INFO", "dnfs2", "服务停止");
        if (_log_queue) _log_queue->shutdown();
    }

private:
    Config _cfg;
    RpcClient _rpc;          // db 调用（retry_on_refused=true，P1-9 修复）
    RpcClient _log_rpc;      // 日志写入（不重试，失败即丢弃）

    std::unique_ptr<AsyncQueue<LogEntry>> _log_queue;

    HttpServer* _server = nullptr;
    static std::atomic<bool> _should_stop;

    static void _signal_handler(int) {
        _should_stop.store(true);
    }

    // ---- 日志 --------------------------------------------------------------

    void _log(const std::string& level, const std::string& service,
              const std::string& message, const std::string& trace_id = "",
              const std::string& extra = "") {
        // 日志级别过滤
        static const std::unordered_map<std::string, int> kLogLevels = {
            {"debug", 0}, {"DEBUG", 0},
            {"info", 1},  {"INFO", 1},
            {"warn", 2},  {"WARN", 2}, {"warning", 2}, {"WARNING", 2},
            {"error", 3}, {"ERROR", 3}
        };
        auto it_lvl = kLogLevels.find(level);
        int msg_lvl = (it_lvl != kLogLevels.end()) ? it_lvl->second : 1;
        auto it_cfg = kLogLevels.find(_cfg.log_level);
        int cfg_lvl = (it_cfg != kLogLevels.end()) ? it_cfg->second : 1;
        if (msg_lvl < cfg_lvl) return;

        LogEntry e;
        e.ts = utc_now();
        e.service = service;
        e.level = level;
        e.message = (message.size() > kLogMaxMessage) ? message.substr(0, kLogMaxMessage) : message;
        e.trace_id = trace_id;
        e.extra = (extra.size() > kLogMaxExtra) ? extra.substr(0, kLogMaxExtra) : extra;
        if (_log_queue) _log_queue->push(std::move(e));
    }

    void _send_log_batch(std::vector<LogEntry>&& batch) {
        for (auto& e : batch) {
            try {
                json::object values;
                values["ts"]       = e.ts;
                values["service"]  = e.service;
                values["level"]    = e.level;
                values["message"]  = e.message;
                if (!e.trace_id.empty()) values["trace_id"] = e.trace_id;
                else values["trace_id"] = nullptr;
                if (!e.extra.empty())   values["extra"]    = e.extra;
                else values["extra"]    = nullptr;

                json::object payload;
                payload["action"] = "insert";
                payload["table"]  = "Logs";
                payload["values"] = values;

                _log_rpc.call("execute", payload);
            } catch (const std::exception& ex) {
                std::fprintf(stderr, "[dnfs2] 日志写入失败: %s\n", ex.what());
            }
        }
    }

    // ---- db 调用封装（P0-4 修复：统一检查 success）--------------------------

    struct DbResult {
        bool success = false;
        json::value data;        // 成功时为 data 字段
        std::string error;       // 失败时为错误信息
    };

    DbResult _call_db(const json::object& payload) {
        try {
            json::value data = _rpc.call("execute", payload);
            return {true, data, ""};
        } catch (const RpcError& e) {
            return {false, nullptr, e.what()};
        } catch (const std::exception& e) {
            // 网络错误（连接失败、超时等）
            _log("ERROR", "dnfs2", std::string("db 调用失败: ") + e.what());
            throw DnfsExp("数据库服务暂时不可用", 502);
        }
    }

    // 查询节点是否存在（P2-N6 修复：db 业务错误抛 500，不误判为"不存在"）
    bool _note_exists(int64_t id) {
        json::object payload;
        payload["action"] = "select";
        payload["table"]  = "Notes";
        payload["where"]  = json::object{{"id", id}};
        payload["limit"]  = 1;

        DbResult r = _call_db(payload);
        if (!r.success) {
            _log("ERROR", "dnfs2", "查询节点存在性失败 id=" + std::to_string(id)
                 + " err=" + r.error);
            throw DnfsExp("查询节点失败", 500);
        }
        return r.data.is_array() && !r.data.as_array().empty();
    }

    // 查询节点 owner：节点不存在返回 nullopt；存在但无 owner 字段返回空字符串（无人匹配，仅管理员有权）
    std::optional<std::string> _note_owner(int64_t id) {
        json::object payload;
        payload["action"] = "select";
        payload["table"]  = "Notes";
        payload["where"]  = json::object{{"id", id}};
        payload["limit"]  = 1;

        DbResult r = _call_db(payload);
        if (!r.success) {
            _log("ERROR", "dnfs2", "查询节点 owner 失败 id=" + std::to_string(id)
                 + " err=" + r.error);
            throw DnfsExp("查询节点失败", 500);
        }
        if (!r.data.is_array() || r.data.as_array().empty()) return std::nullopt;
        const auto& note = r.data.as_array()[0];
        if (note.is_object() && note.as_object().contains("owner")
            && note.as_object().at("owner").is_string()) {
            return std::string(note.as_object().at("owner").as_string());
        }
        return std::string();
    }

    // 观看人数自增（get_notes 打开节点 pid 时调用）。
    // db2 的 update 仅支持字面量绑定（SET col = ?），无法表达 views = views + 1，
    // 故采用「读当前值 → 写 +1」。读改写非原子，高并发下可能少计，作为浏览计数可接受。
    // 任何失败仅记 WARNING 日志并吞掉，绝不影响 get_notes 主流程。
    void _bump_views(int64_t id) {
        try {
            json::object sel;
            sel["action"]  = "select";
            sel["table"]   = "Notes";
            sel["columns"] = json::array{"views"};
            sel["where"]   = json::object{{"id", id}};
            sel["limit"]   = 1;

            DbResult r = _call_db(sel);
            if (!r.success || !r.data.is_array() || r.data.as_array().empty())
                return;  // 节点不存在或查询失败：静默跳过

            const auto& note = r.data.as_array()[0];
            int64_t views = 0;
            if (note.is_object() && note.as_object().contains("views")
                && note.as_object().at("views").is_int64()) {
                views = note.as_object().at("views").as_int64();
            }

            json::object upd;
            upd["action"] = "update";
            upd["table"]  = "Notes";
            upd["values"] = json::object{{"views", views + 1}};
            upd["where"]  = json::object{{"id", id}};

            DbResult ur = _call_db(upd);
            if (!ur.success) {
                _log("WARNING", "dnfs2", "views 自增失败 id=" + std::to_string(id)
                     + " err=" + ur.error);
            }
        } catch (const std::exception& e) {
            // _call_db 网络异常会抛 DnfsExp(502)，一并捕获，绝不影响主流程
            _log("WARNING", "dnfs2", std::string("views 自增异常 id=")
                 + std::to_string(id) + ": " + e.what());
        }
    }

    // 统计各节点的子节点数（get_notes 用于前端"分享数"展示）。
    // 用一次 IN 批量查询所有孙节点（仅取 pid 列，避免拉取 content），按 pid 分组计数，
    // 避免对每个节点单独查询。best-effort：任何失败仅记 WARNING 并返回已有结果，
    // 绝不影响 get_notes 主流程（前端缺省显示 0）。
    std::unordered_map<int64_t, int64_t> _child_counts(const json::array& items) {
        std::unordered_map<int64_t, int64_t> counts;
        try {
            json::array ids;
            for (const auto& item : items) {
                if (item.is_object() && item.as_object().contains("id")
                    && item.as_object().at("id").is_int64()) {
                    ids.push_back(item.as_object().at("id").as_int64());
                }
            }
            if (ids.empty()) return counts;

            json::object payload;
            payload["action"]  = "select";
            payload["table"]   = "Notes";
            payload["columns"] = json::array{"pid"};
            payload["where"]   = json::object{{"pid", json::object{{"in", ids}}}};

            DbResult r = _call_db(payload);
            if (!r.success || !r.data.is_array()) {
                _log("WARNING", "dnfs2", "child_count 统计失败: " + r.error);
                return counts;
            }
            for (const auto& row : r.data.as_array()) {
                if (row.is_object() && row.as_object().contains("pid")
                    && row.as_object().at("pid").is_int64()) {
                    counts[row.as_object().at("pid").as_int64()]++;
                }
            }
        } catch (const std::exception& e) {
            _log("WARNING", "dnfs2", std::string("child_count 统计异常: ") + e.what());
        }
        return counts;
    }

    // ---- Token 验签（通过 gateway /whoami）---------------------------------

    struct UserInfo {
        std::string user;
        bool is_admin = false;
    };

    // 返回 UserInfo；验签失败返回 nullopt；网络异常抛 DnfsExp(503)
    std::optional<UserInfo> decode_token(const std::string& token) {
        if (token.empty()) return std::nullopt;

        HttpClient http;
        http.set_max_response_size(kHttpMaxResponse);
        http.set_timeout(static_cast<long>(_cfg.gateway_timeout));
        http.set_header("Content-Type: application/json");
        http.set_header("Authorization: Bearer " + token);

        std::string response_body;
        try {
            http.post_stream_sync(_cfg.gateway_url, "{}",
                [&response_body](const std::string& chunk) {
                    response_body += chunk;
                });
        } catch (const std::exception& e) {
            _log("WARNING", "dnfs2", std::string("Token 验签失败（gateway 异常）: ") + e.what());
            throw DnfsExp("认证服务暂时不可用", 503);
        }

        long code = http.get_response_code();
        if (code != 200) {
            _log("WARNING", "dnfs2", "Token 验签失败（gateway 返回 " + std::to_string(code) + "）");
            throw DnfsExp("认证服务暂时不可用", 503);
        }

        json::value data;
        try {
            data = json::parse(response_body);
        } catch (const std::exception&) {
            _log("WARNING", "dnfs2", "Token 验签失败（gateway 响应非 JSON）");
            throw DnfsExp("认证服务暂时不可用", 503);
        }

        if (!data.is_object()) return std::nullopt;
        const auto& obj = data.as_object();

        if (!obj.contains("login") || !obj.at("login").is_bool() || !obj.at("login").as_bool()) {
            return std::nullopt;
        }

        UserInfo info;
        if (obj.contains("user") && obj.at("user").is_string()) {
            info.user = std::string(obj.at("user").as_string());
        }
        if (obj.contains("is_admin") && obj.at("is_admin").is_bool()) {
            info.is_admin = obj.at("is_admin").as_bool();
        }
        return info;
    }

    // 从请求头提取 Token
    static std::string extract_token(const http::request<http::string_body>& req) {
        auto it = req.find(http::field::authorization);
        if (it == req.end()) return "";
        std::string auth = std::string(it->value());
        if (auth.rfind("Bearer ", 0) == 0) {
            return auth.substr(7);
        }
        return "";
    }

    // ---- AI 内容审核（fail-closed）-----------------------------------------

    bool review_content(const std::string& content) {
        HttpClient http;
        http.set_max_response_size(kHttpMaxResponse);
        http.set_timeout(static_cast<long>(_cfg.review_timeout));
        http.set_header("Content-Type: application/json");

        json::object payload;
        payload["content"] = content;
        std::string body = json::serialize(payload);

        std::string response_body;
        try {
            http.post_stream_sync(_cfg.review_url, body,
                [&response_body](const std::string& chunk) {
                    response_body += chunk;
                });
        } catch (const std::exception& e) {
            // fail-closed：网络异常拒绝
            _log("WARNING", "dnfs2", std::string("AI 审核失败: ") + e.what());
            return false;
        }

        long code = http.get_response_code();
        if (code != 200) {
            _log("WARNING", "dnfs2", "AI 审核返回 " + std::to_string(code));
            return false;
        }

        json::value data;
        try {
            data = json::parse(response_body);
        } catch (const std::exception&) {
            return false;  // fail-closed
        }

        if (!data.is_object()) return false;
        const auto& obj = data.as_object();

        // invalid 为 false 或 "false" 时表示内容安全
        if (auto it = obj.find("invalid"); it != obj.end()) {
            const auto& v = it->value();
            if (v.is_bool()) return !v.as_bool();
            if (v.is_string()) return std::string(v.as_string()) == "false";
        }
        return false;  // 未知响应，fail-closed
    }

    // ---- JSON 请求体解析 ---------------------------------------------------

    static json::object parse_request_body(const http::request<http::string_body>& req) {
        const std::string& body = req.body();
        if (body.empty()) {
            throw DnfsExp("请求体不能为空", 400);
        }
        json::value val;
        try {
            val = json::parse(body);
        } catch (const std::exception&) {
            throw DnfsExp("请求体不是合法 JSON", 400);
        }
        if (!val.is_object()) {
            throw DnfsExp("请求体必须是 JSON 对象", 400);
        }
        return val.as_object();
    }

    // 从 JSON 对象获取整数（P1-11 修复：严格类型校验）
    static int64_t get_int(const json::object& obj, const std::string& key) {
        auto it = obj.find(key);
        if (it == obj.end() || !it->value().is_int64()) {
            throw DnfsExp("非法参数: " + key + " 必须是整数", 400);
        }
        return it->value().as_int64();
    }

    static std::string get_string(const json::object& obj, const std::string& key) {
        auto it = obj.find(key);
        if (it == obj.end() || !it->value().is_string()) {
            throw DnfsExp("非法参数: " + key + " 必须是字符串", 400);
        }
        std::string s = std::string(it->value().as_string());
        if (s.empty()) {
            throw DnfsExp("非法参数: " + key + " 不能为空", 400);
        }
        return s;
    }

    // ---- 路由 handler ------------------------------------------------------

    http::response<http::string_body> _handle_get_notes(
        const http::request<http::string_body>& req)
    {
        try {
            json::object body = parse_request_body(req);

            // pid 参数校验（P2-15：严格类型校验，非空字符串/None 报错）
            int64_t pid = 0;
            if (auto it = body.find("pid"); it != body.end()) {
                if (!it->value().is_int64()) {
                    throw DnfsExp("非法参数: pid 必须是整数", 400);
                }
                pid = it->value().as_int64();
                if (pid < 0) {
                    throw DnfsExp("非法参数: pid 不能为负数", 400);
                }
            }

            json::object payload;
            payload["action"] = "select";
            payload["table"]  = "Notes";
            payload["where"]  = json::object{{"pid", pid}};

            DbResult r = _call_db(payload);
            if (!r.success) {
                _log("ERROR", "dnfs2", "get_notes 查询失败: " + r.error);
                json::object err;
                err["error"] = "查询失败";
                return make_json_obj(http::status::internal_server_error, err, req);
            }

            // 打开节点 pid 时观看人数 +1（pid=0 为虚拟根节点，无对应行，跳过）。
            // best-effort：失败仅记日志，不影响返回结果。
            if (pid != 0) {
                _bump_views(pid);
            }

            // 解析当前用户（可选）：为每个节点附带 can_operate 标记
            std::optional<UserInfo> info = std::nullopt;
            std::string token = extract_token(req);
            if (!token.empty()) {
                try {
                    info = decode_token(token);
                } catch (const DnfsExp&) {
                    info = std::nullopt;  // Token 无效时，所有节点无权
                }
            }

            if (r.data.is_array()) {
                json::array result = r.data.as_array();
                // 一次性统计各节点的子节点数（供前端"分享数"展示），避免逐节点查询
                auto child_counts = _child_counts(result);
                for (auto& item : result) {
                    if (item.is_object()) {
                        json::object& note = item.as_object();
                        bool can_operate = false;
                        if (info) {
                            if (info->is_admin) {
                                can_operate = true;
                            } else if (note.contains("owner") && note.at("owner").is_string()) {
                                can_operate = std::string(note.at("owner").as_string()) == info->user;
                            }
                        }
                        note["can_operate"] = can_operate;
                        int64_t nid = (note.contains("id") && note.at("id").is_int64())
                                      ? note.at("id").as_int64() : 0;
                        auto cit = child_counts.find(nid);
                        note["child_count"] = (cit != child_counts.end()) ? cit->second : 0;
                    }
                }
                _log("INFO", "dnfs2", "get_notes 查询 pid=" + std::to_string(pid)
                     + " 返回 " + std::to_string(result.size()) + " 条");
                return make_json(http::status::ok, json::serialize(result), req);
            }

            json::array empty;
            return make_json(http::status::ok, json::serialize(empty), req);

        } catch (const DnfsExp& e) {
            _log("WARNING", "dnfs2", std::string("get_notes: ") + e.what());
            return make_error(static_cast<http::status>(e.status()), e.what(), req);
        } catch (const std::exception& e) {
            _log("ERROR", "dnfs2", std::string("get_notes 内部错误: ") + e.what());
            return make_error(http::status::internal_server_error, "内部错误", req);
        }
    }

    http::response<http::string_body> _handle_add_note(
        const http::request<http::string_body>& req)
    {
        try {
            json::object body = parse_request_body(req);

            int64_t pid = get_int(body, "pid");
            std::string title = get_string(body, "title");
            std::string content = get_string(body, "content");

            if (pid < 0) {
                throw DnfsExp("非法参数: pid 不能为负数", 400);
            }

            // Token 验签
            std::string token = extract_token(req);
            auto info = decode_token(token);
            if (!info) {
                throw DnfsExp("未授权访问", 401);
            }

            // 父节点存在性 + 拥有者（特殊节点发布权限判定用）
            std::optional<std::string> parent_owner;
            if (pid != 0) {
                parent_owner = _note_owner(pid);
                if (!parent_owner) {
                    throw DnfsExp("父节点不存在", 400);
                }
            }

            // 特殊节点校验：父节点拥有者或管理员可发布（根层 pid=0 无拥有者，仅管理员）
            json::value sp = parse_sp(content);
            if (!sp.is_null()) {
                if (!info->is_admin
                    && !(parent_owner && *parent_owner == info->user)) {
                    throw DnfsExp("特殊节点仅当前节点拥有者或管理员可发布", 403);
                }
                validate_sp(sp.as_object());
            }

            // 内容审核（特殊节点剔除 base64 负载）
            std::string review_text_val;
            if (!sp.is_null()) {
                review_text_val = sp_review_text(title, sp.as_object());
            } else {
                review_text_val = title + "\n" + content;
            }
            if (!review_content(review_text_val)) {
                _log("WARNING", "dnfs2", "add_note 内容审核不通过 title=" + title);
                throw DnfsExp("内容审核不通过", 400);
            }

            // 执行插入
            json::object values;
            values["pid"]     = pid;
            values["owner"]   = info->user;
            values["title"]   = title;
            values["content"] = content;

            json::object payload;
            payload["action"] = "insert";
            payload["table"]  = "Notes";
            payload["values"] = values;

            DbResult r = _call_db(payload);
            if (!r.success) {
                throw DnfsExp("插入节点失败", 400);
            }

            _log("INFO", "dnfs2", "add_note 成功 title=" + title + " pid=" + std::to_string(pid),
                 "", json::serialize(json::object{{"user", info->user}}));

            json::object result;
            result["success"] = true;
            return make_json_obj(http::status::ok, result, req);

        } catch (const DnfsExp& e) {
            _log("WARNING", "dnfs2", std::string("add_note: ") + e.what());
            return make_error(static_cast<http::status>(e.status()), e.what(), req);
        } catch (const std::exception& e) {
            _log("ERROR", "dnfs2", std::string("add_note 内部错误: ") + e.what());
            return make_error(http::status::internal_server_error, "内部错误", req);
        }
    }

    http::response<http::string_body> _handle_delete_note(
        const http::request<http::string_body>& req)
    {
        try {
            json::object body = parse_request_body(req);

            // P1-11 修复：note_id 显式 as_int64() + 类型校验
            int64_t note_id = get_int(body, "id");
            if (note_id <= 0) {
                throw DnfsExp("Note ID is required", 400);
            }

            // Token 验签
            std::string token = extract_token(req);
            auto info = decode_token(token);
            if (!info) {
                throw DnfsExp("Warning: no Authorization", 401);
            }

            // 检查是否有子节点
            json::object child_payload;
            child_payload["action"] = "select";
            child_payload["table"]  = "Notes";
            child_payload["where"]  = json::object{{"pid", note_id}};

            DbResult child_r = _call_db(child_payload);
            if (!child_r.success || !child_r.data.is_array()) {
                _log("ERROR", "dnfs2", "delete_note 查询子节点失败 id=" + std::to_string(note_id)
                     + " err=" + child_r.error);
                throw DnfsExp("查询子节点失败", 500);
            }
            if (!child_r.data.as_array().empty()) {
                _log("WARNING", "dnfs2", "delete_note 存在子节点 id=" + std::to_string(note_id));
                throw DnfsExp("this node has a child", 400);
            }

            // 检查所有权
            json::object owner_payload;
            owner_payload["action"] = "select";
            owner_payload["table"]  = "Notes";
            owner_payload["where"]  = json::object{{"id", note_id}};

            DbResult owner_r = _call_db(owner_payload);
            if (!owner_r.success) {
                _log("ERROR", "dnfs2", "delete_note 查询节点失败 id=" + std::to_string(note_id)
                     + " err=" + owner_r.error);
                throw DnfsExp("查询节点失败", 500);
            }
            if (!owner_r.data.is_array() || owner_r.data.as_array().empty()) {
                throw DnfsExp("节点不存在", 404);
            }

            if (!info->is_admin) {
                const auto& note = owner_r.data.as_array()[0];
                std::string owner;
                if (note.is_object() && note.as_object().contains("owner")
                    && note.as_object().at("owner").is_string()) {
                    owner = std::string(note.as_object().at("owner").as_string());
                }
                if (owner != info->user) {
                    _log("WARNING", "dnfs2", "delete_note 所有权拒绝 id=" + std::to_string(note_id));
                    throw DnfsExp("It's not your note", 403);
                }
            }

            // 执行删除
            json::object delete_payload;
            delete_payload["action"] = "delete";
            delete_payload["table"]  = "Notes";
            delete_payload["where"]  = json::object{{"id", note_id}};

            DbResult r = _call_db(delete_payload);
            if (!r.success) {
                json::object result;
                result["success"] = false;
                return make_json_obj(http::status::bad_request, result, req);
            }

            _log("INFO", "dnfs2", "delete_note 成功 id=" + std::to_string(note_id),
                 "", json::serialize(json::object{{"user", info->user}}));

            json::object result;
            result["success"] = true;
            return make_json_obj(http::status::ok, result, req);

        } catch (const DnfsExp& e) {
            _log("WARNING", "dnfs2", std::string("delete_note: ") + e.what());
            return make_error(static_cast<http::status>(e.status()), e.what(), req);
        } catch (const std::exception& e) {
            _log("ERROR", "dnfs2", std::string("delete_note 内部错误: ") + e.what());
            return make_error(http::status::internal_server_error, "内部错误", req);
        }
    }

    http::response<http::string_body> _handle_update_note(
        const http::request<http::string_body>& req)
    {
        try {
            json::object body = parse_request_body(req);

            int64_t id = get_int(body, "id");
            int64_t pid = get_int(body, "pid");
            std::string title = get_string(body, "title");
            std::string content = get_string(body, "content");

            if (id <= 0) {
                throw DnfsExp("Invalid param", 400);
            }
            if (pid < 0) {
                throw DnfsExp("Invalid param", 400);
            }

            // Token 验签
            std::string token = extract_token(req);
            auto info = decode_token(token);
            if (!info) {
                throw DnfsExp("Warning: no Authorization", 401);
            }

            // 检查所有权
            json::object owner_payload;
            owner_payload["action"] = "select";
            owner_payload["table"]  = "Notes";
            owner_payload["where"]  = json::object{{"id", id}};

            DbResult owner_r = _call_db(owner_payload);
            if (!owner_r.success) {
                _log("ERROR", "dnfs2", "update_note 查询节点失败 id=" + std::to_string(id)
                     + " err=" + owner_r.error);
                throw DnfsExp("查询节点失败", 500);
            }
            if (!owner_r.data.is_array() || owner_r.data.as_array().empty()) {
                throw DnfsExp("节点不存在", 404);
            }

            // 提取节点 owner（所有权校验与特殊节点发布判定共用）
            std::string note_owner;
            {
                const auto& note = owner_r.data.as_array()[0];
                if (note.is_object() && note.as_object().contains("owner")
                    && note.as_object().at("owner").is_string()) {
                    note_owner = std::string(note.as_object().at("owner").as_string());
                }
            }

            if (!info->is_admin && note_owner != info->user) {
                _log("WARNING", "dnfs2", "update_note 所有权拒绝 id=" + std::to_string(id));
                throw DnfsExp("It's not your note", 403);
            }

            // P1-8 修复：pid≠0 时校验父节点存在
            if (pid != 0 && !_note_exists(pid)) {
                throw DnfsExp("父节点不存在", 400);
            }

            // 特殊节点校验：上方所有权校验已保证是节点拥有者或管理员，此处仅校验信封格式
            json::value sp = parse_sp(content);
            if (!sp.is_null()) {
                validate_sp(sp.as_object());
            }

            // 内容审核
            std::string review_text_val;
            if (!sp.is_null()) {
                review_text_val = sp_review_text(title, sp.as_object());
            } else {
                review_text_val = title + "\n" + content;
            }
            if (!review_content(review_text_val)) {
                _log("WARNING", "dnfs2", "update_note 内容审核不通过 id=" + std::to_string(id)
                     + " title=" + title);
                throw DnfsExp("review failed", 400);
            }

            // 执行更新
            json::object values;
            values["pid"]     = pid;
            values["title"]   = title;
            values["content"] = content;

            json::object payload;
            payload["action"] = "update";
            payload["table"]  = "Notes";
            payload["values"] = values;
            payload["where"]  = json::object{{"id", id}};

            DbResult r = _call_db(payload);
            if (!r.success) {
                json::object result;
                result["success"] = false;
                return make_json_obj(http::status::bad_request, result, req);
            }

            _log("INFO", "dnfs2", "update_note 成功 id=" + std::to_string(id) + " title=" + title,
                 "", json::serialize(json::object{{"user", info->user}}));

            json::object result;
            result["success"] = true;
            return make_json_obj(http::status::ok, result, req);

        } catch (const DnfsExp& e) {
            _log("WARNING", "dnfs2", std::string("update_note: ") + e.what());
            return make_error(static_cast<http::status>(e.status()), e.what(), req);
        } catch (const std::exception& e) {
            _log("ERROR", "dnfs2", std::string("update_note 内部错误: ") + e.what());
            return make_error(http::status::internal_server_error, "内部错误", req);
        }
    }
};

// 静态成员定义
std::atomic<bool> Dnfs2Server::_should_stop{false};

// ============================================================================
// main
// ============================================================================

int main(int argc, char* argv[]) {
    // curl_global_init 必须在多线程启动前调用（libcurl 要求，防首次 curl_easy_init 竞态）
    curl_global_init(CURL_GLOBAL_DEFAULT);

    int ret = 0;
    try {
        Config cfg = load_config(argc, argv);

        std::printf("[dnfs2] listen=%s:%d threads=%d instance=%s\n",
                    cfg.listen_host.c_str(), cfg.listen_port, cfg.threads,
                    cfg.instance.c_str());
        std::printf("[dnfs2] db=%s:%d ai_review=%s gateway=%s\n",
                    cfg.db_host.c_str(), cfg.db_port,
                    cfg.review_url.c_str(), cfg.gateway_url.c_str());
        std::fflush(stdout);

        Dnfs2Server server(std::move(cfg));
        server.run();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[dnfs2] fatal: %s\n", e.what());
        ret = 1;
    }
    curl_global_cleanup();
    return ret;
}
