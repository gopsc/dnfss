#pragma once
#include <memory>
#include <string>
#include <chrono>
#include <us/Thread.hpp>
#include <us/ITask.hpp>
#include <us/RpcServer.hpp>

namespace qing {

/* 配置仓库前置声明（实现位于 ConfsTask.cpp，保持头文件干净） */
class ConfigStore;

/* ----------------
 * 【内嵌 confs 配置服务任务】
 * ----------------
 * 将 confs2（C++20 统一配置服务，pymsg RpcServer）内嵌为 core 的一个 ITask，
 * 与 HttpTask / Tttask 并列，由 taskPool 统一管理生命周期。
 *
 * - 协议与 Python confs / standalone confs2 字节级兼容，消费方零改动
 * - 仅监听 127.0.0.1，路由 get/list/reload/health 不变
 * - 不传 --confs 参数时 core 不加载本任务，行为与原版完全一致
 */
class ConfsTask: public ITask {
public:

	/* 构造内嵌配置服务任务
	 *
	 * addr:       监听地址（默认 127.0.0.1）
	 * port:       监听端口（默认 8010）
	 * workers:    RpcServer 工作线程数（默认 4）
	 * config_dir: 配置文件目录（默认 ./configs） */
	ConfsTask(std::string addr, int port, int workers, std::string config_dir);

	/* 析构需定义于 .cpp（unique_ptr 持有不完整类型 ConfigStore） */
	~ConfsTask();

	/* 任务是否在运行中 */
	bool isRunning() override;

	/* 启动任务（激活线程并等待 RpcServer 就绪） */
	void start() override;

	/* 终止任务（停止 RpcServer 并等待线程关闭） */
	void stop() override;

private:
	std::string addr;                               /* 监听地址 */
	int port;                                       /* 监听端口 */
	int workers;                                    /* 工作线程数 */
	std::unique_ptr<ConfigStore> store;             /* 配置仓库 */
	std::unique_ptr<Thread> th;                     /* 可控线程指针 */
	std::unique_ptr<pmc::net::RpcServer> server;    /* RPC 服务器指针 */
	std::chrono::steady_clock::time_point started_at; /* 启动时间（health uptime） */

	/* 初始化线程，定义线程的行为（RpcServer） */
	void init_thread();

	/* 注册 pymsg 路由（get/list/reload/health） */
	void register_routes(pmc::net::RpcServer& rpc);
};

}
