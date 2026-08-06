#pragma once

/** AsyncQueue — 通用异步批量发送队列（C++ 版，对应 Python 版 pymsg/pymsg/async_queue.py）
 *
 * 提供内存队列 + 后台线程的异步批量处理机制，适用于日志上报、指标采集、事件通知等
 * 非关键路径场景。业务线程 push() 非阻塞立即返回，后台线程攒批处理（批量阈值或超时），
 * 失败时通过错误回调通知，绝不阻塞或影响主业务流程。
 *
 * 设计要点（与 Python AsyncRpcQueue 对齐）：
 *   - 非阻塞入队：push() 立即返回，不等待网络 I/O
 *   - 批量发送：攒到 batch_size 条或超过 flush_interval 秒自动 flush
 *   - 背压保护：max_queue_size 限制内存队列长度，满时丢最旧数据并计数
 *   - 优雅关闭：shutdown() flush 剩余数据后停止后台线程
 *   - RAII：析构自动 shutdown()
 *   - 可插拔：通过 Handler 回调自定义批量处理逻辑（RPC 调用、写文件、发消息等）
 *
 * Usage:
 *   #include <us/AsyncQueue.hpp>
 *
 *   struct LogEntry { std::string service; std::string msg; };
 *
 *   pmc::net::AsyncQueue<LogEntry> queue(
 *       [](std::vector<LogEntry>&& batch) {
 *           for (auto& e : batch) { send_to_server(e); }
 *       },
 *       {50, 2.0, 10000, 0.5},
 *       [](const std::exception& e, std::vector<LogEntry>&&) {
 *           std::cerr << "[queue] send failed: " << e.what() << std::endl;
 *       }
 *   );
 *   queue.start();
 *   queue.push(LogEntry{"gateway", "started"});
 *   queue.shutdown();
 */

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <deque>
#include <exception>
#include <functional>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

namespace pmc {
namespace net {

// ============================================================================
// AsyncQueue 配置
// ============================================================================

struct AsyncQueueConfig {
    size_t batch_size     = 100;    ///< 批量发送阈值，达到此数量立即 flush
    double flush_interval = 2.0;    ///< 定时 flush 间隔（秒）
    size_t max_queue_size = 10000;  ///< 队列最大长度，0 表示无上限；超过时丢最旧数据
    double poll_timeout   = 0.1;    ///< 后台线程轮询超时（秒），越小关闭响应越快但CPU占用略高
};

// ============================================================================
// AsyncQueue<T>
// ============================================================================

/** 通用异步批量发送队列。
 *
 * @tparam T 队列元素类型，需支持移动构造（push 时会被 std::move 到 batch 中）。
 */
template <typename T>
class AsyncQueue {
public:
    /// 批量处理回调：接收一个元素批次的右值引用，处理完后 batch 被销毁。
    using Handler = std::function<void(std::vector<T>&& batch)>;

    /// 错误回调：接收异常和失败的批次（可用于重试或记录）。
    using ErrorHandler = std::function<void(const std::exception& e, std::vector<T>&& batch)>;

    // ---- 构造 / 析构 -------------------------------------------------------

    explicit AsyncQueue(Handler handler,
                        AsyncQueueConfig config = {},
                        ErrorHandler on_error = {})
        : _handler(std::move(handler)),
          _config(config),
          _on_error(std::move(on_error)),
          _running(false),
          _stop(false),
          _wakeup(false),
          _dropped(0) {}

    /// 析构函数：自动 shutdown()，保证队列中剩余数据被 flush。
    ~AsyncQueue() { shutdown(); }

    AsyncQueue(const AsyncQueue&) = delete;
    AsyncQueue& operator=(const AsyncQueue&) = delete;

    // ---- 生命周期 ----------------------------------------------------------

    /// 启动后台发送线程。幂等：已启动时无操作。
    void start() {
        std::lock_guard<std::mutex> lock(_mtx);
        if (_running) return;
        _stop = false;
        _wakeup = false;
        _running = true;
        _thread = std::thread(&AsyncQueue::_run, this);
    }

    /// 优雅关闭：flush 剩余数据后停止后台线程。幂等。
    void shutdown() {
        {
            std::lock_guard<std::mutex> lock(_mtx);
            if (!_running) return;
            _stop = true;
            _wakeup = true;
        }
        _cv.notify_all();
        if (_thread.joinable()) {
            _thread.join();
        }
        // 线程结束后，flush 队列中剩余的所有数据
        _flush_all();
        {
            std::lock_guard<std::mutex> lock(_mtx);
            _running = false;
        }
    }

    // ---- 入队 --------------------------------------------------------------

    /** 非阻塞入队一条数据。
     *
     * 若队列已关闭（shutdown 后），则丢弃数据并返回 false。
     * 若队列长度达到 max_queue_size，则丢弃最旧的数据并递增 dropped 计数。
     *
     * @return true 入队成功；false 队列已关闭。
     */
    bool push(T item) {
        {
            std::lock_guard<std::mutex> lock(_mtx);
            if (_stop) return false;
            if (_config.max_queue_size > 0 && _queue.size() >= _config.max_queue_size) {
                _queue.pop_front();
                ++_dropped;
            }
            _queue.push_back(std::move(item));
            _wakeup = true;
        }
        _cv.notify_one();
        return true;
    }

    // ---- 手动 flush --------------------------------------------------------

    /// 立即触发一次 flush，在调用线程同步处理当前队列中的所有数据。
    /// 对齐 Python：flush() 直接在调用线程执行 _send_batch()，阻塞直到处理完成。
    void flush() {
        _send_batch();
    }

    // ---- 状态查询 ----------------------------------------------------------

    /// 当前队列中待处理的数据量。
    size_t pending() const {
        std::lock_guard<std::mutex> lock(_mtx);
        return _queue.size();
    }

    /// 因队列满而丢弃的数据总数。
    size_t dropped() const {
        return _dropped.load();
    }

    /// 队列是否正在运行（已 start 且未 shutdown）。
    bool is_running() const {
        std::lock_guard<std::mutex> lock(_mtx);
        return _running;
    }

private:
    // ---- 后台线程主循环（对齐 Python AsyncRpcQueue._run）--------------------

    void _run() {
        auto last_flush = std::chrono::steady_clock::now();

        while (true) {
            bool should_stop = false;
            {
                std::unique_lock<std::mutex> lock(_mtx);
                _cv.wait_for(lock,
                    std::chrono::duration<double>(_config.poll_timeout),
                    [this] { return _stop || _wakeup; });
                _wakeup = false;  // 消费唤醒标志
                should_stop = _stop;
            }

            const auto now = std::chrono::steady_clock::now();
            const bool due = std::chrono::duration<double>(now - last_flush).count()
                           >= _config.flush_interval;

            size_t qsize = 0;
            {
                std::lock_guard<std::mutex> lock(_mtx);
                qsize = _queue.size();
            }

            // flush 条件：达到批量阈值 / 超时且有数据 / 停止时还有数据
            if (qsize >= _config.batch_size || (due && qsize > 0) || (should_stop && qsize > 0)) {
                _send_batch();
                last_flush = std::chrono::steady_clock::now();
            }

            if (should_stop) break;
        }
    }

    // ---- 取所有待处理数据并发送（对齐 Python：batch_size 是触发阈值，不是上限）-

    void _send_batch() {
        std::vector<T> batch;
        {
            std::lock_guard<std::mutex> lock(_mtx);
            if (_queue.empty()) return;
            batch.reserve(_queue.size());
            while (!_queue.empty()) {
                batch.push_back(std::move(_queue.front()));
                _queue.pop_front();
            }
        }

        try {
            _handler(std::move(batch));
        } catch (const std::exception& e) {
            if (_on_error) {
                try {
                    _on_error(e, std::move(batch));
                } catch (...) {
                    // 错误回调本身不应传播异常
                }
            }
        }
    }

    // ---- 关闭时 flush 所有剩余数据 ----------------------------------------

    void _flush_all() {
        while (true) {
            std::vector<T> batch;
            {
                std::lock_guard<std::mutex> lock(_mtx);
                if (_queue.empty()) break;
                batch.reserve(_queue.size());
                while (!_queue.empty()) {
                    batch.push_back(std::move(_queue.front()));
                    _queue.pop_front();
                }
            }
            try {
                _handler(std::move(batch));
            } catch (const std::exception& e) {
                if (_on_error) {
                    try {
                        _on_error(e, std::move(batch));
                    } catch (...) {}
                }
            }
        }
    }

    // ---- 成员 --------------------------------------------------------------

    Handler _handler;
    AsyncQueueConfig _config;
    ErrorHandler _on_error;

    mutable std::mutex _mtx;
    std::condition_variable _cv;
    std::deque<T> _queue;
    std::thread _thread;
    bool _running;
    bool _stop;
    bool _wakeup;         ///< 唤醒标志（push/shutdown 置 true，后台线程消费后置 false）

    std::atomic<size_t> _dropped;
};

} // namespace net
} // namespace pmc
