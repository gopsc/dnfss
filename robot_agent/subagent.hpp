#pragma once

#include "agent.hpp"

#include <string>

// 子代理：拥有命令行执行、文件读写等扩展技能（主代理的超集），
// 完成主代理委派的任务后返回结论文本。
std::string run_subagent(const AgentConfig& cfg, const std::string& task);
