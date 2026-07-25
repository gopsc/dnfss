#pragma once
#include <us/HttpClient.hpp>
#include <boost/json.hpp>
namespace qing {
	namespace json = boost::json;
	class DbC {
		public:
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
		private:
			static constexpr const char* _DBURL = "http://127.0.0.1:8005/execute";
	};
}
