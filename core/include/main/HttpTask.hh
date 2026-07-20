#pragma once
#include <us/Thread.hpp>
#include <us/ITask.hpp>
#include <us/HttpServer.hpp>
namespace qing {
	namespace http = boost::beast::http;
	/* ----------------
	 * HttpTask类 - 超文本服务器任务 */
	class HttpTask: public ITask {
		public:

	 		/* 接受一个回调函数，其接受pmc::net::HttpServer对象的引用，在回调函数中对服务器进行配置  */
			HttpTask(int port, std::function<void(pmc::net::HttpServer& server)> handler);

			/* 在一个线程中判断线程是否正在有效运行 */
			bool isRunning() override;

			/* 启动该项任务 */
			void start() override;

			/* 停止该项任务 */
			void stop() override;

		private:
			int port;
			std::function<void(pmc::net::HttpServer& server)> handler;
			std::unique_ptr<Thread> th;			/* ---- 可控线程指针	*/  
			std::unique_ptr<pmc::net::HttpServer> server;   /* ---- HTTP服务器的指针 ---- */

			/* 初始化线程，定义线程的行为（Http服务器） */
			void init_thread();
	};

	/*-----------------------------------------------------------------------------*/



/* ================
 * 进程托管http服务器
 * ================
 * 对操作进程列表的http服务器进行配置，即是输入回调函数  */
namespace process_http_server {

	/* ---- 注册POST路由 ---- */
	void registerRoutes(pmc::net::HttpServer& server);

	/**
	 * @brief 解析请求体中的JSON数据
	 * @param body 请求体字符串
	 * @return JSON解析后的属性树
	 */
	boost::property_tree::ptree parseJsonBody(const std::string& body);

	/**
	 * @brief 构建JSON响应
	 * @param success 是否成功
	 * @param message 消息
	 * @param data 附加数据
	 * @return JSON字符串
	 */
	std::string buildJsonResponse(bool success, const std::string& message,
                                   const boost::property_tree::ptree& data = boost::property_tree::ptree());

	/**
	 * @brief 从请求中提取target参数
	 * @param params 查询参数
	 * @return target值（空字符串表示本机）
	 */
	std::string getTarget(const std::unordered_map<std::string, std::string>& params);

	/**
	 * @brief 从请求中提取exec参数
	 * @param params 查询参数
	 * @return 要执行的指令
	 */
	std::string getExecCommand(const std::unordered_map<std::string, std::string>& params);

	/**
	 * @brief 从请求中提取kill参数
	 * @param params 查询参数
	 * @return 目标进程号
	 */
	int getPid(const std::unordered_map<std::string, std::string>& params);

	/* POST方法处理器 */
	http::response<http::string_body> handleList(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

	http::response<http::string_body> handleExec(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

	http::response<http::string_body> handleKill(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

	/** 内部实现函数 */
	std::string listProcesses(const std::string& target);
	std::string execCommand(const std::string& command, const std::string& target);
	bool killProcess(int pid, const std::string& target);





}


}
