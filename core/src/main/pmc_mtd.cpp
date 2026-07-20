

#include <boost/json.hpp>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/json_parser.hpp>
#include "main/PPool.hh"
#include "main/pmc_mtd.hh"

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
            {"status", ppool[i].check()}
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
 * 如果在解析过程中出错，只是打印异常？ */
void parse_self_init_list(std::string &path) 
try {

	std::ifstream file(path);
	if (!file.is_open()) {	/* ---- 没能打开自启动文件 ---- */
		std::cerr << "Failed to open self-init list" << std::endl;
		return;
	}

	std::string row = "";
	char ch;
	while (file.get(ch))

		if (ch == '\n') { 
	
			auto pos = row.find("#");
			if (pos != -1)
				row = row.substr(0, pos);
			if (row != "") {	/* trim() */
				ppool.clr();
				ppool.crtp(row);	/* 创建进程 */
				row = "";
			}
		}

		else row += ch;

	file.close();

}
catch (std::exception &exp) {
    std::cerr << "Exception at: parse_self_init_list()" << std::endl;
    std::cerr << "Exception type: " << typeid(exp).name() << std::endl;
    std::cerr << "Exception info: " << exp.what() << std::endl;
}


}




}
