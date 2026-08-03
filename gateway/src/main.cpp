#include <us/HttpServer.hpp>
#include <us/HttpClient.hpp>
#include <us/MyRSA.hpp>
#include <us/Base64.hpp>
#include <us/SHA256.hpp>
#include <us/Sqlite3.hpp>
#include <iostream>
#include <algorithm>
#include <cctype>
#include <typeinfo>
#include <memory>
#include <csignal>
#include <random>
#include <regex>
#include <boost/json.hpp>
#include <boost/program_options.hpp>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/kdf.h>
#include "ExpBase.hpp"
#include "LocalTime.hpp"
#include "Conf.hpp"
#include "DbC.hpp"
#include "LogC.hpp"

using namespace pmc::net;
using namespace qing;
namespace json = boost::json;
namespace po = boost::program_options;

constexpr const char *_VER = "0.7.0";

//////////////////////////////////
#include <sstream>
#include <vector>
#include <string>

/* by suiyu
 *
 * 有一丢丢消耗资源（qing） */
std::vector<std::string> split(const std::string& s, char delim) {
    std::vector<std::string> tokens;
    std::istringstream stream(s);
    std::string token;
    while (std::getline(stream, token, delim)) {
        // 跳过空串：避免 "a;b;" 末尾分号产生 "" 元素
        // 防御白名单匹配场景下空串被误判为命中
        if (!token.empty()) {
            tokens.push_back(token);
        }
    }
    return tokens;
}

//////////////////////////////////
// PBKDF2-HMAC-SHA256 password hashing
//////////////////////////////////
class PBKDF2Hasher {
public:
    static const int ITERATIONS = 100000;
    static const int SALT_LEN = 16;
    static const int HASH_LEN = 32;

    static std::string hash_password(const std::string& password) {
        unsigned char salt[SALT_LEN];
        RAND_bytes(salt, SALT_LEN);

        unsigned char hash[HASH_LEN];
        PKCS5_PBKDF2_HMAC(
            password.c_str(), password.size(),
            salt, SALT_LEN,
            ITERATIONS,
            EVP_sha256(),
            HASH_LEN, hash
        );

        // Format: pbkdf2:100000:<hex_salt>:<hex_hash>
        std::string salt_hex = bytes_to_hex(salt, SALT_LEN);
        std::string hash_hex = bytes_to_hex(hash, HASH_LEN);
        return "pbkdf2:" + std::to_string(ITERATIONS) + ":" + salt_hex + ":" + hash_hex;
    }

    static bool verify_password(const std::string& password, const std::string& stored) {
        // Parse stored hash
        if (stored.substr(0, 7) != "pbkdf2:") {
            return false;
        }

        auto parts = split(stored, ':');
        if (parts.size() != 4) return false;

        int iterations = std::stoi(parts[1]);
        auto salt = hex_to_bytes(parts[2]);
        auto expected_hash = hex_to_bytes(parts[3]);

        unsigned char hash[HASH_LEN];
        PKCS5_PBKDF2_HMAC(
            password.c_str(), password.size(),
            salt.data(), salt.size(),
            iterations,
            EVP_sha256(),
            HASH_LEN, hash
        );

        // Constant-time comparison
        if (expected_hash.size() != (size_t)HASH_LEN) return false;
        unsigned char diff = 0;
        for (int i = 0; i < HASH_LEN; i++) {
            diff |= hash[i] ^ expected_hash[i];
        }
        return diff == 0;
    }

private:
    static std::string bytes_to_hex(const unsigned char* data, size_t len) {
        static const char hex[] = "0123456789abcdef";
        std::string result;
        result.reserve(len * 2);
        for (size_t i = 0; i < len; i++) {
            result.push_back(hex[data[i] >> 4]);
            result.push_back(hex[data[i] & 0x0F]);
        }
        return result;
    }

    static std::vector<unsigned char> hex_to_bytes(const std::string& hex_str) {
        std::vector<unsigned char> bytes;
        bytes.reserve(hex_str.size() / 2);
        for (size_t i = 0; i < hex_str.size(); i += 2) {
            auto hi = hex_char_to_nibble(hex_str[i]);
            auto lo = hex_char_to_nibble(hex_str[i + 1]);
            bytes.push_back((hi << 4) | lo);
        }
        return bytes;
    }

    static unsigned char hex_char_to_nibble(char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
        if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
        return 0;
    }
};

//////////////////////////////////
// Password strength validator
//////////////////////////////////
class PasswordValidator {
public:
    struct Result {
        bool valid;
        std::string message;
    };

    static Result validate(const std::string& password) {
        if (password.size() < 8) {
            return {false, "Password must be at least 8 characters"};
        }
        if (password.size() > 128) {
            return {false, "Password too long (max 128)"};
        }

        bool has_upper = false, has_lower = false, has_digit = false, has_special = false;
        for (char c : password) {
            // std::isupper 等函数参数为 int，char 为有符号且非 ASCII 时是 UB
            // 必须 static_cast<unsigned char> 后再传入（P1-2）
            unsigned char uc = static_cast<unsigned char>(c);
            if (std::isupper(uc)) has_upper = true;
            else if (std::islower(uc)) has_lower = true;
            else if (std::isdigit(uc)) has_digit = true;
            else has_special = true;
        }

        int categories = has_upper + has_lower + has_digit + has_special;
        if (categories < 3) {
            return {false, "Password must contain at least 3 of: uppercase, lowercase, digit, special character"};
        }

        return {true, "OK"};
    }
};

//////////////////////////////////
// IP Rate Limiter (in-memory)
//////////////////////////////////
#include <unordered_map>
#include <mutex>
#include <chrono>

class RateLimiter {
public:
    struct AttemptInfo {
        int count;
        std::chrono::steady_clock::time_point first_attempt;
    };

    bool check_and_record(const std::string& ip, int max_attempts = 5, int window_seconds = 300) {
        std::lock_guard<std::mutex> lock(mtx);
        auto now = std::chrono::steady_clock::now();

        auto& info = attempts[ip];

        // Reset if window expired
        auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - info.first_attempt).count();
        if (elapsed > window_seconds) {
            info.count = 0;
            info.first_attempt = now;
        }

        info.count++;

        // Cleanup old entries periodically
        if (attempts.size() > 10000) {
            for (auto it = attempts.begin(); it != attempts.end(); ) {
                auto e = std::chrono::duration_cast<std::chrono::seconds>(now - it->second.first_attempt).count();
                if (e > window_seconds) it = attempts.erase(it);
                else ++it;
            }
        }

        return info.count <= max_attempts;
    }

private:
    std::unordered_map<std::string, AttemptInfo> attempts;
    std::mutex mtx;
};

//////////////////////////////////
/* -------- 数据库异常\数据库操作失败 -------- */
class DatabaseException: public ExceptionBase {
public:
	using ExceptionBase::ExceptionBase;

	std::string build_message() const override {
		return "[Database]" + ExceptionBase::build_message();
	}
};

/* http异常、非200 */
class HttpException: public ExceptionBase {
public:
	HttpException(int code, const std::string& msg,
			int http_status, const std::string& url,
			const std::source_location& loc = std::source_location::current())
		: ExceptionBase(code, msg, loc)
		, m_http_status(http_status)
		, m_url(url) {}
	std::string build_message() const override {
		return "[Http]" + ExceptionBase::build_message();
	}
	int status() const {
		return m_http_status;
	}
	const std::string& url() const {
		return m_url;
	}
private:
    int m_http_status;
    std::string m_url;
};


//void db_init(); /* 初始化数据库 */
void registerRoutes(HttpServer &server);
std::string generate_captcha_text();
bool verify_captcha(const std::string& token, const std::string& answer);

/* http视图包装器，主要是将异常回显到浏览器 */
http::response<http::string_body> wrapperHttp(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		HttpRequestHandler _main);


/* http代理视图，额外接受一个method字符串 */
http::response<http::string_body> handleProxy(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		const char *method
);

/* */
http::response<http::string_body> handleLogin(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

http::response<http::string_body> handleRegister(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

/* 发送注册邮箱验证码：pymsg 调 smtp 服务 send_email 路由 */
http::response<http::string_body> handleSendEmailCode(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

/* 发送重置密码邮箱验证码：pymsg 调 smtp 服务 send_email 路由（定制重置文案） */
http::response<http::string_body> handleResetSendCode(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

/* 重置密码：与注册流程一致，但最后一步由创建用户改为更新密码 */
http::response<http::string_body> handleResetPassword(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

http::response<http::string_body> handleCaptcha(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

http::response<http::string_body> handleLogout(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

http::response<http::string_body> handleRefreshToken(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

/* 当前用户身份查询：验签 Token 后返回 user / is_admin / Token 生命周期信息 */
http::response<http::string_body> handleWhoami(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

/* 代理 captcha 图形验证码：pymsg 调 captcha 服务，返回 PNG 图片 */
http::response<http::string_body> handleCaptchaImage(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

/* 从 Cookie 头中解析指定名称的值（找不到返回空串） */
std::string get_cookie_value(const std::string& cookie_header, const std::string& name);


/*  headers can't processed */
bool is_special_header(const std::string &header);

/* 请求路径安全性校验（防路径遍历/协议注入/空字节） */
bool is_safe_target(const std::string& target);

/* configure items */
static std::string		_ADDR;
static int			_PORT;
static bool			_TLS = false;
static std::string		_DEFT_URL;
static std::vector<std::string>	_OPEN_URL;
static std::string		_DB_PATH;
static int			_TOKEN_KEEP_TIME = 3600;	// Token 有效期（秒），可由 [Token] keep_time 配置
static int			_TOKEN_REFRESH_GRACE_MULTIPLIER = 2;	// Token 刷新宽限期倍数，过期后 iat + keep_time*倍数 窗口内仍可刷新
static int			_TOKEN_MAX_REFRESH = 10;	// Token 生命周期内最大刷新次数，可由 [Token] max_refresh 配置
static std::vector<std::string>	_CORS_ORIGINS;			// CORS 允许的 Origin 白名单，可由 [CORS] allowed_origins 配置
static std::vector<std::string>	_ADMIN_USERS;			// 管理员账号白名单，可由 [Admin] users 配置（分号分隔），登录时写入 Token payload 的 is_admin

/* 下游 pymsg 服务地址（从 confs [Downstream] 段加载，可被环境变量覆盖） */
static std::string		_DB_HOST = "127.0.0.1";
static int			_DB_PORT = 8005;
static std::string		_CAPTCHA_HOST = "127.0.0.1";
static int			_CAPTCHA_PORT = 8009;
static std::string		_SMTP_HOST = "127.0.0.1";
static int			_SMTP_PORT = 8008;
/* confs 引导配置（不依赖 confs 本身，环境变量 CONFS_HOST/CONFS_PORT 覆盖，默认 127.0.0.1:8010） */
static std::string		_CONFS_HOST = "127.0.0.1";
static int			_CONFS_PORT = 8010;

/* WebSocket 代理路由：path -> ws://backend-host:port/target
 * 仅管理员用户可访问，握手阶段由 ws_proxy 回调验签 Token 中的 is_admin */
static std::unordered_map<std::string, std::string> _WS_PROXY_ROUTES;

/* instance items */
static std::unique_ptr<HttpServer>		_SERVE;
static std::unique_ptr<MyRSA::Generator>	_KEYGEN;
//static std::unique_ptr<Sqlite3>			_SQLDB;
static RateLimiter				_LOGIN_RATE;
static RateLimiter				_REGISTER_RATE;
static RateLimiter				_EMAIL_CODE_RATE;	// 发送邮箱验证码 IP 级限流
static RateLimiter				_REFRESH_TOKEN_RATE;	// Token 刷新限流
static RateLimiter				_RESET_PASSWORD_RATE;	// 重置密码 IP 级限流
static RateLimiter				_RESET_CODE_RATE;	// 发送重置密码验证码 IP 级限流
// 邮箱级双窗口限流:同一邮箱在 gateway 层的发送配额。
// 配合 IP 限流(IP 可被代理池绕过)与 smtp 同邮箱 60s 冷却(仅防短期重复)。
// 双窗口设计:小时窗限制短期轰炸,天窗限制长期骚扰。
static RateLimiter				_EMAIL_ADDR_RATE_HOUR;	// 3 次/小时/邮箱
static RateLimiter				_EMAIL_ADDR_RATE_DAY;	// 10 次/天/邮箱

/* pymsg 下游调用超时（秒）：
 * captcha / smtp verify_email 均为本机内存操作，5s 足够；
 * smtp send_email 需完成一次完整外部 SMTP 会话（握手 + TLS + 投递），放宽到 20s。
 * db 调用的默认超时见 DbC.hpp 的 DbC::DEFAULT_TIMEOUT。 */
static constexpr double			_TIMEOUT_CAPTCHA = 5.0;
static constexpr double			_TIMEOUT_SMTP_VERIFY = 5.0;
static constexpr double			_TIMEOUT_SMTP_SEND = 20.0;

/* CAPTCHA store: token -> answer (in-memory, with expiry) */
#include <unordered_map>
#include <mutex>
struct CaptchaEntry {
    std::string answer;
    std::chrono::steady_clock::time_point created;
};
static std::unordered_map<std::string, CaptchaEntry> _CAPTCHA_STORE;
static std::mutex _CAPTCHA_MTX;

/* 集中式日志客户端：推送 gateway 运行日志到 db 模块 Logs 表（异步批量，fail-safe） */
static inline LogC& logs() { return LogC::instance(); }

std::string generate_captcha_text() {
    static const char chars[] = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
    // thread_local：每个线程独立 rng 实例，避免多线程并发调用 dist(rng) 时的数据竞争
    // （static 变量初始化本身线程安全，但 mt19937::operator() 修改内部状态非线程安全）
    thread_local std::mt19937 rng(std::random_device{}());
    thread_local std::uniform_int_distribution<int> dist(0, sizeof(chars) - 2);

    std::string text;
    for (int i = 0; i < 5; i++) {
        text += chars[dist(rng)];
    }
    return text;
}

bool verify_captcha(const std::string& token, const std::string& answer) {
    std::lock_guard<std::mutex> lock(_CAPTCHA_MTX);
    auto it = _CAPTCHA_STORE.find(token);
    if (it == _CAPTCHA_STORE.end()) return false;

    auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::steady_clock::now() - it->second.created).count();
    if (elapsed > 300) { // 5 min expiry
        _CAPTCHA_STORE.erase(it);
        return false;
    }

    bool match = (it->second.answer == answer);
    _CAPTCHA_STORE.erase(it); // one-time use
    return match;
}

/* 调用 captcha 服务（pymsg/TCP :8009）校验图形验证码。
 * Token 一次性消费；服务不可用或响应异常一律视为校验失败（fail-closed）。
 * 与内置 verify_captcha 并存：登录/注册可任选其一接入。 */
bool verify_captcha_remote(const std::string& token, const std::string& answer) {
	try {
		pmc::net::MsgClient client(_CAPTCHA_HOST, _CAPTCHA_PORT, "gateway");
		json::value jv_req = {
			{"_route", "verify"},
			{"token", token},
			{"answer", answer}
		};
		std::string resp = client.call(json::serialize(jv_req), _TIMEOUT_CAPTCHA);
		auto jv = json::parse(resp);
		auto& obj = jv.as_object();
		if (!obj.at("success").as_bool()) return false;
		return obj.at("data").as_object().at("valid").as_bool();
	} catch (const std::exception&) {
		return false;
	}
}

/* 调用 smtp 服务（pymsg/TCP）校验邮箱验证码。
 * 验证码一次性消费；服务不可用或响应异常一律视为校验失败（fail-closed）。 */
bool verify_email_code_remote(const std::string& email, const std::string& code) {
	try {
		pmc::net::MsgClient client(_SMTP_HOST, _SMTP_PORT, "gateway");
		json::value jv_req = {
			{"_route", "verify_email"},
			{"email", email},
			{"code", code}
		};
		std::string resp = client.call(json::serialize(jv_req), _TIMEOUT_SMTP_VERIFY);
		auto jv = json::parse(resp);
		auto& obj = jv.as_object();
		if (!obj.at("success").as_bool()) return false;
		return obj.at("data").as_object().at("valid").as_bool();
	} catch (const std::exception&) {
		return false;
	}
}

/* -------- 信号处理回调函数 -------- */
void signalHandler(int signum) {

	std::cout << "Receive close signal, prepare to shutdown...\n";
	if (_SERVE) _SERVE->stop();
}

/* -------- 注册信号处理钩子 -------- */
void set_signal_action() {
	struct sigaction sa;
	sa.sa_handler = signalHandler;
	sigemptyset(&sa.sa_mask);
	sa.sa_flags = 0;
	sigaction(SIGINT, &sa, nullptr);
	sigaction(SIGTERM, &sa, nullptr);
}

/* -------- 入口 --------*/
int main(int argc, char **argv) {

	/* 首先处理命令行参数 */
	po::options_description desc("cpp language web gateway");
	desc.add_options()
		("help,h",					"output help message")
		("version,v",					"output version message")
	;


	/* 执行解析（类似） */
	po::variables_map vm;
	{
		try {
			po::store(po::parse_command_line(argc, argv, desc), vm);
			po::notify(vm);
		}

		catch (const std::exception &e) {
			std::cerr << "ERROR: "  << e.what() << std::endl;
			std::cerr << "using --help to check options message" << std::endl;
			throw e;
		}
	}


	/* 分支：拉起帮助页面 */
	if (vm.count("help")) {
		std::cout << desc << std::endl;
		return 0;
	}

	/* 分支：输出版本号 */
	if (vm.count("version")) {
		std::cout << "Gateway " << _VER << std::endl;
		return 0;
	}

	/* ==================== 配置加载（两层：confs 强依赖 → 环境变量覆盖） ====================
	 * 与 Python 服务（dnfs/db/static）保持一致的加载模式：
	 *   1. 引导配置：confs 地址从 CONFS_HOST/CONFS_PORT 环境变量读取，缺省 127.0.0.1:8010
	 *   2. 从 confs 拉取 gateway.ini 全文（retry_on_refused=true 等待 confs 就绪）
	 *   3. 用 boost::property_tree 解析 ini
	 *   4. 环境变量覆盖（最高优先级，Docker 环境需要服务名而非 127.0.0.1）
	 *   5. 配置完整性校验：必需字段缺失则启动失败
	 * 不再支持本地 ini 文件回退（与 static 模块一致，配置统一由 confs 管理）。
	 * ==================================================================================== */

	{
		/* 1. 引导配置：confs 连接地址（不依赖 confs 本身，避免循环依赖） */
		_CONFS_HOST = ConfIni::env("CONFS_HOST", "127.0.0.1");
		_CONFS_PORT = ConfIni::env_int("CONFS_PORT", 8010);

		ConfIni cf;
		try {
			/* 从 confs 拉取 gateway.ini */
			std::cout << "[gateway] 从 confs (" << _CONFS_HOST << ":" << _CONFS_PORT
			          << ") 拉取 gateway.ini ..." << std::endl;
			pmc::net::MsgClient confs_client(_CONFS_HOST, _CONFS_PORT, "gateway");
			json::object req;
			req["_route"] = "get";
			req["name"] = "gateway.ini";
			std::string resp_str = confs_client.call(
				json::serialize(req), /*timeout=*/5.0,
				/*retry_on_refused=*/true);
			auto resp = json::parse(resp_str).as_object();
			if (!resp.at("success").as_bool()) {
				std::string err = "unknown";
				if (auto* ev = resp.if_contains("error")) {
					if (ev->is_string()) err = ev->as_string().c_str();
				}
				std::cerr << "[gateway] 致命错误: 从 confs 获取 gateway.ini 失败: " << err << std::endl;
				return 1;
			}
			std::string ini_content = resp.at("data").as_object().at("content").as_string().c_str();
			cf = ConfIni::fromString(ini_content);
			std::cout << "[gateway] 配置来源: confs (gateway.ini @ "
			          << _CONFS_HOST << ":" << _CONFS_PORT << ")" << std::endl;
		}
		catch (ConfIni::IniConfigureFileParseException &e) {
			std::cerr << "ERROR: " << e.what() << std::endl;
			std::cerr << "ini parse error" << std::endl;
			return 1;
		}
		catch (const std::exception &e) {
			std::cerr << "[gateway] 致命错误: 配置加载失败: " << e.what() << std::endl;
			return 1;
		}

		/* 2. 从 ini 读取配置 */
		_ADDR = cf.get<std::string>("Server.address", "");
		_PORT = cf.get<int>("Server.port", -1);
		_TLS = cf.get<bool>("Server.tls", false);
		_DEFT_URL = cf.get<std::string>("Proxy.default", "");
		_OPEN_URL = split(cf.get<std::string>("Proxy.opened", ""), ';');
		_DB_PATH = cf.get<std::string>("Database.path", "");
		_TOKEN_KEEP_TIME = cf.get<int>("Token.keep_time", 3600);
		_TOKEN_REFRESH_GRACE_MULTIPLIER = cf.get<int>("Token.refresh_grace_multiplier", 2);
		_TOKEN_MAX_REFRESH = cf.get<int>("Token.max_refresh", 10);
		_CORS_ORIGINS = split(cf.get<std::string>("CORS.allowed_origins", ""), ';');
		_ADMIN_USERS = split(cf.get<std::string>("Admin.users", ""), ';');

		/* 下游服务地址 */
		_DB_HOST = cf.get<std::string>("Downstream.db_host", "127.0.0.1");
		_DB_PORT = cf.get<int>("Downstream.db_port", 8005);
		_CAPTCHA_HOST = cf.get<std::string>("Downstream.captcha_host", "127.0.0.1");
		_CAPTCHA_PORT = cf.get<int>("Downstream.captcha_port", 8009);
		_SMTP_HOST = cf.get<std::string>("Downstream.smtp_host", "127.0.0.1");
		_SMTP_PORT = cf.get<int>("Downstream.smtp_port", 8008);

		/* [WsProxy] WebSocket 代理路由：格式 /path=ws://host:port/target;... */
		{
			std::string ws_cfg = cf.get<std::string>("WsProxy.routes", "");
			if (!ws_cfg.empty()) {
				auto entries = split(ws_cfg, ';');
				for (auto& entry : entries) {
					auto eq = entry.find('=');
					if (eq != std::string::npos) {
						std::string path = entry.substr(0, eq);
						std::string url  = entry.substr(eq + 1);
						auto trim = [](std::string& s) {
							while (!s.empty() && std::isspace((unsigned char)s.front())) s.erase(s.begin());
							while (!s.empty() && std::isspace((unsigned char)s.back())) s.pop_back();
						};
						trim(path); trim(url);
						if (!path.empty() && !url.empty()) {
							_WS_PROXY_ROUTES[path] = url;
						}
					}
				}
			}
		}

		/* 3. 环境变量覆盖（优先级最高，Docker 环境使用服务名而非 127.0.0.1） */
		std::string env_listen_addr = ConfIni::env("LISTEN_ADDR");
		if (!env_listen_addr.empty()) _ADDR = env_listen_addr;
		int env_listen_port = ConfIni::env_int("LISTEN_PORT", -1);
		if (env_listen_port > 0) _PORT = env_listen_port;

		std::string env_db_host = ConfIni::env("DB_HOST");
		if (!env_db_host.empty()) _DB_HOST = env_db_host;
		int env_db_port = ConfIni::env_int("DB_PORT", -1);
		if (env_db_port > 0) _DB_PORT = env_db_port;

		std::string env_captcha_host = ConfIni::env("CAPTCHA_HOST");
		if (!env_captcha_host.empty()) _CAPTCHA_HOST = env_captcha_host;
		int env_captcha_port = ConfIni::env_int("CAPTCHA_PORT", -1);
		if (env_captcha_port > 0) _CAPTCHA_PORT = env_captcha_port;

		std::string env_smtp_host = ConfIni::env("SMTP_HOST");
		if (!env_smtp_host.empty()) _SMTP_HOST = env_smtp_host;
		int env_smtp_port = ConfIni::env_int("SMTP_PORT", -1);
		if (env_smtp_port > 0) _SMTP_PORT = env_smtp_port;

		/* 4. 配置完整性校验 */
		if (_ADDR.empty() || _PORT == -1) {
			std::cerr << "[gateway] 致命错误: 配置项 [Server] address/port 缺失"
			             "（ini 中未配置且无 LISTEN_ADDR/LISTEN_PORT 环境变量覆盖）" << std::endl;
			return 1;
		}
		if (_DEFT_URL.empty()) {
			std::cerr << "[gateway] 致命错误: 配置项 [Proxy] default 缺失" << std::endl;
			return 1;
		}
		if (_DB_HOST.empty() || _DB_PORT <= 0) {
			std::cerr << "[gateway] 致命错误: 配置项 [Downstream] db_host/db_port 缺失" << std::endl;
			return 1;
		}
	}

	/* 初始化下游客户端地址 */
	DbC::init(_DB_HOST, _DB_PORT);
	logs().set_endpoint(_DB_HOST, _DB_PORT);

	std::cout << "[gateway] 监听: " << _ADDR << ":" << _PORT
	          << " (TLS: " << (_TLS ? "on" : "off") << ")" << std::endl;
	std::cout << "[gateway] 下游服务: db=" << _DB_HOST << ":" << _DB_PORT
	          << ", captcha=" << _CAPTCHA_HOST << ":" << _CAPTCHA_PORT
	          << ", smtp=" << _SMTP_HOST << ":" << _SMTP_PORT << std::endl;

	// 根据 Server.tls 选择 HTTPS（带证书）或 HTTP（无证书）构造函数
	// HTTP 模式用于 WAF 终止 TLS 后的内部转发链路
	if (_TLS) {
		_SERVE = std::make_unique<HttpServer> (_ADDR, _PORT, "full_chain.crt", "server.key", 4);
	} else {
		_SERVE = std::make_unique<HttpServer> (_ADDR, _PORT, 4);
	}
	_KEYGEN = std::make_unique<MyRSA::Generator> ();
	//_SQLDB = std::make_unique<Sqlite3> (_DB_PATH);

	/* 迁移到了外部 */
	//db_init();
	
	/* */
	registerRoutes(*_SERVE);

	/* */
	set_signal_action();

	/* 获取资源 */
	_SERVE->start();


	std::cout << "Gateway " << _VER << " started" << std::endl;
	logs().info("gateway", std::string("Gateway ") + _VER + " started");

	/* 开始监听 */
	_SERVE->run();

	/* 收到停止信号后的收尾：记录并 flush 剩余日志 */
	logs().info("gateway", "Gateway shutdown");
	logs().shutdown();
}

//void db_init() {
//
//	std::string sql = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='Users'";
//
//	auto flag = false;
//	{
//		auto stmt = _SQLDB->stmt(sql);
//		if (stmt.step()) {
//			auto num = stmt.read_int(0);
//			if (num == 0) {
//				flag = true;
//			}
//		}
//	}
//
//	if (flag) {
//		std::cout << "Start database init..." << std::endl;
//		std::string sql_crtt =
//			"CREATE TABLE Users (\n"
//				"id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
//				"email TEXT UNIQUE NOT NULL,\n"
//				"password_hash TEXT NOT NULL,\n"
//				"create_at DATETIME DEFAULT CURRENT_TIMESTAMP,\n"
//				"last_login DATETIME,\n"
//				"is_active BOOLEAN DEFAULT 1\n"
//			");\n\n"
//			"CREATE INDEX idx_users_email ON users(email);\n";
//
//		_SQLDB->exec(sql_crtt);
//
//		// No hardcoded admin - first registered user becomes admin
//		std::cout << "done (no default admin - register manually)" << std::endl;
//	}
//
//	else {
//		std::cout << "The database already init" << std::endl;
//	}
//}


/* WebSocket 代理握手鉴权：从 HTTP Upgrade 请求中提取 Token 并验证 is_admin。
 * 返回 true 表示鉴权通过，out_target 填入后端 ws:// URL；返回 false 拒绝连接。 */
static bool verifyWsAdminToken(const http::request<http::string_body>& req,
                                const std::string& target_url,
                                std::string& out_target) {
	out_target.clear();
	try {
		std::string raw;
		auto it = req.base().find(http::field::authorization);
		if (it != req.base().end()) {
			raw = std::string(it->value());
		} else {
			auto cit = req.base().find(http::field::cookie);
			if (cit != req.base().end()) {
				std::string cookie_token = get_cookie_value(std::string(cit->value()), "q4_token");
				if (!cookie_token.empty())
					raw = "Bearer " + cookie_token;
			}
		}
		if (raw.empty() || raw.substr(0, 7) != "Bearer ")
			return false;

		raw = raw.substr(7);
		size_t idx = raw.find("_");
		if (idx == std::string::npos) return false;

		std::string data = raw.substr(0, idx);
		std::string sign = raw.substr(idx + 1);
		std::string auth = Base64::base64_decode_to_string(data);

		auto publ = MyRSA::Public_Key::from_pem(_KEYGEN->get_public_key_pem());
		if (!publ.Verify(auth, sign)) return false;

		json::value jv_t;
		try { jv_t = json::parse(auth); }
		catch (...) { return false; }
		if (!jv_t.is_object()) return false;
		json::object& obj = jv_t.as_object();

		auto t = json::value_to<std::string>(obj["created_at"]);
		auto keep_time_val = json::value_to<int>(obj["keep_time"]);
		auto created_tp = LocalTime::parse(t);
		if (created_tp) {
			auto now = std::chrono::system_clock::now();
			auto expire_tp = *created_tp + std::chrono::seconds(keep_time_val);
			if (now > expire_tp) return false;
		}

		// 必须是管理员
		bool is_admin = false;
		if (obj.contains("is_admin")) {
			auto& v = obj["is_admin"];
			if (v.is_bool()) is_admin = v.as_bool();
		}
		if (!is_admin) {
			logs().warning("gateway", "WS proxy rejected: non-admin user",
				std::nullopt, std::string(req.target()));
			return false;
		}

		out_target = target_url;
		// 将客户端请求路径附加到目标URL后（支持子路径）
		std::string req_path = std::string(req.target());
		auto qpos = req_path.find('?');
		if (qpos != std::string::npos) req_path = req_path.substr(0, qpos);
		// 不追加路径：代理直接使用配置中指定的完整目标URL
		(void)req_path;
		return true;
	}
	catch (const std::exception& e) {
		std::cerr << "[WS proxy auth error] " << e.what() << std::endl;
		return false;
	}
}

/* 注册http路由 */
void registerRoutes(HttpServer &server) {

	server.post("/captcha", [](const auto& req, const auto& params) {
		return handleCaptcha(req, params);
	});

	/* const http::request<http::string_body>& req, const std::unordered_map<std::string, std::string>& params */
	server.post("/login", [](const auto& req, const auto& params) -> http::response<http::string_body>  {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) {return handleLogin(req, params);});
	});

	server.post("/register", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleRegister(req, params); });
	});

	/* 发送注册邮箱验证码（须在 post("*") 通配符之前注册） */
	server.post("/register/send_code", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleSendEmailCode(req, params); });
	});

	/* 发送重置密码邮箱验证码（须在 post("*") 通配符之前注册） */
	server.post("/reset/send_code", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleResetSendCode(req, params); });
	});

	/* 重置密码（须在 post("*") 通配符之前注册） */
	server.post("/reset_password", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleResetPassword(req, params); });
	});

	server.post("/logout", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleLogout(req, params); });
	});

	server.post("/refresh_token", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleRefreshToken(req, params); });
	});

	server.post("/whoami", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleWhoami(req, params); });
	});

	/* 代理 captcha 图形验证码（须在 get("*") 通配符之前注册） */
	server.get("/captcha/image", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleCaptchaImage(req, params); });
	});

	server.get("*", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleProxy(req, params, "get"); });
	});

	server.post("*", [](const auto& req, const auto& params) {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleProxy(req, params, "post"); });
	});

	/* WebSocket 反向代理（仅管理员）：
	 * ws_proxy 回调在 HTTP Upgrade 握手阶段触发，
	 * 返回非空 ws:// URL 表示鉴权通过并桥接到该后端；返回空串则拒绝连接。 */
	for (const auto& [path, url] : _WS_PROXY_ROUTES) {
		server.ws_proxy(path, [url](const http::request<http::string_body>& upgradeReq) -> std::string {
			std::string target;
			if (verifyWsAdminToken(upgradeReq, url, target)) {
				return target;
			}
			return "";
		});
		std::cout << "  WS-PROXY " << path << " -> " << url << " (admin only)" << std::endl;
	}

}

/* Http视图包装器
 *
 * 该函数收集视图函数抛出的异常 将信息发送给浏览器。这样视图函数有问题就可以直接抛出异常。 */
http::response<http::string_body> wrapperHttp(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		HttpRequestHandler _main)

try { return _main(req, params); }


/* 主动抛出的异常 */
catch (const HttpException& e) {
	http::response<http::string_body> res;
	res.version(req.version());
	// 详细错误信息（e.what() 含源码路径/行号/C++ 模板类型）暂时以注释方式保留（同 handleLogin）
	std::cerr << "[wrapperHttp HttpException] " << e.what() << std::endl;
	// 推送集中式日志：5xx 服务端错误记 ERROR，其余（400/401/403/429 等业务错误）记 WARNING
	if (e.status() >= 500)
		logs().error("gateway", e.what(), std::nullopt,
			json::serialize(json::value{{"url", e.url()}, {"status", e.status()}}));
	else
		logs().warning("gateway", e.what(), std::nullopt,
			json::serialize(json::value{{"url", e.url()}, {"status", e.status()}}));
	json::value jv_r = {
		{"success", false},
		{"message", e.what()}
		/* {"message", "Registration failed"} */
	};
	res.set(http::field::content_type, "application/json");
	res.result(e.status());
	res.body() = json::serialize(jv_r);
	res.prepare_payload();
	return res;
}

/* 预料之外的异常 */
catch (const std::exception& e) {

	http::response<http::string_body> res;
	res.version(req.version());
	const auto& ti = typeid(e);
	// 详细错误信息（ti.name() 含 mangled 类型名、e.what() 含内部细节）暂时以注释方式保留
	// 原因：此处为 wrapperHttp 外层 catch，会捕获 handler 内未处理的 std::exception
	//       透传到客户端会泄露 C++ 类型信息与内部路径（P0-3 遗漏点，已补全）
	// 排障：完整信息仍输出到服务端 stderr
	std::cerr << "[wrapperHttp std::exception] <" << ti.name() << "> " << e.what() << std::endl;
	logs().error("gateway", std::string("<") + ti.name() + "> " + e.what());
	json::value jv_r = {
		{"success", false},
		/* {"message", std::string("<") + ti.name() + "> " + e.what()}, */ // 泄露源码信息，已禁用
		{"message", "Internal Server Error"}
	};
	res.set(http::field::content_type, "application/json");
	res.result(http::status::internal_server_error);
	res.body() = json::serialize(jv_r);
	res.prepare_payload();
	return res;
}

// CAPTCHA endpoint: generates a random text, stores it, returns a simple token
// by shuiyu
http::response<http::string_body> handleCaptcha(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	std::string text = generate_captcha_text();

	// Generate a token for this captcha
	unsigned char token_bytes[16];
	RAND_bytes(token_bytes, 16);
	std::string token;
	static const char hex[] = "0123456789abcdef";
	for (int i = 0; i < 16; i++) {
		token += hex[token_bytes[i] >> 4];
		token += hex[token_bytes[i] & 0x0F];
	}

	{
		std::lock_guard<std::mutex> lock(_CAPTCHA_MTX);
		_CAPTCHA_STORE[token] = {text, std::chrono::steady_clock::now()};
	}

	json::value jv = {
		{"token", token},
		{"text", text}
	};

	res.set(http::field::content_type, "application/json");
	res.result(http::status::ok);
	res.body() = json::serialize(jv);
	res.prepare_payload();
	return res;
}

/* 处理登陆
 *
 * /login  */
http::response<http::string_body> handleLogin(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	// IP rate limit check
	std::string client_ip = "";
	auto it_host = req.base().find("X-Forwarded-For");
	if (it_host != req.base().end())
		client_ip = std::string(it_host->value());
	else
		client_ip = "direct";

	if (!_LOGIN_RATE.check_and_record(client_ip, 5, 300))
		THROW_EX_CUSTOM(HttpException, 2001, "Too many login attempts. Please try again later.", 429, "/login");

	// parse json req body
	json::value jv;
	try { jv = json::parse(req.body()); }
	catch (const boost::wrapexcept<boost::system::system_error>& e) {
		THROW_EX_CUSTOM(HttpException, 2001, "Invalid_json_Argument", 400, "/login");
	}

	if (!jv.is_object()) THROW_EX_CUSTOM(HttpException, 2002, "Invalid_json_object_Argument", 400, "/login");
	auto& obj = jv.as_object();
	auto username = std::string(obj.at("username").as_string());
	auto password = std::string(obj.at("password").as_string());

	// CAPTCHA verification 验证码（对接 captcha pymsg 服务，图形码 Token 一次性消费）
	auto captcha_it = obj.find("captcha_token");
	auto captcha_ans_it = obj.find("captcha_answer");
	if (captcha_it == obj.end() || captcha_ans_it == obj.end())
		THROW_EX_CUSTOM(HttpException, 2002, "Bad_Request", 400, "/login");
	std::string ct = std::string(captcha_it->value().as_string());
	std::string ca = std::string(captcha_ans_it->value().as_string());
	if (!verify_captcha_remote(ct, ca)) THROW_EX_CUSTOM(HttpException, 2201, "Invalid CAPTCHA", 400, "/login");

	std::string stored_hash = "";
	{
		// Fetch stored hash - support both PBKDF2 and legacy SHA256
		//std::string sql = "SELECT password_hash FROM Users WHERE email=?;";
		//auto stmt = _SQLDB->stmt(sql);
		//stmt.bind_text(1, username);
		//if (stmt.step()) {
		//	stored_hash = std::string(reinterpret_cast<const char*>(stmt.read_text(0)));
		//}

		// 新版调用db模块（通过 MsgClient pymsg TCP 协议）
		// 【保留】旧版 HTTP 调用方式：
		//   auto jv_db_r = DbC::call_http(jv_select);
		json::value jv_select = {
			{"action", "select"},
			{"table", "Users"},
			{"where", json::object{
				{"username", username},
			}}
		};
		auto jv_db_r = DbC::call_msg(jv_select);
		auto obj_r = jv_db_r.as_object();
		auto success = obj_r["success"].as_bool();
		if (!success) THROW_EX_CUSTOM(HttpException, 2102, "Login (Query Step) Failed", 500, "/login");
		auto obj_rd = obj_r.at("data").as_array();
		/* 用户不存在时 data 为空数组：直接 at(0) 会抛异常被 wrapper 兜底成 500，
		 * 此处保持 stored_hash 为空，统一走下面的 401 分支 */
		if (!obj_rd.empty()) {
			auto obj_ru = obj_rd.at(0).as_object();
			stored_hash = obj_ru.at("password_hash").as_string();
		}
	}



	bool auth_ok = false;
	if (!stored_hash.empty()) {
		if (stored_hash.substr(0, 7) == "pbkdf2:")
			// New PBKDF2 hash
			auth_ok = PBKDF2Hasher::verify_password(password, stored_hash);
		//else {
			// Legacy SHA256 hash - verify then auto-migrate
			//auto legacy_hash = SHA256::sha256(password);
			//if (legacy_hash == stored_hash) {
			//	auth_ok = true;
				// Auto-migrate to PBKDF2
			//	auto new_hash = PBKDF2Hasher::hash_password(password);
			//	std::string sql_migrate = "UPDATE Users SET password_hash=? WHERE email=?;";
			//	auto stmt_mig = _SQLDB->stmt(sql_migrate);
			//	stmt_mig.bind_text(1, new_hash);
			//	stmt_mig.bind_text(2, username);
			//	stmt_mig.step();
			//	std::cout << "Migrated password hash for " << username << " to PBKDF2" << std::endl;
		//}
	}
	else {
		/* 用户不存在：执行一次等价的 dummy PBKDF2 校验拉平响应时序，
		 * 防止通过响应时间差异枚举有效用户名（用户不存在/密码错误一律 401） */
		static const std::string dummy_hash =
			"pbkdf2:100000:00000000000000000000000000000000:"
			"0000000000000000000000000000000000000000000000000000000000000000";
		PBKDF2Hasher::verify_password(password, dummy_hash);
	}

	if (!auth_ok) THROW_EX_CUSTOM(HttpException, 2101, "Invalid Username or Password", 401, "/login");

	// Update last_login
	//std::string sql_update = "UPDATE Users SET last_login=datetime('now','localtime') WHERE email=?;";
	//auto stmt_upd = _SQLDB->stmt(sql_update);
	//stmt_upd.bind_text(1, username);
	//stmt_upd.step();
	//
	// 新版调用db模块：values 中的 "$now" 哨兵由 db 日期适配层替换为服务端当前 UTC 时间
	//（此前因 db 无法处理日期列而注释，dbv2 增加 _coerce_value 后可用）。
	json::value jv_upd = {
		{"action", "update"},
		{"table", "Users"},
		{"values", json::object{
			{"last_login", "$now"},
		}},
		{"where", json::object{
			{"username", username},
		}}
	};
	auto jv_upd_r = DbC::call_msg(jv_upd);
	if (!jv_upd_r.as_object()["success"].as_bool())
		THROW_EX_CUSTOM(HttpException, 2102, "Login (Update Step) Failed", 500, "/login");


	/* 生成令牌 */
	auto _PRIV = MyRSA::Private_Key::from_pem(_KEYGEN->get_private_key_pem());

	/* 管理员判定：账号命中 [Admin] users 白名单即在 payload 中签发 is_admin，
	 * 下游服务（dnfs 等）直接读取该字段豁免所有权校验，无需各自维护名单 */
	bool is_admin = std::find(_ADMIN_USERS.begin(), _ADMIN_USERS.end(), username) != _ADMIN_USERS.end();

	json::value jv_t = {
		{"user", username},
		{"iat", LocalTime::getCurrent()},
		{"created_at", LocalTime::getCurrent()},
		{"keep_time", _TOKEN_KEEP_TIME},
		{"is_admin", is_admin},
		{"refresh_count", 0},
		{"max_refresh", _TOKEN_MAX_REFRESH}
	};
	std::string data = json::serialize(jv_t);
	std::string auth = Base64::base64_encode(data);
	std::string sign = _PRIV.Sign(data);
	std::string token = auth + "_" + sign;


	/* 生成返回json  TODO: 专门写个类做这事 */
	json::value jv_rr = {
		{"success", true},
		{"data", json::object{{
			"token", token}}
		}
	};
	res.set("Connection", "close");

	/* 签发 HttpOnly Cookie（防 XSS 窃取 Token；JS 不再经手 Token） */
	{
		std::string ck = "q4_token=" + token +
			"; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=" +
			std::to_string(_TOKEN_KEEP_TIME);
		res.set("Set-Cookie", ck);
	}

	logs().info("gateway", "登录成功: " + username, std::nullopt,
		json::serialize(json::value{{"ip", client_ip}}));

	res.result(http::status::ok);
	res.body() = json::serialize(jv_rr);
	res.prepare_payload();
	return res;
}

/* 退出登录 /logout：清除 q4_token Cookie */
http::response<http::string_body> handleLogout(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	res.set("Set-Cookie",
		"q4_token=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0");
	res.set("Connection", "close");
	res.result(http::status::ok);
	res.body() = R"({"success": true, "message": "logged out"})";
	res.prepare_payload();
	return res;
}

/* 当前用户身份查询 /whoami
 *
 * 验签 Token 后返回 user / is_admin / Token 生命周期信息（iat / created_at / keep_time）。
 * Token 来源：Authorization: Bearer 优先，回退 Cookie q4_token。
 * 无 Token 或验签失败返回 {"login": false}，与 dnfs 旧接口契约一致。 */
http::response<http::string_body> handleWhoami(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	auto _PUBL = MyRSA::Public_Key::from_pem(_KEYGEN->get_public_key_pem());

	/* 获取 Token：Authorization 头优先，回退 Cookie */
	std::string raw;
	auto it_auth = req.base().find("Authorization");
	if (it_auth != req.base().end()) {
		raw = std::string(it_auth->value());
	} else {
		auto cit = req.base().find("Cookie");
		if (cit != req.base().end()) {
			std::string cookie_token = get_cookie_value(std::string(cit->value()), "q4_token");
			if (!cookie_token.empty())
				raw = "Bearer " + cookie_token;
		}
	}

	/* 无 Token 或格式错误：返回未登录 */
	if (raw.empty() || raw.substr(0, 7) != "Bearer ") {
		res.set(http::field::content_type, "application/json");
		res.set("Connection", "close");
		res.result(http::status::ok);
		res.body() = R"({"login": false})";
		res.prepare_payload();
		return res;
	}

	raw = raw.substr(7);
	size_t idx = raw.find("_");
	if (idx == std::string::npos) {
		res.set(http::field::content_type, "application/json");
		res.set("Connection", "close");
		res.result(http::status::ok);
		res.body() = R"({"login": false})";
		res.prepare_payload();
		return res;
	}

	std::string data = raw.substr(0, idx);
	std::string sign = raw.substr(idx + 1);
	std::string auth;
	try { auth = Base64::base64_decode_to_string(data); }
	catch (const std::exception&) {
		res.set(http::field::content_type, "application/json");
		res.set("Connection", "close");
		res.result(http::status::ok);
		res.body() = R"({"login": false})";
		res.prepare_payload();
		return res;
	}

	/* RSA 验签：失败按未登录处理 */
	if (!_PUBL.Verify(auth, sign)) {
		res.set(http::field::content_type, "application/json");
		res.set("Connection", "close");
		res.result(http::status::ok);
		res.body() = R"({"login": false})";
		res.prepare_payload();
		return res;
	}

	/* 解析 Token payload */
	json::value jv_t;
	try { jv_t = json::parse(auth); }
	catch (const std::exception&) {
		res.set(http::field::content_type, "application/json");
		res.set("Connection", "close");
		res.result(http::status::ok);
		res.body() = R"({"login": false})";
		res.prepare_payload();
		return res;
	}
	if (!jv_t.is_object()) {
		res.set(http::field::content_type, "application/json");
		res.set("Connection", "close");
		res.result(http::status::ok);
		res.body() = R"({"login": false})";
		res.prepare_payload();
		return res;
	}

	json::object& obj = jv_t.as_object();
	std::string username = json::value_to<std::string>(obj.at("user"));

	/* is_admin：旧 Token 未携带时默认为 false */
	bool is_admin = false;
	auto it_admin = obj.find("is_admin");
	if (it_admin != obj.end() && it_admin->value().is_bool())
		is_admin = it_admin->value().as_bool();

	json::value jv_r = {
		{"login", true},
		{"user", username},
		{"is_admin", is_admin}
	};

	/* 附加 Token 生命周期信息，供前端 Token 管理器估算过期时间 */
	auto it_iat = obj.find("iat");
	if (it_iat != obj.end() && it_iat->value().is_string())
		jv_r.as_object()["iat"] = it_iat->value();
	auto it_created = obj.find("created_at");
	if (it_created != obj.end() && it_created->value().is_string())
		jv_r.as_object()["created_at"] = it_created->value();
	auto it_keep = obj.find("keep_time");
	if (it_keep != obj.end() && it_keep->value().is_int64())
		jv_r.as_object()["keep_time"] = it_keep->value();

	res.set(http::field::content_type, "application/json");
	res.set("Connection", "close");
	res.result(http::status::ok);
	res.body() = json::serialize(jv_r);
	res.prepare_payload();
	return res;
}

/* 刷新 Token：POST /refresh_token
 *
 * 接受当前有效 Token（即使已过期但在宽限期内），签发新 Token。
 * 仅更新 created_at 与 refresh_count；iat 保持不变以限制整条链的总生命周期。
 *
 * 宽限期：iat + keep_time * refresh_grace_multiplier
 *   例：keep_time=3600, multiplier=2 → 首次签发后 2 小时内可刷新
 * 刷新上限：refresh_count < max_refresh
 * 限流：5 次 / 5 分钟 / IP
 * Token 来源：Authorization: Bearer 优先，回退 Cookie q4_token
 *
 * 响应：{ success, data:{ token, refresh_count, refreshes_remaining } }
 */
http::response<http::string_body> handleRefreshToken(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	/* IP 速率限制：5 次 / 300 秒 */
	std::string client_ip = "direct";
	auto it_host = req.base().find("X-Forwarded-For");
	if (it_host != req.base().end())
		client_ip = std::string(it_host->value());
	if (!_REFRESH_TOKEN_RATE.check_and_record(client_ip, 5, 300))
		THROW_EX_CUSTOM(HttpException, 4001, "Too many refresh attempts. Please try again later.",
			429, "/refresh_token");

	auto _PUBL = MyRSA::Public_Key::from_pem(_KEYGEN->get_public_key_pem());

	/* 获取 Token：Authorization 头优先，回退 Cookie */
	std::string raw;
	auto it_auth = req.base().find("Authorization");
	if (it_auth != req.base().end()) {
		raw = std::string(it_auth->value());
	} else {
		auto cit = req.base().find("Cookie");
		if (cit != req.base().end()) {
			std::string cookie_token = get_cookie_value(std::string(cit->value()), "q4_token");
			if (!cookie_token.empty())
				raw = "Bearer " + cookie_token;
		}
	}

	if (raw.empty())
		THROW_EX_CUSTOM(HttpException, 4002, "No token provided", 400, "/refresh_token");

	/* 解析 Bearer <base64>_<signature> */
	if (raw.substr(0, 7) != "Bearer ")
		THROW_EX_CUSTOM(HttpException, 4002, "Invalid token format", 400, "/refresh_token");
	raw = raw.substr(7);

	size_t idx = raw.find("_");
	if (idx == std::string::npos)
		THROW_EX_CUSTOM(HttpException, 4002, "Invalid token format", 400, "/refresh_token");

	std::string data = raw.substr(0, idx);
	std::string sign = raw.substr(idx + 1);
	std::string auth = Base64::base64_decode_to_string(data);

	/* RSA 验签 */
	if (!_PUBL.Verify(auth, sign))
		THROW_EX_CUSTOM(HttpException, 4003, "Invalid token signature", 401, "/refresh_token");

	/* 解析 Token payload */
	json::value jv_t;
	try { jv_t = json::parse(auth); }
	catch (const std::exception&) {
		THROW_EX_CUSTOM(HttpException, 4004, "Bad token payload", 400, "/refresh_token");
	}
	if (!jv_t.is_object())
		THROW_EX_CUSTOM(HttpException, 4004, "Bad token payload", 400, "/refresh_token");

	json::object& obj = jv_t.as_object();

	/* 提取关键字段 */
	std::string username  = json::value_to<std::string>(obj.at("user"));
	std::string iat_str   = json::value_to<std::string>(obj.at("iat"));
	int         keep_time = json::value_to<int>(obj.at("keep_time"));
	int         refresh_count = json::value_to<int>(obj.at("refresh_count"));
	int         max_refresh   = json::value_to<int>(obj.at("max_refresh"));

	/* 刷新次数检查 */
	if (refresh_count >= max_refresh)
		THROW_EX_CUSTOM(HttpException, 4005, "Refresh limit exceeded. Please re-login.",
			400, "/refresh_token");

	/* 宽限期检查：iat + keep_time * grace_multiplier > now */
	auto iat_tp = LocalTime::parse(iat_str);
	if (!iat_tp)
		THROW_EX_CUSTOM(HttpException, 4004, "Bad token iat", 400, "/refresh_token");

	auto now = std::chrono::system_clock::now();
	auto grace_expire = *iat_tp + std::chrono::seconds(keep_time * _TOKEN_REFRESH_GRACE_MULTIPLIER);
	if (now > grace_expire)
		THROW_EX_CUSTOM(HttpException, 4006, "Token expired beyond refresh window. Please re-login.",
			401, "/refresh_token");

	/* 保留 is_admin（旧 Token 未携带时默认为 false） */
	bool is_admin = false;
	auto it_admin = obj.find("is_admin");
	if (it_admin != obj.end() && it_admin->value().is_bool())
		is_admin = it_admin->value().as_bool();

	/* 签发新 Token：iat 不变，created_at 更新，refresh_count++ */
	auto _PRIV = MyRSA::Private_Key::from_pem(_KEYGEN->get_private_key_pem());
	json::value jv_new = {
		{"user", username},
		{"iat", iat_str},
		{"created_at", LocalTime::getCurrent()},
		{"keep_time", keep_time},
		{"is_admin", is_admin},
		{"refresh_count", refresh_count + 1},
		{"max_refresh", max_refresh}
	};
	std::string new_data = json::serialize(jv_new);
	std::string new_auth = Base64::base64_encode(new_data);
	std::string new_sign = _PRIV.Sign(new_data);
	std::string new_token = new_auth + "_" + new_sign;

	/* Set-Cookie */
	{
		std::string ck = "q4_token=" + new_token +
			"; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=" +
			std::to_string(keep_time);
		res.set("Set-Cookie", ck);
	}

	logs().info("gateway", "Token refreshed", std::nullopt,
		json::serialize(json::value{
			{"user", username},
			{"ip", client_ip},
			{"refresh_count", refresh_count + 1},
			{"refreshes_remaining", max_refresh - (refresh_count + 1)}
		}));

	json::value jv_rr = {
		{"success", true},
		{"data", json::object{
			{"token", new_token},
			{"refresh_count", refresh_count + 1},
			{"refreshes_remaining", max_refresh - (refresh_count + 1)}
		}}
	};
	res.set("Connection", "close");
	res.result(http::status::ok);
	res.set(http::field::content_type, "application/json");
	res.body() = json::serialize(jv_rr);
	res.prepare_payload();
	return res;
}

/* 代理 captcha 图形验证码：GET /captcha/image
 *
 * captcha 服务为 pymsg/TCP（:8009），浏览器无法直接访问，
 * 由本路由做 HTTP → pymsg 转换：调 image 路由拿到 base64 PNG 与 token，
 * 解码后以 image/png + X-Captcha-Token 响应头返回（与旧直出接口契约一致）。
 */
http::response<http::string_body> handleCaptchaImage(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	/* 调 captcha 服务（pymsg/TCP），连接失败/超时视为 502 */
	pmc::net::MsgClient client(_CAPTCHA_HOST, _CAPTCHA_PORT, "gateway");
	std::string resp;
	try {
		resp = client.call(R"({"_route":"image"})", _TIMEOUT_CAPTCHA);
	} catch (const std::exception& e) {
		THROW_EX_CUSTOM(HttpException, 2400, "Captcha service unavailable", 502, "/captcha/image");
	}

	/* 解析响应（格式错误同样视为 502） */
	std::string token;
	std::string png;
	try {
		auto jv = json::parse(resp);
		auto& obj = jv.as_object();
		if (!obj.at("success").as_bool())
			THROW_EX_CUSTOM(HttpException, 2401, "Captcha service error", 502, "/captcha/image");
		auto& data = obj.at("data").as_object();
		token = json::value_to<std::string>(data.at("token"));
		auto image_b64 = json::value_to<std::string>(data.at("image"));
		png = Base64::base64_decode_to_string(image_b64);
	} catch (const HttpException&) {
		throw;
	} catch (const std::exception& e) {
		THROW_EX_CUSTOM(HttpException, 2402, "Bad captcha response", 502, "/captcha/image");
	}

	res.set(http::field::content_type, "image/png");
	res.set("X-Captcha-Token", token);
	res.set("Cache-Control", "no-store");   /* 验证码图片禁止缓存 */
	res.set("Connection", "close");
	res.result(http::status::ok);
	res.body() = std::move(png);
	res.prepare_payload();
	return res;
}

/* 处理注册 /register  */
http::response<http::string_body> handleRegister(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	// IP rate limit check
	std::string client_ip = "direct";
	auto it_host = req.base().find("X-Forwarded-For");
	if (it_host != req.base().end())
		client_ip = std::string(it_host->value());
	if (!_REGISTER_RATE.check_and_record(client_ip, 3, 600))
		THROW_EX_CUSTOM(HttpException, 3001, "Too many registration attempts. Please try again later.",
			429, "/register");

	// parse the req body
	json::value jv;
        try { jv = json::parse(req.body()); }
	catch (const boost::wrapexcept<boost::system::system_error>& e) {
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/register");
	}

	if (!jv.is_object()) 
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/register");

	auto& obj = jv.as_object();
	auto username = std::string(obj.at("username").as_string());
	auto password = std::string(obj.at("password").as_string());

	// Strong password check
	auto pw_result = PasswordValidator::validate(password);
	if (!pw_result.valid)
		THROW_EX_CUSTOM(HttpException, 3003, pw_result.message, 401, "/register");

	// Email format check
	std::regex email_regex(R"(^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$)");
	if (!std::regex_match(username, email_regex))
		THROW_EX_CUSTOM(HttpException, 3004, "Invalid email format", 400, "/register");

	// CAPTCHA verification
	auto captcha_it = obj.find("captcha_token");
	auto captcha_ans_it = obj.find("captcha_answer");
	if (captcha_it != obj.end() && captcha_ans_it != obj.end()) {
		std::string ct = std::string(captcha_it->value().as_string());
		std::string ca = std::string(captcha_ans_it->value().as_string());
		if (!verify_captcha(ct, ca))
			THROW_EX_CUSTOM(HttpException, 2201, "Invalid CAPTCHA", 400, "/register");
	}

	// 邮箱验证码校验（smtp 服务，一次性消费；须放在所有本地校验通过之后、写库之前）
	auto ecode_it = obj.find("email_code");
	if (ecode_it == obj.end())
		THROW_EX_CUSTOM(HttpException, 3005, "Email verification code required", 400, "/register");
	std::string email_code = std::string(ecode_it->value().as_string());
	if (!verify_email_code_remote(username, email_code))
		THROW_EX_CUSTOM(HttpException, 3006, "Invalid or expired email verification code", 400, "/register");

	// generate the hash of password
	auto password_hash = PBKDF2Hasher::hash_password(password);

	//std::string sql = "INSERT INTO Users (email, password_hash, last_login) VALUES (?, ?, datetime('now', 'localtime'));";
	//auto stmt = _SQLDB->stmt(sql);
	//stmt.bind_text(1, username);
	//stmt.bind_text(2, password_hash);
	//stmt.step();
	//
	// 新版调用db模块（通过 MsgClient pymsg TCP 协议）
	// 【保留】旧版 HTTP 调用方式：
	//   auto jv_result = DbC::call_http(jv_insert);
	json::value jv_insert = {
		{"action", "insert"},
		{"table", "Users"},
		{"values", json::object{
			{"username", username},
			{"password_hash", password_hash}
		}}
	};
	auto jv_result = DbC::call_msg(jv_insert);
	auto obj_r = jv_result.as_object();
	auto success = obj_r["success"].as_bool();
	if (!success) THROW_EX_CUSTOM(HttpException, 2202, "Registe Failed", 400, "/register");
	auto jv_r = json::value{{"success", success}};

	logs().info("gateway", "注册成功: " + username, std::nullopt,
		json::serialize(json::value{{"ip", client_ip}}));

	res.result(http::status::ok);
	res.body() = json::serialize(jv_r);
	res.prepare_payload();
	return res;
}

/* 处理发送注册邮箱验证码 /register/send_code
 *
 * 经 pymsg 调 smtp 服务 send_email 路由，向目标邮箱发送 6 位验证码。
 * 限流 5 次/10 分钟/IP；smtp 不可用返回 502（fail-closed）。 */
http::response<http::string_body> handleSendEmailCode(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	// IP rate limit check
	std::string client_ip = "direct";
	auto it_host = req.base().find("X-Forwarded-For");
	if (it_host != req.base().end())
		client_ip = std::string(it_host->value());
	if (!_EMAIL_CODE_RATE.check_and_record(client_ip, 5, 600))
		THROW_EX_CUSTOM(HttpException, 3101, "Too many code requests. Please try again later.",
			429, "/register/send_code");

	// parse the req body
	json::value jv;
	try { jv = json::parse(req.body()); }
	catch (const std::exception&) {
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/register/send_code");
	}

	if (!jv.is_object())
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/register/send_code");

	auto& obj = jv.as_object();
	auto email_it = obj.find("email");
	if (email_it == obj.end())
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/register/send_code");
	auto email = std::string(email_it->value().as_string());

	// Email format check
	std::regex email_regex(R"(^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$)");
	if (!std::regex_match(email, email_regex))
		THROW_EX_CUSTOM(HttpException, 3004, "Invalid email format", 400, "/register/send_code");

	// CAPTCHA 强制校验（防邮件轰炸:挡住自动化脚本,见审计报告 P0-1）
	// captcha_token / captcha_answer 必填,经 captcha:8009 pymsg 校验,Token 一次性消费。
	// fail-closed:captcha 服务不可用或响应异常一律视为校验失败。
	auto captcha_token_it = obj.find("captcha_token");
	auto captcha_answer_it = obj.find("captcha_answer");
	if (captcha_token_it == obj.end() || captcha_answer_it == obj.end())
		THROW_EX_CUSTOM(HttpException, 3007, "CAPTCHA required", 400, "/register/send_code");
	if (!verify_captcha_remote(std::string(captcha_token_it->value().as_string()),
	                          std::string(captcha_answer_it->value().as_string())))
		THROW_EX_CUSTOM(HttpException, 2201, "Invalid CAPTCHA", 400, "/register/send_code");

	// 邮箱级双窗口限流（CAPTCHA 通过后才计入配额,避免无效请求消耗限流额度）
	// 3 次/小时 + 10 次/天;IP 限流可被代理池绕过,邮箱维度是更可靠的限制。
	if (!_EMAIL_ADDR_RATE_HOUR.check_and_record(email, 3, 3600))
		THROW_EX_CUSTOM(HttpException, 3104,
			"Too many code requests for this email. Please try again later.",
			429, "/register/send_code");
	if (!_EMAIL_ADDR_RATE_DAY.check_and_record(email, 10, 86400))
		THROW_EX_CUSTOM(HttpException, 3105,
			"Daily email limit exceeded. Please try again tomorrow.",
			429, "/register/send_code");

	// 调 smtp 服务（pymsg/TCP :8008）发送验证码
	bool sent = false;
	int retry_after = 0;   // smtp 同邮箱发送冷却的剩余秒数，0 表示非冷却拒绝
	std::string smtp_err;  // 冷却拒绝时 smtp 返回的提示文案（仅冷却场景透传客户端）
	try {
		pmc::net::MsgClient client(_SMTP_HOST, _SMTP_PORT, "gateway");
		json::value jv_req = {
			{"_route", "send_email"},
			{"email", email}
		};
		std::string resp = client.call(json::serialize(jv_req), _TIMEOUT_SMTP_SEND);
		auto jv_r = json::parse(resp);
		auto& obj_r = jv_r.as_object();
		if (obj_r.at("success").as_bool()) {
			auto& data = obj_r.at("data").as_object();
			sent = data.at("sent").as_bool();
			if (!sent) {
				auto it_ra = data.find("retry_after");
				if (it_ra != data.end() && it_ra->value().is_int64())
					retry_after = static_cast<int>(it_ra->value().as_int64());
				auto it_err = data.find("error");
				if (it_err != data.end() && it_err->value().is_string())
					smtp_err = std::string(it_err->value().as_string());
			}
		}
	} catch (const std::exception&) {
		THROW_EX_CUSTOM(HttpException, 3102, "Mail service unavailable", 502, "/register/send_code");
	}

	if (!sent) {
		// 同邮箱发送冷却：按限流语义返回 429 并透传 smtp 提示文案；
		// 其余发送失败（含 SMTP 内部错误细节）仍用通用文案，不向外暴露
		if (retry_after > 0)
			THROW_EX_CUSTOM(HttpException, 3103,
				smtp_err.empty() ? "Too many code requests. Please try again later." : smtp_err,
				429, "/register/send_code");
		THROW_EX_CUSTOM(HttpException, 3103, "Failed to send verification code", 500, "/register/send_code");
	}

	logs().info("gateway", "验证码邮件发送成功: " + email, std::nullopt,
		json::serialize(json::value{{"ip", client_ip}}));

	auto jv_r = json::value{{"success", true}};
	res.result(http::status::ok);
	res.set(http::field::content_type, "application/json");
	res.body() = json::serialize(jv_r);
	res.prepare_payload();
	return res;
}

/* 发送重置密码邮箱验证码 /reset/send_code
 *
 * 与注册发码（/register/send_code）结构一致，区别在定制"重置密码"主题与文案：
 * 经 pymsg 调 smtp 服务 send_email 路由（subject/content 字段覆盖默认注册文案）。
 * 限流 5 次/10 分钟/IP；CAPTCHA 强制校验；smtp 不可用返回 502（fail-closed）。 */
http::response<http::string_body> handleResetSendCode(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	// IP rate limit check
	std::string client_ip = "direct";
	auto it_host = req.base().find("X-Forwarded-For");
	if (it_host != req.base().end())
		client_ip = std::string(it_host->value());
	if (!_RESET_CODE_RATE.check_and_record(client_ip, 5, 600))
		THROW_EX_CUSTOM(HttpException, 3201, "Too many code requests. Please try again later.",
			429, "/reset/send_code");

	// parse the req body
	json::value jv;
	try { jv = json::parse(req.body()); }
	catch (const std::exception&) {
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/reset/send_code");
	}

	if (!jv.is_object())
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/reset/send_code");

	auto& obj = jv.as_object();
	auto email_it = obj.find("email");
	if (email_it == obj.end())
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/reset/send_code");
	auto email = std::string(email_it->value().as_string());

	// Email format check
	std::regex email_regex(R"(^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$)");
	if (!std::regex_match(email, email_regex))
		THROW_EX_CUSTOM(HttpException, 3004, "Invalid email format", 400, "/reset/send_code");

	// CAPTCHA 强制校验（防邮件轰炸，与 /register/send_code 一致）
	// 经 captcha:8009 pymsg 校验，Token 一次性消费；fail-closed。
	auto captcha_token_it = obj.find("captcha_token");
	auto captcha_answer_it = obj.find("captcha_answer");
	if (captcha_token_it == obj.end() || captcha_answer_it == obj.end())
		THROW_EX_CUSTOM(HttpException, 3007, "CAPTCHA required", 400, "/reset/send_code");
	if (!verify_captcha_remote(std::string(captcha_token_it->value().as_string()),
	                          std::string(captcha_answer_it->value().as_string())))
		THROW_EX_CUSTOM(HttpException, 2201, "Invalid CAPTCHA", 400, "/reset/send_code");

	// 邮箱级双窗口限流（CAPTCHA 通过后才计入配额,避免无效请求消耗限流额度）
	// 与注册发码共用同一配额，防止重置/注册双通道叠加绕过邮箱轰炸防护
	if (!_EMAIL_ADDR_RATE_HOUR.check_and_record(email, 3, 3600))
		THROW_EX_CUSTOM(HttpException, 3104,
			"Too many code requests for this email. Please try again later.",
			429, "/reset/send_code");
	if (!_EMAIL_ADDR_RATE_DAY.check_and_record(email, 10, 86400))
		THROW_EX_CUSTOM(HttpException, 3105,
			"Daily email limit exceeded. Please try again tomorrow.",
			429, "/reset/send_code");

	// 调 smtp 服务（pymsg/TCP :8008）发送重置密码验证码（定制文案）
	bool sent = false;
	int retry_after = 0;   // smtp 同邮箱发送冷却的剩余秒数，0 表示非冷却拒绝
	std::string smtp_err;  // 冷却拒绝时 smtp 返回的提示文案（仅冷却场景透传客户端）
	try {
		pmc::net::MsgClient client(_SMTP_HOST, _SMTP_PORT, "gateway");
		json::value jv_req = {
			{"_route", "send_email"},
			{"email", email},
			{"subject", "QSONT.me - Password Reset"},
			{"content", "您正在重置 QSONT.me 账号密码，验证码为 {code}，10 分钟内有效。如非本人操作，请忽略本邮件。"}
		};
		std::string resp = client.call(json::serialize(jv_req), _TIMEOUT_SMTP_SEND);
		auto jv_r = json::parse(resp);
		auto& obj_r = jv_r.as_object();
		if (obj_r.at("success").as_bool()) {
			auto& data = obj_r.at("data").as_object();
			sent = data.at("sent").as_bool();
			if (!sent) {
				auto it_ra = data.find("retry_after");
				if (it_ra != data.end() && it_ra->value().is_int64())
					retry_after = static_cast<int>(it_ra->value().as_int64());
				auto it_err = data.find("error");
				if (it_err != data.end() && it_err->value().is_string())
					smtp_err = std::string(it_err->value().as_string());
			}
		}
	} catch (const std::exception&) {
		THROW_EX_CUSTOM(HttpException, 3102, "Mail service unavailable", 502, "/reset/send_code");
	}

	if (!sent) {
		// 同邮箱发送冷却：按限流语义返回 429 并透传 smtp 提示文案；
		// 其余发送失败（含 SMTP 内部错误细节）仍用通用文案，不向外暴露
		if (retry_after > 0)
			THROW_EX_CUSTOM(HttpException, 3103,
				smtp_err.empty() ? "Too many code requests. Please try again later." : smtp_err,
				429, "/reset/send_code");
		THROW_EX_CUSTOM(HttpException, 3103, "Failed to send verification code", 500, "/reset/send_code");
	}

	logs().info("gateway", "重置密码验证码邮件发送成功: " + email, std::nullopt,
		json::serialize(json::value{{"ip", client_ip}}));

	auto jv_r = json::value{{"success", true}};
	res.result(http::status::ok);
	res.set(http::field::content_type, "application/json");
	res.body() = json::serialize(jv_r);
	res.prepare_payload();
	return res;
}

/* 处理重置密码 /reset_password
 *
 * 与注册流程一致，最后一步由"创建用户"改为"更新密码"：
 *   校验顺序：密码强度 → 邮箱格式 → CAPTCHA（可选）→ 邮箱验证码（smtp verify_email，一次性）
 *   → 生成 PBKDF2 哈希 → UPDATE Users SET password_hash WHERE username
 * 防枚举：用户不存在（update 匹配 0 行）时仍统一返回成功，不泄露邮箱是否已注册。
 * CAPTCHA 走 captcha:8009 远端校验（与前端 /captcha/image 的 token 匹配）。
 */
http::response<http::string_body> handleResetPassword(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	// IP rate limit check
	std::string client_ip = "direct";
	auto it_host = req.base().find("X-Forwarded-For");
	if (it_host != req.base().end())
		client_ip = std::string(it_host->value());
	if (!_RESET_PASSWORD_RATE.check_and_record(client_ip, 5, 600))
		THROW_EX_CUSTOM(HttpException, 3301, "Too many reset attempts. Please try again later.",
			429, "/reset_password");

	// parse the req body
	json::value jv;
	try { jv = json::parse(req.body()); }
	catch (const std::exception&) {
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/reset_password");
	}

	if (!jv.is_object())
		THROW_EX_CUSTOM(HttpException, 3002, "Invalid Argument", 401, "/reset_password");

	auto& obj = jv.as_object();
	auto username = std::string(obj.at("username").as_string());
	auto password = std::string(obj.at("password").as_string());

	// Strong password check
	auto pw_result = PasswordValidator::validate(password);
	if (!pw_result.valid)
		THROW_EX_CUSTOM(HttpException, 3003, pw_result.message, 401, "/reset_password");

	// Email format check
	std::regex email_regex(R"(^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$)");
	if (!std::regex_match(username, email_regex))
		THROW_EX_CUSTOM(HttpException, 3004, "Invalid email format", 400, "/reset_password");

	// CAPTCHA verification（可选，与注册一致；走 captcha 服务远端校验）
	auto captcha_it = obj.find("captcha_token");
	auto captcha_ans_it = obj.find("captcha_answer");
	if (captcha_it != obj.end() && captcha_ans_it != obj.end()) {
		std::string ct = std::string(captcha_it->value().as_string());
		std::string ca = std::string(captcha_ans_it->value().as_string());
		if (!verify_captcha_remote(ct, ca))
			THROW_EX_CUSTOM(HttpException, 2201, "Invalid CAPTCHA", 400, "/reset_password");
	}

	// 邮箱验证码校验（smtp 服务，一次性消费；须放在所有本地校验通过之后、写库之前）
	auto ecode_it = obj.find("email_code");
	if (ecode_it == obj.end())
		THROW_EX_CUSTOM(HttpException, 3005, "Email verification code required", 400, "/reset_password");
	std::string email_code = std::string(ecode_it->value().as_string());
	if (!verify_email_code_remote(username, email_code))
		THROW_EX_CUSTOM(HttpException, 3006, "Invalid or expired email verification code", 400, "/reset_password");

	// generate the hash of password
	auto password_hash = PBKDF2Hasher::hash_password(password);

	// 更新密码：UPDATE Users SET password_hash=? WHERE username=?（经 db 服务 pymsg execute）
	json::value jv_update = {
		{"action", "update"},
		{"table", "Users"},
		{"values", json::object{
			{"password_hash", password_hash}
		}},
		{"where", json::object{
			{"username", username}
		}}
	};
	auto jv_result = DbC::call_msg(jv_update);
	auto obj_r = jv_result.as_object();
	if (!obj_r["success"].as_bool())
		THROW_EX_CUSTOM(HttpException, 2202, "Reset Failed", 500, "/reset_password");

	// 防枚举：用户不存在（rowcount=0）时仍统一返回成功，不泄露邮箱是否已注册
	logs().info("gateway", "重置密码成功: " + username, std::nullopt,
		json::serialize(json::value{{"ip", client_ip}}));

	auto jv_r = json::value{{"success", true}};
	res.result(http::status::ok);
	res.body() = json::serialize(jv_r);
	res.prepare_payload();
	return res;
}

http::response<http::string_body> handleProxy(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params,
	const char *method)
{
	http::response<http::string_body> res;
	res.version(req.version());

	auto _PUBL = MyRSA::Public_Key::from_pem(_KEYGEN->get_public_key_pem());

	std::string t_url = "";
	std::string url = "";
	std::string auth = "";
	std::string cookie_token = "";	/* 从 Cookie 提取的 Token（用于向下游注入 Authorization） */
	json::value jv_t;
	{
		auto flag = false;



		/* 大写的Target-Url */
		auto it = req.base().find("Target-Url");
		if (it != req.base().end()) {
			t_url = req["Target-Url"];
		}

		/* 小写的Target-Url */
		it = req.base().find("target-url");
		if (it != req.base().end()) {
			t_url = req["target-url"];
		}

		/* 如果请求没设置Target-Url头，就自动设置为默认的 */
		if (t_url == "") {
			t_url =  _DEFT_URL;
			flag = true;
		}

		// 路径规范化校验（P1-1）：防止路径遍历、协议注入、空字节等
		// 校验在 url 拼接前完成，避免恶意 target 污染下游请求
		std::string target_path = std::string(req.target());
		if (!is_safe_target(target_path))
			THROW_EX_CUSTOM(HttpException, 2300, "Invalid request path", 400, "*");

		url = t_url;
		url += "/";
		url += target_path;

		std::string raw = "";
		it = req.base().find("Authorization");
		if (it != req.base().end()) {
			raw = std::string(it->value());
		} else {
			/* 回退：从 HttpOnly Cookie 读取 q4_token（浏览器同源请求自动携带） */
			auto cit = req.base().find("Cookie");
			if (cit != req.base().end()) {
				cookie_token = get_cookie_value(std::string(cit->value()), "q4_token");
				if (!cookie_token.empty())
					raw = "Bearer " + cookie_token;
			}
		}
		if (raw.empty() && !flag)
			THROW_EX_CUSTOM(HttpException, 2301, "Unauthorization", 401, "*");

		if (!flag) {
			/* 仅在需要鉴权的目标上校验凭证。
			 * 默认目标（公开静态资源）必须跳过校验：
			 * 浏览器会给每个请求自动携带 q4_token Cookie，
			 * gateway 重启（内存 RSA 密钥对轮换）或 Token 过期后，
			 * 旧 Cookie 验签失败会让公开页面也误返回 401 */
			if (raw.substr(0, 7) == "Bearer ") {
				raw = raw.substr(7, raw.size() - 7);
				size_t idx  = raw.find("_");
				if (idx == std::string::npos)
					THROW_EX_CUSTOM(HttpException, 2301, "Unauthorization", 400, "*");
				std::string data = raw.substr(0, idx);
				std::string sign = raw.substr(idx+1, raw.size()-idx-1);
				auth = Base64::base64_decode_to_string(data);
				if (!_PUBL.Verify(auth, sign))
					THROW_EX_CUSTOM(HttpException, 2301, "Unauthorization", 401, "*");
			}

			/* 是否过期 */
			try { jv_t = json::parse(auth); }
			catch (const boost::wrapexcept<boost::system::system_error>& e) {
				THROW_EX_CUSTOM(HttpException, 2302, "Bad token", 400, "*");
			}
			if (!jv_t.is_object()) THROW_EX_CUSTOM(HttpException, 2302, "Bad token", 400, "*");
			json::object& obj = jv_t.as_object();

			auto t = json::value_to<std::string>(obj["created_at"]);
			auto keep_time_val = json::value_to<int>(obj["keep_time"]);
			auto created_tp = LocalTime::parse(t);
			if (created_tp) {
				auto now = std::chrono::system_clock::now();
				auto expire_tp = *created_tp + std::chrono::seconds(keep_time_val);
				if (now > expire_tp) THROW_EX_CUSTOM(HttpException, 2000, "Authorization Expired", 401, "*");
			}
		} else {
			/* 公开目标不做凭证校验，也不向下游注入 Authorization */
			cookie_token = "";
		}
	}

	/* 扫描开放url，匹配转发地址 */
	{
		bool flag = false;
		for (auto& s: _OPEN_URL) {
			if (s == t_url) {
				flag = true;
				break;
			}
		}
		if (!flag) {
			logs().warning("gateway", "代理目标不在白名单: " + t_url);
			throw std::runtime_error("403");
		}
	}

	/*
	 * 这是用于读数据的HTTP客户端回调函数
	 *
	 * FIXME: 增加长度限制
	 * FIXME: 试试流式输出
	 * */
	auto callback = [&res](const auto& ret) {
		res.body() += ret;
	};

	/* 复制(几乎)所有的请求头 */
	auto client = HttpClient();
	for (auto const& field: req) {

		if (is_special_header(field.name_string())) continue;
		auto h = std::string();
		h += field.name_string();
		h += ": ";
		h += field.value();
		client.set_header(h);
	}

	// 主动设置可信的 X-Forwarded-For（P1-1）
	// 说明：is_special_header 已剥离客户端可能伪造的 XFF/X-Real-IP/Forwarded
	//       WAF → Gateway 为可信内网链路，收到的 XFF 即客户端真实 IP
	//       下游服务应信任此值做限流/审计，不再被客户端伪造
	auto xff_it = req.base().find("X-Forwarded-For");
	if (xff_it != req.base().end()) {
		client.set_header(std::string("X-Forwarded-For: ") + std::string(xff_it->value()));
	} else {
		// WAF 未传 XFF 时，标记为 unknown，避免下游拿到空值
		client.set_header("X-Forwarded-For: unknown");
	}

	/* Token 来自 Cookie 时，向下游补齐 Authorization 头（dnfs 等下游仍按 Bearer 解析，无需改动） */
	if (!cookie_token.empty()) {
		client.set_header("Authorization: Bearer " + cookie_token);
	}

	/* 转发请求 */
	if (method == "get")
		client.get_stream_sync(url, callback);

	else if (method == "post") {
		std::string payload = req.body();
		client.post_stream_sync(url, payload, callback);
	}

	/* 复制所有的响应头 */
	for (const auto& pair: client.get_response_headers() )
		res.set(pair.first, pair.second);


	res.set("Connection", "close");
	res.result(client.get_response_code());

	res.prepare_payload();
	return res;
}

bool is_special_header(const std::string &header) {

	// 原过滤：传输层/编码相关，由 HttpClient 自行处理
	// 新增过滤（P1-1）：客户端可伪造的 IP 相关头
	//   - X-Forwarded-For / X-Real-IP / Forwarded
	//   剥离后由 gateway 主动设置可信值（见 handleProxy 转发逻辑）
	//   防止客户端伪造 IP 绕过下游限流/审计
	return  header == "Host" || header == "connection"
		|| header == "Accept-Encoding"
		|| header == "X-Forwarded-For"
		|| header == "X-Real-IP"
		|| header == "Forwarded";
}

std::string get_cookie_value(const std::string& cookie_header, const std::string& name) {
	// Cookie 头格式："k1=v1; k2=v2; q4_token=xxx"
	size_t pos = 0;
	while (pos <= cookie_header.size()) {
		size_t end = cookie_header.find(';', pos);
		if (end == std::string::npos) end = cookie_header.size();
		std::string pair = cookie_header.substr(pos, end - pos);
		size_t eq = pair.find('=');
		if (eq != std::string::npos) {
			std::string k = pair.substr(0, eq);
			size_t b = k.find_first_not_of(" \t");
			size_t e = k.find_last_not_of(" \t");
			if (b != std::string::npos) k = k.substr(b, e - b + 1);
			if (k == name) return pair.substr(eq + 1);
		}
		if (end == cookie_header.size()) break;
		pos = end + 1;
	}
	return "";
}

bool is_safe_target(const std::string& target) {
	// 请求路径安全性校验（P1-1）
	// 防御目标：路径遍历、协议注入、空字节、非绝对路径形式

	if (target.empty()) return false;

	// 必须以 '/' 开头（拒绝 "http://evil.com/..." 等绝对 URI 形式）
	if (target[0] != '/') return false;

	// 拒绝空字节（防止截断绕过）
	if (target.find('\0') != std::string::npos) return false;

	// 拒绝路径遍历：明文 ".."
	if (target.find("..") != std::string::npos) return false;

	// 拒绝路径遍历：URL 编码形式（%2e / %2E，含 %2f / %5c 的编码变体）
	std::string lower_target;
	lower_target.reserve(target.size());
	for (char c : target) {
		lower_target.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
	}
	if (lower_target.find("%2e") != std::string::npos) return false;
	if (lower_target.find("%2f") != std::string::npos) return false;
	if (lower_target.find("%5c") != std::string::npos) return false;

	return true;
}
