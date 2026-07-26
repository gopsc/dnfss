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
 *   #include "Msg.hpp"
 *
 *   pmc::net::MsgClient client("127.0.0.1", 8005, "gateway");
 *   std::string resp = client.call(R"({"sql":"SELECT 1"})", 5.0);
 *   client.send("hello");
 */

#include <cerrno>
#include <cstdint>
#include <cstring>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

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
// Exceptions
// ============================================================================

class MsgException : public std::runtime_error {
public:
  explicit MsgException(const std::string &msg) : std::runtime_error(msg) {}
};

class MsgTimeoutException : public MsgException {
public:
  explicit MsgTimeoutException(const std::string &msg) : MsgException(msg) {}
};

class MsgConnectionException : public MsgException {
public:
  explicit MsgConnectionException(const std::string &msg) : MsgException(msg) {}
};

// ============================================================================
// Low-level protocol: 4-byte big-endian length prefix + UTF-8 payload
// ============================================================================

namespace detail {

constexpr size_t kHeaderSize = 4;

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
    ::close(fd);
    throw MsgConnectionException(
        "could not connect to " + addr + ":" + std::to_string(port) +
        " - " + ::strerror(errno));
  }

  return fd;
}

} // namespace detail

// ============================================================================
// 底层协议函数：pmsg_send / pmsg_recv
// ============================================================================

/// 以 pymsg 协议发送一条字符串消息（长度前缀 + UTF-8 载荷）。
inline void pmsg_send(int sockfd, const std::string &data) {
  size_t payload_size = data.size();
  if (payload_size > 0xFFFFFFFFu) {
    throw MsgException("payload too large: " + std::to_string(payload_size));
  }
  char header[detail::kHeaderSize];
  detail::writeUint32BE(header, static_cast<uint32_t>(payload_size));
  detail::sendAll(sockfd, header, detail::kHeaderSize);
  detail::sendAll(sockfd, data.c_str(), payload_size);
}

/// 以 pymsg 协议接收一条字符串消息。
/// @throws MsgConnectionException  连接在收全消息前断开。
/// @throws MsgTimeoutException     等待超时。
inline std::string pmsg_recv(int sockfd) {
  char header[detail::kHeaderSize];
  detail::recvExact(sockfd, header, detail::kHeaderSize);
  uint32_t length = detail::readUint32BE(header);
  if (length > 64 * 1024 * 1024) { // 安全上限 64 MiB
    throw MsgException("message too large: " + std::to_string(length));
  }
  std::string payload(static_cast<size_t>(length), '\0');
  detail::recvExact(sockfd, payload.data(), length);
  return payload;
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
class MsgClient {
public:
  MsgClient(const std::string &addr, int port, const std::string &name)
      : _addr(addr), _port(port), _name(name), _sockfd(-1), _running(false) {}

  ~MsgClient() { stop(); }

  // ---- 连接 ---------------------------------------------------------------

  /// 创建 TCP 套接字并连接到服务器（长连接，供 listen() 使用）。
  void connect() {
    _sockfd = detail::openConnection(_addr, _port, /*timeout=*/10.0);
  }

  // ---- 发送单条消息（短连接，带名字前缀）----------------------------------

  /// 短连接发送一条消息到服务器，消息格式为 ``"name: msg"``。
  /// 不等待回复。短连接（每次创建新 socket，发完即关）。
  void send(const std::string &msg) {
    int fd = detail::openConnection(_addr, _port, /*timeout=*/3.0);
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
  /// @param msg      要发送的原始字符串。
  /// @param timeout  超时秒数，默认 10.0。
  /// @returns        服务器返回的字符串。
  std::string call(const std::string &msg, double timeout = 10.0) {
    int fd = detail::openConnection(_addr, _port, timeout);
    try {
      pmsg_send(fd, msg);
      std::string resp = pmsg_recv(fd);
      ::close(fd);
      return resp;
    } catch (...) {
      ::close(fd);
      throw;
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
    if (_sockfd < 0) {
      throw MsgException("must call connect() before listen()");
    }

    _running = true;
    while (_running) {
      fd_set readfds;
      FD_ZERO(&readfds);
      FD_SET(_sockfd, &readfds);

      struct timeval tv {};
      tv.tv_sec = 0;
      tv.tv_usec = 100'000; // 100 ms 轮询间隔

      int ready =
          ::select(_sockfd + 1, &readfds, nullptr, nullptr, &tv);
      if (ready < 0) {
        if (errno == EINTR) continue;
        break; // EBADF or other fatal error
      }
      if (ready == 0) continue; // timeout, loop around

      try {
        std::string text = pmsg_recv(_sockfd);
        on_message(std::move(text));
      } catch (const MsgConnectionException &) {
        break; // 连接断开，退出循环
      } catch (const MsgTimeoutException &) {
        continue; // 读超时但连接仍在，继续等待
      }
    }

    if (_sockfd >= 0) {
      ::close(_sockfd);
      _sockfd = -1;
    }
  }

  // ---- 停止 ---------------------------------------------------------------

  /// 设置停止标志并关闭套接字。可从任意线程调用。
  void stop() noexcept {
    _running = false;
    if (_sockfd >= 0) {
      ::close(_sockfd);
      _sockfd = -1;
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
  int _sockfd;
  bool _running;
};

} // namespace net
} // namespace pmc
