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

    if (fs::exists(yaml_path)) {
        std::cerr << "[InitParser] 使用 services.yaml: " << yaml_path << std::endl;
        return parseYaml(yaml_path.string());
    }

    std::cerr << "[InitParser] 未找到 services.yaml 于 " << dir << std::endl;
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



} // namespace qing::registry
