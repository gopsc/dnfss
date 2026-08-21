#pragma once

#include <boost/json.hpp>

#include <functional>
#include <string>
#include <vector>

namespace json = boost::json;

struct AgentConfig; // 前向声明（定义见 agent.hpp），避免头文件循环依赖

// 工具执行结果。image_data_url 非空表示工具返回了一张图片，
// 由 agent 主循环把图片注入下一轮对话（OpenAI 协议的 tool 消息只允许文本）。
struct ToolResult {
    std::string text;
    std::string image_data_url;
};

struct Tool {
    std::string name;
    std::string description;
    json::value parameters; // JSON Schema（object）
    std::function<ToolResult(const json::value& args)> execute;
};

class ToolRegistry {
public:
    void add(Tool tool) { tools_.push_back(std::move(tool)); }

    const std::vector<Tool>& all() const { return tools_; }

    bool execute(const std::string& name, const json::value& args, ToolResult& out) const {
        for (const auto& t : tools_) {
            if (t.name == name) {
                out = t.execute(args);
                return true;
            }
        }
        return false;
    }

private:
    std::vector<Tool> tools_;
};

// 拍照工具：simulated_image_path 非空时读取该文件（模拟模式），
// 否则从真实摄像头设备（V4L2，默认 /dev/video0）抓帧。
Tool make_capture_image_tool(const std::string& simulated_image_path,
                             const std::string& camera_device);

// 状态查询工具：返回模拟的机器人状态。
Tool make_robot_status_tool();

// 命令行工具：执行 shell 命令（带超时与输出截断）。
Tool make_run_command_tool();

// 文件工具：读取/写入文本文件。
Tool make_read_file_tool();
Tool make_write_file_tool();

// 子代理工具：把任务委派给拥有扩展技能的子代理执行。
Tool make_spawn_subagent_tool(const AgentConfig& cfg);
