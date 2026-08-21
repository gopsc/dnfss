#pragma once

#include <boost/json.hpp>

#include "tools.hpp"

#include <string>

namespace json = boost::json;

struct AgentConfig {
    std::string model;
    std::string endpoint;
    std::string api_key;
    std::string camera_path;   // 非空 = 模拟摄像头（读文件）
    std::string camera_device; // 真实摄像头设备（默认 /dev/video0）
    int max_turns = 8;
};

// 运行工具循环，直到模型给出最终回答并返回其文本。失败抛 std::runtime_error。
// log_tag 用于区分不同代理的工具日志（输出到 stderr）。
std::string run_chat_loop(const AgentConfig& cfg,
                          const std::string& system_prompt,
                          const std::string& user_question,
                          const ToolRegistry& registry,
                          const std::string& log_tag);

// 消息构造（主代理与子代理共用）
json::object user_text_msg(const std::string& text);
json::object user_image_msg(const std::string& text, const std::string& data_url);
json::object tool_result_msg(const std::string& call_id, const std::string& text);
json::value build_tools_spec(const ToolRegistry& registry);
