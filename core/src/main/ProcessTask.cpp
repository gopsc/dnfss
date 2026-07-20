#include <iostream>
#include "main/ProcessTask.hh"

#ifdef __NEW_VER
namespace qing {
	bool ProcessTask::isRunning() {
		return p && p->running(exit_code);
	}
	int ProcessTask::getExitCode() {
		return exit_code.value();
	}
	void ProcessTask::start() {
		if (!isRunning()) {
			auto ccmd = boost::process::v2::shell(cmd);
			auto exe = ccmd.exe();
			auto args = ccmd.args();
			this->p = std::make_unique<boost::process::v2::process>(ctx, exe, args);
		}
	}
    void ProcessTask::stop() {
		p->terminate(exit_code);
		if (exit_code) {
			std::cerr << "杀死进程失败： " << exit_code.message() << std::endl;
		}
		else {
			p.reset();
		}
	}
}
#endif
