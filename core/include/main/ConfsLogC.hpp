/*
 * ConfsLogC.hpp — core 内嵌 confs 服务的异步日志客户端
 *
 * 基于 libmsg AsyncQueue，异步批量推送日志到 db:8005 的 Logs 表。
 * - 内存队列 + 后台线程攒批（满 50 条或 2 秒 flush）
 * - fail-safe：db 不可用时静默丢弃
 * - API: info() / warning() / error()
 *
 * 改编自 confs2/inc/LogC.hpp，仅改 namespace（qing::confs），
 * 服务名保持 "confs" 与 Python/standalone confs2 日志连续。
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
#include <algorithm>
#include <cctype>

namespace qing {
namespace confs {

struct ConfsLogEntry {
	std::string ts;
	std::string service;
	std::string level;
	std::string message;
	std::string trace_id;
	std::string extra;
};

class ConfsLogC {
public:
	static ConfsLogC& instance() {
		static ConfsLogC inst;
		return inst;
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
	ConfsLogC() {
		const char* host = std::getenv("DB_HOST");
		const char* port = std::getenv("DB_PORT");
		db_host_ = host ? host : "127.0.0.1";
		db_port_ = port ? std::atoi(port) : 8005;

		queue_ = std::make_unique<pmc::net::AsyncQueue<ConfsLogEntry>>(
			[this](std::vector<ConfsLogEntry>&& batch) { send_batch(std::move(batch)); },
			pmc::net::AsyncQueueConfig{.batch_size = 50, .flush_interval = 2.0, .max_queue_size = 10000},
			[](const std::exception& e, std::vector<ConfsLogEntry>&&) {
				std::cerr << "[core/confs-log] send failed: " << e.what() << std::endl;
			});
		queue_->start();
	}

	~ConfsLogC() { if (queue_) queue_->shutdown(); }

	// P2-3 修复：过滤控制字符，防止日志注入
	static std::string sanitize_for_log(std::string s) {
		s.erase(std::remove_if(s.begin(), s.end(), [](unsigned char c) {
			return c < 0x20 && c != '\t';
		}), s.end());
		return s;
	}

	void push(const std::string& svc, const std::string& level, const std::string& msg) {
		auto now = std::chrono::system_clock::now();
		auto t = std::chrono::system_clock::to_time_t(now);
		// 系统本地时间 + 时区偏移
		std::tm* lt = std::localtime(&t);
		char buf[40];
		std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", lt);
		char tz_raw[8];
		std::strftime(tz_raw, sizeof(tz_raw), "%z", lt);
		std::string tz = tz_raw;
		if (tz.length() >= 5)
			tz = tz.substr(0, 3) + ":" + tz.substr(3);

		ConfsLogEntry e;
		e.ts = std::string(buf) + tz;
		e.service = sanitize_for_log(svc);
		e.level = level;
		e.message = sanitize_for_log(msg);
		queue_->push(std::move(e));
	}

	void send_batch(std::vector<ConfsLogEntry>&& batch) {
		try {
			pmc::net::MsgClient client(db_host_, db_port_, "confs-log");
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
			std::cerr << "[core/confs-log] batch drop: " << e.what() << std::endl;
		}
	}

	std::string db_host_;
	int db_port_;
	std::unique_ptr<pmc::net::AsyncQueue<ConfsLogEntry>> queue_;
};

} // namespace confs
} // namespace qing
