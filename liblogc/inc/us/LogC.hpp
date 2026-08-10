#pragma once

/** LogC — 统一集中式日志客户端（C++ 版）
 *
 * 通过 pymsg TCP 调用 dbv2 execute 路由，逐条 INSERT 到 Logs 表。
 *
 * 设计要点：
 *   - 异步非阻塞：基于 pmc::net::AsyncQueue 内存队列 + 后台线程批量推送
 *   - fail-safe：db 服务不可用时静默丢弃（仅 stderr 提示），绝不影响业务
 *   - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush
 *   - 背压保护：队列上限 10000 条，满时丢最旧日志，防止内存无限增长
 *   - 级别过滤：set_level() 设定阈值，低于阈值的日志直接丢弃
 *   - 可配置 endpoint / service_name，各服务复用同一份库
 *
 * 用法：
 *   #include <us/LogC.hpp>
 *
 *   // 启动时配置（默认 127.0.0.1:8005）
 *   qing::LogC::instance().set_endpoint("127.0.0.1", 8005);
 *   qing::LogC::instance().set_service_name("gateway-log");
 *   qing::LogC::instance().set_level("warn");
 *
 *   qing::LogC::instance().info("gateway", "启动完成");
 *   qing::LogC::instance().error("gateway", "连接失败",
 *                                std::nullopt, R"({"errno":111})");
 *
 *   // 进程退出前调用（幂等）
 *   qing::LogC::instance().shutdown();
 */

#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

// forward-declare: 避免在头文件中拉入 AsyncQueue 模板
namespace pmc { namespace net {
template <typename T> class AsyncQueue;
}}

namespace qing {

class LogC {
public:
	struct Entry {
		std::string service;
		std::string level;
		std::string message;
		std::optional<std::string> trace_id;
		std::optional<std::string> extra;	// 已序列化的 JSON 字符串（存 Logs.extra TEXT）
	};

	// 日志级别 → 数值（大写兼容）
	static const std::unordered_map<std::string, int>& level_order() {
		static const std::unordered_map<std::string, int> k = {
			{"debug", 0}, {"info", 1}, {"warn", 2}, {"warning", 2},
			{"error", 3}, {"critical", 3},
			{"DEBUG", 0}, {"INFO", 1}, {"WARN", 2}, {"WARNING", 2},
			{"ERROR", 3}, {"CRITICAL", 3},
		};
		return k;
	}

	static LogC& instance() {
		static LogC inst;
		return inst;
	}

	// ---- 配置（启动时调用，线程安全）-------------------------------------

	void set_endpoint(const std::string& addr, int port);
	void set_service_name(const std::string& name);

	// 设置日志级别阈值（debug|info|warn|error，大小写不限）
	void set_level(const std::string& level);

	// ---- 推送接口（非阻塞，立即返回）-------------------------------------

	void push(const std::string& service, const std::string& level,
			const std::string& message,
			std::optional<std::string> trace_id = std::nullopt,
			std::optional<std::string> extra = std::nullopt);

	void debug(const std::string& service, const std::string& message,
			std::optional<std::string> trace_id = std::nullopt,
			std::optional<std::string> extra = std::nullopt) {
		push(service, "DEBUG", message, std::move(trace_id), std::move(extra));
	}
	void info(const std::string& service, const std::string& message,
			std::optional<std::string> trace_id = std::nullopt,
			std::optional<std::string> extra = std::nullopt) {
		push(service, "INFO", message, std::move(trace_id), std::move(extra));
	}
	void warning(const std::string& service, const std::string& message,
			std::optional<std::string> trace_id = std::nullopt,
			std::optional<std::string> extra = std::nullopt) {
		push(service, "WARNING", message, std::move(trace_id), std::move(extra));
	}
	void error(const std::string& service, const std::string& message,
			std::optional<std::string> trace_id = std::nullopt,
			std::optional<std::string> extra = std::nullopt) {
		push(service, "ERROR", message, std::move(trace_id), std::move(extra));
	}
	void critical(const std::string& service, const std::string& message,
			std::optional<std::string> trace_id = std::nullopt,
			std::optional<std::string> extra = std::nullopt) {
		push(service, "CRITICAL", message, std::move(trace_id), std::move(extra));
	}

	// ---- 生命周期 -------------------------------------------------------

	void flush();		// 手动 flush 当前积攒批次，等待完成
	void shutdown();	// 停止后台线程并 flush 剩余日志（幂等）

private:
	LogC();
	~LogC();

	LogC(const LogC&) = delete;
	LogC& operator=(const LogC&) = delete;

	void _send_batch(std::vector<Entry>&& batch);
	static std::string _utc_now_iso();

	// ---- 配置项 ---------------------------------------------------------
	std::string _db_addr = "127.0.0.1";
	int _db_port = 8005;
	std::string _service_name = "log";
	std::mutex _endpoint_mtx;				// 保护 _db_addr/_db_port/_service_name
	std::atomic<int> _level_threshold{1};	// 默认 INFO(1)

	static constexpr size_t _BATCH_SIZE     = 50;
	static constexpr double _FLUSH_INTERVAL = 2.0;
	static constexpr size_t _MAX_QUEUE_SIZE = 10000;
	static constexpr double _TIMEOUT        = 5.0;

	// ---- 核心队列（unique_ptr 延迟到 .cpp 中实例化，保持头文件轻量）----
	std::unique_ptr<pmc::net::AsyncQueue<Entry>> _queue;
};

} // namespace qing
