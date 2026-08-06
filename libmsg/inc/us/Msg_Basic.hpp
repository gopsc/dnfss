#pragma once

/** libmsg — UniServe 消息通信基础库
 *
 * 提供 pymsg 协议的 C++ 实现：4字节大端长度前缀 + UTF-8 载荷的 TCP 消息帧协议。
 * 对应 Python 版 pymsg/pymsg/__init__.py，字节级兼容。
 *
 * 命名空间 pmc::net，库文件 libus_msg.so，头文件安装至 /usr/include/us/。
 */

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>

#define MSG_CHECK(condition, message) \
	if (!(condition)) { \
		throw std::runtime_error(message); \
	}

namespace pmc {
namespace net {

/// 协议常量
namespace msg_constants {
    constexpr size_t   kHeaderSize  = 4;           ///< 帧头长度（字节）
    constexpr uint32_t kMaxMsgSize  = 64u * 1024u * 1024u;  ///< 消息最大长度 64 MiB
    constexpr int      kDefaultTimeout = 10;       ///< 默认超时（秒）
    constexpr int      kSendTimeout    = 3;        ///< send() 默认超时（秒）
    constexpr int      kListenPollMs   = 100'000;  ///< listen() select 轮询间隔（微秒）
    constexpr double   kRetryLogEvery  = 5.0;      ///< 重试等待日志间隔（秒）
}

// ============================================================================
// 异常层次
// ============================================================================

/// 消息库基础异常
class MsgException : public std::runtime_error {
public:
    explicit MsgException(const std::string &msg) : std::runtime_error(msg) {}
};

/// 消息收发超时
class MsgTimeoutException : public MsgException {
public:
    explicit MsgTimeoutException(const std::string &msg) : MsgException(msg) {}
};

/// 连接异常（断开、重置、地址错误等）
class MsgConnectionException : public MsgException {
public:
    explicit MsgConnectionException(const std::string &msg) : MsgException(msg) {}
};

/// 目标服务未启动（ECONNREFUSED），用于 retry_on_refused 重试机制
class MsgConnectionRefusedException : public MsgConnectionException {
public:
    explicit MsgConnectionRefusedException(const std::string &msg)
        : MsgConnectionException(msg) {}
};

// ============================================================================
// 基类（供 MsgClient 等内部工具方法继承使用）
// ============================================================================

class Msg_Basic {
public:
    class MsgExp : public std::runtime_error {
    public:
        MsgExp(const std::string& msg) : std::runtime_error(msg) {}
    };
protected:
    /// 版本字符串（编译进 .so）
    static const char* version();
};

} // namespace net
} // namespace pmc
