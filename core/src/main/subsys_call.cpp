
#include "iostream"
#include "fstream"
#include <boost/json.hpp>
#include "tt/Mq.h"
#include "tt/Pipe.h"
#include "main/subsys_call.hh"

namespace qing {
namespace subsys_call {


/*-------------------------------------------------------------------------*/


/* -------- 向pmc的消息队列发送启动命令并且从管道接收回复 -------- */
std::string send_start_and_recv_pipe(const std::string& name_mq, const std::string& exec)
try {

	auto name_pipe = std::to_string(getpid());
	auto mq = Mq(name_mq, 2048, 100, Mq::USER); /* 放在这里避免管道文件的残留 */

	boost::json::object obj;
	obj["pipe"] = name_pipe;
	obj["type"] = "exec";
	obj["exec"] = exec;
	auto jsonstr = boost::json::serialize(obj);


	auto pipe = Pipe(name_pipe, Pipe::CREATOR); /* FIXME: 创建管道文件到/tmp下面 */
	if (!pipe.openForRead(true)) {
		std::cerr << "Failed to open pipe" << std::endl;
		return "ERR";
	}

	mq.send(jsonstr);
	if (!pipe.waitForRead(1, 0)) {
		std::cerr << "Pipe Read Timeout" << std::endl;
		return "ERR";
	}

	std::string res = "";
	if (pipe.readPipe(&res) < 0) {
		std::cerr << "Failed to read pipe" << std::endl;
		return "ERR";
	}

	return res;
}

//catch (boost::interprocess::interprocess_exception &exp)  {
//	std::cerr << "Target Pmc Is Not Found" << std::endl;
//	return 1;
//}

catch (std::exception &exp) {
    std::cerr << "Exception at:   send_start_and_recv_pipe()" << std::endl;
    std::cerr << "Exception type: " << typeid(exp).name() << std::endl;
    std::cerr << "Exception info: " << exp.what() << std::endl;
    return "ERR";
}



/* -------- 向目标子系统发送列举命令并收集结果 -------- */
std::string send_list_and_recv_pipe(const std::string& name_mq)
try {

	auto name_pipe = std::to_string(getpid());
	auto mq = Mq(name_mq, 2048, 100, Mq::USER);

	boost::json::object obj;
	obj["pipe"] = name_pipe;
	obj["type"] = "list";
	auto jsonstr = boost::json::serialize(obj);
	auto pipe = Pipe(name_pipe, Pipe::CREATOR);

	if (!pipe.openForRead(true)) {
		std::cerr << "Failed to open pipe" << std::endl;
		return "ERR";
	}


	mq.send(jsonstr);
	if (!pipe.waitForRead(1, 0)) {
		std::cerr << "Pipe Read Timeout" << std::endl;
		return "ERR";
	}

	std::string res = "";
	if (pipe.readPipe(&res) < 0) {
		std::cerr << "Failed to read pipe" << std::endl;
		return "ERR";
	}


	return res;

}

//catch (boost::interprocess::interprocess_exception &exp)  {
//	std::cerr << "Target Pmc Is Not Found" << std::endl;
//	return 1;
//}

catch (std::exception &exp) {
    std::cerr << "Exception at:   send_list_and_recv_pipe()" << std::endl;
    std::cerr << "Exception type: " << typeid(exp).name() << std::endl;
    std::cerr << "Exception info: " << exp.what() << std::endl;
    return "ERR";
}

/* -------- 删除操作调用 -------- */
std::string send_kill_and_recv_pipe(const std::string& name_mq, const int kill)
try{

	auto name_pipe = std::to_string(getpid());
	auto mq = Mq(name_mq, 2048, 100, Mq::USER);

	boost::json::object obj;
	obj["pipe"] = name_pipe;
	obj["type"] = "kill";
	obj["kill"] = kill;
	auto jsonstr = boost::json::serialize(obj);

	auto pipe = Pipe(name_pipe, Pipe::CREATOR);
	if (!pipe.openForRead(true)) {
		std::cerr << "Failed to open pipe" << std::endl;
		return "ERR";
	}

	mq.send(jsonstr);
	if (!pipe.waitForRead(1, 0)) {
		std::cerr << "Pipe Read Timeout" << std::endl;
		return "ERR";
	}

	std::string res = "";
	if (pipe.readPipe(&res) < 0) {
		std::cerr << "Failed to read pipe" << std::endl;
		return "ERR";
	}
	else return res;

}

/* 没有打开目标子系统的消息队列时，会触发这个异常
 * 但是实际上这里捕获的是进程间通信通用异常（旧版） */
//catch (boost::interprocess::interprocess_exception &exp)  {
//	std::cerr << "Target Pmc Is Not Found" << std::endl;
//	return 1;
//}

/* 这些函数抛出异常后可能导致管道文件残留 */
catch (std::exception &exp) {
    std::cerr << "Exception at:   send_kill_and_recv_pipe()" << std::endl;
    std::cerr << "Exception type: " << typeid(exp).name() << std::endl;
    std::cerr << "Exception info: " << exp.what() << std::endl;
    return "ERR";
}

}
}
