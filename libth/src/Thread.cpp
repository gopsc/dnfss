#include "us/Thread.hpp"
namespace qing{

	//Thread::Thread(f_t stop_callback, f_t wake_callback, f_t loop_callback, f_t clear_callback)
        //{
	//	StopEvent = std::make_unique<f_t>(stop_callback);
	//	WakeEvent = std::make_unique<f_t>(wake_callback);
	//	LoopEvent = std::make_unique<f_t>(loop_callback);
	//	ClearEvent = std::make_unique<f_t>(clear_callback);
	//};

	void Thread::set_stop(f_t callback) {
		StopEvent = std::make_unique<f_t> (callback);
	}

	void Thread::set_wake(f_t callback) {
		WakeEvent = std::make_unique<f_t> (callback);
	}

	void Thread::set_loop(f_t callback) {
		LoopEvent = std::make_unique<f_t> (callback);
	}

	void Thread::set_clear(f_t callback) {
		ClearEvent = std::make_unique<f_t> (callback);
	}

 
	Fsm::Stat Thread::check() {
		return fsm.check();
	}

	void Thread::suspend() {
		fsm.suspend();
	}

        /* 唤醒函数不可以进行该类中的锁操作，否则会造成死锁  */
	void Thread::wake() {
		//{
			//std::lock_guard<std::mutex> lk(mtx_th);
			fsm.set(Fsm::Stat::START);
			//ready = true;
		//}
		//cv.notify_all();
	} /* wake */

	/* 上锁、唤醒阻塞的线程 */
	void Thread::stop() {
		{
			std::lock_guard<std::mutex> lk(mtx_th);
			fsm.set(Fsm::Stat::STOP);
			ready = true;
		}
		/* 可能会出现多个线程在等待的情况 */
		cv.notify_all();
	}/* stop */

	void Thread::shut() {
		{
			std::lock_guard<std::mutex> lk(mtx_th);
			fsm.set(Fsm::Stat::SHUT);
			ready = true;
		}
		cv.notify_all();
	} /*shut*/
	
	void Thread::run() {
		{
			std::lock_guard<std::mutex> lk(mtx_th);
			fsm.set(Fsm::Stat::RUNNING);
			ready = true;
		}
		cv.notify_all();
	}/*run*/

	void Thread::Activate() {

		/* 防止重复初始化
		 * FIXME: 抛出异常？感觉也不太合适 */
		if (th) return; 

		/* 申请线程资源 */
		th = std::make_unique<std::thread>(&Thread::main, this);

	}

	void Thread::WaitStart() {
		std::unique_lock<std::mutex> lk(mtx_th);	/* 不进入临界区可能会造成死等，在唤醒过程非常短暂的情况下 */
		wake(); 	/* 这就是为什么wake()不可以获取临界区，否则会造成死锁 */
		ready = false;	/* 这个临界区结束后，WakeEvent()中的run()会唤醒线程的堵塞 */
		cv.wait(lk, [this] { return ready; });
	}

	void Thread::WaitClose() {
		shut();
		if (th->joinable())
			th->join();
	}



    /* 线程主函数 */
    void Thread::main() {

        /* 回字形循环 */
        while (check() != Fsm::Stat::SHUT) {

            try { /* 异常只会使线程静止 */


		//------------------------------------------------
                while (check() == Fsm::Stat::STOP) {
                    (*StopEvent)(*this);	/* 静止状态 */
                }
					
                if (check() == Fsm::Stat::START) {
                    //(*ClearEvent)(*this);       /* 在开始之前做一次清理 */
                    (*WakeEvent)(*this);	/* 开始事件 */
                }

                while (check() == Fsm::Stat::RUNNING) {
                    (*LoopEvent)(*this);	/* 从设置阶段进入运行态 */
                }

                /* 清理事件 （记得检查成员变量有没有被设置） */
                (*ClearEvent)(*this);
		
		//------------------------------------------------

            }

            catch (std::exception& exp) {
                std::cout << "Thread throw a exception: " << exp.what() << std::endl;
                stop(); /* 因为这里的停止，线程在循环内抛出异常进程不会死亡，所以需要特别注意内存泄漏 */
            }

        }
    }

}
