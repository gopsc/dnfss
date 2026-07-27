#pragma once
#include <us/HttpClient.hpp>
#include "Msg.hpp"
#include <boost/json.hpp>
namespace qing {
	namespace json = boost::json;
	class DbC {
		public:
			// 【保留】旧版 HTTP 接口：通过 HttpClient POST 到 dbv2 HTTP /execute 端点。
			// 保留用于兼容性参考，新代码优先使用 call_msg()（pymsg TCP 协议，更可靠）。
			static json::value call_http(json::value& params) {
				
				auto client = pmc::net::HttpClient();
				client.set_header("Content-Type: application/json");
				std::string payload = json::serialize(params);
				std::string res = "";
				client.post_stream_sync(_DBURL, payload, [&res](const auto& ret) {
						res += ret;
				});
				return json::parse(res);
			}

			// 新版 pymsg 接口：通过 MsgClient（TCP 长度前缀帧协议）调用 dbv2。
			// 自动添加 _route = "execute"，返回 dbv2 pymsg 完整 JSON 响应
			//   {"success":..., "data":[...]}。
			// 响应格式与 call_http 兼容，调用方可直接替换。
			static json::value call_msg(const json::value& params,
					double timeout = 5.0) {
				json::value jv_route = params;
				jv_route.as_object()["_route"] = "execute";
				pmc::net::MsgClient client("127.0.0.1", 8005, "gateway");
				std::string resp = client.call(
					json::serialize(jv_route), timeout);
				return json::parse(resp);
			}

		private:
			static constexpr const char* _DBURL = "http://127.0.0.1:8005/execute";
	};
}
