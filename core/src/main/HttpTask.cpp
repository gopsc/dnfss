#include <iostream>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/json_parser.hpp>
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
			this->server = std::make_unique<pmc::net::HttpServer> ("127.0.0.1", port, "server.crt", "server.key", 4);
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


