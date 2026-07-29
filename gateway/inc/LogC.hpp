#pragma once

/** LogC — gateway 统一日志客户端（C++ 版，对应各 Python 服务的 log_client.py）
 *
 * 设计要点（与 Python 版对齐）：
 *   - 通过 pymsg TCP 调用 dbv2 execute 路由，逐条 INSERT 到 Logs 表
 *   - 异步非阻塞：内存队列 + 后台线程批量推送，不拖慢业务
 *   - fail-safe：db 服务不可用时静默丢弃（仅 stderr 提示），绝不影响业务
 *   - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush
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
 */

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <ctime>
#include <deque>
#include <iostream>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include <boost/json.hpp>

#include "Msg.hpp"

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

	// 推送一条日志（非阻塞，立即返回）
	void push(const std::string& service, const std::string& level,
			const std::string& message,
			std::optional<std::string> trace_id = std::nullopt,
			std::optional<std::string> extra = std::nullopt) {
		{
			std::lock_guard<std::mutex> lock(_mtx);
			_queue.push_back(Entry{service, level, message,
					std::move(trace_id), std::move(extra)});
		}
		_cv.notify_one();
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

	// 手动 flush：通知后台线程立即发送当前积攒的批次，并短暂等待其完成
	void flush() {
		_force_flush.store(true);
		_cv.notify_all();
		for (int i = 0; i < 200 && _force_flush.load(); ++i) {
			std::this_thread::sleep_for(std::chrono::milliseconds(10));
		}
	}

	// 停止后台线程并 flush 剩余日志（进程退出前调用，幂等）
	void shutdown() {
		{
			std::lock_guard<std::mutex> lock(_mtx);
			if (_stop) return;
			_stop = true;
		}
		_cv.notify_all();
		if (_thread.joinable()) _thread.join();
	}

	// ---- 配置（构造后、首次推送前可改）------------------------------------
	void set_endpoint(const std::string& addr, int port) {
		_db_addr = addr;
		_db_port = port;
	}

private:
	LogC() : _thread(&LogC::_flush_loop, this) {}
	~LogC() { shutdown(); }

	LogC(const LogC&) = delete;
	LogC& operator=(const LogC&) = delete;

	// 后台线程：积攒到 batch_size 或超时 flush_interval 时发送
	void _flush_loop() {
		std::vector<Entry> batch;
		auto last_flush = std::chrono::steady_clock::now();

		for (;;) {
			{
				std::unique_lock<std::mutex> lock(_mtx);
				// 最多 0.5s 轮询一次（对齐 Python 版 chk_timeout 上限）
				_cv.wait_for(lock, std::chrono::milliseconds(500),
						[&] { return _stop || !_queue.empty(); });
				while (!_queue.empty() && batch.size() < _BATCH_SIZE) {
					batch.push_back(std::move(_queue.front()));
					_queue.pop_front();
				}
			}

			const auto now = std::chrono::steady_clock::now();
			const bool due = std::chrono::duration<double>(now - last_flush).count()
					>= _FLUSH_INTERVAL;
			if (!batch.empty() && (batch.size() >= _BATCH_SIZE || due
					|| _stop || _force_flush.load())) {
				_send(batch);
				batch.clear();
				last_flush = now;
				_force_flush.store(false);
			}

			if (_stop) {
				// 退出前 flush 队列剩余
				{
					std::unique_lock<std::mutex> lock(_mtx);
					while (!_queue.empty()) {
						batch.push_back(std::move(_queue.front()));
						_queue.pop_front();
					}
				}
				if (!batch.empty()) _send(batch);
				_force_flush.store(false);
				return;
			}
		}
	}

	// 通过 pymsg 使用 execute 路由逐条 INSERT 到 dbv2 Logs 表
	void _send(const std::vector<Entry>& batch) {
		const std::string now = _utc_now_iso();	// 批次共享同一时间戳（对齐 Python 版）
		try {
			pmc::net::MsgClient client(_db_addr, _db_port, "gateway-log");
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
						json::serialize(payload), _TIMEOUT);
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
	static constexpr size_t _BATCH_SIZE = 50;		// 队列满 50 条时 flush
	static constexpr double _FLUSH_INTERVAL = 2.0;	// 超时 2 秒强制 flush
	static constexpr double _TIMEOUT = 5.0;			// socket 超时（秒）

	// ---- 状态 -------------------------------------------------------------
	std::deque<Entry> _queue;
	std::mutex _mtx;
	std::condition_variable _cv;
	bool _stop = false;
	std::atomic<bool> _force_flush{false};
	std::thread _thread;
};

} // namespace qing
