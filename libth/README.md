# libth — UniServe 线程库

C++20 轻量级多线程控制库，为 UniServe Framework 提供线程抽象与状态机驱动的执行模型。由 `core`（PMC 进程管理器）的 `HttpTask` 和 `ProcessTask` 直接依赖。

命名空间 `qing`，库文件 `libus_th.so`，头文件安装至 `/usr/include/us/`。

## 架构

```
┌──────────┐     ┌───────────────────────────────┐
│  ITask   │     │           Thread              │
│ (接口)   │     │  持有 Fsm + std::thread       │
│ start()  │     │                               │
│ stop()   │     │  set_stop / set_wake /        │
│ isRunning│     │  set_loop / set_clear         │
└────┬─────┘     │  Activate / WaitStart /       │
     │ 实现      │  WaitClose                    │
     ▼           │                               │
  HttpTask       │  main(): "回字形" 执行循环      │
  ProcessTask    └──────────────┬────────────────┘
  (位于 core)                   │ 持有
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                  ▼
        ┌──────────┐    ┌────────────┐    ┌──────────┐
        │   Fsm    │    │  Cv_wait   │    │  Thread  │
        │ 状态机   │    │ 条件等待   │    │  .cpp    │
        │ SHUT/STOP│    │ Wait()     │    │ 实现文件 │
        │ START/   │    │ WakeCv()   │    │          │
        │ RUNNING  │    │            │    │          │
        └──────────┘    └────────────┘    └──────────┘
```

## 类与 API

### ITask — 任务接口

纯虚基类，定义可运行任务的契约。下游模块（core 中的 HttpTask、ProcessTask）继承实现。

```cpp
class ITask {
    virtual void start() = 0;       // 启动任务
    virtual void stop() = 0;        // 停止任务
    virtual bool isRunning() = 0;   // 查询运行状态
};
```

### Fsm — 线程安全有限状态机

4 个状态：`SHUT(-1)` → `STOP(0)` → `START(1)` → `RUNNING(2)`。Thread 通过 Fsm 驱动其执行流程。

| 方法 | 说明 |
|------|------|
| `check()` | 线程安全地读取当前状态 |
| `set(Stat)` | 加锁设置状态 + 唤醒阻塞线程 |
| `set_without_lock(Stat)` | 不加锁设置（用于临界区内） |
| `set_without_notify(Stat)` | 加锁但不唤醒条件变量 |
| `suspend()` | 阻塞当前线程直至状态变更 |

初始状态为 `STOP`。

### Thread — 线程抽象

基于回调组合（composition over inheritance），持有 `Fsm` 和 `std::thread`。执行模型为"回字形"循环：

```
while (状态 != SHUT) {
    ① STOP:   循环调用 StopEvent 回调（空闲轮询）
    ② START:  单次调用 WakeEvent 回调（启动）
    ③ RUNNING:循环调用 LoopEvent 回调（工作循环）
    ④ 退出后: 调用 ClearEvent 回调（清理）
}
异常捕获：loop 内抛出 std::exception 自动回退到 STOP，进程不崩溃。
```

**生命周期：两阶段构造。** 构造函数只初始化成员，`Activate()` 才真正创建 `std::thread`——避免线程在对象构造完成前启动的竞态条件。

| 方法 | 说明 |
|------|------|
| `set_stop(f_t)` | 设置 STOP 状态回调 |
| `set_wake(f_t)` | 设置 START 状态回调 |
| `set_loop(f_t)` | 设置 RUNNING 状态回调 |
| `set_clear(f_t)` | 设置清理回调 |
| `Activate()` | 创建 std::thread 并启动 main() |
| `WaitStart()` | 唤醒线程并阻塞至其进入 RUNNING |
| `WaitClose()` | 关闭线程并 join 等待退出 |
| `wake()` | 状态 → START（不可持有锁，否则死锁） |
| `stop()` | 状态 → STOP |
| `shut()` | 状态 → SHUT |
| `run()` | 状态 → RUNNING |
| `check()` | 查询当前状态 |
| `suspend()` | 阻塞等待状态变更 |

回调类型：`using f_t = std::function<void(Thread&)>;`

### Cv_wait — 条件变量等待工具

简单的单次等待-唤醒工具，用于线程间同步。

| 方法 | 说明 |
|------|------|
| `Wait()` | 阻塞当前线程至 WakeCv() 被调用 |
| `WakeCv()` | 唤醒所有等待线程 |

> **已知限制**：仅支持单次等待-唤醒周期，不可复用。缺少超时机制，可能永久阻塞。代码中标注建议改用 `std::future`/`std::promise`。core 的 `main.cpp` 使用此类。

## 构建与安装

```bash
# 一键构建 + 安装（需 sudo）
bash _set.sh

# 或手动执行
make            # 编译 libus_th.so
make install    # 安装头文件到 /usr/include/us/ + .so 到 /lib/
make uninstall  # 卸载
make clear      # 清理构建产物
```

安装后使用：

```cpp
#include <us/Thread.hpp>
#include <us/Fsm.hpp>
#include <us/ITask.hpp>
#include <us/Cv_wait.hpp>
```

链接：`g++ -std=c++20 ... -lus_th`

## 使用示例

```cpp
#include <us/Thread.hpp>
#include <iostream>

int main() {
    qing::Thread worker;
    bool done = false;

    // STOP 状态回调：空闲等待
    worker.set_stop([&](qing::Thread& t) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    });

    // START 状态回调：一次性初始化
    worker.set_wake([&](qing::Thread& t) {
        std::cout << "worker started" << std::endl;
        t.run();  // 进入 RUNNING
    });

    // RUNNING 状态回调：工作循环
    worker.set_loop([&](qing::Thread& t) {
        std::cout << "working..." << std::endl;
        std::this_thread::sleep_for(std::chrono::seconds(1));
        done = true;
        t.stop();  // 回到 STOP
    });

    // 清理回调
    worker.set_clear([](qing::Thread& t) {
        std::cout << "cycle cleaned up" << std::endl;
    });

    worker.Activate();
    worker.WaitStart();  // 阻塞至 RUNNING

    while (!done) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    worker.WaitClose();
    return 0;
}
```

## 依赖

- C++20（`g++ --std=c++20`）
- 纯标准库，无第三方依赖
- 线程库需链接 `-pthread`

## 设计要点

- **回调组合优于继承**。2025 年 6 月从派生改为持有 `std::function` 回调，Thread 不再需要子类化。
- **两阶段构造**。构造与 `Activate()` 分离，防止 `std::thread` 在对象构造完成前访问未初始化的成员。
- **异常安全**。main() 循环捕获 `std::exception` 后回退到 STOP 而非崩溃。注意：catch 后继续运行可能造成内存泄漏。
- **wake() 不可加锁**。`WaitStart()` 先持有 `mtx_th` 再调用 `wake()`，若 `wake()` 内部也尝试获取同一锁会造成死锁。
- **Fsm 提供两套条件变量**。Fsm 内部有一套 `cv` 用于 `suspend()`，Thread 自身另有一套 `cv` 用于 `WaitStart()`/`WaitClose()`，互不干扰。

## 注意事项

- Cv_wait 仅支持单次使用，复用时 `flag` 残留为 `true` 会导致 `Wait()` 立即返回而不阻塞。
- Fsm 的 `suspend()` 等待的是 Fsm 内部的 `ready` 标志，由 `set()` 触发；Thread 的 `WaitStart()` 等待的是 Thread 内部的 `ready`，由 `run()`/`stop()`/`shut()` 触发。两者独立。
- `Activate()` 不可重复调用（内部有空指针检查），但不会报错，静默返回。
- 析构函数为虚函数，允许安全的多态删除。

## 安全审计

本模块安全审计文档见 `../docs/security/libth_SECURITY_AUDIT_20260804.md`，包含已知设计缺陷与修复路线图。

## 许可证

MIT License
