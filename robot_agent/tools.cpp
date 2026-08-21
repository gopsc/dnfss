#include "tools.hpp"

#include "agent.hpp"
#include "camera.hpp"
#include "subagent.hpp"

#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <fcntl.h>
#include <fstream>
#include <memory>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>

namespace {

std::string base64_encode(const std::string& in) {
    static const char table[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve((in.size() + 2) / 3 * 4);
    for (size_t i = 0; i < in.size(); i += 3) {
        unsigned n = static_cast<unsigned char>(in[i]) << 16;
        if (i + 1 < in.size()) n |= static_cast<unsigned char>(in[i + 1]) << 8;
        if (i + 2 < in.size()) n |= static_cast<unsigned char>(in[i + 2]);
        out.push_back(table[(n >> 18) & 0x3F]);
        out.push_back(table[(n >> 12) & 0x3F]);
        out.push_back(i + 1 < in.size() ? table[(n >> 6) & 0x3F] : '=');
        out.push_back(i + 2 < in.size() ? table[n & 0x3F] : '=');
    }
    return out;
}

std::string mime_from_path(const std::string& path) {
    auto pos = path.find_last_of('.');
    std::string ext = (pos == std::string::npos) ? "" : path.substr(pos + 1);
    if (ext == "jpg" || ext == "jpeg") return "image/jpeg";
    if (ext == "png") return "image/png";
    if (ext == "webp") return "image/webp";
    if (ext == "gif") return "image/gif";
    throw std::runtime_error("不支持的图片格式: " + ext);
}

} // namespace

Tool make_capture_image_tool(const std::string& simulated_image_path,
                             const std::string& camera_device) {
    Tool t;
    t.name = "capture_image";
    t.description =
        "拍摄一张机器人当前环境的照片。当用户的问题涉及观察、看、识别物体或了解周围环境时，"
        "必须先调用本工具获取照片，再基于照片回答。";
    t.parameters = json::object{{"type", "object"}, {"properties", json::object{}}};

    // 摄像头实例懒加载并常开复用，避免每次抓帧都重新打开设备
    auto camera = std::make_shared<std::shared_ptr<CameraCapture>>();

    t.execute = [simulated_image_path, camera_device, camera](const json::value&) -> ToolResult {
        if (!simulated_image_path.empty()) {
            std::ifstream f(simulated_image_path, std::ios::binary);
            if (!f)
                return {"错误: 无法读取模拟摄像头图片 " + simulated_image_path, ""};
            std::ostringstream ss;
            ss << f.rdbuf();
            std::string data_url;
            try {
                data_url = "data:" + mime_from_path(simulated_image_path) + ";base64," +
                           base64_encode(ss.str());
            } catch (const std::exception& e) {
                return {std::string("错误: ") + e.what(), ""};
            }
            return {"已拍摄当前环境照片", data_url};
        }

        try {
            if (!*camera)
                *camera = std::make_shared<CameraCapture>(camera_device);
            std::string jpeg = (*camera)->grab_jpeg();
            return {"已拍摄当前环境照片（" + std::to_string((*camera)->width()) + "x" +
                        std::to_string((*camera)->height()) + "）",
                    "data:image/jpeg;base64," + base64_encode(jpeg)};
        } catch (const std::exception& e) {
            camera->reset(); // 设备可能重新插拔，下次调用重试打开
            return {std::string("摄像头抓拍失败: ") + e.what(), ""};
        }
    };
    return t;
}

Tool make_robot_status_tool() {
    Tool t;
    t.name = "get_robot_status";
    t.description = "查询机器人当前状态（电量、温度、电机、传感器）。用户询问机器人状态时调用。";
    t.parameters = json::object{{"type", "object"}, {"properties", json::object{}}};
    t.execute = [](const json::value&) -> ToolResult {
        return {"电量 85%，电机正常，温度 42°C，传感器无告警", ""};
    };
    return t;
}

// ---- 命令行执行 ----

constexpr size_t MAX_STREAM_BYTES = 64 * 1024; // 单个流输出上限
constexpr int MAX_CMD_TIMEOUT_S = 60;

struct RunResult {
    int exit_code = -1;
    bool timed_out = false;
    bool truncated = false;
    std::string out;
    std::string err;
};

// 追加输出，超出上限则丢弃并标记截断
void append_capped(std::string& dst, const char* p, size_t n, bool& truncated) {
    size_t room = MAX_STREAM_BYTES - dst.size();
    if (n > room) {
        dst.append(p, room);
        truncated = true;
    } else {
        dst.append(p, n);
    }
}

RunResult run_shell(const std::string& cmd, int timeout_s) {
    int po[2], pe[2];
    if (pipe(po) != 0 || pipe(pe) != 0) throw std::runtime_error("pipe 失败");
    pid_t pid = fork();
    if (pid < 0) throw std::runtime_error("fork 失败");
    if (pid == 0) {
        dup2(po[1], STDOUT_FILENO);
        dup2(pe[1], STDERR_FILENO);
        close(po[0]);
        close(po[1]);
        close(pe[0]);
        close(pe[1]);
        execl("/bin/sh", "sh", "-c", cmd.c_str(), static_cast<char*>(nullptr));
        _exit(127);
    }
    close(po[1]);
    close(pe[1]);
    fcntl(po[0], F_SETFL, O_NONBLOCK);
    fcntl(pe[0], F_SETFL, O_NONBLOCK);

    RunResult r;
    bool exited = false, out_eof = false, err_eof = false, killed = false;
    int status = 0;
    std::array<char, 4096> buf;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout_s);
    auto hard_deadline = deadline + std::chrono::seconds(3);

    while (!(exited && out_eof && err_eof)) {
        struct pollfd fds[2] = {
            {out_eof ? -1 : po[0], POLLIN, 0},
            {err_eof ? -1 : pe[0], POLLIN, 0},
        };
        if (poll(fds, 2, 100) > 0) {
            for (const auto& fd : fds) {
                if (fd.fd < 0 || !(fd.revents & (POLLIN | POLLHUP))) continue;
                std::string& sink = (fd.fd == po[0]) ? r.out : r.err;
                ssize_t n;
                while ((n = read(fd.fd, buf.data(), buf.size())) > 0)
                    append_capped(sink, buf.data(), n, r.truncated);
                if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
                    close(fd.fd);
                    if (fd.fd == po[0]) out_eof = true;
                    else err_eof = true;
                }
            }
        }
        if (!exited) {
            pid_t w = waitpid(pid, &status, WNOHANG);
            if (w == pid) exited = true;
        }
        auto now = std::chrono::steady_clock::now();
        if (!killed && now >= deadline) {
            kill(pid, SIGKILL);
            killed = true;
            r.timed_out = true;
        }
        if (now >= hard_deadline) break;
    }

    if (!out_eof) close(po[0]);
    if (!err_eof) close(pe[0]);
    if (!exited) waitpid(pid, &status, 0); // 已被 SIGKILL，等待回收
    r.exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    return r;
}

std::string format_run_result(const RunResult& r) {
    std::string s = "exit_code=" + std::to_string(r.exit_code);
    if (r.timed_out) s += "（超时，已终止）";
    s += "\nstdout:\n" + (r.out.empty() ? "(空)" : r.out);
    s += "\nstderr:\n" + (r.err.empty() ? "(空)" : r.err);
    if (r.truncated) s += "\n[输出超过 64KB，已截断]";
    return s;
}

Tool make_run_command_tool() {
    Tool t;
    t.name = "run_command";
    t.description =
        "在机器人主机上执行 shell 命令，返回退出码、标准输出和标准错误。"
        "可用于查看系统状态、操作硬件等。命令有超时限制，输出超过 64KB 会被截断。";
    t.parameters = json::object{
        {"type", "object"},
        {"properties", json::object{
            {"command", json::object{{"type", "string"}, {"description", "要执行的 shell 命令"}}},
            {"timeout_seconds", json::object{{"type", "integer"},
                                             {"description", "超时秒数，默认 10，最大 60"}}},
        }},
        {"required", json::array{"command"}},
    };
    t.execute = [](const json::value& args) -> ToolResult {
        const auto& obj = args.as_object();
        auto cit = obj.find("command");
        if (cit == obj.end() || !cit->value().is_string())
            return {"错误: 缺少 command 参数", ""};
        std::string cmd = json::value_to<std::string>(cit->value());

        int timeout_s = 10;
        auto tit = obj.find("timeout_seconds");
        if (tit != obj.end() && tit->value().is_int64())
            timeout_s = static_cast<int>(tit->value().as_int64());
        if (timeout_s < 1) timeout_s = 1;
        if (timeout_s > MAX_CMD_TIMEOUT_S) timeout_s = MAX_CMD_TIMEOUT_S;

        try {
            return {format_run_result(run_shell(cmd, timeout_s)), ""};
        } catch (const std::exception& e) {
            return {std::string("执行失败: ") + e.what(), ""};
        }
    };
    return t;
}

// ---- 文件读写 ----

Tool make_read_file_tool() {
    Tool t;
    t.name = "read_file";
    t.description = "读取文本文件内容。超过 64KB 截断。";
    t.parameters = json::object{
        {"type", "object"},
        {"properties", json::object{
            {"path", json::object{{"type", "string"}, {"description", "文件路径"}}},
        }},
        {"required", json::array{"path"}},
    };
    t.execute = [](const json::value& args) -> ToolResult {
        auto it = args.as_object().find("path");
        if (it == args.as_object().end() || !it->value().is_string())
            return {"错误: 缺少 path 参数", ""};
        std::string path = json::value_to<std::string>(it->value());
        std::ifstream f(path, std::ios::binary);
        if (!f) return {"错误: 无法读取文件 " + path, ""};
        std::string content((std::istreambuf_iterator<char>(f)),
                            std::istreambuf_iterator<char>());
        bool truncated = content.size() > MAX_STREAM_BYTES;
        if (truncated) content.resize(MAX_STREAM_BYTES);
        return {content + (truncated ? "\n[内容已截断]" : ""), ""};
    };
    return t;
}

Tool make_write_file_tool() {
    Tool t;
    t.name = "write_file";
    t.description = "写入文本文件（覆盖已有内容）。";
    t.parameters = json::object{
        {"type", "object"},
        {"properties", json::object{
            {"path", json::object{{"type", "string"}, {"description", "文件路径"}}},
            {"content", json::object{{"type", "string"}, {"description", "要写入的内容"}}},
        }},
        {"required", json::array{"path", "content"}},
    };
    t.execute = [](const json::value& args) -> ToolResult {
        const auto& obj = args.as_object();
        auto pit = obj.find("path");
        auto cit = obj.find("content");
        if (pit == obj.end() || !pit->value().is_string() || cit == obj.end() ||
            !cit->value().is_string())
            return {"错误: 需要 path 和 content 参数", ""};
        std::string path = json::value_to<std::string>(pit->value());
        std::string content = json::value_to<std::string>(cit->value());
        std::ofstream f(path, std::ios::binary);
        if (!f) return {"错误: 无法写入文件 " + path, ""};
        f.write(content.data(), static_cast<std::streamsize>(content.size()));
        if (!f) return {"错误: 写入失败 " + path, ""};
        return {"已写入 " + std::to_string(content.size()) + " 字节到 " + path, ""};
    };
    return t;
}

// ---- 子代理 ----

Tool make_spawn_subagent_tool(const AgentConfig& cfg) {
    Tool t;
    t.name = "spawn_subagent";
    t.description =
        "委派任务给子代理执行。子代理拥有扩展技能：执行 shell 命令、读写文件、拍照、"
        "查询状态。当任务需要运行命令、读写文件等主代理不具备的能力时调用。";
    t.parameters = json::object{
        {"type", "object"},
        {"properties", json::object{
            {"task", json::object{{"type", "string"}, {"description", "委派给子代理的具体任务"}}},
        }},
        {"required", json::array{"task"}},
    };
    t.execute = [cfg](const json::value& args) -> ToolResult {
        auto it = args.as_object().find("task");
        if (it == args.as_object().end() || !it->value().is_string())
            return {"错误: 缺少 task 参数", ""};
        std::string task = json::value_to<std::string>(it->value());
        try {
            return {run_subagent(cfg, task), ""};
        } catch (const std::exception& e) {
            return {std::string("子代理执行失败: ") + e.what(), ""};
        }
    };
    return t;
}
