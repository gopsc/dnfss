#include "subagent.hpp"

#include "tools.hpp"

namespace {

constexpr const char* SUBAGENT_PROMPT =
    "你是机器人的子代理（专家），拥有比主代理更丰富的技能：可以执行 shell 命令、"
    "读写文件、拍照和查询状态。请利用这些技能完成主代理委派的任务，"
    "返回简洁明确的结果，用中文。";

} // namespace

std::string run_subagent(const AgentConfig& cfg, const std::string& task) {
    ToolRegistry registry;
    registry.add(make_run_command_tool());
    registry.add(make_read_file_tool());
    registry.add(make_write_file_tool());
    registry.add(make_capture_image_tool(cfg.camera_path, cfg.camera_device));
    registry.add(make_robot_status_tool());

    return run_chat_loop(cfg, SUBAGENT_PROMPT, task, registry, "子代理");
}
