#pragma once

/** pymsg C++ 实现 — Length-prefixed variable-length socket message framing.
 *
 * Protocol: [4-byte big-endian uint32_t length][UTF-8 payload]
 *
 * 对应 Python 版 pymsg/pymsg/__init__.py，提供：
 *   - pmsg_send() / pmsg_recv()  底层协议收发函数
 *   - MsgClient                   短连接 send / call / 长连接 listen 客户端
 *
 * Usage:
 *   #include <us/Msg.hpp>
 *
 *   pmc::net::MsgClient client("127.0.0.1", 8005, "gateway");
 *   std::string resp = client.call(R"({"sql":"SELECT 1"})", 5.0);
 *   client.send("hello");
 */

#include <us/Msg_Basic.hpp>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <functional>
#include <string>
#include <thread>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/types.h>
#include <unistd.h>

// macOS 没有 MSG_NOSIGNAL，用 0 替代（内核不会发 SIGPIPE）
#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

namespace pmc {
namespace net {

// ============================================================================
// Low-level protocol: 4-byte big-endian length prefix + UTF-8 payload
// ============================================================================

namespace detail {

using namespace msg_constants;

// ---- UTF-8 合法性校验（对齐 Python decode('utf-8')）------------------------ [libmsg-audit P2-4]
// 防御性校验：协议声明载荷为 UTF-8，非法字节序列表示协议错误或攻击，
// 直接抛 MsgConnectionException 断开连接（拒绝 overlong / 代理对 / 超 U+10FFFF）。
inline bool isValidUtf8(const char* data, size_t len) noexcept {
    const unsigned char* bytes = reinterpret_cast<const unsigned char*>(data);
    size_t i = 0;
    while (i < len) {
        unsigned char c = bytes[i];
        int num_extra;
        uint32_t codepoint;
        if (c <= 0x7F) {
            ++i;
            continue;
        } else if ((c & 0xE0) == 0xC0) {
            num_extra = 1;
            codepoint = c & 0x1F;
        } else if ((c & 0xF0) == 0xE0) {
            num_extra = 2;
            codepoint = c & 0x0F;
        } else if ((c & 0xF8) == 0xF0) {
            num_extra = 3;
            codepoint = c & 0x07;
        } else {
            return false;
        }
        if (i + num_extra >= len) return false;
        for (int j = 0; j < num_extra; ++j) {
            ++i;
            if ((bytes[i] & 0xC0) != 0x80) return false;
            codepoint = (codepoint << 6) | (bytes[i] & 0x3F);
        }
        ++i;
        // 拒绝 overlong 编码、代理对、超 U+10FFFF
        if (num_extra == 1 && codepoint < 0x80) return false;                    // 2字节序列应>=U+0080
        if (num_extra == 2 && (codepoint < 0x800 || (codepoint >= 0xD800 && codepoint <= 0xDFFF))) return false;  // 3字节序列>=U+0800且非代理对
        if (num_extra == 3 && (codepoint < 0x10000 || codepoint > 0x10FFFF)) return false;  // 4字节序列U+10000~U+10FFFF
    }
    return true;
}

// ---- 参数校验辅助 -------------------------------------------------------- [libmsg-audit P2-3]
// validateTimeout / validatePort：统一拒绝非法 timeout（(0,300]s）与 port（[1,65535]），
// 防止 0/负值导致 deadline 立即到期、极大值绕过 Slowloris 防护、port 越界 htons 截断。
inline void validateTimeout(double timeout_sec) {
    if (timeout_sec <= 0 || timeout_sec > 300.0) {  // 最大 5 分钟
        throw MsgException("invalid timeout: " + std::to_string(timeout_sec) + " seconds (must be in (0, 300])");
    }
}

inline void validatePort(int port) {
    if (port < 1 || port > 65535) {
        throw MsgConnectionException("invalid port: " + std::to_string(port) + " (must be in [1, 65535])");
    }
}

// ---- 4-byte big-endian 编码 / 解码 ----------------------------------------

inline void writeUint32BE(char *buf, uint32_t val) noexcept {
    buf[0] = static_cast<char>((val >> 24) & 0xFFu);
    buf[1] = static_cast<char>((val >> 16) & 0xFFu);
    buf[2] = static_cast<char>((val >> 8) & 0xFFu);
    buf[3] = static_cast<char>(val & 0xFFu);
}

inline uint32_t readUint32BE(const char *buf) noexcept {
    return (static_cast<uint32_t>(static_cast<unsigned char>(buf[0])) << 24) |
           (static_cast<uint32_t>(static_cast<unsigned char>(buf[1])) << 16) |
           (static_cast<uint32_t>(static_cast<unsigned char>(buf[2])) << 8) |
           (static_cast<uint32_t>(static_cast<unsigned char>(buf[3])));
}

// ---- socket 超时设置 ------------------------------------------------------

inline void setSockTimeout(int sockfd, double seconds) {
    struct timeval tv {};
    tv.tv_sec = static_cast<time_t>(seconds);
    tv.tv_usec =
        static_cast<long>((seconds - static_cast<double>(tv.tv_sec)) * 1'000'000.0);
    ::setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    ::setsockopt(sockfd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

// ---- 精确读取 n 字节 ------------------------------------------------------

inline void recvExact(int sockfd, char *buf, size_t n) {
    size_t received = 0;
    while (received < n) {
        ssize_t r = ::recv(sockfd, buf + received, n - received, 0);
        if (r == 0) {
            throw MsgConnectionException(
                "connection closed: expected " + std::to_string(n) +
                " bytes, got " + std::to_string(received));
        }
        if (r < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                throw MsgTimeoutException("recv timed out");
            }
            throw MsgConnectionException(
                "recv error: " + std::string(::strerror(errno)));
        }
        received += static_cast<size_t>(r);
    }
}

// ---- deadline 模式精确读取 n 字节（总超时，防 Slowloris）------------------- [libmsg-audit P1-1]
// 与 Python 版 _recv_exact 对齐：每次 select 前按剩余时间收紧超时，
// header+payload 共享同一个 deadline，总接收时间不超过 timeout_sec，
// 防止单连接以不完整帧头长期占用接收线程（Slowloris 变体，CWE-400）。

inline void recvExactDeadline(int sockfd, char *buf, size_t n,
                              const std::chrono::steady_clock::time_point& deadline) {
    size_t received = 0;
    while (received < n) {
        auto now = std::chrono::steady_clock::now();
        if (now >= deadline) {
            throw MsgTimeoutException("recv timed out");
        }
        auto remaining_dur = std::chrono::duration_cast<std::chrono::microseconds>(deadline - now);
        double remaining = static_cast<double>(remaining_dur.count()) / 1'000'000.0;
        if (remaining < 0.001) remaining = 0.001;  // 最小 1ms

        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(sockfd, &rfds);
        struct timeval tv;
        tv.tv_sec = static_cast<time_t>(remaining);
        tv.tv_usec = static_cast<long>((remaining - tv.tv_sec) * 1'000'000.0);

        int ready = ::select(sockfd + 1, &rfds, nullptr, nullptr, &tv);
        if (ready < 0) {
            if (errno == EINTR) continue;
            throw MsgConnectionException(std::string("select error: ") + ::strerror(errno));
        }
        if (ready == 0) {
            throw MsgTimeoutException("recv timed out");
        }

        ssize_t r = ::recv(sockfd, buf + received, n - received, 0);
        if (r == 0) {
            throw MsgConnectionException(
                "connection closed: expected " + std::to_string(n) +
                " bytes, got " + std::to_string(received));
        }
        if (r < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
            throw MsgConnectionException(
                "recv error: " + std::string(::strerror(errno)));
        }
        received += static_cast<size_t>(r);
    }
}

// deadline 模式接收完整 pymsg 帧（header + payload）
inline std::string recvFrameDeadline(int sockfd, double timeout_sec) {
    validateTimeout(timeout_sec);
    const auto deadline = std::chrono::steady_clock::now()
        + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(timeout_sec));

    char header[kHeaderSize];
    recvExactDeadline(sockfd, header, kHeaderSize, deadline);
    uint32_t length = readUint32BE(header);
    if (length > kMaxMsgSize) {
        throw MsgException("message too large: " + std::to_string(length));
    }

    std::string payload(static_cast<size_t>(length), '\0');
    if (length > 0) {
        recvExactDeadline(sockfd, payload.data(), length, deadline);
        // [libmsg-audit P2-4] UTF-8 合法性校验（对齐 Python decode('utf-8')），非法载荷视为协议错误
        if (!isValidUtf8(payload.data(), length)) {
            throw MsgConnectionException("invalid UTF-8 payload");
        }
    }
    return payload;
}

// ---- 完整发送 -------------------------------------------------------------

inline void sendAll(int sockfd, const char *data, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        ssize_t w =
            ::send(sockfd, data + sent, len - sent, MSG_NOSIGNAL);
        if (w < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                throw MsgTimeoutException("send timed out");
            }
            throw MsgConnectionException(
                "send error: " + std::string(::strerror(errno)));
        }
        sent += static_cast<size_t>(w);
    }
}

// ---- 短连接 helper：connect → action → close ------------------------------

inline int openConnection(const std::string& addr, int port, double timeout) {
    // [libmsg-audit P2-3] 入口边界校验：port 与 timeout 必须在合法范围。
    validatePort(port);
    validateTimeout(timeout);
    int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        throw MsgConnectionException(
            "socket() failed: " + std::string(::strerror(errno)));
    }

    struct sockaddr_in serv_addr {};
    serv_addr.sin_family = AF_INET;
    serv_addr.sin_port = htons(static_cast<uint16_t>(port));
    if (::inet_pton(AF_INET, addr.c_str(), &serv_addr.sin_addr) <= 0) {
        ::close(fd);
        throw MsgConnectionException("invalid address: " + addr);
    }

    setSockTimeout(fd, timeout);

    if (::connect(fd, reinterpret_cast<struct sockaddr *>(&serv_addr),
                  sizeof(serv_addr)) < 0) {
        int saved_errno = errno;
        ::close(fd);
        std::string err_msg = "could not connect to " + addr + ":" +
                              std::to_string(port) + " - " + ::strerror(saved_errno);
        if (saved_errno == ECONNREFUSED) {
            throw MsgConnectionRefusedException(err_msg);
        }
        throw MsgConnectionException(err_msg);
    }

    return fd;
}

} // namespace detail

// ============================================================================
// 底层协议函数：pmsg_send / pmsg_recv
// ============================================================================

/// 以 pymsg 协议发送一条字符串消息（长度前缀 + UTF-8 载荷）。
/// 注意：sendAll() 依赖 socket 已设置的 SO_SNDTIMEO，否则慢客户端不读数据时
/// send() 会永久阻塞。MsgClient 和 MsgServer 内部均已设置 SO_SNDTIMEO，
/// 直接使用此公共 API 时需自行设置。
inline void pmsg_send(int sockfd, const std::string &data) {
    size_t payload_size = data.size();
    if (payload_size > msg_constants::kMaxMsgSize) {
        throw MsgException(
            "payload too large: " + std::to_string(payload_size) +
            " bytes (max " + std::to_string(msg_constants::kMaxMsgSize) + ")");
    }
    char header[detail::kHeaderSize];
    detail::writeUint32BE(header, static_cast<uint32_t>(payload_size));
    detail::sendAll(sockfd, header, detail::kHeaderSize);
    detail::sendAll(sockfd, data.c_str(), payload_size);
}

/// 以 pymsg 协议接收一条字符串消息（阻塞模式，依赖 socket 已设置的超时）。
/// 注意：调用方必须预先通过 setsockopt(SO_RCVTIMEO) 设置接收超时，否则可能永久阻塞。
/// 推荐使用 pmsg_recv(sockfd, timeout_sec) deadline 模式，不依赖外部状态。
/// @throws MsgConnectionException  连接在收全消息前断开、或载荷不是合法 UTF-8。
/// @throws MsgTimeoutException     等待超时。
inline std::string pmsg_recv(int sockfd) {
    char header[detail::kHeaderSize];
    detail::recvExact(sockfd, header, detail::kHeaderSize);
    uint32_t length = detail::readUint32BE(header);
    if (length > msg_constants::kMaxMsgSize) { // 安全上限 64 MiB
        throw MsgException("message too large: " + std::to_string(length));
    }
    std::string payload(static_cast<size_t>(length), '\0');
    if (length > 0) {
        detail::recvExact(sockfd, payload.data(), length);
        // [libmsg-audit P2-4] UTF-8 合法性校验（对齐 Python decode('utf-8')），非法载荷视为协议错误
        if (!detail::isValidUtf8(payload.data(), length)) {
            throw MsgConnectionException("invalid UTF-8 payload");
        }
    }
    return payload;
}

/// 以 pymsg 协议接收一条字符串消息（deadline 模式，总超时不超过 timeout_sec）。
/// header 和 payload 共享同一个 deadline，防止 Slowloris 攻击。
/// 不依赖 socket 的 SO_RCVTIMEO，使用 select() 实现精确超时控制。
/// @param timeout_sec 总超时秒数
/// @throws MsgConnectionException  连接在收全消息前断开。
/// @throws MsgTimeoutException     总超时到期。
inline std::string pmsg_recv(int sockfd, double timeout_sec) {
    return detail::recvFrameDeadline(sockfd, timeout_sec);
}

// ============================================================================
// MsgClient
// ============================================================================

/// 基于 pymsg 协议的 TCP 客户端。
///
/// 支持三种操作模式：
///   - send()    短连接发送一条消息（不等待回复），消息带 name 前缀。
///   - call()    短连接发送 + 阻塞等待回复（RPC 模式），原始消息不添加前缀。
///   - listen()  基于 connect() 维持的长连接，持续接收消息并回调。
class MsgClient : public Msg_Basic {
public:
    MsgClient(const std::string &addr, int port, const std::string &name)
        : _addr(addr), _port(port), _name(name), _sockfd(-1), _running(false) {
        detail::validatePort(port);
    }

    ~MsgClient() { stop(); }

    // ---- 连接 ---------------------------------------------------------------

    /// 创建 TCP 套接字并连接到服务器（长连接，供 listen() 使用）。
    void connect() {
        int fd = detail::openConnection(_addr, _port, /*timeout=*/static_cast<double>(msg_constants::kDefaultTimeout));
        // 如果已有旧连接，先关闭
        int old = _sockfd.exchange(fd);
        if (old >= 0) {
            ::shutdown(old, SHUT_RDWR);
            ::close(old);
        }
    }

    // ---- 发送单条消息（短连接，带名字前缀）----------------------------------

    /// 短连接发送一条消息到服务器，消息格式为 ``"name: msg"``。
    /// 不等待回复。短连接（每次创建新 socket，发完即关）。
    void send(const std::string &msg) {
        int fd = detail::openConnection(_addr, _port, /*timeout=*/static_cast<double>(msg_constants::kSendTimeout));
        try {
            pmsg_send(fd, _name + ": " + msg);
            ::close(fd);
        } catch (...) {
            ::close(fd);
            throw;
        }
    }

    // ---- 请求-应答（短连接，RPC 模式）---------------------------------------

    /// 短连接发送一条消息，阻塞等待并返回一条回复。
    ///
    /// 不会给消息添加 ``name:`` 前缀（适用于 RPC/API 调用）。
    ///
    /// @param msg              要发送的原始字符串。
    /// @param timeout          超时秒数，默认 10.0。
    /// @param retry_on_refused 目标服务未启动（ECONNREFUSED）时是否阻塞重试，
    ///                         直到连接成功。适用于启动顺序不确定的服务发现场景。
    ///                         默认 false（连接拒绝立即抛异常）。
    /// @param retry_interval   重试间隔秒数，默认 1.0。
    /// @returns                服务器返回的字符串。
    std::string call(const std::string &msg, double timeout = static_cast<double>(msg_constants::kDefaultTimeout),
                     bool retry_on_refused = false,
                     double retry_interval = 1.0) {
        detail::validateTimeout(timeout);
        if (retry_interval <= 0 || retry_interval > 60.0) {
            throw MsgException("invalid retry_interval: " + std::to_string(retry_interval) + " seconds (must be in (0, 60])");
        }
        if (!retry_on_refused) {
            // 快速路径：不重试，直接执行一次
            int fd = detail::openConnection(_addr, _port, timeout);
            try {
                pmsg_send(fd, msg);
                // deadline 模式 recv：header+payload 共享总超时，防止总等待时间翻倍
                std::string resp = pmsg_recv(fd, timeout);
                ::close(fd);
                return resp;
            } catch (...) {
                ::close(fd);
                throw;
            }
        }

        // 重试路径：仅对 ECONNREFUSED 重试，其他错误立即抛出
        int attempt = 0;
        auto last_log = std::chrono::steady_clock::now() -
                        std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                            std::chrono::duration<double>(msg_constants::kRetryLogEvery));

        while (true) {
            try {
                int fd = detail::openConnection(_addr, _port, timeout);
                try {
                    pmsg_send(fd, msg);
                    std::string resp = pmsg_recv(fd, timeout);
                    ::close(fd);
                    return resp;
                } catch (...) {
                    ::close(fd);
                    throw;
                }
            } catch (const MsgConnectionRefusedException &) {
                ++attempt;
                auto now = std::chrono::steady_clock::now();
                double elapsed = std::chrono::duration<double>(now - last_log).count();
                if (elapsed >= msg_constants::kRetryLogEvery || attempt == 1) {
                    std::fprintf(stderr,
                                 "[pymsg] 等待服务 %s:%d 就绪... (已重试 %d 次)\n",
                                 _addr.c_str(), _port, attempt);
                    std::fflush(stderr);
                    last_log = now;
                }
                std::this_thread::sleep_for(
                    std::chrono::duration<double>(retry_interval));
            }
            // 其他异常（MsgTimeoutException、普通MsgConnectionException等）不捕获，直接向上抛出
        }
    }

    // ---- 持续监听（长连接，阻塞回调模式）-----------------------------------

    /// 在长连接上持续接收消息并通过回调处理。
    ///
    /// 阻塞当前线程直到 stop() 被调用或连接断开。
    /// 必须先调用 connect()。
    ///
    /// @param on_message  回调 ``void(const std::string &msg)``。
    void listen(std::function<void(const std::string &)> on_message) {
        int fd = _sockfd.load();
        if (fd < 0) {
            throw MsgException("must call connect() before listen()");
        }

        _running = true;
        while (_running.load()) {
            // 使用局部 fd，避免与 stop() 竞争导致 FD_SET(-1) UB
            // 若 stop() 已将 _sockfd 置为 -1，下次循环 _running=false 退出
            int cur_fd = _sockfd.load();
            if (cur_fd < 0) break;

            fd_set readfds;
            FD_ZERO(&readfds);
            FD_SET(cur_fd, &readfds);

            struct timeval tv {};
            tv.tv_sec = 0;
            tv.tv_usec = msg_constants::kListenPollMs; // 100 ms 轮询间隔

            int ready =
                ::select(cur_fd + 1, &readfds, nullptr, nullptr, &tv);
            if (ready < 0) {
                if (errno == EINTR) continue;
                break; // EBADF (socket 被 stop() 关闭) 或其他致命错误
            }
            if (ready == 0) continue; // timeout, loop around

            try {
                std::string text = pmsg_recv(cur_fd);
                on_message(std::move(text));
            } catch (const MsgConnectionException &) {
                break; // 连接断开，退出循环
            } catch (const MsgTimeoutException &) {
                continue; // 读超时但连接仍在，继续等待
            }
        }

        // 原子地认领 fd 并关闭，防止与 stop() double-close
        int close_fd = _sockfd.exchange(-1);
        if (close_fd >= 0) {
            ::shutdown(close_fd, SHUT_RDWR);
            ::close(close_fd);
        }
    }

    // ---- 停止 ---------------------------------------------------------------

    /// 设置停止标志并关闭套接字。可从任意线程调用。
    void stop() noexcept {
        _running = false;
        // 原子地认领 fd 并关闭，防止与 listen() double-close
        int fd = _sockfd.exchange(-1);
        if (fd >= 0) {
            ::shutdown(fd, SHUT_RDWR);  // 唤醒阻塞在 recv/select 上的线程
            ::close(fd);
        }
    }

    // ---- 访问器 -------------------------------------------------------------

    const std::string &addr() const noexcept { return _addr; }
    int port() const noexcept { return _port; }
    const std::string &name() const noexcept { return _name; }

private:
    std::string _addr;
    int _port;
    std::string _name;
    std::atomic<int> _sockfd;
    std::atomic<bool> _running;
};

} // namespace net
} // namespace pmc
