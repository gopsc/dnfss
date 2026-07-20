#include <iostream>
#include <memory>
#include <us/Thread.hpp>
#include <us/ITask.hpp>
#include "main/Tttask.hh"

namespace qing {
    Tttask::Tttask(const size_t msglen, size_t msgcnt, const sf_t callback)
    : msglen(msglen), msgcnt(msgcnt) {
        this->callback = std::make_unique<sf_t>(callback); /* FIXME: 使用指针似乎有所不妥 */
        init_thread();
    }

    void Tttask::start() {
        if (!this->isRunning()) {

            th->Activate();  /* FIXME: 这里获取了资源，不知道会不会造成内存泄漏 */
            th->WaitStart(); /* TODO: add timeout 增加超时 */
        }
    }

    bool Tttask::isRunning() { 
        auto stat = th->check();
        return !(stat == Fsm::Stat::STOP
            || stat == Fsm::Stat::SHUT);
    }

    void Tttask::init_thread() {

        /* 构造通信线程 */
        this->th = std::make_unique<Thread> ();
            
        /* stop callback 静止事件 */
        this->th->set_stop(  [](Thread& th) -> void {

                /* 由线程提供的等待状态机发生改变信号的方法 */
                th.suspend();
        });

        /* start callback 唤醒事件 */
        this->th->set_wake(  [this](Thread& th) -> void {
                name = std::to_string(getpid()); /* 以进程名构建消息队列 */
                mq = std::make_unique<Mq> (name, msglen, msgcnt, Mq::CREATOR);
                th.run();
        });


        /* loop callback 循环事件 */
        this->th->set_loop(  [this](Thread& th) -> void {
                
                /* 第一个是接收成功的处理事件，第二个是接收失败的处理事件
                *
                * TODO: 作为参数传入延时 
                * TODO: 使用标准库的线程延时*/
                mq->recv(*callback, []() { usleep(10000); });

        });


        /* clean callback 清理事件 */
        this->th->set_clear( [this] (Thread& th) -> void {

                /* 直接重置指针 */
                mq.reset();
        });


    }
}
