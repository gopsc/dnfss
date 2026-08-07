#pragma once

/** MsgServer — 基于 pymsg 协议的多客户端 TCP 服务器（C++ 版，对应 Python 版 pymsg.MsgServer）
 *
 * 提供 select-based 事件循环 + 可选线程池并发处理，支持 reply()/broadcast() 线程安全发送。
 *
 * 设计要点（与 Python 版对齐）：
 *   - select 事件循环，支持多客户端并发连接
 *   - workers=0 同步模式（回调在主线程执行），workers>0 线程池并发模式
 *   - 同一连接消息严格串行：回调执行期间暂停该连接 select 监听
 *   - reply()/broadcast() 经每连接发送锁串行化，防止帧交错
 *   - max_connections 限制活跃连接数，防止连接耗尽攻击
 *   - max_queue_size 限制线程池任务队列深度，慢 handler 时断开连接防 OOM
 *   - deadline 模式 recv 超时，防 Slowloris 攻击
 *   - stop() 可从任意线程（包括回调内部）安全调用，优雅关闭
 *   - worker 线程出错仅标记 failed，主循环统一清理连接，避免 fd 并发操作
 *
 * Usage:
 *   #include <us/MsgServer.hpp>
 *
 *   pmc::net::MsgServer server("0.0.0.0", 8003, {.workers=4});
 *   server.start([&](int fd, const std::string& addr, const std::string& msg) {
 *       server.reply(fd, "echo: " + msg);         // 回复发送者
 *       server.broadcast(addr + ": " + msg, fd);  // 广播给其他人
 *   });
 */

#include <us/Msg_Basic.hpp>
#include <us/Msg.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

namespace pmc {
namespace net {

// ============================================================================
// MsgServer 配置
// ============================================================================

struct MsgServerConfig {
    int    workers         = 0;        ///< 线程池大小；0 = 同步模式（回调在主线程执行）
    double recv_timeout    = 10.0;     ///< 接收单条消息的总超时（秒），防 Slowloris
    int    max_connections = 1000;     ///< 最大并发连接数，超过则拒绝新连接
    int    max_queue_size  = 1000;     ///< 线程池任务队列上限（workers>0 时），满时断开连接
    int    listen_backlog  = 10;       ///< listen() backlog
    double poll_timeout    = 0.1;      ///< select 轮询超时（秒），影响关闭响应速度
};

// ============================================================================
// MsgServer
// ============================================================================

/// 基于 pymsg 长度前缀协议的多客户端 TCP 服务器。
class MsgServer {
public:
    /// 消息回调签名：(客户端 fd, 客户端地址 "ip:port", 消息内容)
    using MessageCallback = std::function<void(int sockfd, const std::string& addr, const std::string& message)>;

    explicit MsgServer(const std::string& addr, int port, MsgServerConfig config = {})
        : _addr(addr), _port(port), _config(config), _server_fd(-1), _running(false) {
        detail::validatePort(port);
        if (config.workers < 0 || config.workers > 256) {
            throw MsgException("invalid workers: " + std::to_string(config.workers) + " (must be in [0, 256])");
        }
        detail::validateTimeout(config.recv_timeout);
        if (config.max_connections < 1 || config.max_connections > 100000) {
            throw MsgException("invalid max_connections: " + std::to_string(config.max_connections));
        }
        if (config.max_queue_size < 1 || config.max_queue_size > 1000000) {
            throw MsgException("invalid max_queue_size: " + std::to_string(config.max_queue_size));
        }
        if (config.listen_backlog < 1 || config.listen_backlog > 4096) {
            throw MsgException("invalid listen_backlog: " + std::to_string(config.listen_backlog));
        }
        if (config.poll_timeout <= 0 || config.poll_timeout > 5.0) {
            throw MsgException("invalid poll_timeout: " + std::to_string(config.poll_timeout));
        }
    }

    ~MsgServer() { stop(); }

    MsgServer(const MsgServer&) = delete;
    MsgServer& operator=(const MsgServer&) = delete;

    // ---- 启动（阻塞当前线程）------------------------------------------------

    /** 启动服务器，阻塞当前线程直到 stop() 被调用或发生致命错误。
     *
     * @param on_message 消息回调，签名 void(int fd, const string& addr, const string& msg)。
     *                   - 同步模式（workers=0）：回调在主线程执行，同一连接消息天然串行。
     *                   - 线程池模式（workers>0）：回调提交到线程池并发执行；同一连接
     *                     上的消息仍严格串行（回调完成前该连接暂停 select 监听）。
     *                   回调内可安全调用 reply()/broadcast()/stop()。
     */
    void start(MessageCallback on_message) {
        _on_message = std::move(on_message);
        _running = true;

        // 创建监听 socket
        _server_fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (_server_fd < 0) {
            throw MsgConnectionException(std::string("socket() failed: ") + ::strerror(errno));
        }

        int optval = 1;
        ::setsockopt(_server_fd, SOL_SOCKET, SO_REUSEADDR, &optval, sizeof(optval));

        struct sockaddr_in serv_addr {};
        serv_addr.sin_family = AF_INET;
        serv_addr.sin_port = htons(static_cast<uint16_t>(_port));
        if (::inet_pton(AF_INET, _addr.c_str(), &serv_addr.sin_addr) <= 0) {
            ::close(_server_fd);
            _server_fd = -1;
            throw MsgConnectionException("invalid bind address: " + _addr);
        }

        if (::bind(_server_fd, reinterpret_cast<struct sockaddr*>(&serv_addr), sizeof(serv_addr)) < 0) {
            int saved_errno = errno;
            ::close(_server_fd);
            _server_fd = -1;
            throw MsgConnectionException(std::string("bind() failed: ") + ::strerror(saved_errno));
        }

        if (::listen(_server_fd, _config.listen_backlog) < 0) {
            int saved_errno = errno;
            ::close(_server_fd);
            _server_fd = -1;
            throw MsgConnectionException(std::string("listen() failed: ") + ::strerror(saved_errno));
        }

        // 启动线程池（如果配置了 workers）
        std::vector<std::thread> pool_threads;
        if (_config.workers > 0) {
            _pool_running = true;
            pool_threads.reserve(_config.workers);
            for (int i = 0; i < _config.workers; ++i) {
                pool_threads.emplace_back(&MsgServer::_worker_loop, this);
            }
        }

        // 主事件循环
        try {
            _event_loop();
        } catch (...) {
            // 确保线程池和连接被清理
            _running = false;
            _shutdown_pool(pool_threads);
            _close_all_clients();
            if (_server_fd >= 0) { ::close(_server_fd); _server_fd = -1; }
            throw;
        }

        _shutdown_pool(pool_threads);
        _close_all_clients();
        if (_server_fd >= 0) { ::close(_server_fd); _server_fd = -1; }
    }

    // ---- 停止 --------------------------------------------------------------

    /// 停止服务器（可从任意线程调用，包括回调内部）。
    /// 关闭监听 socket 以解除 select 阻塞，事件循环退出前清理所有连接。
    void stop() noexcept {
        bool expected = true;
        if (!_running.compare_exchange_strong(expected, false)) return;
        if (_server_fd >= 0) {
            ::shutdown(_server_fd, SHUT_RDWR);
            ::close(_server_fd);
            _server_fd = -1;
        }
    }

    // ---- 发送（线程安全）----------------------------------------------------

    /** 向指定客户端发送一条应答消息（线程安全）。
     *
     * 经每连接发送锁串行化，workers>0 时可在回调线程安全调用。
     * 如果连接已断开则静默忽略（fail-safe，不抛异常）。
     */
    void reply(int sockfd, const std::string& msg) {
        std::shared_ptr<std::mutex> send_lock;
        {
            std::lock_guard<std::mutex> lock(_clients_mtx);
            auto it = _send_locks.find(sockfd);
            if (it == _send_locks.end()) return;  // 连接已断开
            send_lock = it->second;
        }
        std::lock_guard<std::mutex> lock(*send_lock);
        try {
            pmsg_send(sockfd, msg);
        } catch (const MsgException&) {
            // 连接可能已断开，静默忽略
        }
    }

    /** 向所有已连接客户端广播消息（线程安全）。
     *
     * @param msg         要广播的消息。
     * @param except_fd   排除的 fd（通常是发送者自己），默认 -1 表示不排除。
     */
    void broadcast(const std::string& msg, int except_fd = -1) {
        // 先拷贝一份 fd 列表，避免持锁期间发送
        std::vector<std::pair<int, std::shared_ptr<std::mutex>>> targets;
        {
            std::lock_guard<std::mutex> lock(_clients_mtx);
            targets.reserve(_clients.size());
            for (auto& [fd, addr] : _clients) {
                if (fd == except_fd) continue;
                auto it = _send_locks.find(fd);
                if (it != _send_locks.end()) {
                    targets.emplace_back(fd, it->second);
                }
            }
        }
        for (auto& [fd, send_lock] : targets) {
            std::lock_guard<std::mutex> slock(*send_lock);
            try {
                pmsg_send(fd, msg);
            } catch (const MsgException&) {
                // 发送失败，标记为 failed 由主循环清理
                std::lock_guard<std::mutex> flk(_failed_mtx);
                _failed.insert(fd);
            }
        }
    }

    // ---- 状态查询 ----------------------------------------------------------

    /// 当前已连接客户端数量。
    size_t client_count() const {
        std::lock_guard<std::mutex> lock(_clients_mtx);
        return _clients.size();
    }

private:
    // ---- 内部：线程池 -------------------------------------------------------

    struct Task {
        int fd;
        std::string addr;
        std::string message;
        bool needs_recv = false;  // true: worker 负责 pmsg_recv；false: 消息已就绪
    };

    void _worker_loop() {
        while (true) {
            Task task;
            bool has_task = false;
            {
                std::unique_lock<std::mutex> lock(_pool_mtx);
                _pool_cv.wait(lock, [this] { return !_pool_running || !_pool_queue.empty(); });
                if (!_pool_queue.empty()) {
                    task = std::move(_pool_queue.front());
                    _pool_queue.pop();
                    has_task = true;
                } else if (!_pool_running) {
                    return;  // 队列空且已停止，退出
                }
            }
            if (!has_task) continue;

            // workers > 0 模式下，消息接收在 worker 线程中完成（防事件循环阻塞 CWE-400）
            if (task.needs_recv) {
                try {
                    task.message = pmsg_recv(task.fd, _config.recv_timeout);
                } catch (const MsgException&) {
                    std::lock_guard<std::mutex> flk(_failed_mtx);
                    _failed.insert(task.fd);
                    std::lock_guard<std::mutex> blk(_busy_mtx);
                    _busy.erase(task.fd);
                    continue;
                }
            }

            // 执行回调
            bool failed = false;
            try {
                _on_message(task.fd, task.addr, std::move(task.message));
            } catch (const MsgConnectionException&) {
                failed = true;
            } catch (const std::exception& e) {
                std::fprintf(stderr, "[MsgServer] callback error on fd=%d: %s\n",
                             task.fd, e.what());
                std::fflush(stderr);
                failed = true;
            } catch (...) {
                failed = true;
            }
            if (failed) {
                std::lock_guard<std::mutex> flk(_failed_mtx);
                _failed.insert(task.fd);
            }
            // 清除 busy 标记，恢复 select 监听
            {
                std::lock_guard<std::mutex> lock(_busy_mtx);
                _busy.erase(task.fd);
            }
        }
    }

    void _shutdown_pool(std::vector<std::thread>& pool_threads) {
        // 停止接收新任务，唤醒所有 worker 排空队列后退出
        _pool_running = false;
        _pool_cv.notify_all();
        for (auto& t : pool_threads) {
            if (t.joinable()) t.join();
        }
        pool_threads.clear();
        // worker 已排空队列，此时 busy 集合中的任务都已完成
        // 任何仍标记为 failed 的连接由 _close_all_clients 统一清理
    }

    // ---- 内部：主事件循环 ---------------------------------------------------

    void _event_loop() {
        while (_running) {
            // 清理 failed 连接
            _reap_failed();

            // 构建 fd_set：server_fd + 非 busy 的客户端 fd
            fd_set readfds, exceptfds;
            FD_ZERO(&readfds);
            FD_ZERO(&exceptfds);
            int maxfd = -1;

            if (_server_fd >= 0) {
                FD_SET(_server_fd, &readfds);
                maxfd = _server_fd;
            }

            std::vector<int> watched_clients;
            {
                std::lock_guard<std::mutex> clk(_clients_mtx);
                std::lock_guard<std::mutex> blk(_busy_mtx);
                watched_clients.reserve(_clients.size());
                for (auto& [fd, addr] : _clients) {
                    if (_busy.count(fd)) continue;  // 回调处理中，暂停监听
                    FD_SET(fd, &readfds);
                    FD_SET(fd, &exceptfds);
                    if (fd > maxfd) maxfd = fd;
                    watched_clients.push_back(fd);
                }
            }

            if (maxfd < 0) {
                // 没有任何 fd 可监听（server_fd 已关闭且无客户端），退出
                break;
            }

            struct timeval tv;
            tv.tv_sec = 0;
            tv.tv_usec = static_cast<long>(_config.poll_timeout * 1'000'000.0);

            int ready = ::select(maxfd + 1, &readfds, nullptr, &exceptfds, &tv);
            if (ready < 0) {
                if (errno == EINTR) continue;
                break;  // EBADF 等致命错误
            }
            if (ready == 0) continue;

            // 处理异常 fd
            for (int fd : watched_clients) {
                if (FD_ISSET(fd, &exceptfds)) {
                    _remove_client(fd);
                }
            }

            // 处理新连接
            if (_server_fd >= 0 && FD_ISSET(_server_fd, &readfds)) {
                _accept_client();
            }

            // 处理客户端消息
            for (int fd : watched_clients) {
                if (FD_ISSET(fd, &readfds)) {
                    // 检查 fd 是否仍在客户端列表中（可能已被异常处理移除）
                    {
                        std::lock_guard<std::mutex> lock(_clients_mtx);
                        if (!_clients.count(fd)) continue;
                    }
                    _handle_client(fd);
                }
            }
        }
    }

    void _accept_client() {
        struct sockaddr_in client_addr {};
        socklen_t addrlen = sizeof(client_addr);
        int client_fd = ::accept(_server_fd, reinterpret_cast<struct sockaddr*>(&client_addr), &addrlen);
        if (client_fd < 0) {
            // EINVAL/EBADF: server_fd 已被 stop() 关闭；其他错误忽略
            return;
        }
        if (client_fd >= FD_SETSIZE) { ::close(client_fd); return; }

        // 检查连接数上限
        {
            std::lock_guard<std::mutex> lock(_clients_mtx);
            if (static_cast<int>(_clients.size()) >= _config.max_connections) {
                ::close(client_fd);
                return;
            }
            char ip_str[INET_ADDRSTRLEN];
            ::inet_ntop(AF_INET, &client_addr.sin_addr, ip_str, sizeof(ip_str));
            std::string addr = std::string(ip_str) + ":" + std::to_string(ntohs(client_addr.sin_port));
            _clients[client_fd] = addr;
            _send_locks[client_fd] = std::make_shared<std::mutex>();
        }

        // 禁用 Nagle 算法，降低小包延迟
        int flag = 1;
        ::setsockopt(client_fd, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));
    }

    void _handle_client(int fd) {
        std::string addr;
        {
            std::lock_guard<std::mutex> lock(_clients_mtx);
            auto it = _clients.find(fd);
            if (it == _clients.end()) return;
            addr = it->second;
        }

        if (_config.workers == 0) {
            // 同步模式：在主线程接收并执行回调
            std::string message;
            try {
                message = pmsg_recv(fd, _config.recv_timeout);
            } catch (const MsgException&) {
                _remove_client(fd);
                return;
            }
            try {
                _on_message(fd, addr, std::move(message));
            } catch (const MsgConnectionException&) {
                _remove_client(fd);
            } catch (const std::exception& e) {
                std::fprintf(stderr, "[MsgServer] callback error on fd=%d: %s\n", fd, e.what());
                std::fflush(stderr);
                _remove_client(fd);
            }
            return;
        }

        // 线程池模式：标记 busy，提交任务（由 worker 负责 recv + callback，
        // 事件循环线程不阻塞接收，防 Slowloris DoS CWE-400）
        {
            std::lock_guard<std::mutex> blk(_busy_mtx);
            _busy.insert(fd);
        }
        {
            std::lock_guard<std::mutex> plk(_pool_mtx);
            if (static_cast<int>(_pool_queue.size()) >= _config.max_queue_size) {
                // 队列已满：拒绝并断开连接（防 OOM）
                std::lock_guard<std::mutex> blk(_busy_mtx);
                _busy.erase(fd);
                _remove_client(fd);
                return;
            }
            _pool_queue.push(Task{fd, std::move(addr), {}, true});
        }
        _pool_cv.notify_one();
    }

    void _reap_failed() {
        std::unordered_set<int> failed;
        {
            std::lock_guard<std::mutex> flk(_failed_mtx);
            failed.swap(_failed);
        }
        for (int fd : failed) {
            {
                std::lock_guard<std::mutex> blk(_busy_mtx);
                _busy.erase(fd);
            }
            _remove_client(fd);
        }
    }

    void _remove_client(int fd) {
        std::shared_ptr<std::mutex> send_lock;
        {
            std::lock_guard<std::mutex> clk(_clients_mtx);
            if (_clients.erase(fd) == 0) return;  // 已被移除，防止 double-close
            auto it = _send_locks.find(fd);
            if (it != _send_locks.end()) {
                send_lock = it->second;
                _send_locks.erase(it);
            }
        }
        // 持有 send_lock 后再关闭 fd：防止 broadcast/reply 在 fd 关闭后向 OS
        // 复用的新连接发送数据（CWE-362）。
        if (send_lock) {
            std::lock_guard<std::mutex> slk(*send_lock);
            ::close(fd);
        } else {
            ::close(fd);
        }
    }

    void _close_all_clients() {
        std::vector<std::pair<int, std::shared_ptr<std::mutex>>> to_close;
        {
            std::lock_guard<std::mutex> clk(_clients_mtx);
            to_close.reserve(_clients.size());
            for (auto& [fd, addr] : _clients) {
                auto it = _send_locks.find(fd);
                if (it != _send_locks.end()) {
                    to_close.emplace_back(fd, it->second);
                } else {
                    to_close.emplace_back(fd, nullptr);
                }
            }
            _clients.clear();
            _send_locks.clear();
        }
        // 持有每个连接的 send_lock 后再 close(fd)：防止 stop()/析构时
        // reply/broadcast 正在发送，导致数据发到 OS 复用的新连接（CWE-362）
        for (auto& [fd, send_lock] : to_close) {
            if (send_lock) {
                std::lock_guard<std::mutex> slk(*send_lock);
                ::shutdown(fd, SHUT_RDWR);
                ::close(fd);
            } else {
                ::shutdown(fd, SHUT_RDWR);
                ::close(fd);
            }
        }
        std::lock_guard<std::mutex> blk(_busy_mtx);
        _busy.clear();
        std::lock_guard<std::mutex> flk(_failed_mtx);
        _failed.clear();
    }

    // ---- 成员 --------------------------------------------------------------

    std::string _addr;
    int _port;
    MsgServerConfig _config;
    int _server_fd;
    std::atomic<bool> _running;
    MessageCallback _on_message;

    // 客户端管理
    mutable std::mutex _clients_mtx;
    std::unordered_map<int, std::string> _clients;           ///< fd -> "ip:port"
    std::unordered_map<int, std::shared_ptr<std::mutex>> _send_locks;  ///< fd -> 发送锁

    // 同连接串行
    std::mutex _busy_mtx;
    std::unordered_set<int> _busy;                           ///< 回调执行中的 fd

    // 失败连接（worker 线程标记，主循环清理）
    std::mutex _failed_mtx;
    std::unordered_set<int> _failed;

    // 线程池
    std::atomic<bool> _pool_running{false};
    std::mutex _pool_mtx;
    std::condition_variable _pool_cv;
    std::queue<Task> _pool_queue;
};

} // namespace net
} // namespace pmc
