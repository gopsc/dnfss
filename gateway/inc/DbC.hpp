#pragma once
#include <us/HttpClient.hpp>
#include <us/Msg.hpp>
#include <boost/json.hpp>
namespace qing {
	namespace json = boost::json;
	class DbC {
		public:
			/* 本机 db 轻量查询的默认超时（秒）；慢操作由调用方显式放宽。
			 * smtp/captcha 等其他下游的超时常量见 main.cpp。 */
			static constexpr double DEFAULT_TIMEOUT = 5.0;

			/// 初始化 db 服务地址（启动时从配置设置，默认 127.0.0.1:8005）
			static void init(const std::string& host, int port) {
				_host = host;
				_port = port;
			}

			// 【保留】旧版 HTTP 接口：通过 HttpClient POST 到 dbv2 HTTP /execute 端点。
			// 保留用于兼容性参考，新代码优先使用 call_msg()（pymsg TCP 协议，更可靠）。
			static json::value call_http(json::value& params) {
				
				auto client = pmc::net::HttpClient();
				client.set_header("Content-Type: application/json");
				std::string payload = json::serialize(params);
				std::string res = "";
				std::string url = "http://" + _host + ":" + std::to_string(_port) + "/execute";
				client.post_stream_sync(url, payload, [&res](const auto& ret) {
						res += ret;
				});
				return json::parse(res);
			}

			// 新版 pymsg 接口：通过 MsgClient（TCP 长度前缀帧协议）调用 dbv2。
			// 自动添加 _route = "execute"，返回 dbv2 pymsg 完整 JSON 响应
			//   {"success":..., "data":[...]}。
			// 响应格式与 call_http 兼容，调用方可直接替换。
			// db 是强依赖，启用 retry_on_refused：db 未启动时阻塞等待，避免启动顺序问题。
			static json::value call_msg(const json::value& params,
					double timeout = DEFAULT_TIMEOUT) {
				json::value jv_route = params;
				jv_route.as_object()["_route"] = "execute";
				pmc::net::MsgClient client(_host, _port, "gateway");
				std::string resp = client.call(
					json::serialize(jv_route), timeout,
					/*retry_on_refused=*/true);
				return json::parse(resp);
			}

			static const std::string& host() { return _host; }
			static int port() { return _port; }

		private:
			static inline std::string _host = "127.0.0.1";
			static inline int _port = 8005;
	};
}
