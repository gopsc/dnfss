/** LogC.cpp — 统一集中式日志客户端实现
 *
 * 包含 _send_batch（pymsg → db execute INSERT）和 _utc_now_iso（ISO 8601）。
 * 编译为 libus_logc.so，运行时依赖 libus_msg.so。
 */

#include <us/LogC.hpp>

#include <chrono>
#include <cstdio>
#include <ctime>
#include <iostream>
#include <memory>

#include <boost/json.hpp>

#include <us/AsyncQueue.hpp>
#include <us/Msg.hpp>

namespace json = boost::json;

namespace qing {

// ---- 生命周期 ---------------------------------------------------------------

LogC::LogC()
	: _queue(std::make_unique<pmc::net::AsyncQueue<Entry>>(
		[this](std::vector<Entry>&& batch) { this->_send_batch(std::move(batch)); },
		pmc::net::AsyncQueueConfig{_BATCH_SIZE, _FLUSH_INTERVAL, _MAX_QUEUE_SIZE, 0.5},
		[](const std::exception& e, std::vector<Entry>&&) {
			std::cerr << "[LogC] 发送失败: " << e.what() << std::endl;
		}))
{
	_queue->start();
}

LogC::~LogC() { shutdown(); }

// ---- 配置接口 ---------------------------------------------------------------

void LogC::set_endpoint(const std::string& addr, int port) {
	std::lock_guard<std::mutex> lock(_endpoint_mtx);
	_db_addr = addr;
	_db_port = port;
}

void LogC::set_service_name(const std::string& name) {
	std::lock_guard<std::mutex> lock(_endpoint_mtx);
	_service_name = name;
}

void LogC::set_level(const std::string& level) {
	auto it = level_order().find(level);
	if (it != level_order().end()) {
		_level_threshold.store(it->second, std::memory_order_relaxed);
	}
}

// ---- 推送 -------------------------------------------------------------------

void LogC::push(const std::string& service, const std::string& level,
		const std::string& message,
		std::optional<std::string> trace_id,
		std::optional<std::string> extra) {
	auto it = level_order().find(level);
	int lvl = (it != level_order().end()) ? it->second : 1;
	if (lvl < _level_threshold.load(std::memory_order_relaxed)) return;
	_queue->push(Entry{service, level, message,
			std::move(trace_id), std::move(extra)});
}

void LogC::flush()    { _queue->flush(); }
void LogC::shutdown() { _queue->shutdown(); }

// ---- 内部实现 ---------------------------------------------------------------

void LogC::_send_batch(std::vector<Entry>&& batch) {
	std::string addr;
	int port;
	std::string client_name;
	{
		std::lock_guard<std::mutex> lock(_endpoint_mtx);
		addr = _db_addr;
		port = _db_port;
		client_name = _service_name;
	}

	const std::string now = _utc_now_iso();
	try {
		pmc::net::MsgClient client(addr, port, client_name);
		for (const auto& e : batch) {
			json::object values;
			values["ts"]       = now;
			values["service"]  = e.service;
			values["level"]    = e.level;
			values["message"]  = e.message;
			values["trace_id"] = e.trace_id ? json::value(*e.trace_id)
					: json::value(nullptr);
			values["extra"]    = e.extra ? json::value(*e.extra)
					: json::value(nullptr);

			json::object payload;
			payload["_route"] = "execute";
			payload["action"] = "insert";
			payload["table"]  = "Logs";
			payload["values"] = std::move(values);

			const std::string resp = client.call(
					json::serialize(payload), _TIMEOUT,
					/*retry_on_refused=*/false);
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

std::string LogC::_utc_now_iso() {
	const auto now = std::chrono::system_clock::now();
	const auto t   = std::chrono::system_clock::to_time_t(now);
	const auto us  = std::chrono::duration_cast<std::chrono::microseconds>(
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

} // namespace qing
