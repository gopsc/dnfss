/*
 * confs2 — C++20 统一配置服务（pymsg RPC）
 *
 * 基于 libmsg RpcServer，协议与 Python confs 完全兼容，消费方零改动。
 * ConfigStore 磁盘缓存 + mtime 自动重载，安全机制逐项 C++ 实现。
 */

#include <us/Msg.hpp>
#include <us/RpcServer.hpp>
#include <us/RpcClient.hpp>

#include <boost/json.hpp>
#include <boost/program_options.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "LogC.hpp"

namespace json = boost::json;
namespace po = boost::program_options;
namespace fs = std::filesystem;

using confs2::LogC;

// ═══════════════════════════════════════════════════════════════════════
// 1. 常量与异常
// ═══════════════════════════════════════════════════════════════════════

static const std::unordered_set<std::string> kAllowedExt = {
    ".ini", ".conf", ".cfg", ".json", ".yaml", ".yml", ".toml", ".txt"
};

static constexpr std::size_t kMaxConfigSize = 1 * 1024 * 1024;  // 1 MiB

class ConfigError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};
class ConfigNotFound : public ConfigError {
public:
    using ConfigError::ConfigError;
};
class ConfigNameInvalid : public ConfigError {
public:
    using ConfigError::ConfigError;
};
class KeyNotFound : public ConfigError {
public:
    using ConfigError::ConfigError;
};

// ═══════════════════════════════════════════════════════════════════════
// 2. 工具函数
// ═══════════════════════════════════════════════════════════════════════

static std::string get_extension(const std::string& name) {
    auto dot = name.rfind('.');
    if (dot == std::string::npos) return "";
    std::string ext = name.substr(dot);
    std::transform(ext.begin(), ext.end(), ext.begin(),
        [](unsigned char c) { return std::tolower(c); });
    return ext;
}

static std::string iso_from_mtime(fs::file_time_type mtime) {
    auto now_sys = std::chrono::system_clock::now();
    auto now_file = fs::file_time_type::clock::now();
    auto sys_tp = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
        mtime - now_file + now_sys);
    std::time_t t = std::chrono::system_clock::to_time_t(sys_tp);

    // 提取微秒部分（与 Python datetime.isoformat() 一致）
    auto us = std::chrono::duration_cast<std::chrono::microseconds>(
        sys_tp.time_since_epoch()).count() % 1000000;

    // 系统本地时间 + 时区偏移
    std::tm* lt = std::localtime(&t);
    char buf[48];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", lt);
    char tz_raw[8];
    std::strftime(tz_raw, sizeof(tz_raw), "%z", lt);  // "+0000" or "+0800"
    std::string tz = tz_raw;
    if (tz.length() >= 5)
        tz = tz.substr(0, 3) + ":" + tz.substr(3);  // "+00:00"

    if (us == 0)
        return std::string(buf) + tz;  // Python: 微秒为0时省略
    char out[64];
    std::snprintf(out, sizeof(out), "%s.%06lld%s",
                  buf, static_cast<long long>(us), tz.c_str());
    return out;
}

static int parse_int(const std::string& key, const std::string& val) {
    try {
        return std::stoi(val);
    } catch (const std::exception& ex) {
        std::cerr << "[confs2] 配置解析失败: " << key << " = '" << val
                  << "' (" << ex.what() << ")" << std::endl;
        std::exit(1);
    }
}

// ═══════════════════════════════════════════════════════════════════════
// 3. ConfigStore — 配置文件仓库（线程安全）
// ═══════════════════════════════════════════════════════════════════════

struct CacheEntry {
    std::string content;
    fs::file_time_type mtime;
    std::size_t size;
};

struct ConfigMeta {
    std::string name;
    std::size_t size;
    std::string mtime_iso;
    bool cached;
};

class ConfigStore {
public:
    explicit ConfigStore(const std::string& root) {
        root_ = fs::weakly_canonical(fs::absolute(root));
        fs::create_directories(root_);
    }

    std::string root_string() const { return root_.string(); }

    // 取配置全文（带缓存 + mtime 自动重载）
    // 返回 CacheEntry 的拷贝（防缓存污染，与 Python 版 dict(entry) 语义一致）
    CacheEntry get(const std::string& name) {
        validate_name(name);  // 锁外快速拒绝非法名
        std::lock_guard<std::mutex> lock(mtx_);
        auto path = resolve_locked(name);  // 锁内: canonical + 前缀校验 + 存在性检查

        // 锁内: stat → mtime 比对 → size 校验 → open → read，无 TOCTOU 窗口
        auto mtime = fs::last_write_time(path);
        auto size  = fs::file_size(path);

        auto it = cache_.find(name);
        if (it != cache_.end() && it->second.mtime == mtime)
            return it->second;  // 缓存命中，返回拷贝

        if (size > kMaxConfigSize)
            throw ConfigError("配置文件过大: " + name);

        std::ifstream f(path);
        if (!f.is_open())
            throw ConfigError("配置读取失败: " + name);
        std::string content((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());

        CacheEntry entry{content, mtime, content.size()};
        cache_[name] = entry;
        return entry;  // 返回拷贝
    }

    // 列出配置目录全部文件元信息
    std::vector<ConfigMeta> list() {
        std::vector<ConfigMeta> out;
        std::lock_guard<std::mutex> lock(mtx_);
        std::string root_str = root_.string();

        std::vector<std::string> names;
        for (const auto& entry : fs::directory_iterator(root_)) {
            auto name = entry.path().filename().string();
            if (kAllowedExt.find(get_extension(name)) == kAllowedExt.end())
                continue;
            // 解析符号链接，逃逸的跳过
            auto path = fs::weakly_canonical(entry.path());
            std::string path_str = path.string();
            if (path_str.find(root_str) != 0 || path_str == root_str)
                continue;
            if (path_str.size() > root_str.size() && path_str[root_str.size()] != '/')
                continue;
            if (!fs::is_regular_file(path))
                continue;
            names.push_back(name);
        }

        std::sort(names.begin(), names.end());

        for (const auto& name : names) {
            auto path = fs::weakly_canonical(root_ / name);
            try {
                auto mtime = fs::last_write_time(path);
                auto size  = fs::file_size(path);
                out.push_back({name, size, iso_from_mtime(mtime),
                               cache_.count(name) > 0});
            } catch (const std::exception&) {
                continue;  // 文件可能在遍历时被删除，跳过
            }
        }
        return out;
    }

    // 重载指定配置 / 清空全部缓存
    std::vector<std::string> reload(const std::string* name) {
        if (name != nullptr) {
            validate_name(*name);
            {
                std::lock_guard<std::mutex> lock(mtx_);
                resolve_locked(*name);  // 锁内检查存在性，防止 TOCTOU
                cache_.erase(*name);
            }
            get(*name);  // 重新读取并缓存（get 内部自己加锁）
            return {*name};
        }
        // 清空全部缓存
        std::vector<std::string> dropped;
        std::lock_guard<std::mutex> lock(mtx_);
        for (const auto& [k, v] : cache_)
            dropped.push_back(k);
        cache_.clear();
        return dropped;
    }

    std::size_t cached_count() const {
        std::lock_guard<std::mutex> lock(mtx_);
        return cache_.size();
    }

private:
    // 扩展名白名单校验（纯字符串操作，不访问文件系统）
    static void validate_name(const std::string& name) {
        if (name.empty())
            throw ConfigNameInvalid("非法配置名: (空)");
        if (name.find('/') != std::string::npos || name.find('\\') != std::string::npos)
            throw ConfigNameInvalid("非法配置名");
        auto ext = get_extension(name);
        if (kAllowedExt.find(ext) == kAllowedExt.end())
            throw ConfigNameInvalid("不支持的配置类型: " + (ext.empty() ? "(无扩展名)" : ext));
    }

    // 持锁解析路径（canonical 解析符号链接 + 前缀校验 + 文件存在性）
    fs::path resolve_locked(const std::string& name) {
        auto path = fs::weakly_canonical(root_ / name);
        std::string path_str = path.string();
        std::string root_str = root_.string();

        // 校验路径在 root_ 之下
        if (path_str == root_str ||
            path_str.size() <= root_str.size() ||
            path_str[root_str.size()] != '/' ||
            path_str.find(root_str) != 0)
            throw ConfigNameInvalid("非法配置名");

        if (!fs::is_regular_file(path))
            throw ConfigNotFound("配置不存在: " + name);

        return path;
    }

    fs::path root_;
    std::unordered_map<std::string, CacheEntry> cache_;
    mutable std::mutex mtx_;
};

// ═══════════════════════════════════════════════════════════════════════
// 4. INI 按键取值 — 与 Python ConfigParser(interpolation=None) 行为对等
// ═══════════════════════════════════════════════════════════════════════
//
// 大小写策略（与 Python configparser 一致）：
// - Section 名：大小写敏感（[Server] ≠ [server]）
// - Option 名：大小写不敏感（port = Port = PORT）
// - 值：原样返回，不做任何转换
// - 插值：禁用，%(key)s 原样返回

static std::optional<std::string> extract_ini_key(
    const std::string& content, const std::string& key)
{
    auto dot = key.find('.');
    if (dot == std::string::npos || dot == 0 || dot == key.size() - 1)
        return std::nullopt;

    std::string section = key.substr(0, dot);
    std::string option = key.substr(dot + 1);
    // option 大小写不敏感 → 转小写比较
    std::string option_lower = option;
    std::transform(option_lower.begin(), option_lower.end(), option_lower.begin(),
        [](unsigned char c) { return std::tolower(c); });

    std::istringstream stream(content);
    std::string line;
    std::string current_section;

    while (std::getline(stream, line)) {
        // 去 \r
        if (!line.empty() && line.back() == '\r')
            line.pop_back();

        auto start = line.find_first_not_of(" \t");
        if (start == std::string::npos) continue;  // 空行
        if (line[start] == ';' || line[start] == '#') continue;  // 注释

        if (line[start] == '[') {
            auto end = line.find(']', start);
            if (end != std::string::npos)
                current_section = line.substr(start + 1, end - start - 1);
            continue;
        }

        auto eq = line.find('=', start);
        if (eq == std::string::npos) continue;

        std::string key_name = line.substr(start, eq - start);
        auto ke = key_name.find_last_not_of(" \t");
        if (ke != std::string::npos) key_name = key_name.substr(0, ke + 1);

        std::string key_lower = key_name;
        std::transform(key_lower.begin(), key_lower.end(), key_lower.begin(),
            [](unsigned char c) { return std::tolower(c); });

        std::string value = line.substr(eq + 1);
        auto vs = value.find_first_not_of(" \t");
        if (vs != std::string::npos) value = value.substr(vs);
        auto ve = value.find_last_not_of(" \t\r");
        if (ve != std::string::npos) value = value.substr(0, ve + 1);

        if (current_section == section && key_lower == option_lower)
            return value;
    }

    return std::nullopt;
}

// ═══════════════════════════════════════════════════════════════════════
// 5. JSON 点路径取值
// ═══════════════════════════════════════════════════════════════════════

static json::value extract_json_key(
    const std::string& content, const std::string& key)
{
    json::value node = json::parse(content);  // 解析失败会抛异常

    // 按点分割
    std::vector<std::string> parts;
    std::istringstream iss(key);
    std::string part;
    while (std::getline(iss, part, '.')) {
        if (!part.empty()) parts.push_back(part);
    }

    for (const auto& p : parts) {
        if (node.is_object()) {
            auto it = node.as_object().find(p);
            if (it == node.as_object().end())
                throw KeyNotFound("键不存在: -> " + key);
            node = it->value();
        } else if (node.is_array()) {
            if (p.empty() || !std::all_of(p.begin(), p.end(), [](char c) { return std::isdigit(c); }))
                throw KeyNotFound("数组下标必须是数字: " + p);
            std::size_t idx = std::stoul(p);
            if (idx >= node.as_array().size())
                throw KeyNotFound("数组下标越界: " + p);
            node = node.as_array()[idx];
        } else {
            throw KeyNotFound("无法继续遍历: " + p);
        }
    }
    return node;
}

// ═══════════════════════════════════════════════════════════════════════
// 6. 配置结构
// ═══════════════════════════════════════════════════════════════════════

struct ConfsConfig {
    std::string host = "127.0.0.1";
    int port = 8010;
    int workers = 4;
    std::string config_dir;  // 默认同级 configs/
};

static void apply_env_overrides(ConfsConfig& cfg) {
    if (auto* v = std::getenv("LISTEN_ADDR")) cfg.host = v;
    if (auto* v = std::getenv("LISTEN_PORT")) cfg.port = parse_int("LISTEN_PORT", v);
    if (auto* v = std::getenv("WORKERS"))     cfg.workers = parse_int("WORKERS", v);
    if (auto* v = std::getenv("CONFS_DIR"))   cfg.config_dir = v;
}

static void validate_config(const ConfsConfig& cfg) {
    if (cfg.host.empty()) { std::cerr << "[confs2] 配置缺失: host" << std::endl; std::exit(1); }
    if (cfg.port <= 0 || cfg.port > 65535) { std::cerr << "[confs2] 配置无效: port" << std::endl; std::exit(1); }
    if (cfg.workers < 0) { std::cerr << "[confs2] workers 不能为负数" << std::endl; std::exit(1); }
    if (cfg.config_dir.empty()) { std::cerr << "[confs2] 配置缺失: config_dir" << std::endl; std::exit(1); }
}

// ═══════════════════════════════════════════════════════════════════════
// 7. 全局实例 + 路由 + main()
// ═══════════════════════════════════════════════════════════════════════

static ConfsConfig g_config;
static ConfigStore* g_store = nullptr;
static auto& g_log = LogC::instance();
static const auto g_start_time = std::chrono::steady_clock::now();

static std::int64_t uptime_seconds() {
    auto now = std::chrono::steady_clock::now();
    return std::chrono::duration_cast<std::chrono::seconds>(now - g_start_time).count();
}

int main(int argc, char* argv[]) {
    try {
        // ── 默认配置目录：同级 configs/ ──
        {
            // 可执行文件同目录下的 configs/
            // 编译时不知道运行时路径，用 __FILE__ 作为开发期 fallback
            g_config.config_dir = "./configs";
        }

        // ── 命令行参数 ──
        po::options_description desc("confs2 — C++20 统一配置服务（pymsg RPC）");
        desc.add_options()
            ("help,h", "显示帮助")
            ("addr,a", po::value<std::string>(), "监听地址")
            ("port,p", po::value<int>(), "监听端口")
            ("workers,w", po::value<int>(), "工作线程数")
            ("dir,d", po::value<std::string>(), "配置文件目录");
        po::variables_map vm;
        po::store(po::parse_command_line(argc, argv, desc), vm);
        po::notify(vm);

        if (vm.count("help")) {
            std::cout << desc << std::endl;
            return 0;
        }

        // ── 配置加载：硬编码默认 → 环境变量 → 命令行 ──
        apply_env_overrides(g_config);
        if (vm.count("addr"))    g_config.host = vm["addr"].as<std::string>();
        if (vm.count("port"))    g_config.port = vm["port"].as<int>();
        if (vm.count("workers")) g_config.workers = vm["workers"].as<int>();
        if (vm.count("dir"))     g_config.config_dir = vm["dir"].as<std::string>();

        validate_config(g_config);

        // ── 初始化 ConfigStore ──
        ConfigStore store(g_config.config_dir);
        g_store = &store;

        std::cerr << "[confs2] 配置目录: " << store.root_string() << std::endl;
        std::cerr << "[confs2] 监听 tcp://" << g_config.host << ":" << g_config.port
                  << " workers=" << g_config.workers << std::endl;

        g_log.info("confs", "启动中... 监听 tcp://" + g_config.host + ":"
                   + std::to_string(g_config.port) + " 配置目录 " + store.root_string());

        // ── 启动 RpcServer ──
        pmc::net::RpcServer rpc(g_config.host, g_config.port,
            pmc::net::MsgServerConfig{.workers = g_config.workers},
            [](const std::string& route, const std::exception& e) {
                g_log.error("confs", "路由 " + route + " 处理异常: " + e.what());
            });

        // ── 路由: get ──
        rpc.add_route("get", [](const json::object& req) -> json::value {
            auto name_it = req.find("name");
            if (name_it == req.end() || !name_it->value().is_string())
                throw ConfigError("缺少 name 字段");
            std::string name = json::value_to<std::string>(name_it->value());

            auto entry = g_store->get(name);

            // 无 key：返回全文
            auto key_it = req.find("key");
            if (key_it == req.end())
                return json::object{
                    {"name", name},
                    {"content", entry.content},
                    {"size", (std::int64_t)entry.size},
                    {"mtime", iso_from_mtime(entry.mtime)}
                };

            // 有 key：校验
            if (!key_it->value().is_string())
                throw ConfigError("key 字段必须是字符串");
            std::string key = json::value_to<std::string>(key_it->value());
            if (key.empty())
                throw ConfigError("key 字段不能为空");

            auto ext = get_extension(name);

            if (ext == ".ini" || ext == ".conf" || ext == ".cfg") {
                auto val = extract_ini_key(entry.content, key);
                if (!val)
                    throw KeyNotFound("键不存在: " + name + " -> " + key);
                g_log.info("confs", "分发配置键 " + name + " -> " + key);
                return json::object{{"name", name}, {"key", key}, {"value", *val}};
            }

            if (ext == ".json") {
                auto val = extract_json_key(entry.content, key);
                g_log.info("confs", "分发配置键 " + name + " -> " + key);
                return json::object{{"name", name}, {"key", key}, {"value", val}};
            }

            throw ConfigError(ext + " 类型不支持按键查询，请取全文后自行解析");
        });

        // ── 路由: list ──
        rpc.add_route("list", [](const json::object&) -> json::value {
            auto configs = g_store->list();
            json::array arr;
            for (const auto& c : configs)
                arr.push_back(json::object{
                    {"name", c.name},
                    {"size", (std::int64_t)c.size},
                    {"mtime", c.mtime_iso},
                    {"cached", c.cached}
                });
            return json::object{
                {"config_dir", g_store->root_string()},
                {"configs", arr}
            };
        });

        // ── 路由: reload ──
        rpc.add_route("reload", [](const json::object& req) -> json::value {
            auto it = req.find("name");

            // 省略 name 或 null → 清空全部缓存
            if (it == req.end() || it->value().is_null()) {
                auto names = g_store->reload(nullptr);
                g_log.info("confs", "清空配置缓存（" + std::to_string(names.size()) + " 项）");
                json::array arr;
                for (const auto& n : names) arr.push_back(json::value(n));
                return json::object{{"reloaded", arr}};
            }

            // 非字符串 → 报错
            if (!it->value().is_string())
                throw ConfigError("name 字段必须是字符串");

            std::string name = json::value_to<std::string>(it->value());
            if (name.empty())
                throw ConfigError("name 字段不能为空（要清空全部缓存请省略 name 字段）");

            auto names = g_store->reload(&name);
            g_log.info("confs", "重载配置 " + names[0]);
            json::array arr;
            for (const auto& n : names) arr.push_back(json::value(n));
            return json::object{{"reloaded", arr}};
        });

        // ── 路由: health ──
        rpc.add_route("health", [](const json::object&) -> json::value {
            return json::object{
                {"status", "ok"},
                {"service", "confs"},
                {"config_dir", g_store->root_string()},
                {"cached", (std::int64_t)g_store->cached_count()},
                {"uptime", uptime_seconds()}
            };
        });

        std::cerr << "[confs2] 开始监听 tcp://" << g_config.host << ":"
                  << g_config.port << std::endl;
        g_log.info("confs", "开始监听 tcp://" + g_config.host + ":"
                   + std::to_string(g_config.port));

        rpc.start();  // 阻塞

    } catch (const std::exception& e) {
        std::cerr << "[confs2] 致命错误: " << e.what() << std::endl;
        g_log.error("confs", std::string("致命错误: ") + e.what());
        return 1;
    }

    return 0;
}
