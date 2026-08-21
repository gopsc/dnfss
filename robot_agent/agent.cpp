#include "agent.hpp"

#include <us/HttpClient.hpp>

#include <boost/system/error_code.hpp>

#include <ctime>
#include <iostream>
#include <stdexcept>

using pmc::net::HttpClient;

namespace {

// 本地时间 ISO8601，如 2026-08-21T14:30:05+08:00
std::string now_iso8601() {
    std::time_t tt = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm tm{};
    localtime_r(&tt, &tm);
    char buf[64];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S%z", &tm);
    std::string s(buf);
    if (s.size() >= 5 && (s[s.size() - 5] == '+' || s[s.size() - 5] == '-'))
        s.insert(s.size() - 2, ":");
    return s;
}

} // namespace

json::object user_text_msg(const std::string& text) {
    return json::object{{"role", "user"}, {"content", text}};
}

json::object user_image_msg(const std::string& text, const std::string& data_url) {
    return json::object{
        {"role", "user"},
        {"content", json::array{
            json::object{{"type", "text"}, {"text", text}},
            json::object{{"type", "image_url"},
                         {"image_url", json::object{{"url", data_url}}}},
        }},
    };
}

json::object tool_result_msg(const std::string& call_id, const std::string& text) {
    return json::object{{"role", "tool"}, {"tool_call_id", call_id}, {"content", text}};
}

json::value build_tools_spec(const ToolRegistry& registry) {
    json::array arr;
    for (const auto& t : registry.all()) {
        arr.push_back(json::object{
            {"type", "function"},
            {"function", json::object{
                {"name", t.name},
                {"description", t.description},
                {"parameters", t.parameters},
            }},
        });
    }
    return arr;
}

std::string run_chat_loop(const AgentConfig& cfg,
                          const std::string& system_prompt,
                          const std::string& user_question,
                          const ToolRegistry& registry,
                          const std::string& log_tag) {
    json::array messages;
    // 会话开始注入一次当前时间（会话内保持不变，避免模型混淆"现在"）
    std::string timed_prompt =
        system_prompt + "\n当前时间: " + now_iso8601() + "（本会话内保持不变）";
    messages.push_back(json::object{{"role", "system"}, {"content", timed_prompt}});
    messages.push_back(user_text_msg(user_question));

    HttpClient client;
    client.set_header("Authorization: Bearer " + cfg.api_key);
    client.set_header("Content-Type: application/json");

    for (int turn = 0; turn < cfg.max_turns; ++turn) {
        json::object body;
        body["model"] = cfg.model;
        body["messages"] = messages;
        body["tools"] = build_tools_spec(registry);
        body["stream"] = false;

        std::string response_body;
        client.post_stream_sync(cfg.endpoint, json::serialize(body),
                                [&](const std::string& chunk) { response_body += chunk; });

        long code = client.get_response_code();
        if (code != 200)
            throw std::runtime_error("HTTP " + std::to_string(code) + ": " + response_body);

        boost::system::error_code ec;
        json::value resp = json::parse(response_body, ec);
        if (ec)
            throw std::runtime_error("响应不是合法 JSON: " + response_body.substr(0, 200));
        if (resp.as_object().contains("error"))
            throw std::runtime_error("API 错误: " + json::serialize(resp.at("error")));

        auto& choices = resp.at("choices").as_array();
        if (choices.empty()) throw std::runtime_error("响应中没有 choices");
        auto& message = choices[0].as_object().at("message").as_object();

        auto tc_it = message.find("tool_calls");
        bool has_tool_calls = tc_it != message.end() && !tc_it->value().is_null() &&
                              !tc_it->value().as_array().empty();

        if (has_tool_calls) {
            messages.push_back(message); // 回传 assistant 的 tool_calls 消息

            for (const auto& call : tc_it->value().as_array()) {
                const auto& c = call.as_object();
                std::string call_id =
                    c.contains("id") ? json::value_to<std::string>(c.at("id")) : "";
                const auto& fn = c.at("function").as_object();
                std::string name = json::value_to<std::string>(fn.at("name"));

                json::value args;
                auto args_it = fn.find("arguments");
                if (args_it != fn.end() && !args_it->value().is_null()) {
                    std::string args_str = json::value_to<std::string>(args_it->value());
                    boost::system::error_code aec;
                    args = json::parse(args_str, aec);
                    if (aec) args = json::object();
                } else {
                    args = json::object();
                }

                ToolResult result;
                if (!registry.execute(name, args, result))
                    result = {"错误: 未知工具 " + name, ""};

                // 工具结果统一附执行时间戳，供模型判断数据新旧
                std::string timed_result = "工具执行时间: " + now_iso8601() + "\n" + result.text;
                std::cerr << "[" << log_tag << "][工具] " << name << " → "
                          << timed_result.substr(0, 120) << "\n";
                messages.push_back(tool_result_msg(call_id, timed_result));
                if (!result.image_data_url.empty())
                    messages.push_back(
                        user_image_msg("工具返回的图片如下，请结合图片继续回答。",
                                       result.image_data_url));
            }
            continue;
        }

        if (message.contains("content") && message.at("content").is_string())
            return json::value_to<std::string>(message.at("content"));
        throw std::runtime_error("模型没有返回文本内容");
    }

    throw std::runtime_error("达到最大工具轮次 (" + std::to_string(cfg.max_turns) +
                             ")，未获得最终回答");
}
