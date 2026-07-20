#pragma once
#include <boost/process.hpp>
#include <us/ITask.hpp>

namespace qing {

/* ----------------
 * 【子进程任务】 
 * ----------------
 * 输入要执行的命令，构造子进程任务
 * TODO: 我们可以重定向子进程的输入和输出，实时交互  */
class ProcessTask: public ITask {
public:

	/* 输入一个字符串（要执行的命令）（但是并没有执行起来） */
	ProcessTask(std::string cmd): cmd(cmd) {}

	/* 任务是否在运行中 */
	bool isRunning() override { return p && p->running(); }

	/* 启动任务 */
	void start() override {
		if (!isRunning())
			this->p = std::make_unique<boost::process::child>(cmd); }

	/* 终止该任务（并且重置子进程指针） */
 	void stop() override {
		if (isRunning()) p->terminate(); 
		p.reset();
	}

	/* 获取该任务（子进程）的命令 */
	std::string check()  { return this->cmd; }

	/* 获取该子进程的pid（进程编号） */
	pid_t pid() { return p->id();  }

private:
	std::unique_ptr<boost::process::child> p;  /* 子进程指针 */
	std::string cmd;                           /* 该进程执行的命令 */
};

}


#ifdef __NEW_VER
/* ---- [子进程任务]boost.process库升级到了v2 ---- */
class ProcessTask: public ITask {
public:
	ProcessTask(std::string cmd): cmd(cmd) {}
	bool isRunning() override;
	int getExitCode();
	void start() override;
	/* ---- NOTE(202606025-qing): 杀死进程失败只是打印错误信息 ---- */
 	void stop() override;
	std::string check()  {
		return this->cmd;
	}
	pid_t pid() {
		return p->id();
	}

private:
	boost::asio::io_context ctx;	/* 异步上下文 */
	std::unique_ptr<boost::process::v2::process> p;	/* 子进程指针 */
	boost::system::error_code exit_code;	/* 进程退出的代码 */
	std::string cmd;	/* 要执行的命令 */
};
}
#endif
