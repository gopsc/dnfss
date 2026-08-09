#include <us/HttpServer.hpp>
#include <us/HttpClient.hpp>
#include <iostream>
#include <algorithm>
#include <cctype>
#include <typeinfo>
#include <memory>
#include <csignal>
#include <thread>
#include <future>
#include <chrono>
#include <unistd.h>
#include <boost/json.hpp>
#include <boost/program_options.hpp>
#include "ExpBase.hpp"
#include "Conf.hpp"
#include "LogC.hpp"

using namespace pmc::net;
using namespace qing;
namespace json = boost::json;
namespace po = boost::program_options;

/* v0.9.0：认证独立化瘦身（waf → ca → gateway 前置拓扑）
 * - 认证资产（Token 签发/验签、登录/注册/重置、WS 代理鉴权）已全部迁至 ca 模块
 * - gateway 退化为纯转发服务：仅 loopback 监听（默认 8445），不验签
 * - 身份契约：显式 Target-Url 请求必须携带 ca 注入的 X-Auth-User 头（存在性不变量）
 * - 安全审计结论见 docs/security/gateway_SECURITY_AUDIT.md（合并文档：开放项/历史追溯/上线清单）；
 *   已修复项以“审计 A*”/“P1-1”等标记内联于下方对应代码 */
constexpr const char *_VER = "0.9.0";

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

void registerRoutes(HttpServer &server);

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

/* 特殊请求头过滤（转发时剥离，见 handleProxy） */
bool is_special_header(const std::string &header);

/* 请求路径安全性校验（防路径遍历/协议注入/空字节） */
bool is_safe_target(const std::string& target);

/* configure items */
static std::string		_ADDR;
static int			_PORT;
static bool			_TLS = false;
static std::string		_DEFT_URL;
static std::vector<std::string>	_OPEN_URL;
/* 下游 pymsg 服务地址（日志推送端点，从 confs [Downstream] 段加载，可被环境变量覆盖） */
static std::string		_DB_HOST = "127.0.0.1";
static int			_DB_PORT = 8005;
/* confs 引导配置（不依赖 confs 本身，环境变量 CONFS_HOST/CONFS_PORT 覆盖，默认 127.0.0.1:8010） */
static std::string		_CONFS_HOST = "127.0.0.1";
static int			_CONFS_PORT = 8010;

/* instance items */
static std::unique_ptr<HttpServer>		_SERVE;

/* 集中式日志客户端：推送 gateway 运行日志到 db 模块 Logs 表（异步批量，fail-safe） */
static inline LogC& logs() { return LogC::instance(); }

/* -------- 信号处理 --------
 *
 * 安全设计原则：
 * 1. 信号处理函数中只做 async-signal-safe 操作：设置 volatile sig_atomic_t 标志
 * 2. 绝不调用 std::cout、malloc、锁、ioc_->stop() 等非安全函数（可能导致死锁）
 * 3. 主线程在安全点检测标志后再执行完整的关闭流程
 * 4. 忽略 SIGPIPE（写已关闭 socket 时内核默认发送该信号终止进程）
 */
static volatile std::sig_atomic_t _stop_requested = 0;

void signalHandler(int signum) {
	// 仅设置标志，不做其他操作（write 是 async-signal-safe 的，用于输出提示）
	const char msg[] = "\n[gateway] 收到关闭信号，准备退出...\n";
	::write(STDERR_FILENO, msg, sizeof(msg) - 1);
	_stop_requested = 1;
}

void sigpipeHandler(int signum) {
	// 忽略 SIGPIPE，write 调用会返回 EPIPE 错误而非终止进程
}

/* -------- 注册信号处理钩子 -------- */
void set_signal_action() {
	struct sigaction sa;

	// SIGINT / SIGTERM：安全设置关闭标志
	sa.sa_handler = signalHandler;
	sigemptyset(&sa.sa_mask);
	sa.sa_flags = 0;
	sigaction(SIGINT, &sa, nullptr);
	sigaction(SIGTERM, &sa, nullptr);

	// SIGPIPE：忽略，防止写关闭的 socket 时进程意外终止
	struct sigaction sa_pipe;
	sa_pipe.sa_handler = sigpipeHandler;
	sigemptyset(&sa_pipe.sa_mask);
	sa_pipe.sa_flags = 0;
	sigaction(SIGPIPE, &sa_pipe, nullptr);
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

		/* 下游服务地址（日志推送端点） */
		_DB_HOST = cf.get<std::string>("Downstream.db_host", "127.0.0.1");
		_DB_PORT = cf.get<int>("Downstream.db_port", 8005);

		/* 3. 环境变量覆盖（优先级最高，Docker 环境使用服务名而非 127.0.0.1） */
		std::string env_listen_addr = ConfIni::env("LISTEN_ADDR");
		if (!env_listen_addr.empty()) _ADDR = env_listen_addr;
		int env_listen_port = ConfIni::env_int("LISTEN_PORT", -1);
		if (env_listen_port > 0) _PORT = env_listen_port;

		std::string env_db_host = ConfIni::env("DB_HOST");
		if (!env_db_host.empty()) _DB_HOST = env_db_host;
		int env_db_port = ConfIni::env_int("DB_PORT", -1);
		if (env_db_port > 0) _DB_PORT = env_db_port;

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

		/* 5. loopback 强制自检（v0.9.0 前置拓扑）：
		 * gateway 不再验签，身份完全信任 ca 注入的 X-Auth-User 头，
		 * 因此必须只监听 loopback；非 loopback 地址意味着外部可直连伪造身份，拒绝启动 */
		if (_ADDR != "127.0.0.1" && _ADDR != "::1" && _ADDR != "localhost") {
			std::cerr << "[gateway] 致命错误: 前置拓扑（waf → ca → gateway）下 gateway 仅允许 loopback 监听，"
			          << "当前 address=" << _ADDR << "，请改为 127.0.0.1" << std::endl;
			return 1;
		}
	}

	/* 初始化日志推送端点 */
	logs().set_endpoint(_DB_HOST, _DB_PORT);

	std::cout << "[gateway] 监听: " << _ADDR << ":" << _PORT
	          << " (TLS: " << (_TLS ? "on" : "off") << ")" << std::endl;

	// 根据 Server.tls 选择 HTTPS（带证书）或 HTTP（无证书）构造函数
	// HTTP 模式用于 ca 前置后的内部转发链路
	if (_TLS) {
		_SERVE = std::make_unique<HttpServer> (_ADDR, _PORT, "full_chain.crt", "server.key", 4);
	} else {
		_SERVE = std::make_unique<HttpServer> (_ADDR, _PORT, 4);
	}

	/* */
	registerRoutes(*_SERVE);

	/* */
	set_signal_action();

	std::cout << "Gateway " << _VER << " starting..." << std::endl;

	/* 在独立线程中运行服务器（start + run）
	 * 原因：
	 * 1. 信号处理函数只能安全设置 atomic 标志，不能直接调用 stop()
	 * 2. 主线程轮询检测关闭信号后，在安全上下文调用 stop()
	 * 3. 避免在信号处理函数中操作非 async-signal-safe 的 io_context、锁、iostream
	 */
	std::atomic<bool> server_exited{false};
	std::thread server_thread([&]() {
		try {
			_SERVE->run();  // run() 内部会调用 start() 启动 listener 和 worker 线程
		} catch (const std::exception& e) {
			std::cerr << "[gateway] Server thread exception: " << e.what() << std::endl;
		}
		server_exited = true;
	});

	// 等待服务器启动完成（简单延迟，确保started日志打印后再输出提示）
	std::this_thread::sleep_for(std::chrono::milliseconds(200));
	std::cout << "Gateway " << _VER << " started" << std::endl;
	logs().info("gateway", std::string("Gateway ") + _VER + " started");

	/* 主线程：等待关闭信号，带超时轮询 */
	while (!_stop_requested && !server_exited) {
		std::this_thread::sleep_for(std::chrono::milliseconds(200));
	}

	/* 收到信号或服务器异常退出：安全执行关闭流程 */
	if (!server_exited && _SERVE) {
		std::cerr << "[gateway] 正在停止服务器..." << std::endl;

		// 给服务器 5 秒时间优雅关闭，防止挂死
		std::thread stop_thread([&]() {
			try {
				_SERVE->stop();
			} catch (const std::exception& e) {
				std::cerr << "[gateway] Stop exception: " << e.what() << std::endl;
			}
		});

		// 最多等待 8 秒，超时则 detach 让系统回收（极端情况下保证进程退出）
		if (stop_thread.joinable()) {
			auto future = std::async(std::launch::async, &std::thread::join, &stop_thread);
			if (future.wait_for(std::chrono::seconds(8)) == std::future_status::timeout) {
				std::cerr << "[gateway] 警告：服务器停止超时，强制退出" << std::endl;
				stop_thread.detach();
				std::exit(1);  // 极端情况：8秒还没停完，强制退出（日志可能丢失但进程不挂死）
			}
		}
	}

	if (server_thread.joinable()) {
		server_thread.join();
	}

	/* 收到停止信号后的收尾：记录并 flush 剩余日志（最多等待3秒） */
	std::cerr << "[gateway] 正在刷新日志..." << std::endl;
	logs().info("gateway", "Gateway shutdown");
	{
		std::thread log_flush_thread([]() {
			logs().shutdown();
		});
		auto future = std::async(std::launch::async, &std::thread::join, &log_flush_thread);
		if (future.wait_for(std::chrono::seconds(3)) == std::future_status::timeout) {
			std::cerr << "[gateway] 警告：日志刷新超时，退出" << std::endl;
			log_flush_thread.detach();
		}
		/* 非超时分支不得再 join：async 已完成 join，线程已不可 join，
		 * 重复 join 抛 std::system_error 导致 terminate（审计 A1） */
	}

	std::cerr << "[gateway] 已退出" << std::endl;
	_SERVE.reset();
}

/* 注册http路由 */
void registerRoutes(HttpServer &server) {

	/* const http::request<http::string_body>& req, const std::unordered_map<std::string, std::string>& params */
	server.get("*", [](const auto& req, const auto& params) -> http::response<http::string_body> {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleProxy(req, params, "get"); });
	});

	server.post("*", [](const auto& req, const auto& params) {
		return wrapperHttp(req, params,
			[](const auto& req, const auto& params) { return handleProxy(req, params, "post"); });
	});

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
		/* 客户端只给纯消息（e.message()）；e.what() 的 [Http][Error NNNN] 内部前缀
		 * 仅保留在服务端 stderr/集中日志，不外泄（审计 A8） */
		{"message", e.message()}
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

/* http 代理转发视图（v0.9.0 纯转发版）
 *
 * 认证职责已迁至 ca：gateway 不验签，仅做存在性不变量检查——
 * 显式携带 Target-Url 的受保护请求必须带有 ca 注入的 X-Auth-User 头，
 * 缺失说明请求未经 ca（如直连 8445），一律 401。
 * X-Auth-User / X-Auth-Admin 头不在 is_special_header 剥离列表中，原样透传给下游。 */
http::response<http::string_body> handleProxy(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params,
	const char *method)
{
	http::response<http::string_body> res;
	res.version(req.version());

	std::string t_url = "";
	std::string url = "";
	bool is_public_target = false;
	{
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
			is_public_target = true;
		}

		// 路径规范化校验（P1-1）：防止路径遍历、协议注入、空字节等
		// 校验在 url 拼接前完成，避免恶意 target 污染下游请求
		std::string target_path = std::string(req.target());
		if (!is_safe_target(target_path))
			THROW_EX_CUSTOM(HttpException, 2300, "Invalid request path", 400, "*");

		url = t_url;
		/* 避免 target_path 以 "/" 开头时产生双斜杠（如 http://host:port//list），
		 * 下游服务（core 等）使用精确路径匹配，双斜杠会导致 404 */
		if (target_path.empty() || target_path[0] != '/')
			url += "/";
		url += target_path;

		if (!is_public_target) {
			/* 存在性不变量（非验签）：受保护请求必须携带 ca 注入的 X-Auth-User。
			 * Beast name_string() 保留线上原始大小写，统一转小写比较 */
			bool has_auth_user = false;
			for (auto const& field : req) {
				std::string h;
				h.reserve(field.name_string().size());
				for (unsigned char c : field.name_string())
					h.push_back(static_cast<char>(std::tolower(c)));
				if (h == "x-auth-user" && !field.value().empty()) {
					has_auth_user = true;
					break;
				}
			}
			if (!has_auth_user)
				THROW_EX_CUSTOM(HttpException, 2301, "Unauthorization", 401, "*");
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
			/* 不得抛 std::runtime_error：会被 wrapper 的 std::exception 分支
			 * 兜底成 500，与文档宣称的 403 不符（审计 A2） */
			THROW_EX_CUSTOM(HttpException, 2303, "Forbidden: target not allowed", 403, "*");
		}
	}

	/*
	 * HTTP客户端回调函数
	 * 响应体大小限制由 HttpClient 默认 10MB 上限保证（libhttp P1-2 已修复） */
	auto callback = [&res](const auto& ret) {
		res.body() += ret;
	};

	/* 复制(几乎)所有的请求头（含 ca 注入的 X-Auth-User / X-Auth-Admin / Authorization） */
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
	//       ca → gateway 为可信内网链路，收到的 XFF 即客户端真实 IP（由 waf/ca 链路注入）
	//       下游服务应信任此值做限流/审计，不再被客户端伪造
	auto xff_it = req.base().find("X-Forwarded-For");
	if (xff_it != req.base().end()) {
		client.set_header(std::string("X-Forwarded-For: ") + std::string(xff_it->value()));
	} else {
		// 上游未传 XFF 时，标记为 unknown，避免下游拿到空值
		client.set_header("X-Forwarded-For: unknown");
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
	// A5 修复：Beast name_string() 保留线上原始大小写，比较前统一转小写，
	//   否则小写 x-forwarded-for 等变体绕过剥离，与注入的可信 XFF 并存到达下游
	// 注意：x-auth-user / x-auth-admin 不在剥离列表——它们是 ca 注入的身份头，须透传给下游
	std::string h;
	h.reserve(header.size());
	for (unsigned char c: header)
		h.push_back(static_cast<char>(std::tolower(c)));
	return  h == "host" || h == "connection"
		|| h == "accept-encoding"
		|| h == "x-forwarded-for"
		|| h == "x-real-ip"
		|| h == "forwarded";
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
