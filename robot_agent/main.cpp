#include "agent.hpp"
#include "tools.hpp"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace {

constexpr const char* DEFAULT_ENDPOINT = "https://api.deepseek.com/chat/completions";
constexpr const char* DEFAULT_MODEL = "deepseek-v4-flash-vision-exp";

constexpr const char* MAIN_SYSTEM_PROMPT =
    "你是控制硬件机器人的主智能体。当用户的问题涉及观察环境、看东西、识别物体时，"
    "必须先调用 capture_image 工具获取照片再回答。"
    "当任务需要执行 shell 命令、读写文件等操作时，调用 spawn_subagent 工具委派给子代理，"
    "不要自己尝试执行。回答使用中文。";

struct Options {
    std::string question;
    std::string prompt_file;  // 非空 = 从文件读取问题
    std::string camera_path;   // 非空 = 模拟摄像头（读文件）
    std::string camera_device; // 空 = 自动探测 /dev/video*
    std::string model = DEFAULT_MODEL;
    std::string endpoint = DEFAULT_ENDPOINT;
    std::string api_key;
    int max_turns = 8;
};

void print_usage(const char* prog) {
    std::cerr << "用法: " << prog << " \"问题\" [选项]\n"
              << "      " << prog << " --prompt-file <文件> [选项]\n"
              << "选项:\n"
              << "  --prompt-file <文件>  从文件读取问题（与位置参数互斥）\n"
              << "  --camera <文件>    模拟摄像头：拍照工具读该图片（不指定则用真实摄像头）\n"
              << "  --camera-device <设备>  摄像头设备（默认自动探测 /dev/video*）\n"
              << "  --model <名称>     模型名（默认 " << DEFAULT_MODEL << "）\n"
              << "  --key <API_KEY>    API key（默认读环境变量 DEEPSEEK_API_KEY）\n"
              << "  --endpoint <URL>   接口地址（默认 DeepSeek 官方）\n"
              << "  --max-turns <N>    最大工具轮次（默认 8）\n";
}

Options parse_args(int argc, char** argv) {
    Options opt;
    if (const char* key_env = std::getenv("DEEPSEEK_API_KEY")) opt.api_key = key_env;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto next = [&](const char* name) -> std::string {
            if (i + 1 >= argc) {
                std::cerr << "错误: " << name << " 缺少参数\n";
                std::exit(2);
            }
            return argv[++i];
        };
        if (arg == "--camera") opt.camera_path = next("--camera");
        else if (arg == "--camera-device") opt.camera_device = next("--camera-device");
        else if (arg == "--prompt-file") opt.prompt_file = next("--prompt-file");
        else if (arg == "--model") opt.model = next("--model");
        else if (arg == "--key") opt.api_key = next("--key");
        else if (arg == "--endpoint") opt.endpoint = next("--endpoint");
        else if (arg == "--max-turns") opt.max_turns = std::stoi(next("--max-turns"));
        else if (arg == "--help" || arg == "-h") { print_usage(argv[0]); std::exit(0); }
        else if (!arg.empty() && arg[0] == '-') {
            std::cerr << "错误: 未知选项 " << arg << "\n";
            print_usage(argv[0]);
            std::exit(2);
        } else {
            opt.question = arg;
        }
    }
    return opt;
}

} // namespace

int main(int argc, char** argv) {
    Options opt = parse_args(argc, argv);

    if (!opt.prompt_file.empty()) {
        if (!opt.question.empty()) {
            std::cerr << "错误: --prompt-file 与位置参数问题不能同时使用\n";
            return 2;
        }
        std::ifstream f(opt.prompt_file);
        if (!f) {
            std::cerr << "错误: 无法读取提示词文件 " << opt.prompt_file << "\n";
            return 2;
        }
        std::ostringstream ss;
        ss << f.rdbuf();
        opt.question = ss.str();
    }

    if (opt.question.empty()) {
        std::cerr << "错误: 缺少问题参数（用位置参数或 --prompt-file）\n";
        print_usage(argv[0]);
        return 2;
    }
    if (opt.api_key.empty()) {
        std::cerr << "错误: 缺少 API key（用 --key 或设置环境变量 DEEPSEEK_API_KEY）\n";
        return 2;
    }

    AgentConfig cfg;
    cfg.model = opt.model;
    cfg.endpoint = opt.endpoint;
    cfg.api_key = opt.api_key;
    cfg.camera_path = opt.camera_path;
    cfg.camera_device = opt.camera_device;
    cfg.max_turns = opt.max_turns;

    ToolRegistry registry;
    registry.add(make_capture_image_tool(opt.camera_path, opt.camera_device));
    registry.add(make_robot_status_tool());
    registry.add(make_spawn_subagent_tool(cfg));

    try {
        std::string answer =
            run_chat_loop(cfg, MAIN_SYSTEM_PROMPT, opt.question, registry, "主代理");
        std::cout << answer << "\n";
    } catch (const std::exception& e) {
        std::cerr << "错误: " << e.what() << "\n";
        return 1;
    }
    return 0;
}
