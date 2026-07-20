#pragma once
#include <iostream>
namespace qing {
/* ----------------
 * 进程托管服务方法集
 * ----------------
 * 直接对进程组列表进行操作
 *
 */
namespace pmc_mtd
{
	auto list() -> std::string;				/* 列出所有的进程 */
	auto kill(const int pid) -> bool;			/* 杀死一个进程，需提供PID */
	auto exec(const std::string& cmd) -> bool;		/* 执行一个进程，输入要执行的命令行 */
	auto parse_self_init_list(std::string &path) -> void;	/* 解析自启动脚本 */
}



}
