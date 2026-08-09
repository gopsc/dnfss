#pragma once

#include <string>
#include <vector>
#include <map>

namespace qing::registry {

/* 服务启动配置项
 *
 * 从 services.yaml 或 init.txt 解析出的单个服务声明。
 * YAML 模式下包含完整的元数据；TXT 模式下仅 cmd 有值。
 */
struct ServiceConfig {
    std::string name;                           /* 服务唯一标识 */
    std::string cmd;                            /* 启动命令（如 bash /opt/.../_run.sh） */
    std::vector<std::string> args;              /* 传递给 _run.sh 的命令行参数 */
    std::map<std::string, std::string> env;     /* 启动前设置的环境变量 */
    std::vector<std::string> depends_on;        /* 依赖的服务名列表（未来 DAG 排序用） */
    int sleep_before = 0;                       /* 启动前等待秒数 */

    /* 拼接完整命令：cmd + " " + args... */
    std::string toCommand() const;
};

/* 启动配置解析器
 *
 * 优先读 services.yaml（结构化声明），不存在则回退 init.txt（纯命令列表）。
 * 两种格式都输出 std::vector<ServiceConfig>，调用方无需关心来源。
 */
class InitParser {
public:
    /* 入口：传入配置目录（如 /etc/Nidus）
     *
     * 查找顺序：
     *   1. <dir>/services.yaml  → parseYaml()
     *   2. <dir>/init.txt       → parseTxt()
     *   3. 都不存在 → 返回空 vector，调用方自行处理
     */
    static std::vector<ServiceConfig> load(const std::string& dir);

    /* 解析 services.yaml */
    static std::vector<ServiceConfig> parseYaml(const std::string& path);

    /* 解析 init.txt（向后兼容，每行一条命令，# 开头为注释） */
    static std::vector<ServiceConfig> parseTxt(const std::string& path);
};

} // namespace qing::registry
