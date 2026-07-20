/* ===============================================
 *  通用子系统、进程机器、进程托管服务
 *
 *
 *  【主函数文件】
 *
 *  直接子系统调用，或者启动一个pmc子系统（可以打开http）。
 * ===============================================
 */
#include <iostream>
#include <string> 
#include <fstream>
#include <sstream>
#include <thread>
#include <memory>
#include <vector>
#include <list>
#include <cstddef>
#include <csignal>
#include <cctype>
#include <iomanip>
#include <unistd.h>
#include <sys/types.h>
#include <boost/json.hpp>
#include <boost/program_options.hpp> 
#include <boost/property_tree/ptree.hpp>

#include "logs/Logger.hpp"
#include <us/Thread.hpp>
#include <us/Cv_wait.hpp>
#include <us/ITask.hpp>
#include "tt/Pipe.h"
#include "main/Tttask.hh"
#include "main/HttpTask.hh"
#include "main/pmc_mtd.hh"
#include "main/subsys_call.hh"

/* 中文模式 Chinese Mode */
#include "cn/中文化.hpp"

/* 静态区 - 存放全局变量 */
static const char *PMC_VERSION = "0.0.10"; 	/* 版本号 */
static const size_t MSGLEN = 2048;		/* 单条消息的长度 */
static const size_t MSGCNT = 100;		/* 消息条数 */

/* ---- 命名空间 ---- */
using namespace qing;

/* ---- 给命令行参数的命名空间取别名 ---- */
namespace po = boost::program_options;


void pmc_init(po::variables_map&);		/* 初始化pmc并行机器 */
void pmc_serve(po::variables_map&);		/* pmc的http伺服器模式 */
void parse_data_from_mq(std::string&);		/* 解析消息队列收到的消息 */

//-------------------------------------------------------------
//-------------------------------------------------------------


/*-------------------------------------------------------------------------*/
/*-------------------------------------------------------------------------*/
Cv_wait cv =  Cv_wait();  /* 条件变量的等待机制（让主程序等待中断信号） */

auto taskPool = std::vector<std::shared_ptr<ITask>>{};  /* 基层任务池  -  !!! 非子进程 !!! */






/*-------------------------------------------------------------------------*/


/* 捕获中断信号，结束线程并且退出 */
void signalHandler(int signum)
{
    LOG_INFO("收到终止信号，准备停止服务器...");
    cv.WakeCv();    /* 唤醒条件信号即关闭程序 */
    //if (server) server->stop(); /* TODO: 封装成ITask */
}

/*---------------- SIGNAL ACTION ----------------*/
//
/* 对信号的响应行为  TODO: 我应该系统地学习一下这个库 */
void set_signal_action() {

	/* 该结构体用于描述信号的处理方式 */
	struct sigaction sa;


 	/* 绑定处理函数 */
	sa.sa_handler = signalHandler;
    
	/*
	 * 清空并初始化一个信号掩码集  --------------> 用于指定哪些信号在当前或即将执行的信号处理函数期间应该被阻塞（位掩码）
	 *                                “在执行这个信号处理函数时，除了当前正在处理的信号外，还希望临时阻塞其他某些信号”
	 * 不阻塞其他信号
	 * 确保在执行期间没有其他特定的信号会被阻塞
	 * 防止处理过程中被其他信号中断
	 */
	sigemptyset(&sa.sa_mask);

	/*
	 * 用于设置信号处理函数的行为特性。
	 * 0 - 默认选项
	 * SA_NODEFER - 表示在执行信号处理函数时不允许其他信号被阻塞
	 * SA_RESETHAND - 在信号处理函数返回后，将信号的处理方式重置为默认
	 * SA_NOCLDSTOP - 对于子进程发送的停止信号，父进程不会停止。
	 * SA_NOCLDWAIT - 父进程在等待等待子进程终止时不会因为子进程发送信号而收到通知
	 * SA_SIGINFO - 使用扩展的信号信息，信号处理函数接收三个参数而不是一个 
	 */
	sa.sa_flags = 0;

	/* 注册信号 */
	sigaction(SIGINT, &sa, nullptr);  /* Ctrl + C */
	sigaction(SIGTERM, &sa, nullptr); /* kill 或 systemctl stop */

}

/*-------------------------------------------------------------------------*/
/*-------------------------------------------------------------------------*/
/* 入口函数 */
int main(int argc, char** argv) {

	/*------------------------------*/
	/* 设置信号行为*/
	set_signal_action();

	/*------------------------------*/
	/* 解析命令行参数 */
	po::options_description desc("pmc subsystem start options");
	desc.add_options()  /* 定义选项 */
		("help,h",    "display help message")		/* 帮助菜单 */
		("version,v", "display version message")	/* 显示版本 */
		/******** 子系统调用模式 *********/
		("send,s", po::value<std::string>(), "send a message to pmc sys")	/* 输入目标pid */
		("exec",   po::value<std::string>(), "message body")			/* 启动一个进程 */
		("list", "list process all run")					/* 列出运行中的进程 */
		("kill",   po::value<int>(),         "kill a process")			/* 杀死一个进程 */
		/********* 子系统服务模式 ********/
		("init,i", po::value<std::string>(), "self-init list")			/* 输入自启动列表 */
		("serve",  po::value<int>(),         "Run as http server");		/* 启用http伺服器 */

 	/* 参数变量映射关系 */
	po::variables_map vm;

	try {  /* 开始解析 */
    		po::store(po::parse_command_line(argc, argv, desc), vm);
		po::notify(vm);
		if (vm.count("help")) { /* 获取帮助页面 */
			std::cout << desc << std::endl;
			std::cout << "--------- API MAP ---------" << std::endl;
			std::cout << "Available endpoints:" << std::endl;
			std::cout << "  POST /list?target=optional" << std::endl;
			std::cout << "  POST /exec?command=ls&target=optional" << std::endl;
			std::cout << "  POST /kill?pid=1234&target=optional" << std::endl;
			return 0;
		}
		if (vm.count("version")) { /* 获取版本信息 */
			std::cout << PMC_VERSION << std::endl;
			return 0;
		}
	}

	catch (const std::exception& e) {
		std::cerr << "ERROR: " << e.what() << std::endl;
		std::cerr << "using --help to check options message" << std::endl;
		throw e;
	}

	/*----------------------------------------*/

	if ( vm.count("send") && vm.count("exec")) {
		auto send = vm["send"].as<std::string>();
		auto exec = vm["exec"].as<std::string> ();
		auto msg =  subsys_call::send_start_and_recv_pipe(send, exec);
		std::cout << msg << std::endl;
		return 0;
	}

	else if ( vm.count("send") && vm.count("list")){
		auto send = vm["send"].as<std::string> ();
		auto msg = subsys_call::send_list_and_recv_pipe(send);
		std::cout << msg << std::endl;
		return 0;
	}

	else if (vm.count("send") && vm.count("kill")) {
		auto send = vm["send"].as<std::string> ();
		auto kill = vm["kill"].as<int>();
		auto msg =  subsys_call::send_kill_and_recv_pipe(send, kill);
		std::cout << msg << std::endl;
		return 0;
	}

	/* 初始化日志模块 */
	Logger::getInstance().init(LogLevel::DEBUG, true);


	/* 列表自启动 */
	if (vm.count("init")){
		std::string path = vm["init"].as<std::string>();
		pmc_mtd::parse_self_init_list(path);
	}

	
	/**********************************************/

	/* TODO: 抽象出来 */
	if (vm.count("serve"))
		pmc_serve(vm);
	

	/* pmc初始化函数 */
	pmc_init(vm);

	/*
		* 主线程等待
		*
		* 触发器在信号处理那里
		*/
	cv.Wait();

	/* 主线程被唤醒，停止所有任务 */
	for (auto& task: taskPool) {
		task->stop();
	}

	
}




/*-----------------------------------------------------------------------------*/
/* 初始化子系统 即是启动通信 */
void pmc_init(po::variables_map& vm) {

	/*-------------------*/
	auto ttt = std::make_shared<Tttask> (MSGLEN, MSGCNT,
		[](const char *data) { /* 处理收到的数据 */
			auto json_str = std::string(data);
			parse_data_from_mq(json_str);
		});
	ttt->start();
	taskPool.push_back(ttt);
}

/* http伺服器模式（只监听本地）TODO: 集成为ITask */
void pmc_serve(po::variables_map& vm)
{
	int port = vm["serve"].as<int>();
	auto server = std::make_shared<HttpTask> (port, [](pmc::net::HttpServer& server_) {
			process_http_server::registerRoutes(server_);
	});
	server->start();
	taskPool.push_back(server);
		
}





/* 解析从消息队列中接收到的数据，运行在服务端 */
void parse_data_from_mq(std::string& json_str)
try {
	boost::json::value jv = boost::json::parse(json_str);
	std::string namepipe = jv.at("pipe").as_string().c_str(); 
	std::string typ = jv.at("type").as_string().c_str();
	auto pipe = Pipe(namepipe, Pipe::USER);
	pipe.openForWrite();


	if (typ == "list") {
		auto msg = pmc_mtd::list();
		pipe.writePipe(msg);
		return;
	}

	else if (typ == "kill") {
		int trg = jv.at("kill").as_int64();
		auto flag = pmc_mtd::kill(trg);
		std::string msg = flag ? "OK" : "Nope";
		pipe.writePipe(msg);
		return;
	}

	else if (typ == "exec"){
		std::string cmd = jv.at("exec").as_string().c_str();
		std::string msg = (pmc_mtd::exec(cmd)) ? "OK" : "ERR";
		pipe.writePipe(msg);
		return;
	}
}

/* 捕获异常后，仅仅只是打印出来？ */
catch (std::exception &exp) {
    std::cerr << "Exception at:   parse_data_from_mq()" << std::endl;
    std::cerr << "Exception type: " << typeid(exp).name() << std::endl;
    std::cerr << "Exception info: " << exp.what() << std::endl;
}



