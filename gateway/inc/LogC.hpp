#pragma once

/** LogC — gateway 统一日志客户端（C++ 版，对应各 Python 服务的 log_client.py）
 *
 * 设计要点（与 Python 版对齐）：
 *   - 通过 pymsg TCP 调用 dbv2 execute 路由，逐条 INSERT 到 Logs 表
 *   - 异步非阻塞：基于 pmc::net::AsyncQueue 内存队列 + 后台线程批量推送，不拖慢业务
 *   - fail-safe：db 服务不可用时静默丢弃（仅 stderr 提示），绝不影响业务
 *   - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush
 *   - 背压保护：队列上限 10000 条，满时丢最旧日志，防止内存无限增长
 *
 * 用法：
 *   #include "LogC.hpp"
 *
 *   qing::LogC::instance().info("gateway", "启动完成");
 *   qing::LogC::instance().error("gateway", "数据库连接失败",
 *                                std::nullopt, R"({"errno":2002})");
 *
 *   // 进程退出前：
 *   qing::LogC::instance().shutdown();   // 停止后台线程并 flush 剩余日志（幂等）
 *
 *   // 设置日志级别（main 启动时调用，低于此级别的日志直接丢弃）
 *   qing::LogC::instance().set_level("warn");  // 只输出 warn/error
 */

#include <atomic>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include <boost/json.hpp>

#include <us/AsyncQueue.hpp>
#include <us/Msg.hpp>

namespace qing {

namespace json = boost::json;

class LogC {
public:
	struct Entry {
		std::string service;
		std::string level;
		std::string message;
		std::optional<std::string> trace_id;
		std::optional<std::string> extra;	// 已序列化的 JSON 字符串（存 Logs.extra  TEXT）
	};

	static LogC& instance() {
		static LogC inst;
		return inst;
	}

	// ---- 公开接口 --------------------------------------------------------

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

	// 设置日志级别阈值（debug|info|warn|error，大小写不限）
	// 低于此级别的日志在 push 时直接丢弃
	void set_level(const std::string& level) {
		auto it = level_order().find(level);
		if (it != level_order().end()) {
			_level_threshold.store(it->second, std::memory_order_relaxed);
		}
	}

	// 推送一条日志（非阻塞，立即返回）
	void push(const std::string& service, const std::string& level,
			const std::string& message,
			std::optional<std::string> trace_id = std::nullopt,
			std::optional<std::string> extra = std::nullopt) {
		// 日志级别过滤：低于阈值直接丢弃
		auto it = level_order().find(level);
		int lvl = (it != level_order().end()) ? it->second : 1;
		if (lvl < _level_threshold.load(std::memory_order_relaxed)) return;
		_queue.push(Entry{service, level, message,
				std::move(trace_id), std::move(extra)});
	}

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

	// 手动 flush：通知后台线程立即发送当前积攒的批次，并等待其完成
	void flush() {
		_queue.flush();
	}

	// 停止后台线程并 flush 剩余日志（进程退出前调用，幂等）
	void shutdown() {
		_queue.shutdown();
	}

	// ---- 配置（构造后、首次推送前可改）------------------------------------
	void set_endpoint(const std::string& addr, int port) {
		std::lock_guard<std::mutex> lock(_endpoint_mtx);
		_db_addr = addr;
		_db_port = port;
	}

private:
	LogC()
		: _queue(
			[this](std::vector<Entry>&& batch) { this->_send_batch(std::move(batch)); },
			{_BATCH_SIZE, _FLUSH_INTERVAL, _MAX_QUEUE_SIZE, 0.5},
			[](const std::exception& e, std::vector<Entry>&&) {
				std::cerr << "[LogC] 发送失败: " << e.what() << std::endl;
			})
	{
		_queue.start();
	}

	~LogC() { shutdown(); }

	LogC(const LogC&) = delete;
	LogC& operator=(const LogC&) = delete;

	// 通过 pymsg 使用 execute 路由逐条 INSERT 到 dbv2 Logs 表
	void _send_batch(const std::vector<Entry>& batch) {
		std::string addr;
		int port;
		{
			std::lock_guard<std::mutex> lock(_endpoint_mtx);
			addr = _db_addr;
			port = _db_port;
		}

		const std::string now = _utc_now_iso();	// 批次共享同一时间戳（对齐 Python 版）
		try {
			pmc::net::MsgClient client(addr, port, "gateway-log");
			for (const auto& e : batch) {
				json::object values;
				values["ts"] = now;
				values["service"] = e.service;
				values["level"] = e.level;
				values["message"] = e.message;
				values["trace_id"] = e.trace_id ? json::value(*e.trace_id)
						: json::value(nullptr);
				values["extra"] = e.extra ? json::value(*e.extra)
						: json::value(nullptr);

				json::object payload;
				payload["_route"] = "execute";
				payload["action"] = "insert";
				payload["table"] = "Logs";
				payload["values"] = std::move(values);

				const std::string resp = client.call(
						json::serialize(payload), _TIMEOUT,
						/*retry_on_refused=*/false);  // 日志是 fail-safe，db 不可用时静默丢弃，绝不阻塞重试
				auto obj = json::parse(resp).as_object();
				if (!obj.at("success").as_bool()) {
					std::string err = "unknown";
					if (auto* ev = obj.if_contains("error")) {
						if (ev->is_string()) err = ev->as_string().c_str();
					}
					std::cerr << "[LogC] INSERT 失败: " << err << std::endl;
				}
			}
		} catch (const std::exception& e) {
			// fail-safe：db 不可用时静默丢弃，绝不影响业务
			std::cerr << "[LogC] 发送失败: " << e.what() << std::endl;
		}
	}

	// UTC ISO8601 时间戳（对齐 Python datetime.now(timezone.utc).isoformat()）
	// 形如 2026-07-29T08:30:00.123456+00:00，字典序即时间序，便于 db 按 ts 清理
	static std::string _utc_now_iso() {
		const auto now = std::chrono::system_clock::now();
		const auto t = std::chrono::system_clock::to_time_t(now);
		const auto us = std::chrono::duration_cast<std::chrono::microseconds>(
				now.time_since_epoch()).count() % 1000000;
		std::tm tm{};
		::gmtime_r(&t, &tm);
		char buf[40];
		std::snprintf(buf, sizeof buf,
				"%04d-%02d-%02dT%02d:%02d:%02d.%06d+00:00",
				tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
				tm.tm_hour, tm.tm_min, tm.tm_sec, static_cast<int>(us));
		return buf;
	}

	// ---- 配置项 -----------------------------------------------------------
	std::string _db_addr = "127.0.0.1";
	int _db_port = 8005;
	std::mutex _endpoint_mtx;	// 保护 _db_addr/_db_port 读写（set_endpoint 与后台线程并发）
	std::atomic<int> _level_threshold{1};	// 日志级别阈值，默认 INFO(1)

	static constexpr size_t _BATCH_SIZE     = 50;      // 队列满 50 条时 flush
	static constexpr double _FLUSH_INTERVAL = 2.0;     // 超时 2 秒强制 flush
	static constexpr size_t _MAX_QUEUE_SIZE = 10000;   // 队列上限，满时丢最旧
	static constexpr double _TIMEOUT        = 5.0;     // socket 超时（秒）

	// ---- 核心队列 ---------------------------------------------------------
	pmc::net::AsyncQueue<Entry> _queue;
};

} // namespace qing
