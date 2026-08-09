/** fsys — C++20 静态文件托管服务（对齐 Python static 模块）
 *
 * 基于 libhttp + libmsg + boost::json 实现，接口与 Python static 完全兼容：
 *   GET  /                      首页（index.html 或 __view.html）
 *   GET  /<path>                静态文件 / 目录浏览
 *   GET  /api/filelist?path=    文件列表 JSON
 *   GET  /video/...             视频/音频流媒体（HTTP Range 206）
 *   POST /api/hash              SHA-256 密码哈希（限速 20/min/IP）
 *   POST /api/check_user        用户存在性检查（限速 10/min/IP）
 *   GET  /api/favicon?url=      favicon SSRF 防护代理
 *   POST /get_notes 等 4 个     dnfs2 反向代理
 *   GET  /health                健康检查
 *
 * 安全改进（相比 Python static）：
 *   F-1  server.setMaxBodySize(10MB) 限制请求体大小
 *   F-2  libhttp 原生 HTTPS 支持，无需回退开发服务器
 *   F-3  命令行参数 --host/--addr/-a --port/-p --threads/-t --log-level --instance --config --help
 *   F-4  所有端口/线程数有严格边界校验
 *   F-5  Boost.Beast 异步多线程替代 Flask 同步
 *   F-6  消除 Python/Flask/waitress 依赖
 *   F-7  日志客户端复用主配置（confs 拉取的 db host/port）
 *
 * 依赖: libhttp (HttpServer/HttpClient), libmsg (RpcClient/AsyncQueue),
 *       boost::json, OpenSSL, libcurl
 */

#include <us/HttpServer.hpp>
#include <us/HttpClient.hpp>
#include <us/RpcClient.hpp>
#include <us/AsyncQueue.hpp>

#include <boost/json.hpp>
#include <curl/curl.h>
#include <openssl/evp.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>

namespace json = boost::json;
namespace http = boost::beast::http;
namespace fs   = std::filesystem;
using namespace pmc::net;

// ============================================================================
// 常量
// ============================================================================

static constexpr size_t kMaxBodySize = 10 * 1024 * 1024;

// 日志
static constexpr size_t kLogMaxMessage  = 8192;
static constexpr size_t kLogMaxExtra    = 4096;
static constexpr size_t kLogBatchSize   = 50;
static constexpr double kLogFlushInterval = 2.0;
static constexpr size_t kLogQueueMax    = 10000;
static constexpr double kLogRpcTimeout  = 5.0;

// favicon 代理
static constexpr size_t kFaviconMaxBytes = 512 * 1024;
static constexpr long   kFaviconTimeout  = 5;

// HTTP 响应大小上限
static constexpr size_t kHttpMaxResponse = 10 * 1024 * 1024;

// 单次文件响应大小上限（string_body 全量读入内存，防大文件 DoS；审计 F-4）
static constexpr uintmax_t kMaxServeFileSize = 50 * 1024 * 1024;

// 限速
static constexpr int kHashRateLimit       = 20;
static constexpr int kCheckUserRateLimit  = 10;
static constexpr int kRateWindowSeconds   = 60;
static constexpr size_t kRateMaxKeys      = 65536;  // 限速键数上限，防伪造 XFF 旋转导致内存增长

// 视图文件名
static const std::string kViewFile = "__view.html";

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

static std::string sha256_hex(const std::string& input) {
    EVP_MD* md = EVP_MD_fetch(nullptr, "SHA2-256", nullptr);
    const bool fetched = (md != nullptr);
    if (!md) {
        // OpenSSL < 3.0 回退
        md = const_cast<EVP_MD*>(EVP_sha256());
    }
    unsigned char hash[EVP_MAX_MD_SIZE];
    unsigned int hash_len = 0;
    EVP_Digest(input.data(), input.size(), hash, &hash_len, md, nullptr);
    // EVP_MD_fetch 返回的无论 digest 成败都需释放，EVP_sha256 返回的不需要
    if (fetched) {
        EVP_MD_free(md);
    }
    static const char hex_chars[] = "0123456789abcdef";
    std::string result;
    result.reserve(hash_len * 2);
    for (unsigned int i = 0; i < hash_len; ++i) {
        result += hex_chars[hash[i] >> 4];
        result += hex_chars[hash[i] & 0x0f];
    }
    return result;
}

static std::string format_file_size(uintmax_t size) {
    double s = static_cast<double>(size);
    const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    int i = 0;
    while (s >= 1024.0 && i < 4) { s /= 1024.0; ++i; }
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%.1f%s", s, units[i]);
    return buf;
}

// URL 解码（%XX；plus_is_space=true 时 '+' 解为空格，仅用于查询串；路径解码不转换 '+'）
static std::string url_decode(const std::string& s, bool plus_is_space) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (c == '%' && i + 2 < s.size() &&
            std::isxdigit(static_cast<unsigned char>(s[i + 1])) &&
            std::isxdigit(static_cast<unsigned char>(s[i + 2]))) {
            char hex[3] = {s[i + 1], s[i + 2], '\0'};
            out += static_cast<char>(std::strtol(hex, nullptr, 16));
            i += 2;
        } else if (c == '+' && plus_is_space) {
            out += ' ';
        } else {
            out += c;
        }
    }
    return out;
}

// URL 查询参数解析（值做 URL 解码，与 Flask request.args 行为对齐）
static std::unordered_map<std::string, std::string> parse_query(const std::string& target) {
    std::unordered_map<std::string, std::string> params;
    auto q = target.find('?');
    if (q == std::string::npos) return params;
    std::string qs = target.substr(q + 1);
    std::istringstream ss(qs);
    std::string pair;
    while (std::getline(ss, pair, '&')) {
        auto eq = pair.find('=');
        if (eq == std::string::npos) continue;
        params[url_decode(pair.substr(0, eq), true)] = url_decode(pair.substr(eq + 1), true);
    }
    return params;
}

// 从请求 target 提取路径（去除查询参数）
static std::string extract_path(const std::string& target) {
    auto q = target.find('?');
    return (q == std::string::npos) ? target : target.substr(0, q);
}

// ============================================================================
// INI 解析（白名单模式，与 dnfs2 一致）
// ============================================================================

using IniMap = std::map<std::string, std::map<std::string, std::string>>;

static IniMap parse_ini(const std::string& text) {
    IniMap result;
    std::string section;
    std::istringstream ss(text);
    std::string line;
    while (std::getline(ss, line)) {
        auto b = line.find_first_not_of(" \t\r\n");
        if (b == std::string::npos) continue;
        line = line.substr(b);
        auto e = line.find_last_not_of(" \t\r\n");
        if (e != std::string::npos) line = line.substr(0, e + 1);

        if (line.empty() || line[0] == '#' || line[0] == ';') continue;
        if (line.front() == '[' && line.back() == ']') {
            section = line.substr(1, line.size() - 2);
            continue;
        }
        auto eq = line.find('=');
        if (eq == std::string::npos || section.empty()) continue;
        std::string key = line.substr(0, eq);
        std::string val = line.substr(eq + 1);
        auto trim = [](std::string& s) {
            auto bb = s.find_first_not_of(" \t");
            auto ee = s.find_last_not_of(" \t");
            s = (bb == std::string::npos) ? "" : s.substr(bb, ee - bb + 1);
        };
        trim(key); trim(val);
        result[section][key] = val;
    }
    return result;
}

// ============================================================================
// 配置
// ============================================================================

struct Config {
    std::string listen_host  = "127.0.0.1";
    int         listen_port  = 9200;
    int         threads      = 4;

    std::string static_root  = "./public";

    bool        ssl_enabled  = false;
    std::string ssl_cert     = "cert.pem";
    std::string ssl_key      = "key.pem";

    std::vector<std::string> cors_origins;

    std::string confs_host   = "127.0.0.1";
    int         confs_port   = 8010;

    std::string db_host      = "127.0.0.1";
    int         db_port      = 8005;

    std::string dnfs_url     = "http://127.0.0.1:8006";

    // CLI 标准化参数（Step 3）
    std::string log_level         = "info";       // debug|info|warn|error
    std::string instance_name     = "fsys";       // 实例名（注册中心用）
    std::string confs_config_name = "static.ini"; // confs 拉取的配置名
};

static Config load_config(int argc, char* argv[]) {
    Config cfg;

    // 0. 预扫描 --help 和 --config（需在 confs 拉取前处理）
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            std::printf(
                "fsys — C++20 静态文件托管服务\n\n"
                "Usage: fsys [OPTIONS]\n\n"
                "Options:\n"
                "  -h, --help              Show this help and exit\n"
                "  -a, --addr <addr>       Listen address (alias: --host)\n"
                "      --host <addr>       Listen address (canonical name)\n"
                "  -p, --port <port>       Listen port\n"
                "  -t, --threads <n>       Worker threads\n"
                "      --config <name>     confs config name (default: static.ini)\n"
                "      --log-level <level> debug|info|warn|error (default: info)\n"
                "      --instance <name>   Instance name (default: fsys)\n\n"
                "Config priority: CLI > env > confs > defaults\n");
            std::exit(0);
        }
        if (arg == "--config" && i + 1 < argc) {
            cfg.confs_config_name = argv[++i];
        }
    }

    // 1. confs 引导地址
    if (const char* h = std::getenv("CONFS_HOST")) cfg.confs_host = h;
    if (const char* p = std::getenv("CONFS_PORT")) {
        cfg.confs_port = safe_stoi(p, "CONFS_PORT");
        if (cfg.confs_port < 1 || cfg.confs_port > 65535) {
            std::fprintf(stderr, "[fsys] invalid CONFS_PORT: %d (must be 1-65535)\n", cfg.confs_port);
            std::exit(1);
        }
    }

    // 2. 从 confs 拉取配置（retry_on_refused=true）
    std::string ini_text;
    try {
        RpcClient confs_client(cfg.confs_host, cfg.confs_port, "fsys",
                               /*timeout=*/5.0, /*retry_on_refused=*/true);
        json::value data = confs_client.call("get", {{"name", cfg.confs_config_name}});
        if (data.is_object() && data.as_object().contains("content")) {
            ini_text = std::string(data.as_object().at("content").as_string());
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[fsys] 从 confs 获取 %s 失败: %s\n",
                     cfg.confs_config_name.c_str(), e.what());
        std::exit(1);
    }

    // 3. 解析 INI
    IniMap ini = parse_ini(ini_text);

    auto get = [&](const std::string& sec, const std::string& key,
                   const std::string& def = "") -> std::string {
        auto si = ini.find(sec);
        if (si == ini.end()) return def;
        auto ki = si->second.find(key);
        return (ki == si->second.end()) ? def : ki->second;
    };

    cfg.listen_host = get("server",    "host", cfg.listen_host);
    cfg.listen_port = safe_stoi(get("server", "port", std::to_string(cfg.listen_port)), "[server] port");
    cfg.static_root = get("directory", "root", cfg.static_root);

    std::string ssl_str = get("ssl", "enabled", "false");
    cfg.ssl_enabled = (ssl_str == "true" || ssl_str == "True" || ssl_str == "1");
    cfg.ssl_cert    = get("ssl", "cert_file", cfg.ssl_cert);
    cfg.ssl_key     = get("ssl", "key_file",  cfg.ssl_key);

    std::string origins_str = get("cors", "allowed_origins", "");
    if (!origins_str.empty()) {
        std::istringstream oss(origins_str);
        std::string origin;
        while (std::getline(oss, origin, ',')) {
            auto b = origin.find_first_not_of(" \t");
            auto e = origin.find_last_not_of(" \t");
            if (b != std::string::npos)
                cfg.cors_origins.push_back(origin.substr(b, e - b + 1));
        }
    }

    // 4. 环境变量覆盖（Docker 兼容）
    if (const char* v = std::getenv("LISTEN_ADDR"))  cfg.listen_host = v;
    if (const char* v = std::getenv("LISTEN_PORT"))  cfg.listen_port = safe_stoi(v, "LISTEN_PORT");
    if (const char* v = std::getenv("STATIC_ROOT"))  cfg.static_root = v;
    if (const char* v = std::getenv("DB_HOST"))      cfg.db_host = v;
    if (const char* v = std::getenv("DB_PORT"))      cfg.db_port = safe_stoi(v, "DB_PORT");
    if (const char* v = std::getenv("DNFS_URL"))     cfg.dnfs_url = v;

    // 5. 命令行参数（最高优先级）
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "--addr"    || arg == "-a" || arg == "--host") && i + 1 < argc) cfg.listen_host = argv[++i];
        else if ((arg == "--port" || arg == "-p") && i + 1 < argc) cfg.listen_port = safe_stoi(argv[++i], "--port");
        else if ((arg == "--threads" || arg == "-t") && i + 1 < argc) cfg.threads = safe_stoi(argv[++i], "--threads");
        else if (arg == "--log-level" && i + 1 < argc) cfg.log_level = argv[++i];
        else if (arg == "--instance" && i + 1 < argc) cfg.instance_name = argv[++i];
        // --config 和 --help 已在预扫描阶段处理
    }

    // 6. 校验必需配置项
    if (cfg.listen_host.empty()) {
        std::fprintf(stderr, "[fsys] 缺少必需配置: [server] host\n");
        std::exit(1);
    }
    if (cfg.static_root.empty()) {
        std::fprintf(stderr, "[fsys] 缺少必需配置: [directory] root\n");
        std::exit(1);
    }
    if (cfg.cors_origins.empty()) {
        std::fprintf(stderr, "[fsys] 缺少必需配置: [cors] allowed_origins\n");
        std::exit(1);
    }

    // 7. 数值范围校验
    if (cfg.listen_port < 1 || cfg.listen_port > 65535) {
        std::fprintf(stderr, "[fsys] invalid listen_port: %d (must be 1-65535)\n", cfg.listen_port);
        std::exit(1);
    }
    if (cfg.threads < 1 || cfg.threads > 256) {
        std::fprintf(stderr, "[fsys] invalid threads: %d (must be 1-256)\n", cfg.threads);
        std::exit(1);
    }

    // log_level 校验
    {
        std::string ll = cfg.log_level;
        std::transform(ll.begin(), ll.end(), ll.begin(), ::tolower);
        if (ll != "debug" && ll != "info" && ll != "warn" && ll != "error") {
            std::fprintf(stderr, "[fsys] invalid --log-level: %s (must be debug|info|warn|error)\n",
                         cfg.log_level.c_str());
            std::exit(1);
        }
        cfg.log_level = ll;
    }

    // 8. 路径规范化：static_root/SSL 相对路径基于当前工作目录
    //    （_run.sh 以模块目录为 cwd 启动，与 Python 版 BASE_DIR 语义一致；
    //      Docker 场景使用绝对路径 STATIC_ROOT，不受影响）
    if (!cfg.static_root.empty() && cfg.static_root[0] != '/') {
        cfg.static_root = (fs::current_path() / cfg.static_root)
                              .lexically_normal().string();
    }
    if (!cfg.ssl_cert.empty() && cfg.ssl_cert[0] != '/') {
        cfg.ssl_cert = (fs::current_path() / cfg.ssl_cert)
                           .lexically_normal().string();
        cfg.ssl_key  = (fs::current_path() / cfg.ssl_key)
                           .lexically_normal().string();
    }

    return cfg;
}

// ============================================================================
// 异常类
// ============================================================================

class FsysExp : public std::runtime_error {
public:
    FsysExp(const std::string& msg, int code)
        : std::runtime_error(msg), status_(code) {}
    int status() const noexcept { return status_; }
private:
    int status_;
};

// ============================================================================
// MIME 类型映射
// ============================================================================

static const std::unordered_map<std::string, std::string> kFileMimeTypes = {
    {".html", "text/html"}, {".js", "application/javascript"}, {".css", "text/css"},
    {".json", "application/json"}, {".png", "image/png"}, {".jpg", "image/jpeg"},
    {".jpeg", "image/jpeg"}, {".gif", "image/gif"}, {".txt", "text/plain"},
    {".pdf", "application/pdf"}, {".svg", "image/svg+xml"}, {".ico", "image/x-icon"},
    {".xml", "application/xml"}, {".woff", "font/woff"}, {".woff2", "font/woff2"},
    {".ttf", "font/ttf"}, {".eot", "application/vnd.ms-fontobject"},
    {".md", "text/markdown"}, {".wasm", "application/wasm"},
};

static const std::unordered_map<std::string, std::string> kVideoMimeTypes = {
    {".mp4", "video/mp4"}, {".webm", "video/webm"}, {".ogg", "video/ogg"},
    {".avi", "video/x-msvideo"}, {".mov", "video/quicktime"}, {".mkv", "video/x-matroska"},
};

static const std::unordered_map<std::string, std::string> kAudioMimeTypes = {
    {".mp3", "audio/mpeg"}, {".wav", "audio/wav"},
    {".flac", "audio/flac"}, {".aac", "audio/aac"}, {".m4a", "audio/mp4"},
};

static std::string get_file_mime(const std::string& path) {
    std::string ext = fs::path(path).extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
    auto it = kFileMimeTypes.find(ext);
    return (it != kFileMimeTypes.end()) ? it->second : "application/octet-stream";
}

static bool is_video_file(const std::string& path) {
    std::string ext = fs::path(path).extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
    return kVideoMimeTypes.count(ext) > 0;
}

static bool is_audio_file(const std::string& path) {
    std::string ext = fs::path(path).extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
    // .ogg 同时在 video 和 audio 中，这里按 audio 处理时额外检查
    if (kAudioMimeTypes.count(ext) > 0) return true;
    // .ogg 默认归 video，但 audio/ogg 也合法
    if (ext == ".ogg") return true;
    return false;
}

static std::string get_media_mime(const std::string& path) {
    std::string ext = fs::path(path).extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
    auto vit = kVideoMimeTypes.find(ext);
    if (vit != kVideoMimeTypes.end()) return vit->second;
    auto ait = kAudioMimeTypes.find(ext);
    if (ait != kAudioMimeTypes.end()) return ait->second;
    if (ext == ".ogg") return "audio/ogg";
    return "application/octet-stream";
}

// ============================================================================
// 路径安全
// ============================================================================

// 路径遍历防护：返回规范化路径，非法时返回空字符串
static std::string safe_path_join(const std::string& base, const std::string& user_path) {
    // 拒绝空字节截断（解码后的 %00）
    if (user_path.find('\0') != std::string::npos) return "";
    if (user_path.empty() || user_path == "/" || user_path.find_first_not_of("/ ") == std::string::npos) {
        try { return fs::canonical(base).string(); }
        catch (...) { return base; }
    }
    std::string rel = user_path;
    if (!rel.empty() && rel[0] == '/') rel = rel.substr(1);
    fs::path full = fs::weakly_canonical(fs::path(base) / rel);
    fs::path base_canon;
    try { base_canon = fs::canonical(base); }
    catch (...) { return ""; }

    std::string full_str = full.string();
    std::string base_str = base_canon.string();
    if (full_str != base_str &&
        full_str.substr(0, base_str.size() + 1) != base_str + "/") {
        return "";  // 路径逃逸
    }
    return full_str;
}

// ============================================================================
// SSRF 防护：检测私有 IP
// ============================================================================

static bool is_ip_private(const struct sockaddr* addr) {
    if (addr->sa_family == AF_INET) {
        auto* sa = reinterpret_cast<const struct sockaddr_in*>(addr);
        uint32_t ip = ntohl(sa->sin_addr.s_addr);
        uint8_t a = (ip >> 24) & 0xFF;
        uint8_t b = (ip >> 16) & 0xFF;
        // 127.0.0.0/8
        if (a == 127) return true;
        // 10.0.0.0/8
        if (a == 10) return true;
        // 172.16.0.0/12
        if (a == 172 && b >= 16 && b <= 31) return true;
        // 192.168.0.0/16
        if (a == 192 && b == 168) return true;
        // 169.254.0.0/16 (link-local)
        if (a == 169 && b == 254) return true;
        // 0.0.0.0
        if (ip == 0) return true;
        // 224.0.0.0/4 (multicast)
        if (a >= 224 && a <= 239) return true;
        // 240.0.0.0/4 (reserved)
        if (a >= 240) return true;
        return false;
    } else if (addr->sa_family == AF_INET6) {
        auto* sa6 = reinterpret_cast<const struct sockaddr_in6*>(addr);
        const uint8_t* b = sa6->sin6_addr.s6_addr;
        // ::1 loopback
        if (b[0] == 0 && b[1] == 0 && b[14] == 0 && b[15] == 1)
            return true;
        // fe80::/10 link-local
        if (b[0] == 0xfe && (b[1] & 0xc0) == 0x80) return true;
        // fc00::/7 unique-local
        if ((b[0] & 0xfe) == 0xfc) return true;
        // :: unspecified
        bool all_zero = true;
        for (int i = 0; i < 16; ++i) if (b[i] != 0) { all_zero = false; break; }
        if (all_zero) return true;
        // ff00::/8 multicast
        if (b[0] == 0xff) return true;
        return false;
    }
    return true;  // 未知地址族，拒绝
}

static bool is_private_host(const std::string& hostname) {
    struct addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int ret = getaddrinfo(hostname.c_str(), nullptr, &hints, &res);
    if (ret != 0 || !res) return true;  // 解析失败，fail-closed

    bool private_found = false;
    for (auto* p = res; p; p = p->ai_next) {
        if (is_ip_private(p->ai_addr)) {
            private_found = true;
            break;
        }
    }
    freeaddrinfo(res);
    return private_found;
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

static http::response<http::string_body> make_secured_response(
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
    http::status status, const std::string& json_str,
    const http::request<http::string_body>& req)
{
    return make_secured_response(status, json_str, "application/json", req);
}

static http::response<http::string_body> make_json_obj(
    http::status status, const json::object& obj,
    const http::request<http::string_body>& req)
{
    return make_json(status, json::serialize(obj), req);
}

static http::response<http::string_body> make_error(
    http::status status, const std::string& msg,
    const http::request<http::string_body>& req)
{
    json::object obj;
    obj["error"] = msg;
    return make_json_obj(status, obj, req);
}

// ============================================================================
// FsysServer
// ============================================================================

class FsysServer {
public:
    explicit FsysServer(Config cfg)
        : _cfg(std::move(cfg)),
          _rpc(_cfg.db_host, _cfg.db_port, "fsys", 5.0,
               /*retry_on_refused=*/true),
          _log_rpc(_cfg.db_host, _cfg.db_port, "fsys-log", kLogRpcTimeout,
                   /*retry_on_refused=*/false)
    {
        // 初始化异步日志队列
        _log_queue = std::make_unique<AsyncQueue<LogEntry>>(
            [this](std::vector<LogEntry>&& batch) { _send_log_batch(std::move(batch)); },
            AsyncQueueConfig{kLogBatchSize, kLogFlushInterval, kLogQueueMax, 0.5},
            [](const std::exception& e, std::vector<LogEntry>&&) {
                std::fprintf(stderr, "[fsys] 日志队列异常: %s\n", e.what());
            }
        );
        _log_queue->start();

        // 确保 static_root 目录存在
        std::error_code ec;
        fs::create_directories(_cfg.static_root, ec);
        if (ec) {
            std::fprintf(stderr, "[fsys] 警告: 无法创建目录 %s: %s\n",
                         _cfg.static_root.c_str(), ec.message().c_str());
            _filesystem_enabled = false;
        }

        std::printf("[fsys] 配置来源: confs (%s @ %s:%d)\n",
                    _cfg.confs_config_name.c_str(),
                    _cfg.confs_host.c_str(), _cfg.confs_port);
        std::printf("[fsys] 实例: %s | 日志级别: %s\n",
                    _cfg.instance_name.c_str(), _cfg.log_level.c_str());
        std::printf("[fsys] 静态目录: %s\n", _cfg.static_root.c_str());
    }

    ~FsysServer() {
        if (_log_queue) _log_queue->shutdown();
    }

    void run();

private:
    Config _cfg;
    RpcClient _rpc;        // db RPC（retry_on_refused=true）
    RpcClient _log_rpc;    // 日志写入

    std::unique_ptr<AsyncQueue<LogEntry>> _log_queue;
    bool _filesystem_enabled = true;

    HttpServer* _server = nullptr;
    static std::atomic<bool> _should_stop;

    // 限速存储
    std::mutex _rate_mutex;
    std::unordered_map<std::string, std::vector<std::chrono::steady_clock::time_point>> _rate_store;

    static void _signal_handler(int) { _should_stop.store(true); }

    // ---- 日志 --------------------------------------------------------------

    void _log(const std::string& level, const std::string& service,
              const std::string& message, const std::string& trace_id = "",
              const std::string& extra = "") {
        // 日志级别过滤
        static const std::unordered_map<std::string, int> kLogLevels = {
            {"debug", 0}, {"DEBUG", 0},
            {"info", 1}, {"INFO", 1},
            {"warn", 2}, {"WARNING", 2},
            {"error", 3}, {"ERROR", 3}
        };
        auto cfg_it = kLogLevels.find(_cfg.log_level);
        auto msg_it = kLogLevels.find(level);
        if (cfg_it != kLogLevels.end() && msg_it != kLogLevels.end()) {
            if (msg_it->second < cfg_it->second) return;
        }

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
                values["trace_id"] = e.trace_id.empty() ? nullptr : json::value(e.trace_id);
                values["extra"]    = e.extra.empty()    ? nullptr : json::value(e.extra);

                json::object payload;
                payload["action"] = "insert";
                payload["table"]  = "Logs";
                payload["values"] = values;

                _log_rpc.call("execute", payload);
            } catch (const std::exception& ex) {
                std::fprintf(stderr, "[fsys] 日志写入失败: %s\n", ex.what());
            }
        }
    }

    // ---- db 调用 -----------------------------------------------------------

    struct DbResult {
        bool success = false;
        json::value data;
        std::string error;
    };

    DbResult _call_db(const json::object& payload) {
        try {
            json::value data = _rpc.call("execute", payload);
            return {true, data, ""};
        } catch (const RpcError& e) {
            return {false, nullptr, e.what()};
        } catch (const std::exception& e) {
            _log("ERROR", "fsys", std::string("db 调用失败: ") + e.what());
            throw FsysExp("数据库服务暂时不可用", 502);
        }
    }

    // ---- 客户端 IP ---------------------------------------------------------

    static std::string client_ip(const http::request<http::string_body>& req) {
        auto xff = req.find("X-Forwarded-For");
        if (xff != req.end()) {
            std::string val(xff->value());
            auto comma = val.find(',');
            std::string ip = (comma == std::string::npos) ? val : val.substr(0, comma);
            auto b = ip.find_first_not_of(" ");
            auto e = ip.find_last_not_of(" ");
            if (b != std::string::npos) return ip.substr(b, e - b + 1);
        }
        return "unknown";
    }

    // ---- 限速 --------------------------------------------------------------

    bool _check_rate_limit(const std::string& key, int max_requests) {
        auto now = std::chrono::steady_clock::now();
        auto cutoff = now - std::chrono::seconds(kRateWindowSeconds);
        std::lock_guard<std::mutex> lock(_rate_mutex);
        // 键数超限：全局驱逐过期窗口（防伪造 XFF 旋转导致无限增长）
        if (_rate_store.size() > kRateMaxKeys) {
            for (auto it = _rate_store.begin(); it != _rate_store.end();) {
                auto& ts = it->second;
                ts.erase(std::remove_if(ts.begin(), ts.end(),
                                        [&](const auto& t) { return t < cutoff; }),
                         ts.end());
                if (ts.empty()) it = _rate_store.erase(it);
                else ++it;
            }
        }
        auto& timestamps = _rate_store[key];
        timestamps.erase(
            std::remove_if(timestamps.begin(), timestamps.end(),
                           [&](const auto& t) { return t < cutoff; }),
            timestamps.end());
        if (static_cast<int>(timestamps.size()) >= max_requests) return false;
        timestamps.push_back(now);
        return true;
    }

    // ---- CORS --------------------------------------------------------------

    bool _is_allowed_origin(const std::string& origin) const {
        for (const auto& o : _cfg.cors_origins) {
            if (o == origin) return true;
        }
        return false;
    }

    void _apply_cors(http::response<http::string_body>& res,
                     const http::request<http::string_body>& req) {
        auto origin_it = req.find(http::field::origin);
        if (origin_it != req.end()) {
            std::string origin(origin_it->value());
            if (_is_allowed_origin(origin)) {
                res.set("Access-Control-Allow-Origin", origin);
                res.set("Vary", "Origin");
            }
        }
        res.set("Access-Control-Allow-Headers", "Content-Type, Authorization");
        res.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    }

    http::response<http::string_body> _cors_preflight(
        const http::request<http::string_body>& req)
    {
        http::response<http::string_body> res;
        res.result(http::status::no_content);
        res.version(req.version());
        res.keep_alive(req.keep_alive());
        _apply_cors(res, req);
        res.prepare_payload();
        return res;
    }

    // ---- 路由 handler 声明 -------------------------------------------------

    http::response<http::string_body> _handle_root(
        const http::request<http::string_body>& req);
    http::response<http::string_body> _handle_catch_all(
        const http::request<http::string_body>& req);
    http::response<http::string_body> _handle_filelist(
        const http::request<http::string_body>& req);
    http::response<http::string_body> _handle_video(
        const http::request<http::string_body>& req);
    http::response<http::string_body> _handle_hash(
        const http::request<http::string_body>& req);
    http::response<http::string_body> _handle_check_user(
        const http::request<http::string_body>& req);
    http::response<http::string_body> _handle_favicon(
        const http::request<http::string_body>& req);
    http::response<http::string_body> _handle_dnfs_proxy(
        const http::request<http::string_body>& req,
        const std::string& route);
    http::response<http::string_body> _handle_health(
        const http::request<http::string_body>& req);
};

// 静态成员定义
std::atomic<bool> FsysServer::_should_stop{false};

// ============================================================================
// run() — 注册路由 + 启动服务器
// ============================================================================

void FsysServer::run() {
    std::unique_ptr<HttpServer> server_ptr;

    if (_cfg.ssl_enabled && fs::exists(_cfg.ssl_cert) && fs::exists(_cfg.ssl_key)) {
        server_ptr = std::make_unique<HttpServer>(
            _cfg.listen_host, static_cast<unsigned short>(_cfg.listen_port),
            _cfg.ssl_cert, _cfg.ssl_key,
            static_cast<unsigned int>(_cfg.threads));
        std::printf("[fsys] HTTPS 模式\n");
    } else {
        server_ptr = std::make_unique<HttpServer>(
            _cfg.listen_host, static_cast<unsigned short>(_cfg.listen_port),
            static_cast<unsigned int>(_cfg.threads));
    }

    HttpServer& server = *server_ptr;
    server.setMaxBodySize(kMaxBodySize);

    // 注册具体路由（优先于通配符）
    server.get("/",             [this](const auto& req, const auto&) { return _handle_root(req); });
    server.get("/api/filelist", [this](const auto& req, const auto&) { return _handle_filelist(req); });
    server.get("/api/favicon",  [this](const auto& req, const auto&) { return _handle_favicon(req); });
    server.get("/health",       [this](const auto& req, const auto&) { return _handle_health(req); });

    server.post("/api/hash",       [this](const auto& req, const auto&) { return _handle_hash(req); });
    server.post("/api/check_user", [this](const auto& req, const auto&) { return _handle_check_user(req); });

    // dnfs2 代理路由
    auto dnfs_handler = [this](const std::string& route) {
        return [this, route](const auto& req, const auto&) {
            return _handle_dnfs_proxy(req, route);
        };
    };
    server.post("/get_notes",    dnfs_handler("get_notes"));
    server.post("/add_note",     dnfs_handler("add_note"));
    server.post("/delete_note",  dnfs_handler("delete_note"));
    server.post("/update_note",  dnfs_handler("update_note"));

    // CORS 预检
    auto options_handler = [this](const auto& req, const auto&) {
        return _cors_preflight(req);
    };
    server.options("/get_notes",   options_handler);
    server.options("/add_note",    options_handler);
    server.options("/delete_note", options_handler);
    server.options("/update_note", options_handler);

    // 视频流路由（通配符）
    server.get("/video/*", [this](const auto& req, const auto&) { return _handle_video(req); });

    // 通配符路由（静态文件 / 目录）—— 必须最后注册
    server.get("/*", [this](const auto& req, const auto&) { return _handle_catch_all(req); });

    // 信号处理（优雅关闭）
    _server = &server;
    std::signal(SIGINT,  &FsysServer::_signal_handler);
    std::signal(SIGTERM, &FsysServer::_signal_handler);

    std::thread stop_watcher([this]() {
        while (!_should_stop.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        if (_server) _server->stop();
    });

    _log("INFO", "fsys", "服务启动，监听 " + std::string(_cfg.ssl_enabled ? "https" : "http")
         + "://" + _cfg.listen_host + ":" + std::to_string(_cfg.listen_port)
         + "（root=" + _cfg.static_root + "）");

    server.run();

    _should_stop.store(true);
    if (stop_watcher.joinable()) stop_watcher.join();

    _log("INFO", "fsys", "服务停止");
    if (_log_queue) _log_queue->shutdown();
}

// ============================================================================
// GET / — 根路径
// ============================================================================

http::response<http::string_body> FsysServer::_handle_root(
    const http::request<http::string_body>& req)
{
    try {
        if (!_filesystem_enabled)
            return make_error(http::status::forbidden, "\u6587\u4ef6\u7cfb\u7edf\u672a\u542f\u7528", req);

        // 优先 index.html
        std::string index_path = _cfg.static_root + "/index.html";
        if (fs::exists(index_path)) {
            std::ifstream f(index_path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());
            return make_secured_response(http::status::ok, body, "text/html", req);
        }

        // 回退 __view.html
        std::string view_path = _cfg.static_root + "/" + kViewFile;
        if (fs::exists(view_path)) {
            std::ifstream f(view_path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());
            return make_secured_response(http::status::ok, body, "text/html", req);
        }

        return make_error(http::status::internal_server_error,
                          "\u89c6\u56fe\u6587\u4ef6 " + kViewFile + " \u4e0d\u5b58\u5728", req);
    } catch (const std::exception& e) {
        _log("ERROR", "fsys", std::string("\u6839\u8def\u5f84\u9519\u8bef: ") + e.what());
        return make_error(http::status::internal_server_error, "\u5185\u90e8\u9519\u8bef", req);
    }
}

// ============================================================================
// GET /* — 静态文件 / 目录浏览
// ============================================================================

http::response<http::string_body> FsysServer::_handle_catch_all(
    const http::request<http::string_body>& req)
{
    try {
        if (!_filesystem_enabled)
            return make_error(http::status::forbidden, "\u6587\u4ef6\u7cfb\u7edf\u672a\u542f\u7528", req);

        std::string path = extract_path(std::string(req.target()));
        if (path.size() > 1 && path[0] == '/') path = path.substr(1);
        path = url_decode(path, /*plus_is_space=*/false);  // 浏览器/前端 encodeURIComponent

        std::string full_path = safe_path_join(_cfg.static_root, path);
        if (full_path.empty())
            return make_error(http::status::forbidden, "\u65e0\u6548\u7684\u8def\u5f84", req);

        if (!fs::exists(full_path))
            return make_error(http::status::not_found, "\u8def\u5f84\u4e0d\u5b58\u5728", req);

        // 文件处理
        if (fs::is_regular_file(full_path)) {
            // 大小上限（string_body 全量读入内存，防大文件 DoS）
            if (fs::file_size(full_path) > kMaxServeFileSize)
                return make_error(http::status::payload_too_large, "\u6587\u4ef6\u8fc7\u5927", req);

            // 下载模式
            auto params = parse_query(std::string(req.target()));
            bool download = (params.count("download") && params["download"] == "true");

            std::ifstream f(full_path, std::ios::binary);
            if (!f) return make_error(http::status::internal_server_error, "\u6587\u4ef6\u8bfb\u53d6\u5931\u8d25", req);
            std::string body((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());

            std::string mime;
            if (is_video_file(full_path) || is_audio_file(full_path)) {
                mime = get_media_mime(full_path);
            } else {
                mime = get_file_mime(full_path);
            }

            auto res = make_secured_response(http::status::ok, body, mime, req);
            if (download) {
                res.set("Content-Disposition",
                        "attachment; filename=\"" + fs::path(full_path).filename().string() + "\"");
            }
            return res;
        }

        // 目录处理：返回 __view.html
        std::string view_path = _cfg.static_root + "/" + kViewFile;
        if (fs::exists(view_path)) {
            std::ifstream f(view_path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());
            return make_secured_response(http::status::ok, body, "text/html", req);
        }

        return make_error(http::status::not_found, "\u76ee\u5f55\u65e0\u6cd5\u6d4f\u89c8", req);

    } catch (const std::exception& e) {
        _log("ERROR", "fsys", std::string("\u9759\u6001\u6587\u4ef6\u9519\u8bef: ") + e.what());
        return make_error(http::status::internal_server_error, "\u5185\u90e8\u9519\u8bef", req);
    }
}

// ============================================================================
// GET /api/filelist — 文件列表
// ============================================================================

http::response<http::string_body> FsysServer::_handle_filelist(
    const http::request<http::string_body>& req)
{
    try {
        if (!_filesystem_enabled)
            return make_error(http::status::forbidden, "\u6587\u4ef6\u7cfb\u7edf\u672a\u542f\u7528", req);

        auto params = parse_query(std::string(req.target()));
        std::string requested_path = params.count("path") ? params["path"] : "";

        std::string full_path = safe_path_join(_cfg.static_root, requested_path);
        if (full_path.empty())
            return make_error(http::status::forbidden, "\u65e0\u6548\u7684\u8def\u5f84", req);

        if (!fs::exists(full_path) || !fs::is_directory(full_path))
            return make_error(http::status::not_found, "\u76ee\u5f55\u4e0d\u5b58\u5728", req);

        json::array items;
        for (const auto& entry : fs::directory_iterator(full_path)) {
            std::string name = entry.path().filename().string();
            // 过滤以 __ 开头的系统文件
            if (name.size() >= 2 && name[0] == '_' && name[1] == '_') continue;

            bool is_dir = entry.is_directory();
            std::string file_type = is_dir ? "dir" : "file";
            if (!is_dir) {
                if (is_video_file(entry.path().string())) file_type = "video";
                else if (is_audio_file(entry.path().string())) file_type = "audio";
            }

            std::string rel_path = requested_path.empty()
                ? name : requested_path + "/" + name;

            uintmax_t file_size = 0;
            std::string modified;
            try {
                if (!is_dir) file_size = entry.file_size();
                auto ftime = fs::last_write_time(entry);
                auto sctp = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
                    ftime - fs::file_time_type::clock::now() + std::chrono::system_clock::now());
                std::time_t tt = std::chrono::system_clock::to_time_t(sctp);
                struct tm tmbuf;
                localtime_r(&tt, &tmbuf);
                char tbuf[32];
                std::strftime(tbuf, sizeof(tbuf), "%Y-%m-%d %H:%M:%S", &tmbuf);
                modified = tbuf;
            } catch (...) { modified = ""; }

            json::object item;
            item["name"]           = name;
            item["type"]           = file_type;
            item["path"]           = rel_path;
            item["size"]           = is_dir ? 0 : static_cast<int64_t>(file_size);
            item["size_formatted"] = is_dir ? "" : format_file_size(file_size);
            item["modified"]       = modified;
            items.push_back(item);
        }

        // 排序：目录在前，然后按名称
        std::sort(items.begin(), items.end(), [](const json::value& a, const json::value& b) {
            const auto& oa = a.as_object();
            const auto& ob = b.as_object();
            bool a_dir = (std::string(oa.at("type").as_string()) == "dir");
            bool b_dir = (std::string(ob.at("type").as_string()) == "dir");
            if (a_dir != b_dir) return a_dir;
            std::string na = std::string(oa.at("name").as_string());
            std::string nb = std::string(ob.at("name").as_string());
            std::transform(na.begin(), na.end(), na.begin(), ::tolower);
            std::transform(nb.begin(), nb.end(), nb.begin(), ::tolower);
            return na < nb;
        });

        // parent_path 计算
        std::string parent_path;
        if (!requested_path.empty()) {
            auto slash = requested_path.rfind('/');
            parent_path = (slash == std::string::npos) ? "" : requested_path.substr(0, slash);
        }

        json::object result;
        result["success"]     = true;
        result["path"]        = requested_path.empty() ? "/" : requested_path;
        result["parent_path"] = parent_path;
        result["items"]       = items;
        return make_json_obj(http::status::ok, result, req);

    } catch (const FsysExp& e) {
        return make_error(static_cast<http::status>(e.status()), e.what(), req);
    } catch (const std::exception& e) {
        _log("ERROR", "fsys", std::string("\u6587\u4ef6\u5217\u8868\u9519\u8bef: ") + e.what());
        return make_error(http::status::internal_server_error, "\u5185\u90e8\u9519\u8bef", req);
    }
}

// ============================================================================
// GET /video/* — 视频/音频流媒体（HTTP Range 206）
// ============================================================================

http::response<http::string_body> FsysServer::_handle_video(
    const http::request<http::string_body>& req)
{
    try {
        if (!_filesystem_enabled)
            return make_error(http::status::forbidden, "\u6587\u4ef6\u7cfb\u7edf\u672a\u542f\u7528", req);

        // 从 target 提取文件路径（去掉 /video/ 前缀）
        std::string target = extract_path(std::string(req.target()));
        std::string file_path;
        if (target.size() > 7) {  // "/video/" = 7 chars
            file_path = target.substr(7);
        }
        file_path = url_decode(file_path, /*plus_is_space=*/false);

        std::string full_path = safe_path_join(_cfg.static_root, file_path);
        if (full_path.empty() || !fs::exists(full_path) || !fs::is_regular_file(full_path))
            return make_error(http::status::not_found, "\u6587\u4ef6\u4e0d\u5b58\u5728", req);

        if (!is_video_file(full_path) && !is_audio_file(full_path))
            return make_error(http::status::bad_request, "\u4e0d\u652f\u6301\u7684\u6587\u4ef6\u7c7b\u578b", req);

        uintmax_t file_size = fs::file_size(full_path);
        std::string mime = get_media_mime(full_path);

        // 检查 Range 头
        auto range_it = req.find(http::field::range);
        if (range_it != req.end()) {
            std::string range_val(range_it->value());
            // 解析 "bytes=start-end"
            if (range_val.rfind("bytes=", 0) != 0)
                return make_error(http::status::bad_request, "\u65e0\u6548\u7684 Range \u8bf7\u6c42", req);

            std::string range_spec = range_val.substr(6);
            auto dash = range_spec.find('-');
            if (dash == std::string::npos)
                return make_error(http::status::bad_request, "\u65e0\u6548\u7684 Range \u8bf7\u6c42", req);

            int64_t start = 0, end = static_cast<int64_t>(file_size) - 1;
            try {
                std::string s_start = range_spec.substr(0, dash);
                std::string s_end   = range_spec.substr(dash + 1);
                if (!s_start.empty()) start = std::stoll(s_start);
                if (!s_end.empty())   end   = std::stoll(s_end);
            } catch (...) {
                return make_error(http::status::bad_request, "\u65e0\u6548\u7684 Range \u8bf7\u6c42", req);
            }

            // 边界校验
            if (start < 0 || start >= static_cast<int64_t>(file_size) ||
                end >= static_cast<int64_t>(file_size) || end < start) {
                return make_error(http::status::range_not_satisfiable,
                                  "\u8bf7\u6c42\u8303\u56f4\u8d85\u51fa\u6587\u4ef6\u5927\u5c0f", req);
            }

            int64_t length = end - start + 1;

            // 片段大小上限（防 bytes=0- 全量拉取大文件）
            if (static_cast<uintmax_t>(length) > kMaxServeFileSize)
                return make_error(http::status::range_not_satisfiable,
                                  "\u8bf7\u6c42\u8303\u56f4\u8fc7\u5927", req);

            // 读取文件片段
            std::ifstream f(full_path, std::ios::binary);
            f.seekg(start);
            std::string body(length, '\0');
            f.read(body.data(), length);

            auto res = make_secured_response(http::status::partial_content, body, mime, req);
            res.set("Content-Range", "bytes " + std::to_string(start) + "-"
                    + std::to_string(end) + "/" + std::to_string(file_size));
            res.set("Accept-Ranges", "bytes");
            res.set("Cache-Control", "no-cache");
            return res;
        }

        // 无 Range：返回完整文件（大文件超限拒绝，浏览器拉取媒体总会带 Range）
        if (file_size > kMaxServeFileSize)
            return make_error(http::status::payload_too_large, "\u6587\u4ef6\u8fc7\u5927", req);

        std::ifstream f(full_path, std::ios::binary);
        std::string body((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());
        auto res = make_secured_response(http::status::ok, body, mime, req);
        res.set("Accept-Ranges", "bytes");
        return res;

    } catch (const std::exception& e) {
        _log("ERROR", "fsys", std::string("视频流传输错误: ") + e.what());
        return make_error(http::status::internal_server_error, "内部错误", req);
    }
}

// ============================================================================
// POST /api/hash — SHA-256 密码哈希
// ============================================================================

http::response<http::string_body> FsysServer::_handle_hash(
    const http::request<http::string_body>& req)
{
    try {
        std::string ip = client_ip(req);
        if (!_check_rate_limit("hash:" + ip, kHashRateLimit))
            return make_error(http::status::too_many_requests, "rate limit exceeded", req);

        json::value val;
        try { val = json::parse(req.body()); }
        catch (...) { return make_error(http::status::bad_request, "invalid input", req); }

        if (!val.is_object())
            return make_error(http::status::bad_request, "invalid input", req);

        const auto& obj = val.as_object();
        auto it = obj.find("password");
        if (it == obj.end() || !it->value().is_string())
            return make_error(http::status::bad_request, "invalid input", req);

        std::string password(it->value().as_string());
        std::string hash = sha256_hex(password);

        json::object result;
        result["hash"] = hash;
        return make_json_obj(http::status::ok, result, req);

    } catch (const std::exception& e) {
        _log("ERROR", "fsys", std::string("hash 错误: ") + e.what());
        return make_error(http::status::internal_server_error, "内部错误", req);
    }
}

// ============================================================================
// POST /api/check_user — 检查用户是否存在
// ============================================================================

http::response<http::string_body> FsysServer::_handle_check_user(
    const http::request<http::string_body>& req)
{
    try {
        std::string ip = client_ip(req);
        if (!_check_rate_limit("check_user:" + ip, kCheckUserRateLimit))
            return make_error(http::status::too_many_requests, "rate limit exceeded", req);

        json::value val;
        try { val = json::parse(req.body()); }
        catch (...) {
            json::object r; r["success"] = false; r["error"] = "missing email";
            return make_json_obj(http::status::bad_request, r, req);
        }

        if (!val.is_object()) {
            json::object r; r["success"] = false; r["error"] = "missing email";
            return make_json_obj(http::status::bad_request, r, req);
        }

        const auto& obj = val.as_object();
        auto it = obj.find("email");
        if (it == obj.end() || !it->value().is_string()) {
            json::object r; r["success"] = false; r["error"] = "missing email";
            return make_json_obj(http::status::bad_request, r, req);
        }

        std::string email(it->value().as_string());
        // trim
        auto b = email.find_first_not_of(" \t");
        auto e = email.find_last_not_of(" \t");
        if (b == std::string::npos) {
            json::object r; r["success"] = false; r["error"] = "missing email";
            return make_json_obj(http::status::bad_request, r, req);
        }
        email = email.substr(b, e - b + 1);

        json::object payload;
        payload["action"]  = "select";
        payload["table"]   = "Users";
        payload["where"]   = json::object{{"email", email}};
        payload["columns"] = json::array{"email"};

        DbResult r = _call_db(payload);
        if (!r.success) {
            _log("WARNING", "fsys", "check_user 查询失败: " + r.error);
            json::object result; result["success"] = false; result["error"] = "query failed";
            return make_json_obj(http::status::internal_server_error, result, req);
        }

        bool found = r.data.is_array() && !r.data.as_array().empty();
        json::object result;
        result["success"] = true;
        result["found"]   = found;
        return make_json_obj(http::status::ok, result, req);

    } catch (const FsysExp& exp) {
        json::object r; r["success"] = false; r["error"] = exp.what();
        return make_json_obj(static_cast<http::status>(exp.status()), r, req);
    } catch (const std::exception& e) {
        _log("ERROR", "fsys", std::string("check_user 错误: ") + e.what());
        json::object r; r["success"] = false; r["error"] = "internal error";
        return make_json_obj(http::status::internal_server_error, r, req);
    }
}

// ============================================================================
// GET /api/favicon?url= — favicon SSRF 防护代理
// ============================================================================

// URL SSRF 校验：仅 http/https + 端口范围 + 私有 IP 检测（fail-closed）
// 返回 0 合法（origin_out = scheme://hostport）；1 bad url；2 bad port；3 forbidden host
static int validate_url_ssrf(const std::string& url, std::string& origin_out) {
    if (url.rfind("http://", 0) != 0 && url.rfind("https://", 0) != 0)
        return 1;

    auto host_start = url.find("://") + 3;
    auto host_end = url.find('/', host_start);
    std::string hostport = (host_end == std::string::npos)
        ? url.substr(host_start)
        : url.substr(host_start, host_end - host_start);

    std::string hostname = hostport;
    auto colon = hostport.rfind(':');
    if (colon != std::string::npos) {
        hostname = hostport.substr(0, colon);
        try {
            int port = std::stoi(hostport.substr(colon + 1));
            if (port < 1 || port > 65535) return 2;
        } catch (...) {
            return 2;
        }
    }

    if (hostname.empty()) return 1;
    if (is_private_host(hostname)) return 3;

    origin_out = url.substr(0, host_end == std::string::npos ? url.size() : host_end);
    return 0;
}

// libcurl 写入回调
static size_t curl_write_cb(char* ptr, size_t size, size_t nmemb, void* userdata) {
    auto* buf = static_cast<std::string*>(userdata);
    size_t total = size * nmemb;
    if (buf->size() + total > kFaviconMaxBytes + 1) return 0;  // 中止：超限
    buf->append(ptr, total);
    return total;
}

http::response<http::string_body> FsysServer::_handle_favicon(
    const http::request<http::string_body>& req)
{
    try {
        auto params = parse_query(std::string(req.target()));
        std::string target_url = params.count("url") ? params["url"] : "";

        // 初始 URL 校验：仅 http/https + 端口 + 私有 IP（SSRF 防护）
        std::string origin;
        int v = validate_url_ssrf(target_url, origin);
        if (v == 1) return make_error(http::status::bad_request, "bad url", req);
        if (v == 2) return make_error(http::status::bad_request, "bad port", req);
        if (v == 3) return make_error(http::status::forbidden, "forbidden host", req);

        std::string current_url = origin + "/favicon.ico";

        // 手动逐跳重定向：每一跳重新校验目标（防 30x 跳转绕过 SSRF 检查，
        // 对齐 Python 版 _FaviconRedirectHandler）
        std::string response_body;
        std::string content_type;
        long http_code = 0;
        int redirects = 0;
        bool ok = false;

        while (true) {
            CURL* curl = curl_easy_init();
            if (!curl) return make_error(http::status::bad_gateway, "fetch failed", req);

            response_body.clear();
            curl_easy_setopt(curl, CURLOPT_URL, current_url.c_str());
            curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_write_cb);
            curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_body);
            curl_easy_setopt(curl, CURLOPT_TIMEOUT, kFaviconTimeout);
            curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, kFaviconTimeout);
            curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);  // 手动逐跳校验
            curl_easy_setopt(curl, CURLOPT_PROTOCOLS_STR, "http,https");
            curl_easy_setopt(curl, CURLOPT_USERAGENT, "qsont-favicon-fetcher/1.0");
            curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);

            CURLcode res_code = curl_easy_perform(curl);
            if (res_code != CURLE_OK) {
                curl_easy_cleanup(curl);
                _log("INFO", "fsys", "favicon 拉取失败: " + std::string(curl_easy_strerror(res_code))
                     + " url=" + target_url.substr(0, 128));
                return make_error(http::status::bad_gateway, "fetch failed", req);
            }

            curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);

            // 3xx：取重定向目标，重新走 SSRF 校验
            if (http_code >= 300 && http_code < 400) {
                char* loc = nullptr;
                curl_easy_getinfo(curl, CURLINFO_REDIRECT_URL, &loc);
                std::string next = loc ? loc : "";
                curl_easy_cleanup(curl);
                if (next.empty() || ++redirects > 3)
                    return make_error(http::status::bad_gateway, "fetch failed", req);
                std::string redir_origin;
                if (validate_url_ssrf(next, redir_origin) != 0)
                    return make_error(http::status::forbidden, "forbidden host", req);
                current_url = next;
                continue;
            }

            char* ct = nullptr;
            curl_easy_getinfo(curl, CURLINFO_CONTENT_TYPE, &ct);
            if (ct) content_type = ct;
            curl_easy_cleanup(curl);
            ok = true;
            break;
        }

        if (!ok || http_code != 200) {
            return make_error(http::status::bad_gateway, "fetch failed", req);
        }

        if (response_body.size() > kFaviconMaxBytes) {
            return make_error(http::status::bad_gateway, "favicon too large", req);
        }

        // Content-Type 校验：必须是 image/*
        if (content_type.empty() || content_type.rfind("image/", 0) != 0) {
            content_type = "image/x-icon";
        }

        auto out = make_secured_response(http::status::ok, response_body, content_type, req);
        out.set("Cache-Control", "public, max-age=86400");
        return out;

    } catch (const std::exception& e) {
        _log("INFO", "fsys", std::string("favicon 异常: ") + e.what());
        return make_error(http::status::bad_gateway, "fetch failed", req);
    }
}

// Cookie 按名精确解析（分号拆分后按名匹配，避免 x_q4_token= 等子串误匹配）
static std::string get_cookie(const std::string& cookies, const std::string& name) {
    size_t pos = 0;
    while (pos <= cookies.size()) {
        auto sc = cookies.find(';', pos);
        std::string pair = cookies.substr(
            pos, sc == std::string::npos ? std::string::npos : sc - pos);
        auto eq = pair.find('=');
        if (eq != std::string::npos) {
            std::string k = pair.substr(0, eq);
            auto b = k.find_first_not_of(" \t");
            auto e = k.find_last_not_of(" \t");
            if (b != std::string::npos) {
                k = k.substr(b, e - b + 1);
                if (k == name) return pair.substr(eq + 1);
            }
        }
        if (sc == std::string::npos) break;
        pos = sc + 1;
    }
    return "";
}

// ============================================================================
// POST /get_notes 等 — dnfs2 反向代理
// ============================================================================

http::response<http::string_body> FsysServer::_handle_dnfs_proxy(
    const http::request<http::string_body>& req,
    const std::string& route)
{
    try {
        // 构建转发请求
        HttpClient http;
        http.set_max_response_size(kHttpMaxResponse);
        http.set_header("Content-Type: application/json");

        // Authorization 头 或 Cookie→Bearer 转换
        auto auth_it = req.find(http::field::authorization);
        if (auth_it != req.end()) {
            http.set_header(std::string("Authorization: ") + std::string(auth_it->value()));
        } else {
            // 检查 Cookie 中的 q4_token（HttpOnly Cookie 模式：Cookie 转 Bearer）
            auto cookie_it = req.find(http::field::cookie);
            if (cookie_it != req.end()) {
                std::string token = get_cookie(std::string(cookie_it->value()), "q4_token");
                if (!token.empty()) {
                    http.set_header("Authorization: Bearer " + token);
                }
            }
        }

        std::string upstream_url = _cfg.dnfs_url + "/" + route;
        std::string response_body;

        http.post_stream_sync(upstream_url, req.body(),
            [&response_body](const std::string& chunk) {
                response_body += chunk;
            });

        long code = http.get_response_code();
        auto res = make_json(static_cast<http::status>(code), response_body, req);
        _apply_cors(res, req);
        return res;

    } catch (const std::exception& e) {
        _log("WARNING", "fsys", "dnfs " + route + " 代理失败: " + e.what());
        json::object err;
        err["error"] = "upstream unavailable";
        auto res = make_json_obj(http::status::bad_gateway, err, req);
        _apply_cors(res, req);
        return res;
    }
}

// ============================================================================
// GET /health — 健康检查
// ============================================================================

http::response<http::string_body> FsysServer::_handle_health(
    const http::request<http::string_body>& req)
{
    json::object obj;
    obj["status"] = "ok";
    return make_json_obj(http::status::ok, obj, req);
}

// ============================================================================
// main
// ============================================================================

int main(int argc, char* argv[]) {
    curl_global_init(CURL_GLOBAL_DEFAULT);

    int ret = 0;
    try {
        Config cfg = load_config(argc, argv);

        std::printf("[fsys] listen=%s:%d threads=%d\n",
                    cfg.listen_host.c_str(), cfg.listen_port, cfg.threads);
        std::printf("[fsys] static_root=%s ssl=%s\n",
                    cfg.static_root.c_str(), cfg.ssl_enabled ? "on" : "off");
        std::printf("[fsys] db=%s:%d dnfs=%s\n",
                    cfg.db_host.c_str(), cfg.db_port, cfg.dnfs_url.c_str());
        std::printf("[fsys] instance=%s log-level=%s config=%s\n",
                    cfg.instance_name.c_str(), cfg.log_level.c_str(),
                    cfg.confs_config_name.c_str());
        std::fflush(stdout);

        FsysServer server(std::move(cfg));
        server.run();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[fsys] fatal: %s\n", e.what());
        ret = 1;
    }
    curl_global_cleanup();
    return ret;
}
