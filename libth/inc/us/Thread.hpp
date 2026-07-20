/* 线程
 *
 *
 * 包含一个简易的多线程控制机制，需要共享的变量和函数声明为成员。
 * 
 * 线程主函数包括了流程控制，要使用需要实现其中的各个事件方法
 *
 * 20250621: 从派生改为持有
 *
 * NOTE: 在对象构造完之后应该激活
 */

#pragma once
#include <thread>
#include <memory> /* 内存管理 */
#include <chrono> /* 用于线程休息 */
#include <iostream>
#include <stdexcept>
#include <functional>
#include "us/Fsm.hpp" /* 有限状态机 */
namespace qing {
class Thread;
//using f_t = void(*)(Thread&);
using f_t = std::function<void(Thread&)>;
class Thread {
public:

	/* 作为可变的函数，持有回调可能比继承更好
	 *
	 * FIXME: 可以改为链式传递参数 */
	//Thread(f_t stop_callback, f_t wake_callback, f_t loop_callback, f_t clear_callback);

	/* 使用默认初始化方法，然后用几个函数分别进行回调初始化 */
	Thread() =  default;

	/*
	 * 删除复制构造函数。
	 *
	 * 因为持有线程类，不要轻举妄动。
	 */
	Thread(const Thread&) = delete;


	/*
	 * 虚析构函数
	 *
	 * FIXME: 具体有什么用，忘了
	 */
	virtual ~Thread() {}


	/* 设置停止回调函数 */
	void set_stop(f_t callback);

	/* 设置唤醒回调函数 */
	void set_wake(f_t callback);

	/* 设置循环体回调函数 */
	void set_loop(f_t callback);

	/* 设置清理回调函数 */
	void set_clear(f_t callback);

    /*-----------------------------------*/
    /*-----------------------------------*/
    /*
     * 检查状态的方法
     *
     * NOTE: 做成虚函数，万一子类有特别要求呢
     *
     * FIXME: 将检查器和变异器做成一个简单的回调函数
     */
    Fsm::Stat check();

    /*-----------------------------------*/
    /*-----------------------------------*/
    /* 让线程能够阻塞直到新的状态被赋予的方法 */
    void suspend();

    /*-----------------------------------*/
    /*-----------------------------------*/
    /* 状态改变的方法 - 变异器 */

    /* 进入唤醒状态
     *
     * 唤醒函数不可以进行该类中的锁操作，否则会造成死锁
     */
    void wake();

    /* 进入静止状态
     *
     * 上锁、唤醒阻塞的线程
     */
    void stop();

    /* 进入关闭状态 */
    void shut();

    /* 进入运行状态 */
    void run();

    /*-----------------------------------*/
    /*-----------------------------------*/

    /*
     * 激活线程，申请线程对象
     *
     * 该函数不能放在构造函数内，因为需要确保对象已经被构造了
     */
    void Activate();

    /*
     * 尝试唤醒线程并等待它开始 据说该函数有问题可能导致异常
     */
    void WaitStart();

    /* 等待线程关闭 */
    void WaitClose();

    /*-----------------------------------*/
    /* 线程主函数 */
    void main();

private:

    qing::Fsm fsm{};	/* 持有状态机类型 */

    std::unique_ptr<std::thread> th;	/* 持有线程类型 */

    std::mutex mtx_th;	/* 持有锁类型 */

    /*
     * 这里持有一个条件变量，用于阻塞以等待线程的关闭
     * 
     * 注意状态机中的cv是另一个变量
     */
    std::condition_variable cv;

    /* 辅助条件变量的逻辑型 */
    bool ready;

    /* 事件回调 */
    std::unique_ptr<f_t> StopEvent;
    std::unique_ptr<f_t> WakeEvent;
    std::unique_ptr<f_t> LoopEvent;
    std::unique_ptr<f_t> ClearEvent;

};
}
