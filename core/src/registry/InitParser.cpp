#include "registry/InitParser.hh"

#include <yaml-cpp/yaml.h>
#include <fstream>
#include <sstream>
#include <iostream>
#include <filesystem>

namespace qing::registry {

/* -------- ServiceConfig -------- */

std::string ServiceConfig::toCommand() const {
    std::ostringstream oss;
    oss << cmd;
    for (const auto& a : args) {
        oss << ' ' << a;
    }
    return oss.str();
}

/* -------- InitParser -------- */

std::vector<ServiceConfig> InitParser::load(const std::string& dir) {
    namespace fs = std::filesystem;

    auto yaml_path = fs::path(dir) / "services.yaml";
    auto txt_path  = fs::path(dir) / "init.txt";

    /* 优先 services.yaml */
    if (fs::exists(yaml_path)) {
        std::cerr << "[InitParser] 使用 services.yaml: " << yaml_path << std::endl;
        return parseYaml(yaml_path.string());
    }

    /* 回退 init.txt */
    if (fs::exists(txt_path)) {
        std::cerr << "[InitParser] 回退 init.txt: " << txt_path << std::endl;
        return parseTxt(txt_path.string());
    }

    std::cerr << "[InitParser] 未找到 services.yaml 或 init.txt 于 " << dir << std::endl;
    return {};
}

std::vector<ServiceConfig> InitParser::parseYaml(const std::string& path) {
    std::vector<ServiceConfig> result;

    YAML::Node root;
    try {
        root = YAML::LoadFile(path);
    } catch (const std::exception& e) {
        std::cerr << "[InitParser] YAML 解析失败: " << e.what() << std::endl;
        return {};
    }

    if (!root["services"] || !root["services"].IsSequence()) {
        std::cerr << "[InitParser] services.yaml 缺少 services 序列" << std::endl;
        return {};
    }

    for (const auto& node : root["services"]) {
        ServiceConfig svc;

        if (node["name"])
            svc.name = node["name"].as<std::string>();

        if (node["cmd"])
            svc.cmd = node["cmd"].as<std::string>();

        if (node["args"] && node["args"].IsSequence())
            for (const auto& a : node["args"])
                svc.args.push_back(a.as<std::string>());

        if (node["env"] && node["env"].IsMap())
            for (const auto& kv : node["env"])
                svc.env[kv.first.as<std::string>()] = kv.second.as<std::string>();

        if (node["depends_on"] && node["depends_on"].IsSequence())
            for (const auto& d : node["depends_on"])
                svc.depends_on.push_back(d.as<std::string>());

        if (node["sleep_before"])
            svc.sleep_before = node["sleep_before"].as<int>(0);

        if (svc.cmd.empty()) {
            std::cerr << "[InitParser] 跳过缺少 cmd 的服务: " << svc.name << std::endl;
            continue;
        }

        result.push_back(std::move(svc));
    }

    std::cerr << "[InitParser] 解析完成，共 " << result.size() << " 个服务" << std::endl;
    return result;
}

std::vector<ServiceConfig> InitParser::parseTxt(const std::string& path) {
    std::vector<ServiceConfig> result;

    std::ifstream file(path);
    if (!file.is_open()) {
        std::cerr << "[InitParser] 无法打开 init.txt: " << path << std::endl;
        return {};
    }

    std::string line;
    while (std::getline(file, line)) {
        /* 去除注释（# 之后的内容） */
        auto pos = line.find('#');
        if (pos != std::string::npos)
            line = line.substr(0, pos);

        /* trim 空白 */
        auto first = line.find_first_not_of(" \t\r\n");
        if (first == std::string::npos)
            continue;  /* 空行 */
        auto last = line.find_last_not_of(" \t\r\n");
        line = line.substr(first, last - first + 1);

        ServiceConfig svc;
        svc.cmd = line;
        /* init.txt 模式下 name/args/env/depends_on/sleep_before 均为空/默认 */
        result.push_back(std::move(svc));
    }

    std::cerr << "[InitParser] init.txt 解析完成，共 " << result.size() << " 个服务" << std::endl;
    return result;
}

} // namespace qing::registry
