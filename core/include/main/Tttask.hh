#pragma onces
#include "tt/Mq.h"
namespace qing {


/*
 * TRANSMIT TASK 通信任务
 * （创建一个通信伺服器，它收到消息就执行回调）
 * （长时间伺服运行会不会有问题）
 */
class Tttask: public ITask {
public:

/* 构造【通信任务】
 * msglen: 单条消息的长度
 * msgcnt: 消息伺服长度
 * callback: 回调函数  */
Tttask(const size_t msglen, size_t msgcnt, const sf_t callback);

/* 启动该通信伺服任务 */
void start() override;

/* 终止这个通信伺服任务 
 *
 * TODO: 研究一下要不要释放一些资源
 * TODO: 增加超时  */
void stop() override {
    if (this->isRunning())
        th->WaitClose();
}


/* 该任务是否在运行
 *
 * FIXME: 对START状态的处理还应该考虑
 * FIXME: 如果在STOP状态而没有stop()任务，可能会造成线程未关闭，一直占用资源？ */
bool isRunning() override;


private:
std::unique_ptr<Thread> th;	/* 可控线程指针			*/
std::unique_ptr<sf_t> callback;	/* 回调行为（接收之后）指针	*/
std::unique_ptr<Mq> mq;		/* 消息队列指针			*/
size_t msglen=0, msgcnt=0;	/* 消息的缓存规格		*/
std::string name;		/* 消息队列名（进程号） 	*/




/* 初始化通信伺服线程，
 * 初始化方法就是通过传入一些函数，
 * 构建线程 */
void init_thread();



};
}
