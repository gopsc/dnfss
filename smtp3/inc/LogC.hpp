/*
 * LogC.hpp — smtp3 异步日志客户端（简化版）
 *
 * 基于 libmsg AsyncQueue，异步批量推送日志到 db:8005 的 Logs 表。
 * - 内存队列 + 后台线程攒批（满 50 条或 2 秒 flush）
 * - fail-safe：db 不可用时静默丢弃
 * - P3-5: 长连接复用（底层 openConnection + pmsg_send/recv），
 *   不再每批新建 TCP 连接；连接故障时关闭重建，本批丢弃
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

#include <unistd.h>

namespace smtp3 {

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
	static LogC& instance() {
		static LogC inst;
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
	LogC() {
		const char* host = std::getenv("DB_HOST");
		const char* port = std::getenv("DB_PORT");
		db_host_ = host ? host : "127.0.0.1";
		db_port_ = port ? std::atoi(port) : 8005;

		queue_ = std::make_unique<pmc::net::AsyncQueue<LogEntry>>(
			[this](std::vector<LogEntry>&& batch) { send_batch(std::move(batch)); },
			pmc::net::AsyncQueueConfig{.batch_size = 50, .flush_interval = 2.0, .max_queue_size = 10000},
			[](const std::exception& e, std::vector<LogEntry>&&) {
				std::cerr << "[smtp3/log] send failed: " << e.what() << std::endl;
			});
		queue_->start();
	}

	~LogC() {
		if (queue_) queue_->shutdown();
		if (fd_ >= 0) ::close(fd_);
	}

	void push(const std::string& svc, const std::string& level, const std::string& msg) {
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

	// P3-5: 长连接复用——同一 fd 上逐条 pmsg_send + pmsg_recv（db 对每条
	// execute 都回复，须消费以保持帧同步）；任一异常即关闭连接、丢弃本批
	void send_batch(std::vector<LogEntry>&& batch) {
		try {
			if (fd_ < 0)
				fd_ = pmc::net::detail::openConnection(db_host_, db_port_, 1.0);
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

				// 与 MsgClient::call 线路格式一致：无前缀，逐条请求-应答
				pmc::net::pmsg_send(fd_, boost::json::serialize(req));
				(void)pmc::net::pmsg_recv(fd_, 1.0);
			}
		} catch (const std::exception& e) {
			if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
			std::cerr << "[smtp3/log] batch drop: " << e.what() << std::endl;
		}
	}

	std::string db_host_;
	int db_port_;
	std::unique_ptr<pmc::net::AsyncQueue<LogEntry>> queue_;
	int fd_ = -1;   // 长连接 fd（仅后台 flush 线程访问，无并发）
};

} // namespace smtp3
