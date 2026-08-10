/*
 * LogC.hpp — captcha2 异步日志客户端（简化版）
 *
 * 基于 libmsg AsyncQueue，异步批量推送日志到 db:8005 的 Logs 表。
 * - 内存队列 + 后台线程攒批（满 50 条或 2 秒 flush）
 * - fail-safe：db 不可用时静默丢弃
 * - API: info() / warning() / error()
 */
#pragma once

#include <us/Msg.hpp>
#include <us/AsyncQueue.hpp>
#include <boost/json.hpp>
#include <iostream>
#include <string>
#include <vector>
#include <chrono>
#include <cstdlib>
#include <atomic>
#include <unordered_map>

namespace captcha2 {

struct LogEntry {
	std::string ts;
	std::string service;
	std::string level;
	std::string message;
	std::string trace_id;
	std::string extra;
};

class LogC {
public:
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

	// 设置日志级别阈值（debug|info|warn|error，大小写不限）
	// 低于此级别的日志在 push 时直接丢弃
	void set_level(const std::string& level) {
		auto it = level_order().find(level);
		if (it != level_order().end()) {
			level_threshold_.store(it->second, std::memory_order_relaxed);
		}
	}

	void info(const std::string& svc, const std::string& msg) {
		push(svc, "INFO", msg);
	}
	void warning(const std::string& svc, const std::string& msg) {
		push(svc, "WARNING", msg);
	}
	void error(const std::string& svc, const std::string& msg) {
		push(svc, "ERROR", msg);
	}

private:
	LogC() {
		const char* host = std::getenv("DB_HOST");
		const char* port = std::getenv("DB_PORT");
		db_host_ = host ? host : "127.0.0.1";
		db_port_ = port ? std::atoi(port) : 8005;

		queue_ = std::make_unique<pmc::net::AsyncQueue<LogEntry>>(
			[this](std::vector<LogEntry>&& batch) { send_batch(std::move(batch)); },
			pmc::net::AsyncQueueConfig{.batch_size = 50, .flush_interval = 2.0, .max_queue_size = 10000},
			[](const std::exception& e, std::vector<LogEntry>&&) {
				std::cerr << "[captcha2/log] send failed: " << e.what() << std::endl;
			});
		queue_->start();
	}

	~LogC() { if (queue_) queue_->shutdown(); }

	void push(const std::string& svc, const std::string& level, const std::string& msg) {
		// 日志级别过滤：低于阈值直接丢弃
		auto it = level_order().find(level);
		int lvl = (it != level_order().end()) ? it->second : 1;
		if (lvl < level_threshold_.load(std::memory_order_relaxed)) return;

		auto now = std::chrono::system_clock::now();
		auto t = std::chrono::system_clock::to_time_t(now);
		char buf[32];
		std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&t));

		LogEntry e;
		e.ts = buf;
		e.service = svc;
		e.level = level;
		e.message = msg;
		queue_->push(std::move(e));
	}

	void send_batch(std::vector<LogEntry>&& batch) {
		try {
			// P3-4: 复用单连接发送整批日志，超时 1.0s（日志非关键路径，db2 慢时不阻塞 flush 线程过久）
			pmc::net::MsgClient client(db_host_, db_port_, "captcha2-log");
			for (auto& e : batch) {
				boost::json::object vals;
				vals["ts"] = e.ts;
				vals["service"] = e.service;
				vals["level"] = e.level;
				vals["message"] = e.message;
				vals["trace_id"] = e.trace_id.empty() ? nullptr : boost::json::value(e.trace_id);
				vals["extra"] = e.extra.empty() ? nullptr : boost::json::value(e.extra);

				boost::json::object req;
				req["_route"] = "execute";
				req["action"] = "insert";
				req["table"] = "Logs";
				req["values"] = vals;

				client.call(boost::json::serialize(req), 1.0);
			}
		} catch (const std::exception& e) {
			std::cerr << "[captcha2/log] batch drop: " << e.what() << std::endl;
		}
	}

	std::string db_host_;
	int db_port_;
	std::atomic<int> level_threshold_{1};	// 日志级别阈值，默认 INFO(1)
	std::unique_ptr<pmc::net::AsyncQueue<LogEntry>> queue_;
};

} // namespace captcha2
