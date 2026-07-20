#pragma once
#include <iostream>
#include <memory>
#include <list>
#include "main/ProcessTask.hh"

namespace qing {



/* -----------------
 * subprocess pool 子进程池
 * ----------------
 * FIXME: 将列表（序列化）功能放到这里来 */
class PPool {
public:

	/* CREATE PROCESS - 创建进程
	 *
	 * cmd: 启动命令 */
	void crtp(const std::string& cmd);


	/* CLEAR 清理  -  删除所有已经停下的进程 */
	void clr();


	/* 获取进程池的大小 */
	size_t size() { return pool.size(); }


	/* 删除一个进程，需要输入进程的下标。 
	 *
	 * FIXME: 输入进程下标似乎不太自然 */
	void kill(int idx);

	/* 取下标运算符重载 - 取元素对象 */
	ProcessTask& operator[](const size_t idx);


	/* TODO: 实现迭代器和begin()、end() 
	 * ...... */

private:

	/* 进程任务的shared指针的容器 */
	std::list<std::shared_ptr<ProcessTask>> pool{};

};



}
