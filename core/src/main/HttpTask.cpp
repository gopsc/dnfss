#include <iostream>
#include <cstdlib>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/json_parser.hpp>
#include <boost/json.hpp>
#include <us/HttpClient.hpp>
#include "main/HttpTask.hh"
#include "main/pmc_mtd.hh"
#include "main/subsys_call.hh"

using namespace qing;
namespace qing {


	/* -------- HttpTask -------- */
 
	HttpTask::HttpTask(int port, std::function<void(pmc::net::HttpServer& server)> handler)
		: port(port), handler(handler) {
		this->th = std::make_unique<Thread> ();
		init_thread();
	}
            
	bool HttpTask::isRunning() { 
		auto stat = th->check();
		return !(stat == Fsm::Stat::STOP || stat == Fsm::Stat::SHUT);
	}


	void HttpTask::start() {
		if (!this->isRunning()) {
			th->Activate();  /* FIXME: 这里获取了资源，不知道会不会造成内存泄漏 */
			th->WaitStart(); /* TODO: add timeout 增加超时 */
		}
	}

	void HttpTask::stop() { 
		this->server->stop();
		if (this->isRunning())
			th->WaitClose();
	}

	void HttpTask::init_thread() {

		/* 构造通信线程 */
		this->th = std::make_unique<Thread> ();

		/* stop callback 静止事件 */
		this->th->set_stop(  [](Thread& th) -> void {                    
			/* 由线程提供的等待状态机发生改变信号的方法 */
			th.suspend();
		});


		/* start callback 唤醒事件 */
		this->th->set_wake(  [this](Thread& th) -> void {
			// core 的 HTTP 服务仅供 gateway 内部代理转发（WAF 已终止 TLS）
			// 使用无证书的纯 HTTP 构造，不启用 TLS
			this->server = std::make_unique<pmc::net::HttpServer> ("127.0.0.1", port, 4);
			handler(*this->server);
			th.run();

			this->server->start();  /* 这里包含了端口启动提示符输出 */
			this->server->run();    /* 堵死线程，等待服务器运行结束 */
		});


		/* loop callback 循环事件 */
		this->th->set_loop(  [this](Thread& th) -> void {
						/* 如果服务器是主动退出的 */
				th.stop();	/* 停止线程，防止跌入死循环 */
		});


		/* clean callback 清理事件 */
		this->th->set_clear( [this] (Thread& th) -> void {
				this->server.reset();;
		});

	}


/*==============================================================*/
/* 这些是尚未整理的工具 */

/* --------------------------- */
/* json字符串转boost.ptree
 * FIXME: 这个方法好像在ProcessManagerService进程托管服务中集成了*/
boost::property_tree::ptree parseJsonToPtree(const std::string& jsonStr) {
    boost::property_tree::ptree pt;
    try {
        std::stringstream ss(jsonStr);
        boost::property_tree::read_json(ss, pt);
    } catch (const boost::property_tree::json_parser_error& e) {
        // 解析失败处理
        std::cerr << "JSON parse error: " << e.what() << std::endl;
    }
    return pt;
}

/* --------------------------- */
/* url 解码 */
static std::string url_decode(const std::string& str) {
    std::string result;
    std::istringstream iss(str);
    char ch;
    
    while (iss.get(ch)) {
        if (ch == '%') {
            int hex;
            if (iss >> std::hex >> hex) {
                result += static_cast<char>(hex);
            }
        } else if (ch == '+') {
            result += ' ';
        } else {
            result += ch;
        }
    }
    return result;
}

/*==============================================================*/


/**
 * @brief 进程托管服务 - 相关模块
 *
 * 提供进程的列表、执行和终止功能
 * TODO(20260629-qing): 将这个模块合入HttpTask类模块
 */

namespace process_http_server {

	/* 从 Cookie 头中解析指定名称的值（找不到返回空串） */
	static std::string get_cookie_value(const std::string& cookie_header, const std::string& name) {
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

	/* 管理员验证：调用 gateway 的 /admin/verify 验签端点，验证请求携带的 Token 是否为有效管理员。
	 * gateway 是 Token 签发与验证的唯一权威（RSA 验签 + is_admin + 过期检查）。
	 * gateway 地址由环境变量 GATEWAY_HOST/GATEWAY_PORT 配置，默认 127.0.0.1:8443。
	 * gateway 不可用或响应异常一律视为未授权（fail-closed）。 */
	static bool verify_admin_token(const http::request<http::string_body>& req) {
		try {
			/* 提取 Token：Authorization 头优先，回退 Cookie */
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
			if (raw.empty() || raw.substr(0, 7) != "Bearer ") return false;

			/* gateway 地址 */
			std::string host = "127.0.0.1";
			int port = 8443;
			if (const char* h = std::getenv("GATEWAY_HOST")) host = h;
			if (const char* p = std::getenv("GATEWAY_PORT")) {
				int v = std::atoi(p);
				if (v > 0) port = v;
			}

			std::string url = "http://" + host + ":" + std::to_string(port) + "/admin/verify";

			pmc::net::HttpClient client;
			client.set_header("Authorization: " + raw);
			std::string body;
			client.post_stream_sync(url, "", [&](const std::string& chunk) { body += chunk; });

			auto jv = boost::json::parse(body);
			if (!jv.is_object()) return false;
			auto& obj = jv.as_object();
			auto it_success = obj.find("success");
			auto it_admin = obj.find("is_admin");
			if (it_success == obj.end() || it_admin == obj.end()) return false;
			if (!it_success->value().is_bool() || !it_admin->value().is_bool()) return false;
			return it_success->value().as_bool() && it_admin->value().as_bool();
		}
		catch (const std::exception&) {
			return false;
		}
	}

	/* ---- 注册POST路由 ---- */
	void registerRoutes(pmc::net::HttpServer& server) {
		server.post("/list", [](const auto& req, const auto& params) {
			return handleList(req, params);
		});

		server.post("/exec", [](const auto& req, const auto& params) {
			return handleExec(req, params);
		});

		server.post("/kill", [](const auto& req, const auto& params) {
			return handleKill(req, params);
		});
	}

	/* ---- 解析json ---- */
	boost::property_tree::ptree parseJsonBody(const std::string& body) {
		boost::property_tree::ptree pt;
		try {
			std::stringstream ss(body);
			boost::property_tree::read_json(ss, pt);
		}
		catch (...) {
			/* ---- JSON解析失败，返回空树 ---- */
		}
		return pt;
	}

	std::string buildJsonResponse(bool success, const std::string& message,
                                                      const boost::property_tree::ptree& data) {
		boost::property_tree::ptree response;
		response.put("success", success);
		response.put("message", message);
		if (!data.empty()) {
			response.add_child("data", data);
		}

		std::stringstream ss;
		boost::property_tree::write_json(ss, response);
		return ss.str();
	}

	std::string getTarget(const std::unordered_map<std::string, std::string>& params) {
		auto it = params.find("target");
		if (it != params.end())
			return url_decode(it->second); // <--- 这里
		return "";
	}

	std::string getExecCommand(const std::unordered_map<std::string, std::string>& params) {
		auto it = params.find("command");
		if (it != params.end())
			return url_decode(it->second); // <--- 这里
		return "";
	}

	int getPid(const std::unordered_map<std::string, std::string>& params) {
		auto it = params.find("pid");
		if (it != params.end()) {
			std::string decoded = url_decode(it->second); // <--- 这里
			return std::stoi(decoded);
		}
		return -1;
	}

	http::response<http::string_body> handleList(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params) {

		http::response<http::string_body> res;
		res.version(req.version());
		res.set(http::field::content_type, "application/json");

		/* 管理员认证：调用 gateway 验签，未授权直接拒绝 */
		if (!verify_admin_token(req)) {
			res.result(http::status::unauthorized);
			res.body() = buildJsonResponse(false, "Unauthorized: admin token required");
			res.prepare_payload();
			return res;
		}

		try {
			std::string target = getTarget(params);

			std::string result = listProcesses(target);
			boost::property_tree::ptree data = parseJsonToPtree(result);

			res.result(http::status::ok);
			res.body() = buildJsonResponse(true, "Process list retrieved successfully", data);
		}

		catch (const std::exception& e) {
			res.result(http::status::internal_server_error);
			res.body() = buildJsonResponse(false, std::string("Error: ") + e.what());
		}

		res.prepare_payload();
		return res;
	}

	http::response<http::string_body> handleExec(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params) {

		http::response<http::string_body> res;
		res.version(req.version());
		res.set(http::field::content_type, "application/json");

		/* 管理员认证：调用 gateway 验签，未授权直接拒绝 */
		if (!verify_admin_token(req)) {
			res.result(http::status::unauthorized);
			res.body() = buildJsonResponse(false, "Unauthorized: admin token required");
			res.prepare_payload();
			return res;
		}

		try {
			std::string target = getTarget(params);
			std::string command = getExecCommand(params);

			if (command.empty()) {
				// 尝试从POST body中获取command
				auto body_json = parseJsonBody(req.body());
				command = body_json.get<std::string>("command", "");
			}

			if (command.empty()) {
				res.result(http::status::bad_request);
				res.body() = buildJsonResponse(false, "Missing required parameter: command");
				res.prepare_payload();
				return res;
			}

			// 实现命令执行逻辑
			std::string result = execCommand(command, target);

			boost::property_tree::ptree data;
			data.put("command", command);
			data.put("output", result);

			res.result(http::status::ok);
			res.body() = buildJsonResponse(true, "Command executed successfully", data);
		}
		
		catch (const std::exception& e) {
			res.result(http::status::internal_server_error);
			res.body() = buildJsonResponse(false, std::string("Error: ") + e.what());
		}

		res.prepare_payload();
		return res;
	}

	http::response<http::string_body> handleKill(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params) {

		http::response<http::string_body> res;
		res.version(req.version());
		res.set(http::field::content_type, "application/json");

		/* 管理员认证：调用 gateway 验签，未授权直接拒绝 */
		if (!verify_admin_token(req)) {
			res.result(http::status::unauthorized);
			res.body() = buildJsonResponse(false, "Unauthorized: admin token required");
			res.prepare_payload();
			return res;
		}

		try {
			std::string target = getTarget(params);
			int pid = getPid(params);

			if (pid <= 0 && !req.body().empty()) {
				// 尝试从POST body中获取pid
				auto body_json = parseJsonBody(req.body());
				pid = body_json.get<int>("pid", -1);
			}

			if (pid <= 0) {
				res.result(http::status::bad_request);
				res.body() = buildJsonResponse(false, "Missing or invalid parameter: pid");
				res.prepare_payload();
				return res;
			}

			bool success = killProcess(pid, target);

			if (success) {
				boost::property_tree::ptree data;
				data.put("pid", pid);
				res.result(http::status::ok);
				res.body() = buildJsonResponse(true, "Process terminated successfully", data);
			} else {
				res.result(http::status::internal_server_error);
				res.body() = buildJsonResponse(false, "Failed to terminate process");
			}
		}
		
		catch (const std::exception& e) {
			res.result(http::status::internal_server_error);
			res.body() = buildJsonResponse(false, std::string("Error: ") + e.what());
		}

		res.prepare_payload();
		return res;
	}

	// 如果target为空，在本机执行
	// 否则在目标服务器执行
	std::string listProcesses(const std::string& target)
	{
		if (target == "")
			return pmc_mtd::list();
		else
			return subsys_call::send_list_and_recv_pipe(target);
	}

	std::string execCommand(const std::string& command, const std::string& target)
	{
		if (target == "")
			return pmc_mtd::exec(command) ? "OK" :"ERR";
		else
			return subsys_call::send_start_and_recv_pipe(target, command); /* 发送启动命令并且接收回复 */
	}

	bool killProcess(int pid, const std::string& target)
	{
		if (target == "")
			return pmc_mtd::kill(pid);
		else
			return subsys_call::send_kill_and_recv_pipe(target, pid) == "OK";
	}


}




}


