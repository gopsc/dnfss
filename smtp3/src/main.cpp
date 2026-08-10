/*
 * smtp3 — C++20 邮箱验证码服务（pymsg RPC）
 *
 * 基于 libmsg RpcServer + OpenSSL SMTP_SSL。
 * 协议与 Python smtp_v2.py 完全兼容，gateway 零改动。
 *
 * 审计修复清单（一并根除 Python 版 7 项遗留）：
 *   P1-F1: ts_id 精确回滚替代浮点 now== 比较
 *   P2-F2: 日志不打印验证码明文
 *   P2-F3: std::random_device 替代 random.randint
 *   P3-F4: 常数时间验证码比较
 *   P3-F5: 严格邮箱正则（与 gateway 对齐）
 *   P3-F6: subject/content 长度上限
 *   P3-F7: 失败响应脱敏，SMTP 细节仅入日志
 * 2026-08-09 审计修复：
 *   P0-1: read_response 按完整行解析 + 64KB 上限（顺带 P2-1/P3-2）
 *   P0-2: base64 移位经 uint8_t 转无符号
 *   P1-2: 忽略 SIGPIPE，防止 SMTP 出站写断开时进程被内核终止
 *   P3-1: 日志写入前剥离控制字符（verify_email 日志注入）
 *   P3-3: DNS 解析改 AF_UNSPEC 支持 IPv6
 *   P3-4: 验证码改 uniform_int_distribution 消除模偏差
 *   P3-5: LogC 改长连接复用（见 LogC.hpp）
 */

#include <us/Msg.hpp>
#include <us/RpcServer.hpp>
#include <us/AsyncQueue.hpp>

#include <boost/json.hpp>
#include <boost/program_options.hpp>

#include <openssl/ssl.h>
#include <openssl/err.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <random>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <us/LogC.hpp>

namespace json = boost::json;
namespace po = boost::program_options;

using qing::LogC;

// ═══════════════════════════════════════════════════════════════════════════
// 辅助: 安全的 stoi
// ═══════════════════════════════════════════════════════════════════════════

static int parse_int(const std::string& key, const std::string& val) {
	try {
		return std::stoi(val);
	} catch (const std::exception& ex) {
		std::cerr << "[smtp3] 配置解析失败: " << key << " = '" << val
		          << "' (" << ex.what() << ")" << std::endl;
		std::exit(1);
	}
}

// P3-1 修复: 日志写入前剥离控制字符，防日志注入。
// verify_email 的 email 未经正则校验（send_email 路径已过严格正则，不受影响）
static std::string sanitize_for_log(const std::string& s) {
	std::string out;
	out.reserve(s.size());
	for (unsigned char c : s)
		out.push_back((c >= 0x20 && c != 0x7F) ? char(c) : '?');
	return out;
}

// ═══════════════════════════════════════════════════════════════════════════
// 辅助: Base64 编码（用于 SMTP AUTH LOGIN）
// ═══════════════════════════════════════════════════════════════════════════

static const char kBase64Chars[] =
	"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static std::string base64_encode(const std::string& data) {
	std::string out;
	out.reserve(((data.size() + 2) / 3) * 4);
	for (size_t i = 0; i < data.size(); i += 3) {
		// P0-2 修复: char 默认有符号，≥ 0x80 的字节会符号扩展污染整组，
		// 必须先经 uint8_t 转无符号再移位
		uint32_t n = (uint32_t(uint8_t(data[i])) << 16);
		if (i + 1 < data.size()) n |= (uint32_t(uint8_t(data[i + 1])) << 8);
		if (i + 2 < data.size()) n |= uint32_t(uint8_t(data[i + 2]));

		out.push_back(kBase64Chars[(n >> 18) & 0x3F]);
		out.push_back(kBase64Chars[(n >> 12) & 0x3F]);
		out.push_back((i + 1 < data.size()) ? kBase64Chars[(n >> 6) & 0x3F] : '=');
		out.push_back((i + 2 < data.size()) ? kBase64Chars[n & 0x3F] : '=');
	}
	return out;
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. 配置结构
// ═══════════════════════════════════════════════════════════════════════════

struct SmtpConfig {
	// [server]
	std::string host = "127.0.0.1";
	int port = 8008;
	int workers = 4;

	// [smtp]
	std::string smtp_server;
	int smtp_port = 465;
	std::string smtp_user;
	std::string smtp_password;
	std::string mail_from;                // 空则用 smtp_user
	int code_len = 6;
	int code_ttl = 600;
	int send_interval = 60;
	int max_verify_attempts = 5;
	int email_hourly_limit = 3;
	int email_daily_limit = 10;
	int smtp_hourly_limit = 100;
	std::string default_subject = "DNFS 邮箱验证";
	int max_subject_len = 256;
	int max_content_len = 4096;
};

// 环境变量覆盖（Docker 兼容；CLI > 环境变量 > 默认值；兼容 Python 版 __PASSWORD）
static void apply_env_overrides(SmtpConfig& cfg) {
	if (auto* v = std::getenv("LISTEN_ADDR"))      cfg.host = v;
	if (auto* v = std::getenv("LISTEN_PORT"))      cfg.port = parse_int("LISTEN_PORT", v);
	if (auto* v = std::getenv("WORKERS"))          cfg.workers = parse_int("WORKERS", v);
	if (auto* v = std::getenv("SMTP_SERVER"))      cfg.smtp_server = v;
	if (auto* v = std::getenv("SMTP_PORT"))        cfg.smtp_port = parse_int("SMTP_PORT", v);
	if (auto* v = std::getenv("SMTP_USER"))        cfg.smtp_user = v;
	if (auto* v = std::getenv("SMTP_PASSWORD"))    cfg.smtp_password = v;
	if (auto* v = std::getenv("__PASSWORD"))       cfg.smtp_password = v;  // 兼容旧名
	if (auto* v = std::getenv("MAIL_FROM"))        cfg.mail_from = v;
	if (auto* v = std::getenv("CODE_TTL"))         cfg.code_ttl = parse_int("CODE_TTL", v);
	if (auto* v = std::getenv("SEND_INTERVAL"))    cfg.send_interval = parse_int("SEND_INTERVAL", v);
	if (auto* v = std::getenv("EMAIL_HOURLY_LIMIT")) cfg.email_hourly_limit = parse_int("EMAIL_HOURLY_LIMIT", v);
	if (auto* v = std::getenv("EMAIL_DAILY_LIMIT"))  cfg.email_daily_limit = parse_int("EMAIL_DAILY_LIMIT", v);
	if (auto* v = std::getenv("SMTP_HOURLY_LIMIT"))  cfg.smtp_hourly_limit = parse_int("SMTP_HOURLY_LIMIT", v);
}

// 配置完整性校验（SMTP 凭据缺失仅警告，不阻止启动）
static void validate_config(const SmtpConfig& cfg) {
	if (cfg.host.empty()) { std::cerr << "[smtp3] 配置缺失: server.host" << std::endl; std::exit(1); }
	if (cfg.port <= 0 || cfg.port > 65535) { std::cerr << "[smtp3] 配置无效: server.port" << std::endl; std::exit(1); }
	if (cfg.smtp_port <= 0 || cfg.smtp_port > 65535) { std::cerr << "[smtp3] 配置无效: smtp.port" << std::endl; std::exit(1); }
	if (cfg.code_len < 4 || cfg.code_len > 10) { std::cerr << "[smtp3] code_len 应在 4~10 之间" << std::endl; std::exit(1); }
	if (cfg.code_ttl < 30) { std::cerr << "[smtp3] code_ttl 至少 30 秒" << std::endl; std::exit(1); }

	// SMTP 凭据缺失：警告但不退出（send_email 路由会返回错误）
	std::vector<std::string> missing;
	if (cfg.smtp_server.empty()) missing.push_back("SMTP_SERVER");
	if (cfg.smtp_user.empty()) missing.push_back("SMTP_USER");
	if (cfg.smtp_password.empty()) missing.push_back("SMTP_PASSWORD");
	if (!missing.empty()) {
		std::string list;
		for (size_t i = 0; i < missing.size(); ++i) {
			if (i > 0) list += ",";
			list += missing[i];
		}
		std::cerr << "[smtp3] 警告: SMTP 配置缺失: " << list << "，send_email 将始终失败" << std::endl;
	}
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. CodeStore — 线程安全验证码存储
// ═══════════════════════════════════════════════════════════════════════════

class CodeStore {
public:
	struct Entry {
		std::string code;
		double expires_at;    // unix timestamp
		int attempts = 0;
	};

	void put(const std::string& email, const std::string& code, double ttl_seconds) {
		std::lock_guard<std::mutex> lock(mtx_);
		expire_locked();
		store_[email] = {code, now_unixtime() + ttl_seconds, 0};
	}

	// 校验 + 一次性消费。返回 valid；invalidated 出口参数。
	bool verify(const std::string& email, const std::string& code,
	            int max_attempts, bool& invalidated) {
		std::lock_guard<std::mutex> lock(mtx_);
		invalidated = false;

		auto it = store_.find(email);
		if (it == store_.end()) return false;

		// 过期清理
		if (now_unixtime() > it->second.expires_at) {
			store_.erase(it);
			return false;
		}

		// 常数时间比较（P3-F4 修复）
		bool match = const_time_eq(it->second.code, code);

		if (match) {
			store_.erase(it);  // 一次性消费
			return true;
		}

		// 仅对未过期的记录计失败次数
		it->second.attempts++;
		if (it->second.attempts >= max_attempts) {
			store_.erase(it);
			invalidated = true;
		}
		return false;
	}

	// 发送失败回滚：删除指定邮箱的验证码
	void remove(const std::string& email) {
		std::lock_guard<std::mutex> lock(mtx_);
		store_.erase(email);
	}

private:
	static double now_unixtime() {
		auto now = std::chrono::system_clock::now();
		auto dur = now.time_since_epoch();
		return std::chrono::duration<double>(dur).count();
	}

	void expire_locked() {
		double now = now_unixtime();
		for (auto it = store_.begin(); it != store_.end(); ) {
			if (now > it->second.expires_at)
				it = store_.erase(it);
			else
				++it;
		}
	}

	// 常数时间字符串比较（抗时序侧信道）
	static bool const_time_eq(const std::string& a, const std::string& b) {
		if (a.size() != b.size()) return false;
		int diff = 0;
		for (size_t i = 0; i < a.size(); ++i)
			diff |= a[i] ^ b[i];
		return diff == 0;
	}

	std::mutex mtx_;
	std::unordered_map<std::string, Entry> store_;
};

// ═══════════════════════════════════════════════════════════════════════════
// 3. SendLimiter — 双窗口限流 + 全局熔断（ts_id 精确回滚）
// ═══════════════════════════════════════════════════════════════════════════

class SendLimiter {
public:
	// 尝试消费配额。返回 true 表示允许；失败时通过 out_error 返回原因。
	// ts_id 用于失败时精确回滚预占（P1-F1 根治）。
	bool try_consume(const std::string& email,
	                 int hourly_limit, int daily_limit,
	                 int global_hourly_limit,
	                 uint64_t& ts_id,
	                 std::string& out_error) {
		std::lock_guard<std::mutex> lock(mtx_);
		double now = now_unixtime();

		// 清理滑出窗口的时间戳
		cleanup_locked(now);

		// 邮箱级配额检查
		auto& log = send_log_[email];
		if (hourly_limit > 0 && (int)log.hour.size() >= hourly_limit) {
			out_error = "该邮箱每小时发送上限（" + std::to_string(hourly_limit) + "次）已达，请稍后重试";
			return false;
		}
		if (daily_limit > 0 && (int)log.day.size() >= daily_limit) {
			out_error = "该邮箱每日发送上限（" + std::to_string(daily_limit) + "次）已达，请明日重试";
			return false;
		}

		// 全局熔断
		if (global_hourly_limit > 0 && (int)global_log_.size() >= global_hourly_limit) {
			out_error = "邮件服务发送量已达上限，请稍后重试";
			return false;
		}

		// 预占
		ts_id = next_ts_id_++;
		log.hour.push_back({ts_id, now});
		log.day.push_back({ts_id, now});
		global_log_.push_back({ts_id, now});
		return true;
	}

	// 精确回滚：移除 ts_id 匹配的预占记录（P1-F1 修复）
	void rollback(const std::string& email, uint64_t ts_id) {
		std::lock_guard<std::mutex> lock(mtx_);

		auto it = send_log_.find(email);
		if (it != send_log_.end()) {
			auto& log = it->second;
			erase_by_tsid(log.hour, ts_id);
			erase_by_tsid(log.day, ts_id);
			// 清理空记录
			if (log.hour.empty() && log.day.empty())
				send_log_.erase(it);
		}
		erase_by_tsid(global_log_, ts_id);
	}

private:
	struct TimedEntry {
		uint64_t ts_id;
		double timestamp;
	};

	struct EmailLog {
		std::deque<TimedEntry> hour;
		std::deque<TimedEntry> day;
	};

	static double now_unixtime() {
		auto now = std::chrono::system_clock::now();
		return std::chrono::duration<double>(now.time_since_epoch()).count();
	}

	void cleanup_locked(double now) {
		// 清理各邮箱日志
		for (auto it = send_log_.begin(); it != send_log_.end(); ) {
			auto& log = it->second;
			while (!log.hour.empty() && now - log.hour.front().timestamp > 3600.0)
				log.hour.pop_front();
			while (!log.day.empty() && now - log.day.front().timestamp > 86400.0)
				log.day.pop_front();
			if (log.hour.empty() && log.day.empty())
				it = send_log_.erase(it);
			else
				++it;
		}
		// 清理全局日志
		while (!global_log_.empty() && now - global_log_.front().timestamp > 3600.0)
			global_log_.pop_front();
	}

	static void erase_by_tsid(std::deque<TimedEntry>& dq, uint64_t ts_id) {
		for (auto it = dq.begin(); it != dq.end(); ++it) {
			if (it->ts_id == ts_id) {
				dq.erase(it);
				return;  // 每个 ts_id 在每条 deque 中只出现一次
			}
		}
	}

	std::mutex mtx_;
	std::unordered_map<std::string, EmailLog> send_log_;
	std::deque<TimedEntry> global_log_;
	uint64_t next_ts_id_ = 1;
};

// ═══════════════════════════════════════════════════════════════════════════
// 4. SmtpClient — SMTP_SSL 客户端
// ═══════════════════════════════════════════════════════════════════════════

class SmtpClient {
public:
	struct Config {
		std::string server;
		int port = 465;
		std::string user;
		std::string password;
		double timeout = 20.0;
	};

	explicit SmtpClient(const Config& cfg) : cfg_(cfg) {}

	// 发送邮件。失败抛 std::runtime_error（含原始错误，由 handler 脱敏后返回）。
	void send(const std::string& from, const std::string& to,
	          const std::string& data) {
		SSL_CTX* ctx = nullptr;
		SSL* ssl = nullptr;
		int sock = -1;

		try {
			// 1. TCP 连接
			sock = connect_to_server();
			set_socket_timeout(sock, cfg_.timeout);

			// 2. TLS 握手
			ctx = SSL_CTX_new(TLS_client_method());
			if (!ctx) throw std::runtime_error("SSL_CTX_new 失败");
			ssl = SSL_new(ctx);
			if (!ssl) throw std::runtime_error("SSL_new 失败");
			SSL_set_fd(ssl, sock);
			// SNI: 某些 SMTP 服务器（如 Gmail、Outlook）要求 TLS SNI 扩展
			SSL_set_tlsext_host_name(ssl, cfg_.server.c_str());
			if (SSL_connect(ssl) != 1)
				throw std::runtime_error("TLS 握手失败: " + ssl_error());

			// 3. SMTP 协议序列
			read_response(ssl, 220);  // greeting

			send_cmd(ssl, "EHLO localhost");
			read_response(ssl, 250);

			send_cmd(ssl, "AUTH LOGIN");
			read_response(ssl, 334);  // Username:
			send_cmd(ssl, base64_encode(cfg_.user));
			read_response(ssl, 334);  // Password:
			send_cmd(ssl, base64_encode(cfg_.password));
			read_response(ssl, 235);  // Authenticated

			send_cmd(ssl, "MAIL FROM:<" + from + ">");
			read_response(ssl, 250);

			send_cmd(ssl, "RCPT TO:<" + to + ">");
			read_response(ssl, 250);

			send_cmd(ssl, "DATA");
			read_response(ssl, 354);  // Start mail input

			send_data(ssl, data + "\r\n.\r\n");
			read_response(ssl, 250);

			send_cmd(ssl, "QUIT");
			read_response(ssl, 221);

		} catch (...) {
			cleanup(ssl, ctx, sock);
			throw;
		}

		cleanup(ssl, ctx, sock);
	}

private:
	Config cfg_;

	static int connect_to_server(const std::string& server, int port) {
		struct addrinfo hints = {}, *res = nullptr;
		hints.ai_family = AF_UNSPEC;   // P3-3: 支持 IPv4/IPv6 双栈
		hints.ai_socktype = SOCK_STREAM;

		std::string port_str = std::to_string(port);
		int gai_err = getaddrinfo(server.c_str(), port_str.c_str(), &hints, &res);
		if (gai_err != 0 || !res)
			throw std::runtime_error("DNS 解析失败: " + server + " (" + gai_strerror(gai_err) + ")");

		int sock = -1;
		for (auto* rp = res; rp; rp = rp->ai_next) {
			sock = ::socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
			if (sock < 0) continue;
			if (::connect(sock, rp->ai_addr, rp->ai_addrlen) == 0) break;
			::close(sock);
			sock = -1;
		}
		freeaddrinfo(res);
		if (sock < 0)
			throw std::runtime_error("无法连接到 " + server + ":" + port_str);
		return sock;
	}

	int connect_to_server() {
		return connect_to_server(cfg_.server, cfg_.port);
	}

	static void set_socket_timeout(int sock, double seconds) {
		struct timeval tv;
		tv.tv_sec = static_cast<time_t>(seconds);
		tv.tv_usec = static_cast<suseconds_t>((seconds - tv.tv_sec) * 1e6);
		::setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
		::setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
	}

	static void send_cmd(SSL* ssl, const std::string& cmd) {
		std::string line = cmd + "\r\n";
		int total = 0;
		while (total < (int)line.size()) {
			int n = SSL_write(ssl, line.data() + total, line.size() - total);
			if (n <= 0) {
				int err = SSL_get_error(ssl, n);
				throw std::runtime_error("SSL_write 失败: " + ssl_error_str(err));
			}
			total += n;
		}
	}

	static void send_data(SSL* ssl, const std::string& data) {
		int total = 0;
		while (total < (int)data.size()) {
			int n = SSL_write(ssl, data.data() + total, data.size() - total);
			if (n <= 0) {
				int err = SSL_get_error(ssl, n);
				throw std::runtime_error("SSL_write 失败: " + ssl_error_str(err));
			}
			total += n;
		}
	}

	// 读取 SMTP 响应，校验最终响应码。
	// 处理 RFC 5321 多行响应：250-SIZE\r\n250-AUTH\r\n250 OK
	// P0-1 修复: 按"完整行"（以 CRLF 结尾）解析，最后一个完整行的第 4
	// 字符为空格即最终行（'-' 为续行）；缓冲区尾部未闭合的数据视为未读完。
	// 响应码逐字符校验数字（顺带修复 P3-2 stoi 异常），累计上限 64KB（P2-1）。
	static std::string read_response(SSL* ssl, int expected_code) {
		std::string full;
		for (;;) {
			if (full.size() > 65536)
				throw std::runtime_error("SMTP 响应超过 64KB 上限");

			char buf[4096];
			int n = SSL_read(ssl, buf, sizeof(buf) - 1);
			if (n <= 0) {
				int err = SSL_get_error(ssl, n);
				throw std::runtime_error("SSL_read 失败: " + ssl_error_str(err));
			}
			full.append(buf, n);

			// 定位最后一个完整行（以 CRLF 结尾）
			size_t pos = 0;
			size_t last_line_start = std::string::npos;
			size_t last_line_end = 0;
			for (;;) {
				auto crlf = full.find("\r\n", pos);
				if (crlf == std::string::npos) break;
				last_line_start = pos;
				last_line_end = crlf;
				pos = crlf + 2;
			}
			if (last_line_start == std::string::npos) continue;  // 尚无完整行

			// 格式: "NNN "(最终行) 或 "NNN-"(续行)
			size_t line_len = last_line_end - last_line_start;
			if (line_len >= 4 && full[last_line_start + 3] == ' ' &&
			    std::isdigit((unsigned char)full[last_line_start]) &&
			    std::isdigit((unsigned char)full[last_line_start + 1]) &&
			    std::isdigit((unsigned char)full[last_line_start + 2])) {
				int code = (full[last_line_start] - '0') * 100 +
				           (full[last_line_start + 1] - '0') * 10 +
				           (full[last_line_start + 2] - '0');
				if (code != expected_code) {
					// 去掉 \r\n 便于日志
					std::string clean = full;
					for (auto& c : clean) if (c == '\r' || c == '\n') c = ' ';
					throw std::runtime_error("SMTP 响应码 " + std::to_string(code) +
						" (预期 " + std::to_string(expected_code) + "): " + clean);
				}
				return full;
			}
			// "NNN-" 续行或畸形行 → 继续读（畸形行由 64KB 上限兜底）
		}
	}

	static std::string ssl_error() {
		char buf[256];
		ERR_error_string_n(ERR_get_error(), buf, sizeof(buf));
		return std::string(buf);
	}

	static std::string ssl_error_str(int ssl_err) {
		switch (ssl_err) {
			case SSL_ERROR_ZERO_RETURN: return "连接被对端关闭";
			case SSL_ERROR_SYSCALL:     return "syscall 错误: " + std::string(strerror(errno));
			case SSL_ERROR_SSL:         return "SSL 协议错误: " + ssl_error();
			default:                    return "SSL 错误码 " + std::to_string(ssl_err);
		}
	}

	static void cleanup(SSL* ssl, SSL_CTX* ctx, int sock) {
		if (ssl) { SSL_shutdown(ssl); SSL_free(ssl); }
		if (ctx) SSL_CTX_free(ctx);
		if (sock >= 0) ::close(sock);
	}
};

// ═══════════════════════════════════════════════════════════════════════════
// 5. generate_code() — 安全随机验证码
// ═══════════════════════════════════════════════════════════════════════════

static std::string generate_code(int len) {
	std::random_device rd;
	// P3-4: 用均匀分布消除 rd() % 10 的模偏差
	std::uniform_int_distribution<int> digit(0, 9);
	std::string code;
	code.reserve(len);
	for (int i = 0; i < len; ++i)
		code.push_back('0' + digit(rd));
	return code;
}

// ═══════════════════════════════════════════════════════════════════════════
// 6. build_mime() — MIME 邮件拼装
// ═══════════════════════════════════════════════════════════════════════════

static std::string build_mime(const std::string& from,
                               const std::string& to,
                               const std::string& subject,
                               const std::string& body_plain) {
	std::string subject_b64 = base64_encode(subject);
	std::string body_b64 = base64_encode(body_plain);

	// RFC 2045: base64 每行最多 76 字符
	std::string body_wrapped;
	for (size_t i = 0; i < body_b64.size(); i += 76) {
		if (!body_wrapped.empty()) body_wrapped += "\r\n";
		body_wrapped += body_b64.substr(i, 76);
	}

	std::ostringstream os;
	os << "From: " << from << "\r\n"
	   << "To: " << to << "\r\n"
	   << "Subject: =?UTF-8?B?" << subject_b64 << "?=\r\n"
	   << "MIME-Version: 1.0\r\n"
	   << "Content-Type: text/plain; charset=UTF-8\r\n"
	   << "Content-Transfer-Encoding: base64\r\n"
	   << "\r\n"
	   << body_wrapped;
	return os.str();
}

// ═══════════════════════════════════════════════════════════════════════════
// 7. 全局状态
// ═══════════════════════════════════════════════════════════════════════════

static SmtpConfig g_config;
static CodeStore g_code_store;
static SendLimiter g_limiter;
// 发送冷却（同邮箱 60s 间隔）: email → 上次成功发送时间戳
static std::unordered_map<std::string, double> g_cooldown;
static std::mutex g_cooldown_mtx;
static auto& g_log = LogC::instance();
static const auto _log_init = (LogC::instance().set_service_name("smtp3-log"), true);

// ═══════════════════════════════════════════════════════════════════════════
// 8. main() — RpcServer 启动
// ═══════════════════════════════════════════════════════════════════════════

int main(int argc, char* argv[]) {
	try {
		// P1-2: 忽略 SIGPIPE（与 gateway 一致）。SMTP 服务器中途断开连接时，
		// SSL_write 应返回错误走发送失败回滚路径，而不是终止整个进程
		std::signal(SIGPIPE, SIG_IGN);

		// ── 命令行参数 ──
		po::options_description desc("smtp3 — pymsg 邮箱验证码服务");
		desc.add_options()
			("help,h", "显示帮助")
			("instance", po::value<std::string>()->default_value("smtp"),
				"实例名（注册中心用）")
			("log-level", po::value<std::string>()->default_value("info"),
				"日志级别（debug|info|warn|error）")
			("addr,a", po::value<std::string>(), "监听地址")
			("port,p", po::value<int>(), "监听端口")
			("workers,w", po::value<int>(), "工作线程数")
			("smtp-port", po::value<int>(), "SMTP 服务器端口（SSL，默认 465）")
			("code-len", po::value<int>(), "验证码长度（4~10，默认 6）")
			("code-ttl", po::value<int>(), "验证码有效期秒数（默认 600）")
			("send-interval", po::value<int>(),
				"同一邮箱最小发送间隔秒数（默认 60）")
			("max-verify-attempts", po::value<int>(),
				"验证码最大校验失败次数（默认 5）")
			("email-hourly-limit", po::value<int>(),
				"单邮箱每小时发送上限（默认 3）")
			("email-daily-limit", po::value<int>(),
				"单邮箱每日发送上限（默认 10）")
			("smtp-hourly-limit", po::value<int>(),
				"全局每小时发送上限/熔断（默认 100）")
			("default-subject", po::value<std::string>(),
				"默认邮件标题")
			("max-subject-len", po::value<int>(),
				"邮件标题长度上限（默认 256）")
			("max-content-len", po::value<int>(),
				"邮件正文长度上限（默认 4096）");
		po::variables_map vm;
		po::store(po::parse_command_line(argc, argv, desc), vm);
		po::notify(vm);

		if (vm.count("help")) {
			std::cout << desc << std::endl;
			return 0;
		}

		// ── 配置加载：CLI > 环境变量 > 默认值（SMTP 账号经 .env 配置）──
		apply_env_overrides(g_config);

		if (vm.count("addr"))               g_config.host = vm["addr"].as<std::string>();
		if (vm.count("port"))               g_config.port = vm["port"].as<int>();
		if (vm.count("workers"))            g_config.workers = vm["workers"].as<int>();
		if (vm.count("smtp-port"))          g_config.smtp_port = vm["smtp-port"].as<int>();
		if (vm.count("code-len"))           g_config.code_len = vm["code-len"].as<int>();
		if (vm.count("code-ttl"))           g_config.code_ttl = vm["code-ttl"].as<int>();
		if (vm.count("send-interval"))      g_config.send_interval = vm["send-interval"].as<int>();
		if (vm.count("max-verify-attempts")) g_config.max_verify_attempts = vm["max-verify-attempts"].as<int>();
		if (vm.count("email-hourly-limit"))  g_config.email_hourly_limit = vm["email-hourly-limit"].as<int>();
		if (vm.count("email-daily-limit"))   g_config.email_daily_limit = vm["email-daily-limit"].as<int>();
		if (vm.count("smtp-hourly-limit"))   g_config.smtp_hourly_limit = vm["smtp-hourly-limit"].as<int>();
		if (vm.count("default-subject"))      g_config.default_subject = vm["default-subject"].as<std::string>();
		if (vm.count("max-subject-len"))      g_config.max_subject_len = vm["max-subject-len"].as<int>();
		if (vm.count("max-content-len"))      g_config.max_content_len = vm["max-content-len"].as<int>();

		validate_config(g_config);

		// 日志级别
		std::string log_level = vm["log-level"].as<std::string>();
		std::string instance = vm["instance"].as<std::string>();
		g_log.set_level(log_level);

		// 默认验证码有效期的文案
		std::string default_content =
			"DNFS: 您的邮箱验证码为 {code}，" +
			std::to_string(g_config.code_ttl / 60) + " 分钟内有效。";

		std::string mail_from = g_config.mail_from.empty()
			? g_config.smtp_user : g_config.mail_from;

		std::cerr << "[smtp3] instance=" << instance << " log-level=" << log_level << std::endl;
		std::cerr << "[smtp3] 配置来源: CLI > 环境变量 > 默认值（SMTP 账号经 .env 配置）" << std::endl;

		// ── 启动 RpcServer ──
		g_log.info("smtp3", "启动中... 监听 tcp://" + g_config.host + ":" + std::to_string(g_config.port));

		pmc::net::RpcServer rpc(g_config.host, g_config.port,
			pmc::net::MsgServerConfig{.workers = g_config.workers});

		// ═══════════════════════════════════════════════════════════════
		// 路由: send_email
		// ═══════════════════════════════════════════════════════════════
		rpc.add_route("send_email", [&](const json::object& req) -> json::value {
			// 取 email
			auto email_it = req.find("email");
			if (email_it == req.end() || !email_it->value().is_string()) {
				json::object data;
				data["sent"] = false;
				data["error"] = "invalid email";
				return data;
			}
			std::string email = json::value_to<std::string>(email_it->value());

			// P3-F5: 严格邮箱正则（与 gateway 对齐）
			static const std::regex email_re(R"(^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$)");
			if (!std::regex_match(email, email_re)) {
				json::object data;
				data["sent"] = false;
				data["error"] = "invalid email";
				return data;
			}

			// SMTP 配置完整性
			if (g_config.smtp_server.empty() || g_config.smtp_user.empty() || g_config.smtp_password.empty()) {
				g_log.error("smtp3", email + ": SMTP 配置缺失，无法发送");
				json::object data;
				data["sent"] = false;
				data["error"] = "SMTP 服务未配置";
				return data;
			}

			// P3-F6: subject/content 长度限制
			std::string subject = g_config.default_subject;
			std::string content = default_content;
			if (auto it = req.find("subject"); it != req.end() && it->value().is_string()) {
				subject = json::value_to<std::string>(it->value());
				if (subject.size() > (size_t)g_config.max_subject_len) {
					json::object data;
					data["sent"] = false;
					data["error"] = "subject too long";
					return data;
				}
			}
			if (auto it = req.find("content"); it != req.end() && it->value().is_string()) {
				content = json::value_to<std::string>(it->value());
				if (content.size() > (size_t)g_config.max_content_len) {
					json::object data;
					data["sent"] = false;
					data["error"] = "content too long";
					return data;
				}
			}

			// 发送冷却
			{
				std::lock_guard<std::mutex> lock(g_cooldown_mtx);
				double now = std::chrono::duration<double>(
					std::chrono::system_clock::now().time_since_epoch()).count();
				auto cd_it = g_cooldown.find(email);
				if (cd_it != g_cooldown.end()) {
					double remain = g_config.send_interval - (now - cd_it->second);
					if (remain > 0) {
						int wait = static_cast<int>(remain) + 1;
						g_log.info("smtp3", email + ": 发送冷却中，剩余 " + std::to_string(wait) + "s，已拒绝");
						json::object data;
						data["sent"] = false;
						data["retry_after"] = wait;
						data["error"] = "发送过于频繁，请 " + std::to_string(wait) + " 秒后重试";
						return data;
					}
				}
				g_cooldown[email] = now;  // 预占（失败时回滚）
			}

			// 双窗口限流 + 全局熔断预占
			uint64_t ts_id = 0;
			std::string limit_err;
			if (!g_limiter.try_consume(email,
				g_config.email_hourly_limit, g_config.email_daily_limit,
				g_config.smtp_hourly_limit, ts_id, limit_err)) {
				// 回滚冷却预占
				{
					std::lock_guard<std::mutex> lock(g_cooldown_mtx);
					g_cooldown.erase(email);
				}
				json::object data;
				data["sent"] = false;
				data["error"] = limit_err;
				return data;
			}

			// 生成验证码
			std::string code = generate_code(g_config.code_len);
			content = std::regex_replace(content, std::regex("\\{code\\}"), code);

			// 写入 CodeStore
			g_code_store.put(email, code, g_config.code_ttl);

			// SMTP 发送
			try {
				SmtpClient::Config smtp_cfg;
				smtp_cfg.server = g_config.smtp_server;
				smtp_cfg.port = g_config.smtp_port;
				smtp_cfg.user = g_config.smtp_user;
				smtp_cfg.password = g_config.smtp_password;
				smtp_cfg.timeout = 20.0;

				SmtpClient smtp(smtp_cfg);
				std::string mime = build_mime(mail_from, email, subject, content);
				smtp.send(mail_from, email, mime);

			} catch (const std::exception& err) {
				// 发送失败：回滚 CodeStore、冷却预占、限流预占
				g_code_store.remove(email);
				{
					std::lock_guard<std::mutex> lock(g_cooldown_mtx);
					g_cooldown.erase(email);
				}
				g_limiter.rollback(email, ts_id);

				std::string smtp_detail = err.what();
				g_log.error("smtp3", email + ": SMTP 发送失败(" + smtp_detail + ")");

				// P3-F7: 返回通用文案，SMTP 细节仅入日志
				json::object data;
				data["sent"] = false;
				data["error"] = "验证码发送失败，请稍后重试";
				return data;
			}

			// 发送成功：刷新冷却时间戳（保持预占即可，已在检查时占位）
			// 惰性清理过期冷却记录
			{
				std::lock_guard<std::mutex> lock(g_cooldown_mtx);
				double now = std::chrono::duration<double>(
					std::chrono::system_clock::now().time_since_epoch()).count();
				g_cooldown[email] = now;
				for (auto it = g_cooldown.begin(); it != g_cooldown.end(); ) {
					if (now - it->second > g_config.send_interval)
						it = g_cooldown.erase(it);
					else
						++it;
				}
			}

			// P2-F2: 日志不打印验证码明文
			g_log.info("smtp3", email + ": 验证码发送成功");

			json::object data;
			data["sent"] = true;
			data["ttl"] = g_config.code_ttl;
			return data;
		});

		// ═══════════════════════════════════════════════════════════════
		// 路由: verify_email
		// ═══════════════════════════════════════════════════════════════
		rpc.add_route("verify_email", [&](const json::object& req) -> json::value {
			// 取 email
			auto email_it = req.find("email");
			std::string email;
			if (email_it != req.end() && email_it->value().is_string())
				email = json::value_to<std::string>(email_it->value());

			// 取 code（兼容旧字段 input_captcha）
			std::string code;
			if (auto it = req.find("code"); it != req.end() && it->value().is_string())
				code = json::value_to<std::string>(it->value());
			else if (auto it = req.find("input_captcha"); it != req.end() && it->value().is_string())
				code = json::value_to<std::string>(it->value());

			if (email.empty() || code.empty()) {
				json::object data;
				data["valid"] = false;
				data["error"] = "email and code required";
				return data;
			}

			bool invalidated = false;
			bool valid = g_code_store.verify(email, code,
				g_config.max_verify_attempts, invalidated);

			// P3-1: email 未过正则，入日志前剥离控制字符
			std::string safe_email = sanitize_for_log(email);

			if (valid) {
				g_log.info("smtp3", safe_email + ": 验证通过");
				json::object data;
				data["valid"] = true;
				return data;
			}
			if (invalidated) {
				g_log.info("smtp3", safe_email + ": 连续校验失败 " +
					std::to_string(g_config.max_verify_attempts) + " 次，验证码已作废");
				json::object data;
				data["valid"] = false;
				data["invalidated"] = true;
				data["error"] = "验证码连续错误 " +
					std::to_string(g_config.max_verify_attempts) + " 次，已失效，请重新获取";
				return data;
			}
			g_log.info("smtp3", safe_email + ": 验证失败");
			json::object data;
			data["valid"] = false;
			data["error"] = "invalid or expired code";
			return data;
		});

		// ═══════════════════════════════════════════════════════════════
		// 路由: health
		// ═══════════════════════════════════════════════════════════════
		rpc.add_route("health", [](const json::object&) -> json::value {
			json::object data;
			data["status"] = "ok";
			data["service"] = "smtp";
			return data;
		});

		std::cerr << "[smtp3] 监听 tcp://" << g_config.host << ":" << g_config.port << std::endl;
		g_log.info("smtp3", "开始监听 tcp://" + g_config.host + ":" + std::to_string(g_config.port));

		rpc.start(); // 阻塞

	} catch (const std::exception& e) {
		std::cerr << "[smtp3] 致命错误: " << e.what() << std::endl;
		g_log.error("smtp3", std::string("致命错误: ") + e.what());
		return 1;
	}

	return 0;
}
