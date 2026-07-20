#include <iostream>
#include <stdexcept>
#include "main/PPool.hh"

namespace qing {

	void PPool::crtp(const std::string& cmd) try
	{
		auto pt = std::make_shared<ProcessTask>(cmd);
		pt->start(); /* 直接创建了进程丢进容器 */
		pool.push_back(pt);
	}

	/* 如果boost的子进程模块报错，表示子进程创建失败？ */
	catch (boost::system::system_error &exp) {
		std::cerr << "Failed to execute this command: " << cmd << std::endl;
	}


    void PPool::clr() {
		auto it = --pool.end();         /* 由最后一个元素开始 */
		for (; it != pool.begin(); --it) /* 回溯遍历 */
			if (!(*it)-> isRunning()) { /* 如果任务已经终止 */
				auto tmp= it;	 /* 储存当前位置 */
				it++;		 /* 返回上一个结点 */
				pool.erase(tmp); /* 自动释放终止的节点   */
			}

		/* 循环结束时it == begin()，如果容器为空则end() == begin() */
		if (pool.size() > 0 && !(*it)->isRunning()) /* */
			pool.erase(it); /* 如果end() == begin()，此步将会试图删除哨兵 */
	}

    void PPool::kill(int idx) {


		/* 从头部向后计数idx */
		auto it = pool.begin();
		int i  = 0;
		for (; i < idx &&  it != pool.end(); ++i, ++it);

		if (it != pool.end()) {	/* 如果找到了目标（因到达idx而停止）（计数器没有指向尾节点） */
			(*it)->stop();  /* 关闭当前任务 */
			pool.erase(it); /* 从容器中移除当前任务 */
		}

		/* 因抵达终点而停下（下标越界） */
		else {
			throw std::out_of_range("PPool::kill()");
		}

	}

	ProcessTask& PPool::operator[](const size_t idx) {
		auto it = pool.begin();
		for (int i = 0; i < idx; ++it, ++i) /* 遍历idx次 */
			if (it == pool.end())	/* 如果该节点是尾部哨兵（下标越界） */
				throw std::out_of_range("PPool::operator[]()");
		return **it; /* 返回当前节点 */
	}
}