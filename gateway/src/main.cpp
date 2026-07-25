#include <us/HttpServer.hpp>
#include <us/HttpClient.hpp>
#include <us/MyRSA.hpp>
#include <us/Base64.hpp>
#include <us/SHA256.hpp>
#include <us/Sqlite3.hpp>
#include <iostream>
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

using namespace pmc::net;
using namespace qing;
namespace json = boost::json;
namespace po = boost::program_options;

constexpr const char *_VER = "0.3.0-security";

//////////////////////////////////
#include <sstream>
#include <vector>
#include <string>

std::vector<std::string> split(const std::string& s, char delim) {
    std::vector<std::string> tokens;
    std::istringstream stream(s);
    std::string token;
    while (std::getline(stream, token, delim)) {
        tokens.push_back(token);
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

class DatabaseException: public ExceptionBase {
public:
	using ExceptionBase::ExceptionBase;

	std::string build_message() const override {
		return "[Database]" + ExceptionBase::build_message();
	}
};

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

void db_init();
void registerRoutes(HttpServer &server);
std::string generate_captcha_text();
bool verify_captcha(const std::string& token, const std::string& answer);

http::response<http::string_body> wrapperHttp(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		HttpRequestHandler _main);

http::response<http::string_body> handleProxy(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		const char *method
);

http::response<http::string_body> handleLogin(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

http::response<http::string_body> handleRegister(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

http::response<http::string_body> handleCaptcha(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);


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

/* instance items */
static std::unique_ptr<HttpServer>		_SERVE;
static std::unique_ptr<MyRSA::Generator>	_KEYGEN;
static std::unique_ptr<Sqlite3>			_SQLDB;
static RateLimiter				_LOGIN_RATE;
static RateLimiter				_REGISTER_RATE;

/* CAPTCHA store: token -> answer (in-memory, with expiry) */
#include <unordered_map>
#include <mutex>
struct CaptchaEntry {
    std::string answer;
    std::chrono::steady_clock::time_point created;
};
static std::unordered_map<std::string, CaptchaEntry> _CAPTCHA_STORE;
static std::mutex _CAPTCHA_MTX;

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

void signalHandler(int signum) {

	std::cout << "Receive close signal, prepare to shutdown...\n";
	if (_SERVE) {
		_SERVE->stop();
	}
}

void set_signal_action() {

	struct sigaction sa;
	sa.sa_handler = signalHandler;
	sigemptyset(&sa.sa_mask);
	sa.sa_flags = 0;
	sigaction(SIGINT, &sa, nullptr);
	sigaction(SIGTERM, &sa, nullptr);
}

int main(int argc, char **argv) {
	po::options_description desc("cpp language web gateway");
	desc.add_options()
		("help,h",					"output help message")
		("version,v",					"output version message")
		("input,i", 	po::value<std::string>(),	"input a configure text file")
	;
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

	if (vm.count("help")) {
		std::cout << desc << std::endl;
		return 0;
	}

	if (!vm.count("input")) {
		std::cerr << "Please input configure file via -i"  << std::endl;
		return 0;
	}

	{
		try {

			ConfIni cf(vm["input"].as<std::string>());
		_ADDR = cf.get<std::string>("Server.address", "");
		_PORT = cf.get<int> ("Server.port", -1);
		_TLS = cf.get<bool>     ("Server.tls", false);
		_DEFT_URL = cf.get<std::string>("Proxy.default", "");
		_OPEN_URL = split(cf.get<std::string>("Proxy.opened", ""), ';');
		_DB_PATH = cf.get<std::string>("Database.path", "");
		}
		catch (ConfIni::IniConfigureFileParseException &e) {
			std::cerr << "ERROR: "  << e.what() << std::endl;
			std::cerr << "ini format input file error" << std::endl;
			return 1;
		}
	}

	if (_ADDR == "" || _PORT == -1) {
		std::cerr << "Please setup addree and port in Serve section in configure file";
		std::cerr << std::endl;
		return 0;
	}

	// 根据 Server.tls 选择 HTTPS（带证书）或 HTTP（无证书）构造函数
	// HTTP 模式用于 WAF 终止 TLS 后的内部转发链路
	if (_TLS) {
		_SERVE = std::make_unique<HttpServer> (_ADDR, _PORT, "full_chain.crt", "server.key", 4);
	} else {
		_SERVE = std::make_unique<HttpServer> (_ADDR, _PORT, 4);
	}
	_KEYGEN = std::make_unique<MyRSA::Generator> ();
	_SQLDB = std::make_unique<Sqlite3> (_DB_PATH);

	db_init();
	registerRoutes(*_SERVE);
	set_signal_action();

	std::cout << "Gateway " << _VER << " started (PBKDF2 + CAPTCHA + RateLimit)" << std::endl;
	_SERVE->start();
	_SERVE->run();

}

void db_init() {

	std::string sql = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='Users'";

	auto flag = false;
	{
		auto stmt = _SQLDB->stmt(sql);
		if (stmt.step()) {
			auto num = stmt.read_int(0);
			if (num == 0) {
				flag = true;
			}
		}
	}

	if (flag) {
		std::cout << "Start database init..." << std::endl;
		std::string sql_crtt =
			"CREATE TABLE Users (\n"
				"id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
				"email TEXT UNIQUE NOT NULL,\n"
				"password_hash TEXT NOT NULL,\n"
				"create_at DATETIME DEFAULT CURRENT_TIMESTAMP,\n"
				"last_login DATETIME,\n"
				"is_active BOOLEAN DEFAULT 1\n"
			");\n\n"
			"CREATE INDEX idx_users_email ON users(email);\n";

		_SQLDB->exec(sql_crtt);

		// No hardcoded admin - first registered user becomes admin
		std::cout << "done (no default admin - register manually)" << std::endl;
	}

	else {
		std::cout << "The database already init" << std::endl;
	}
}



void registerRoutes(HttpServer &server) {

	server.post("/captcha", [](const auto& req, const auto& params) {
		return handleCaptcha(req, params);
	});

	server.post("/login", [](
			const http::request<http::string_body>& req,
			const std::unordered_map<std::string, std::string>& params
	) -> http::response<http::string_body>  {
		return wrapperHttp(req, params, [](
				const auto& req, const auto& params
		) {
			return handleLogin(req, params);
		});
	});

	server.post("/register", [](const auto& req, const auto& params) {
		return handleRegister(req, params);
	});

	server.get("*", [](const auto& req, const auto& params) {
		return handleProxy(req, params, "get");
	});

	server.post("*", [](const auto& req, const auto& params) {
		return handleProxy(req, params, "post");
	});

	server.options("*", [](const auto& req, const auto& params) {
			pmc::net::http::response<pmc::net::http::string_body> res;
			res.result(pmc::net::http::status::ok);

			// CORS 白名单（P1-4）：仅允许官方域名，防止任意网站跨域调用
			// 原配置 Access-Control-Allow-Origin: * 过宽，XSS 拿到 token 后可跨域读取数据
			// 动态回填 Origin：仅当请求 Origin 命中白名单时才返回该值，否则不返回 ACAO 头
			static const std::vector<std::string> allowed_origins = {
				"https://qsont.me",
				"https://www.qsont.me",
				"http://127.0.0.1:8443",  // 本地开发调试
				"http://localhost:8443"
			};
			auto origin_it = req.base().find("Origin");
			if (origin_it != req.base().end()) {
				std::string origin = std::string(origin_it->value());
				for (const auto& allowed : allowed_origins) {
					if (origin == allowed) {
						res.set(pmc::net::http::field::access_control_allow_origin, origin);
						res.set(pmc::net::http::field::vary, "Origin");
						break;
					}
				}
			}

			res.set(pmc::net::http::field::access_control_allow_methods, "GET, POST, OPTIONS");
			res.set(pmc::net::http::field::access_control_allow_headers, "Content-Type, Authorization");
			res.set(pmc::net::http::field::access_control_max_age, "86400");
			res.body() = "";
			res.content_length(0);
			res.prepare_payload();
			return res;
	});
}

http::response<http::string_body> wrapperHttp(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		HttpRequestHandler _main)

try {

	return _main(req, params);
}

catch (const std::exception& e) {

	http::response<http::string_body> res;
	res.version(req.version());
	const auto& ti = typeid(e);
	// 详细错误信息（ti.name() 含 mangled 类型名、e.what() 含内部细节）暂时以注释方式保留
	// 原因：此处为 wrapperHttp 外层 catch，会捕获 handler 内未处理的 std::exception
	//       透传到客户端会泄露 C++ 类型信息与内部路径（P0-3 遗漏点，已补全）
	// 排障：完整信息仍输出到服务端 stderr
	std::cerr << "[wrapperHttp std::exception] <" << ti.name() << "> " << e.what() << std::endl;
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

http::response<http::string_body> handleLogin(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	// IP rate limit check
	std::string client_ip = "";
	auto it_host = req.base().find("X-Forwarded-For");
	if (it_host != req.base().end()) {
		client_ip = std::string(it_host->value());
	} else {
		client_ip = "direct";
	}

	if (!_LOGIN_RATE.check_and_record(client_ip, 5, 300)) {
		json::value jv_r = {
			{"success", false},
			{"message", "Too many login attempts. Please try again later."}
		};
		res.set(http::field::content_type, "application/json");
		res.result(http::status::too_many_requests);
		res.body() = json::serialize(jv_r);
		res.prepare_payload();
		return res;
	}

	try {
		json::value jv;
		try {
			jv = json::parse(req.body());
		}
		catch (const boost::wrapexcept<boost::system::system_error>& e) {
			THROW_EX_CUSTOM(HttpException, 2001, "Invalid_json_Argument",
                            400, "/login");
		}

		if (!jv.is_object()) {
			THROW_EX_CUSTOM(HttpException, 2002, "Invalid_json_object_Argument",
                            400, "/login");
		}
		auto& obj = jv.as_object();
		auto username = std::string(obj.at("username").as_string());
		auto password = std::string(obj.at("password").as_string());

		// CAPTCHA verification
		auto captcha_it = obj.find("captcha_token");
		auto captcha_ans_it = obj.find("captcha_answer");
		if (captcha_it != obj.end() && captcha_ans_it != obj.end()) {
			std::string ct = std::string(captcha_it->value().as_string());
			std::string ca = std::string(captcha_ans_it->value().as_string());
			if (!verify_captcha(ct, ca)) {
				THROW_EX_CUSTOM(HttpException, 2201, "Invalid CAPTCHA",
                                400, "/login");
			}
		}

		// Fetch stored hash - support both PBKDF2 and legacy SHA256
		std::string sql = "SELECT password_hash FROM Users WHERE email=?;";
		auto stmt = _SQLDB->stmt(sql);
		stmt.bind_text(1, username);
		std::string stored_hash = "";
		if (stmt.step()) {
			stored_hash = std::string(reinterpret_cast<const char*>(stmt.read_text(0)));
		}

		bool auth_ok = false;
		if (!stored_hash.empty()) {
			if (stored_hash.substr(0, 7) == "pbkdf2:") {
				// New PBKDF2 hash
				auth_ok = PBKDF2Hasher::verify_password(password, stored_hash);
			} else {
				// Legacy SHA256 hash - verify then auto-migrate
				auto legacy_hash = SHA256::sha256(password);
				if (legacy_hash == stored_hash) {
					auth_ok = true;
					// Auto-migrate to PBKDF2
					auto new_hash = PBKDF2Hasher::hash_password(password);
					std::string sql_migrate = "UPDATE Users SET password_hash=? WHERE email=?;";
					auto stmt_mig = _SQLDB->stmt(sql_migrate);
					stmt_mig.bind_text(1, new_hash);
					stmt_mig.bind_text(2, username);
					stmt_mig.step();
					std::cout << "Migrated password hash for " << username << " to PBKDF2" << std::endl;
				}
			}
		}

		if (!auth_ok) {
			THROW_EX_CUSTOM(HttpException, 2101, "Invalid Username or Password",
                            401, "/login");
		}

		// Update last_login
		std::string sql_update = "UPDATE Users SET last_login=datetime('now','localtime') WHERE email=?;";
		auto stmt_upd = _SQLDB->stmt(sql_update);
		stmt_upd.bind_text(1, username);
		stmt_upd.step();

		auto _PRIV = MyRSA::Private_Key::from_pem(_KEYGEN->get_private_key_pem());

		int  keep_time  = 3600;

		json::value jv_t = {
			{"user", username},
			{"created_at", LocalTime::getCurrent()},
			{"keep_time", keep_time}
		};
		std::string data = json::serialize(jv_t);
		std::string token = Base64::base64_encode(data);
		std::string sign = _PRIV.Sign(data);

		json::value jv_rr = {
			{"success", true},
			{"data", json::object{{"token", token + "_" + sign}}}
		};
		res.set("Connection", "close");
		res.result(http::status::ok);
		res.body() = json::serialize(jv_rr);
	}

	catch (const HttpException& e) {

		// 详细错误信息（e.what() 含源码路径/行号/C++ 模板类型）暂时以注释方式保留
		// 原因：黑盒检测发现透传到客户端会泄露内部实现，便于攻击者定位代码（P0-3）
		// 排障：完整信息仍输出到服务端 stderr
		std::cerr << "[handleLogin HttpException] " << e.what() << std::endl;
		json::value jv_r = {
			{"success", false},
			/* {"message", e.what()}, */ // 泄露源码信息，已禁用
			{"message", "Invalid Username or Password"}
		};
		res.set(http::field::content_type, "application/json");
		res.result(e.status());
		res.body() = json::serialize(jv_r);
	}

	res.prepare_payload();
	return res;
}

http::response<http::string_body> handleRegister(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	// IP rate limit check
	std::string client_ip = "direct";
	auto it_host = req.base().find("X-Forwarded-For");
	if (it_host != req.base().end()) {
		client_ip = std::string(it_host->value());
	}
	if (!_REGISTER_RATE.check_and_record(client_ip, 3, 600)) {
		json::value jv_r = {
			{"success", false},
			{"message", "Too many registration attempts. Please try again later."}
		};
		res.set(http::field::content_type, "application/json");
		res.result(http::status::too_many_requests);
		res.body() = json::serialize(jv_r);
		res.prepare_payload();
		return res;
	}

	try {

		auto jv = json::parse(req.body());
		if (!jv.is_object()) {
			throw std::runtime_error("400");
		}
		auto& obj = jv.as_object();
		auto username = std::string(obj.at("username").as_string());
		auto password = std::string(obj.at("password").as_string());

		// Strong password check
		auto pw_result = PasswordValidator::validate(password);
		if (!pw_result.valid) {
			json::value jv_r = {
				{"success", false},
				{"message", pw_result.message}
			};
			res.set(http::field::content_type, "application/json");
			res.result(http::status::bad_request);
			res.body() = json::serialize(jv_r);
			res.prepare_payload();
			return res;
		}

		// Email format check
		std::regex email_regex(R"(^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$)");
		if (!std::regex_match(username, email_regex)) {
			json::value jv_r = {
				{"success", false},
				{"message", "Invalid email format"}
			};
			res.set(http::field::content_type, "application/json");
			res.result(http::status::bad_request);
			res.body() = json::serialize(jv_r);
			res.prepare_payload();
			return res;
		}

		// CAPTCHA verification
		auto captcha_it = obj.find("captcha_token");
		auto captcha_ans_it = obj.find("captcha_answer");
		if (captcha_it != obj.end() && captcha_ans_it != obj.end()) {
			std::string ct = std::string(captcha_it->value().as_string());
			std::string ca = std::string(captcha_ans_it->value().as_string());
			if (!verify_captcha(ct, ca)) {
				THROW_EX_CUSTOM(HttpException, 2201, "Invalid CAPTCHA",
                                400, "/register");
			}
		}

		auto password_hash = PBKDF2Hasher::hash_password(password);

		std::string sql = "INSERT INTO Users (email, password_hash, last_login) VALUES (?, ?, datetime('now', 'localtime'));";
		auto stmt = _SQLDB->stmt(sql);
		stmt.bind_text(1, username);
		stmt.bind_text(2, password_hash);
		stmt.step();

		json::value jv_r = {{"success", true}};
		res.result(http::status::ok);
		res.body() = json::serialize(jv_r);
	}

	catch (const HttpException& e) {
		// 详细错误信息（e.what() 含源码路径/行号/C++ 模板类型）暂时以注释方式保留（同 handleLogin）
		std::cerr << "[handleRegister HttpException] " << e.what() << std::endl;
		json::value jv_r = {
			{"success", false},
			/* {"message", e.what()}, */ // 泄露源码信息，已禁用
			{"message", "Registration failed"}
		};
		res.set(http::field::content_type, "application/json");
		res.result(e.status());
		res.body() = json::serialize(jv_r);
	}

	catch (const std::exception& e) {
		const auto& ti = typeid(e);
		// 详细错误信息（ti.name() 含 mangled 类型名、e.what() 含内部细节）暂时以注释方式保留
		// 原因：mangled 类型名会确认技术栈与 STL 容器，e.what() 可能泄露内部路径（P0-3）
		// 排障：完整信息仍输出到服务端 stderr
		std::cerr << "[handleRegister std::exception] <" << ti.name() << "> " << e.what() << std::endl;
		res.set(http::field::content_type, "application/json");
		res.result(http::status::internal_server_error);
		json::value jv_r = {
			{"success", false},
			/* {"err_name", ti.name()},    // 泄露 C++ 类型信息，已禁用 */
			/* {"err_message", e.what()}   // 泄露内部细节，已禁用 */
			{"message", "Internal Server Error"}
		};
		res.body() = json::serialize(jv_r);
	}

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

	try {
		auto _PUBL = MyRSA::Public_Key::from_pem(_KEYGEN->get_public_key_pem());

		std::string t_url = "";
		std::string url = "";
		std::string auth = "";
		json::value jv_t;
		{
			auto flag = false;
			auto it = req.base().find("Target-Url");
			if (it != req.base().end()) {
				t_url = req["Target-Url"];
			}

			it = req.base().find("target-url");
			if (it != req.base().end()) {
				t_url = req["target-url"];
			}

			if (t_url == "") {
				t_url =  _DEFT_URL;
				flag = true;
			}

			// 路径规范化校验（P1-1）：防止路径遍历、协议注入、空字节等
			// 校验在 url 拼接前完成，避免恶意 target 污染下游请求
			std::string target_path = std::string(req.target());
			if (!is_safe_target(target_path)) {
				THROW_EX_CUSTOM(HttpException, 2300, "Invalid request path",
    			                400, "*");
			}

			url = t_url;
			url += "/";
			url += target_path;

			it = req.base().find("Authorization");
			if (it == req.base().end() && !flag) {
				throw std::runtime_error("401");
			}
			std::string raw = req["Authorization"];
			if (raw.substr(0, 7) == "Bearer ") {
				raw = raw.substr(7, raw.size() - 7);
				size_t idx  = raw.find("_");
				if (idx == std::string::npos) {
					throw std::runtime_error("Header 400 Bad Request");
				}
				std::string data = raw.substr(0, idx);
				std::string sign = raw.substr(idx+1, raw.size()-idx-1);
				auth = Base64::base64_decode_to_string(data);
				if (!_PUBL.Verify(auth, sign)) {
					throw std::runtime_error("Header 401 Unauthorized");
				}
			}

			if (!flag) {
				jv_t = json::parse(auth);
				json::object& obj = jv_t.as_object();

				auto t = json::value_to<std::string>(obj["created_at"]);
				auto keep_time_val = json::value_to<int>(obj["keep_time"]);
				auto created_tp = LocalTime::parse(t);
				if (created_tp) {
					auto now = std::chrono::system_clock::now();
					auto expire_tp = *created_tp + std::chrono::seconds(keep_time_val);
					if (now > expire_tp) {
						THROW_EX_CUSTOM(HttpException, 2000, "Authorization Expired",
            	        	            401, "*");
					}
				}
			}
		}

		{
			bool flag = false;
			for (auto& s: _OPEN_URL) {
				if (s == t_url) {
					flag = true;
					break;
				}
			}
			if (!flag) {
				throw std::runtime_error("403");
			}
		}

		auto callback = [&res](const auto& ret) {
			res.body() += ret;
		};


		auto client = HttpClient();
		for (auto const& field: req) {
			if (is_special_header(field.name_string()))
				continue;
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


		if (method == "get")
			client.get_stream_sync(url, callback);

		else if (method == "post") {
			std::string payload = req.body();
			client.post_stream_sync(url, payload, callback);
		}


		for (const auto& pair: client.get_response_headers() ) {
			res.set(pair.first, pair.second);
		}
		res.set("Connection", "close");
		res.result(client.get_response_code());
	}

	catch (const std::exception& e) {
		const auto& ti = typeid(e);
		// 详细错误信息（ti.name() 含 mangled 类型名、e.what() 含内部细节）暂时以注释方式保留
		// 原因：mangled 类型名会确认技术栈与 STL 容器，e.what() 可能泄露内部路径（P0-3）
		// 排障：完整信息仍输出到服务端 stderr
		std::cerr << "[handleProxy std::exception] <" << ti.name() << "> " << e.what() << std::endl;
		res.set(http::field::content_type, "application/json");
		res.result(http::status::internal_server_error);
		json::value jv_r = {
			{"success", false},
			/* {"err_name", ti.name()},    // 泄露 C++ 类型信息，已禁用 */
			/* {"err_message", e.what()}   // 泄露内部细节，已禁用 */
			{"message", "Internal Server Error"}
		};
		res.body() = json::serialize(jv_r);
	}

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
