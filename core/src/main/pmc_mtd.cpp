

#include <boost/json.hpp>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/json_parser.hpp>
#include "main/PPool.hh"
#include "main/pmc_mtd.hh"
#include "registry/InitParser.hh"

#include <unistd.h>
#include <thread>
#include <chrono>
#include <filesystem>

namespace qing {

/* -------- 用于存放子进程的进程池 -------- */
static PPool ppool;

/*-------------------------------------------------------------------------*/

/* --------【pmc method子进程操作】集合 -------- */
namespace pmc_mtd {

/* 
 * 列举出所有子进程。
 *
 * {
 * 	"pipe": "<int>",
 * 	"type": "list",
 * } */
auto list() -> std::string {
    boost::json::array processes;
    
    /* 遍历子进程池
     *
     * FIXME: 子进程池的迭代器完成后，改为使用for-in迭代 */
    //ppool.clr() /* 保留已经跑完的进程，因为可能会涉及到输出的提取 *?
    for (int i = 0; i < ppool.size(); ++i) {
        processes.push_back({ /* 向json中推入对象 */
            {"index", i},
            {"pid", ppool[i].pid()},
            {"running", ppool[i].isRunning()},
            {"command", ppool[i].check()}
        });
    }
    
    /* 返回序列化的json对象 */
    return boost::json::serialize(processes);
}

/* 
 * 删除一个子进程，需要提供pid
 *
 * {
 *	"pipe": "<integer>",
 *	"type": "kill",
 *	"kill": <integer>
 * } */
auto kill(const int pid) -> bool {

	/* 遍历子进程池对比pid */
	for (int i=0; i<ppool.size(); ++i)
		if ( ppool[i].pid() == pid) {  /* 寻找目标进程 */
			ppool.kill(i); /* 杀死该进程 */
			return true; } /* 成功返回逻辑真 */

	/* 失败（没找到）返回逻辑假 */
	return false;
}

/* 
 * 启动一个子进程
 *
 * {
 *	"pipe": "<integer>",
 *	"type": "exec",
 *	"exec": "<commandline>"
 * } */
auto exec(const std::string& cmd) -> bool try
{
	//ppool.clr();  /* 保留跑完的子进程 */
	ppool.crtp(cmd);
	return true;  /* 成功返回逻辑真 */
}

/* 如果出现任何异常，返回假 */
catch(std::exception& exp) {
	return false;
}



/* 解析自启动列表、添加自启动项
 *
 * 仅支持 services.yaml 格式（结构化声明，含 args/env/depends_on/sleep_before）
 *
 * path 可以是：
 *   - 目录路径 → 自动查找目录下的 services.yaml
 *   - .yaml 文件 → 按 YAML 解析
 *
 * 如果在解析过程中出错，只是打印异常 */
void parse_self_init_list(std::string &path)
try {
	namespace fs = std::filesystem;

	std::vector<registry::ServiceConfig> services;

	if (fs::is_directory(path)) {
		/* 目录模式：InitParser::load 查找 services.yaml */
		services = registry::InitParser::load(path);
	} else if (path.size() > 5 && path.substr(path.size() - 5) == ".yaml") {
		/* YAML 文件模式 */
		services = registry::InitParser::parseYaml(path);
	} else {
		/* 不再支持 init.txt 纯命令列表格式 */
		std::cerr << "parse_self_init_list: 不支持的配置格式，请使用 services.yaml" << std::endl;
		return;
	}

	if (services.empty()) {
		std::cerr << "parse_self_init_list: 没有可启动的服务" << std::endl;
		return;
	}

	for (const auto& svc : services) {
		/* 启动前等待（替代散落在 _run.sh 中的 sleep） */
		if (svc.sleep_before > 0) {
			std::cerr << "[pmc] " << svc.name
			          << " 等待 " << svc.sleep_before << "s..." << std::endl;
			std::this_thread::sleep_for(
				std::chrono::seconds(svc.sleep_before));
		}

		/* 设置环境变量 */
		for (const auto& [k, v] : svc.env)
			setenv(k.c_str(), v.c_str(), 1);

		/* 清理已停止的进程，然后创建新进程 */
		ppool.clr();

		std::string full_cmd = svc.toCommand();
		std::cerr << "[pmc] 启动: " << svc.name
		          << " -> " << full_cmd << std::endl;
		ppool.crtp(full_cmd);
	}
}

catch (std::exception &exp) {
    std::cerr << "Exception at: parse_self_init_list()" << std::endl;
    std::cerr << "Exception type: " << typeid(exp).name() << std::endl;
    std::cerr << "Exception info: " << exp.what() << std::endl;
}


}




}
