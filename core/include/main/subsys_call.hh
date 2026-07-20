#pragma once
namespace qing {

/* ---------------- 
 * 子系统调用
 * ----------------
 *  通过进程间通信与子系统进行通讯，调用不同的功能
 *
 */


namespace subsys_call
{
	std::string send_list_and_recv_pipe(const std::string& name_mq);
	std::string send_start_and_recv_pipe(const std::string& name_mq, const std::string& exec);
	std::string send_kill_and_recv_pipe(const std::string& name_mq, const int kill);
}

}
