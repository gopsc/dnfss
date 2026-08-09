/*
 * ConfsTask.cpp — core 内嵌 confs 统一配置服务（pymsg RPC）
 *
 * 业务逻辑移植自 confs2/src/main.cpp（C++20 统一配置服务），
 * ConfigStore / INI 按键取值 / JSON 点路径 / 四个路由与原实现逐项对齐，
 * 协议与 Python confs 字节级兼容，消费方零改动。
 *
 * 与 standalone confs2 的差异：
 * - 入口由 main() 改为 ConfsTask（ITask），生命周期由 core 的 taskPool 管理
 * - 引导配置由 core 的 main.cpp 解析（--confs / CONFS_* 环境变量）后注入
 */

#include <us/RpcServer.hpp>

#include <boost/json.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "main/ConfsTask.hh"
#include "main/ConfsLogC.hpp"

namespace json = boost::json;
namespace fs = std::filesystem;

using qing::confs::ConfsLogC;

namespace qing {

// ═══════════════════════════════════════════════════════════════════════
// 1. 常量与异常
// ═══════════════════════════════════════════════════════════════════════

namespace {

const std::unordered_set<std::string> kAllowedExt = {
    ".ini", ".conf", ".cfg", ".json", ".yaml", ".yml", ".toml", ".txt"
};

constexpr std::size_t kMaxConfigSize = 1 * 1024 * 1024;  // 1 MiB

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

std::string get_extension(const std::string& name) {
    auto dot = name.rfind('.');
    if (dot == std::string::npos) return "";
    std::string ext = name.substr(dot);
    std::transform(ext.begin(), ext.end(), ext.begin(),
        [](unsigned char c) { return std::tolower(c); });
    return ext;
}

std::string iso_from_mtime(fs::file_time_type mtime) {
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

} // anonymous namespace

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
            throw ConfigNameInvalid("非法配置名: '" + name + "'");
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
            throw ConfigNameInvalid("非法配置名: '" + name + "'");

        if (!fs::is_regular_file(path))
            throw ConfigNotFound("配置不存在: " + name);

        return path;
    }

    fs::path root_;
    std::unordered_map<std::string, CacheEntry> cache_;
    mutable std::mutex mtx_;
};

namespace {

// ═══════════════════════════════════════════════════════════════════════
// 4. INI 按键取值 — 与 Python ConfigParser(interpolation=None) 行为对等
// ═══════════════════════════════════════════════════════════════════════
//
// 大小写策略（与 Python configparser 一致）：
// - Section 名：大小写敏感（[Server] ≠ [server]）
// - Option 名：大小写不敏感（port = Port = PORT）
// - 值：原样返回，不做任何转换
// - 插值：禁用，%(key)s 原样返回

std::optional<std::string> extract_ini_key(
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

json::value extract_json_key(
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

} // anonymous namespace

// ═══════════════════════════════════════════════════════════════════════
// 6. ConfsTask — ITask 实现（生命周期与 HttpTask 同构）
// ═══════════════════════════════════════════════════════════════════════

ConfsTask::ConfsTask(std::string addr_, int port_, int workers_, std::string config_dir)
    : addr(std::move(addr_)), port(port_), workers(workers_),
      started_at(std::chrono::steady_clock::now())
{
    store = std::make_unique<ConfigStore>(config_dir);
    this->th = std::make_unique<Thread>();
    init_thread();
}

ConfsTask::~ConfsTask() = default;

bool ConfsTask::isRunning() {
    auto stat = th->check();
    return !(stat == Fsm::Stat::STOP || stat == Fsm::Stat::SHUT);
}

void ConfsTask::start() {
    if (!this->isRunning()) {
        th->Activate();
        th->WaitStart();
    }
}

void ConfsTask::stop() {
    if (this->server) this->server->stop();
    if (this->isRunning())
        th->WaitClose();
}

void ConfsTask::init_thread() {

    /* stop callback 静止事件 */
    this->th->set_stop(  [](Thread& th) -> void {
        th.suspend();
    });

    /* wake callback 唤醒事件：创建 RpcServer 并阻塞运行 */
    this->th->set_wake(  [this](Thread& th) -> void {
        this->server = std::make_unique<pmc::net::RpcServer> (addr, port,
            pmc::net::MsgServerConfig{.workers = workers},
            [](const std::string& route, const std::exception& e) {
                ConfsLogC::instance().error("confs", "路由 " + route + " 处理异常: " + e.what());
            });
        register_routes(*this->server);
        th.run();

        ConfsLogC::instance().info("confs", "开始监听 tcp://" + addr + ":"
                                   + std::to_string(port));
        this->server->start();  /* 堵死线程，等待 stop() 解除 */
    });

    /* loop callback 循环事件：服务器主动退出时停止线程防死循环 */
    this->th->set_loop(  [](Thread& th) -> void {
        th.stop();
    });

    /* clean callback 清理事件 */
    this->th->set_clear( [this](Thread&) -> void {
        this->server.reset();
    });
}

// ═══════════════════════════════════════════════════════════════════════
// 7. 路由注册（get / list / reload / health，与 confs2 逐项对齐）
// ═══════════════════════════════════════════════════════════════════════

void ConfsTask::register_routes(pmc::net::RpcServer& rpc) {

    /* ── 路由: get ── */
    rpc.add_route("get", [this](const json::object& req) -> json::value {
        auto name_it = req.find("name");
        if (name_it == req.end() || !name_it->value().is_string())
            throw ConfigError("缺少 name 字段");
        std::string name = json::value_to<std::string>(name_it->value());

        auto entry = store->get(name);

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
            ConfsLogC::instance().info("confs", "分发配置键 " + name + " -> " + key);
            return json::object{{"name", name}, {"key", key}, {"value", *val}};
        }

        if (ext == ".json") {
            auto val = extract_json_key(entry.content, key);
            ConfsLogC::instance().info("confs", "分发配置键 " + name + " -> " + key);
            return json::object{{"name", name}, {"key", key}, {"value", val}};
        }

        throw ConfigError(ext + " 类型不支持按键查询，请取全文后自行解析");
    });

    /* ── 路由: list ── */
    rpc.add_route("list", [this](const json::object&) -> json::value {
        auto configs = store->list();
        json::array arr;
        for (const auto& c : configs)
            arr.push_back(json::object{
                {"name", c.name},
                {"size", (std::int64_t)c.size},
                {"mtime", c.mtime_iso},
                {"cached", c.cached}
            });
        return json::object{
            {"config_dir", store->root_string()},
            {"configs", arr}
        };
    });

    /* ── 路由: reload ── */
    rpc.add_route("reload", [this](const json::object& req) -> json::value {
        auto it = req.find("name");

        // 省略 name 或 null → 清空全部缓存
        if (it == req.end() || it->value().is_null()) {
            auto names = store->reload(nullptr);
            ConfsLogC::instance().info("confs", "清空配置缓存（" + std::to_string(names.size()) + " 项）");
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

        auto names = store->reload(&name);
        ConfsLogC::instance().info("confs", "重载配置 " + names[0]);
        json::array arr;
        for (const auto& n : names) arr.push_back(json::value(n));
        return json::object{{"reloaded", arr}};
    });

    /* ── 路由: health ── */
    rpc.add_route("health", [this](const json::object&) -> json::value {
        auto now = std::chrono::steady_clock::now();
        auto uptime = std::chrono::duration_cast<std::chrono::seconds>(now - started_at).count();
        return json::object{
            {"status", "ok"},
            {"service", "confs"},
            {"config_dir", store->root_string()},
            {"cached", (std::int64_t)store->cached_count()},
            {"uptime", uptime}
        };
    });
}

}
