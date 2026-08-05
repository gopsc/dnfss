# libth 安全审计报告

**审计日期**: 2026-08-04  
**审计范围**: `libth/inc/us/`（`Thread.hpp`、`Fsm.hpp`、`ITask.hpp`、`Cv_wait.hpp`）、`libth/src/Thread.cpp`  
**代码版本**: dev_qing 分支

---

## 修复进度

| 优先级 | 总数 | 已修复 | 部分修复 | 未修复 |
|--------|------|--------|----------|--------|
| P0 | 0 | 0 | 0 | 0 |
| P1 | 1 | 0 | 0 | 1 |
| P2 | 4 | 0 | 0 | 4 |
| P3 | 2 | 0 | 0 | 2 |
| **合计** | **7** | **0** | **0** | **7** |

> 说明：libth 为底层基础线程库，无对外网络攻击面，风险集中在**同步原语缺失超时导致的潜在永久阻塞（DoS）**与**内存/生命周期健壮性**。以下问题均为设计层面缺陷，源码中已有 FIXME/README 标注，尚未修复。

---

## 🟠 P1 — 高危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P1-1 | `Cv_wait` 无超时机制，可永久阻塞 | ❌ 未修复 | `Cv_wait.hpp` L21-25 `Wait()` 依赖 `cv.wait(lock, [this]{return flag;})`，无超时与超时中断。若 `WakeCv()` 永不触发（如等待的信号/事件丢失），调用线程将**永久阻塞**。被 `core` 的 `main.cpp` 直接使用，一旦误用即造成服务线程卡死（DoS）。源码 FIXME 已标注"仅支持单次等待-唤醒、可能导致永久堵塞，应改用 `std::future`/`std::promise`" |

---

## 🟡 P2 — 中危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P2-1 | `Thread::suspend()` 无超时 | ❌ 未修复 | `Thread.cpp` L33-35 透传 `Fsm::suspend()`（`Fsm.hpp` L61-65），阻塞等待状态变更却无超时。若其他线程永不调用 `set()`/`run()`/`stop()`/`shut()`，当前线程永久挂起，造成线程资源耗尽 |
| P2-2 | 异常回退后未释放资源，可能内存泄漏 | ❌ 未修复 | `Thread.cpp` L132-135 `catch(std::exception)` 后仅 `stop()` 回退 STOP，不触发清理。若 `LoopEvent` 在循环中反复抛异常，会反复进入 STOP→START→RUNNING 循环，每次泄漏部分资源，长期运行内存持续增长。README 已注明"catch 后继续运行可能造成内存泄漏" |
| P2-3 | `wake()`/`WaitStart()` 死锁约束脆弱 | ❌ 未修复 | `Thread.cpp` L87-92 `WaitStart()` 先持有 `mtx_th` 再调 `wake()`，而 `wake()`（L38-45）内部禁止再次加锁。该约束依赖调用方自觉遵守，一旦在 `WakeEvent` 回调中误加锁即死锁，缺少防御性校验（如 `std::recursive_mutex` 或断言） |
| P2-4 | `WaitStart()` 存在潜在死锁/异常风险 | ❌ 未修复 | `Thread.hpp` L113 注释"据说该函数有问题可能导致异常"。`WaitStart()` 在 `wake()` 后置 `ready=false` 再 `cv.wait`，若 `WakeEvent` 中未调用 `run()`/`stop()`/`shut()`，`ready` 永不置位，调用线程永久阻塞 |

---

## 🔵 P3 — 低危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P3-1 | `Activate()` 重复调用静默返回 | ❌ 未修复 | `Thread.cpp` L80 若 `th` 已存在直接 `return`，不报错也不提示。调用方误以为重复激活有效，实际第二个线程未创建，状态不一致且无任何反馈 |
| P3-2 | `Cv_wait` 复用后行为异常 | ❌ 未修复 | `Cv_wait.hpp` L19 `flag` 非原子且复用后残留 `true`，导致后续 `Wait()` 立即返回而不阻塞。README 已注明"仅支持单次使用，复用导致 `Wait()` 不阻塞" |

---

## ✅ 已有安全机制

1. ✅ **线程安全状态机**: `Fsm` 用 `std::mutex` 保护状态读写（`Fsm.hpp` L31-34、L50-57），避免并发设置状态的数据竞争
2. ✅ **两阶段构造防竞态**: `Activate()` 与构造函数分离（`Thread.hpp` L110），避免 `std::thread` 在对象构造完成前访问未初始化成员
3. ✅ **异常捕获防进程崩溃**: `main()` 循环捕获 `std::exception` 回退 STOP（`Thread.cpp` L108-135），单线程异常不会拖垮整个进程
4. ✅ **RAII 资源管理**: 使用 `std::unique_ptr<std::thread>`、`std::unique_ptr<f_t>`（`Thread.hpp` L128-146），避免裸指针管理
5. ✅ **复制构造禁用**: `Thread(const Thread&) = delete`（`Thread.hpp` L41），防止线程对象被误拷贝造成双重管理
6. ✅ **回调组合优于继承**: 持 `std::function` 回调（`Thread.hpp` L24），接口收敛、避免子类化的状态泄漏

---

## 修复路线图

### 短期（72小时）
- [ ] P1-1 `Cv_wait` 增加超时等待（`wait_for` + 超时返回），或按源码 FIXME 改用 `std::future`/`std::promise`
- [ ] P2-1 `Fsm::suspend()`/`Thread::suspend()` 增加超时参数，避免永久挂起

### 中期
- [ ] P2-2 异常回退时统一触发 `ClearEvent` 清理，避免长期运行内存泄漏
- [ ] P2-3 将 `wake()` 的锁约束改为可防御形式（如 `std::recursive_mutex` 或加锁断言诊断）
- [ ] P2-4 `WaitStart()` 增加超时与状态校验，避免 `WakeEvent` 未推进状态时永久阻塞

### 长期
- [ ] P3-1 `Activate()` 重复调用改为抛异常或返回错误码，避免静默失败
- [ ] P3-2 `Cv_wait` 的 `flag` 改为原子变量并支持复用，或明确废弃并替换

---

## 代码位置索引

| 功能 | 文件 | 行号 |
|------|------|------|
| `Cv_wait::Wait()` 无超时 | `inc/us/Cv_wait.hpp` | L21-25 |
| `Cv_wait::flag` 非原子/复用残留 | `inc/us/Cv_wait.hpp` | L18-19 |
| `Fsm::suspend()` 无超时 | `inc/us/Fsm.hpp` | L61-65 |
| `Thread::suspend()` 透传 | `src/Thread.cpp` | L33-35 |
| `Thread::WaitStart()` 唤醒等待 | `src/Thread.cpp` | L87-92 |
| `Thread::wake()` 禁止加锁 | `src/Thread.cpp` | L38-45 |
| `Thread::Activate()` 重复调用静默返回 | `src/Thread.cpp` | L76-85 |
| `Thread::main()` 异常回退 | `src/Thread.cpp` | L103-138 |
| `Thread` 复制构造禁用 | `inc/us/Thread.hpp` | L41 |
| `Thread` 两阶段构造 | `inc/us/Thread.hpp` | L110 |