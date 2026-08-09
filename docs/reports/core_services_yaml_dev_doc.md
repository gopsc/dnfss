# Core 启动配置 YAML 化与 CLI 参数标准化 · 开发文档

> 文档类型：功能开发计划
> 模块：core（PMC / Nidus）+ 全部子服务
> 作者：自动生成
> 日期：2026-08-10
> 前置关系：本文档是 `core_service_registry_dev_doc.md` P7 阶段（services.yaml）的前置拆分，聚焦"配置格式迁移 + 参数透传 + CLI 标准化"三步基础设施改造

---

## 1. 概述

### 1.1 背景与动机

当前 core 通过 `init.txt`（纯命令列表）拉起 11 个子服务，存在以下问题：

| 现状 | 问题 |
|------|------|
| `init.txt` 每行一条 bash 命令，无结构化字段 | 无法为每个服务指定命令行参数、环境变量、依赖关系 |
| 5/11 个 `_run.sh` 不转发 `"$@"` | core 即便想传参也传不进去 |
| `sleep 2` 等依赖等待逻辑散落在各 `_run.sh` 中 | 无统一管理，调整需逐个脚本修改 |
| waf 的 `-c config.yaml` 硬编码在 `_run.sh` | 参数不可外部覆盖 |
| 各服务 CLI 接口不统一 | ca/gateway 仅有 `--help/--version`，waf 有 `-c/-list/-test/-check`，其余 8 个服务零 CLI 支持 |

**本方案目标**：通过三步改造，让 core 能够以结构化 YAML 声明每个服务的启动命令和参数，参数能透传到各服务二进制/脚本，并建立统一的 CLI 参数规范——为注册中心 `services.yaml` + `depends_on` DAG 拓扑排序打下基础。

### 1.2 设计目标

1. **结构化启动声明**：`init.txt` → `services.yaml`，每个服务可声明 `cmd`、`args`、`env`、`depends_on`、`sleep_before`
2. **参数透传**：全部 11 个 `_run.sh` 统一转发 `"$@"`，消除硬编码参数
3. **CLI 标准化**：定义通用参数约定（`--host`/`--port`/`--config`/`--log-level`/`--instance`），各服务逐步适配
4. **向后兼容**：`services.yaml` 不存在时回退 `init.txt`，现有部署零中断
5. **yaml-cpp 引入**：vendored 到 `core/third_party/yaml-cpp/`，不依赖系统包管理器

### 1.3 与注册中心计划的关系

```
本文档（三步改造）                注册中心计划（core_service_registry_dev_doc.md）
┌─────────────────────┐          ┌─────────────────────────────┐
│ Step 1: services.yaml│ ──────▶  │ P7: services.yaml DAG 排序   │
│ Step 2: _run.sh 透传  │ ──────▶  │ P2: init 解析后自动填充注册表  │
│ Step 3: CLI 标准化    │ ──────▶  │ P5: RegistrationClient SDK   │
└─────────────────────┘          └─────────────────────────────┘
```

本方案是注册中心的**基础设施前置**——做完这三步，P2/P5/P7 的工作量直接减半。

---

## 2. 现状分析

### 2.1 init.txt 当前内容

```
bash /opt/micro_servers/db2/_run.sh
bash /opt/micro_servers/ai_review/_run.sh
bash /opt/micro_servers/dnfs2/_run.sh
bash /opt/micro_servers/smtp/_run.sh
bash /opt/micro_servers/fsys/_run.sh
bash /opt/micro_servers/waf/_run.sh
bash /opt/micro_servers/ca/_run.sh
bash /opt/micro_servers/gateway/_run.sh
bash /opt/micro_servers/captcha2/_run.sh
bash /opt/micro_servers/aibox/_run.sh
bash /opt/micro_servers/redirect80/_run.sh
```

纯命令列表，无参数、无元数据、无依赖声明。

### 2.2 各服务 _run.sh 现状

| 服务 | 语言 | `"$@"` 转发 | 硬编码参数 | 特殊处理 | sleep 等待 |
|------|------|-------------|-----------|----------|------------|
| db2 | C++ | ✅ `"$@"` | — | `set -e` | — |
| dnfs2 | C++ | ✅ `"$@"` | — | `set -e` | — |
| fsys | C++ | ✅ `"$@"` | — | `set -e` | — |
| ca | C++ | ✅ `"$@"` | — | — | `sleep 2` |
| gateway | C++ | ✅ `"$@"` | — | — | `sleep 2` |
| waf | Go | ❌ | `-c config.yaml` | — | — |
| captcha2 | C++ | ❌ | — | — | `sleep 1` |
| aibox | Python | ⚠️ `$@`（未加引号） | — | `_setenv.sh` + venv | — |
| ai_review | Python | ❌ | — | venv | — |
| smtp | Python | ❌ | — | venv | `sleep 2` |
| redirect80 | Python | ❌ | — | setcap + venv | — |

**统计**：5 个已转发、1 个半转发（未加引号）、5 个未转发。3 个脚本含 `sleep` 硬编码。

### 2.3 各服务 CLI 参数支持现状

| 服务 | 语言 | CLI 框架 | 实际参数 | 配置来源 |
|------|------|----------|----------|----------|
| db2 | C++ | 无 | 无 | 硬编码常量 |
| dnfs2 | C++ | 无 | 无 | 硬编码 + confs |
| fsys | C++ | 无 | 无 | 硬编码 |
| captcha2 | C++ | 无 | 无 | 硬编码 |
| ca | C++ | `boost::program_options` | `--help` `--version`（仅此） | confs (ca.ini) + 环境变量 |
| gateway | C++ | `boost::program_options` | `--help` `--version`（仅此） | confs (gateway.ini) + 环境变量 |
| waf | Go | `flag` | `-c` `-list` `-test` `-check` | YAML 配置文件 |
| aibox | Python | `argparse`（已 import 未使用） | 无 | `_setenv.sh` + 硬编码 |
| ai_review | Python | 无 | 无 | 硬编码 |
| smtp | Python | 无 | 无 | 硬编码 |
| redirect80 | Python | 无 | 无 | 硬编码 |

**关键发现**：
- ca 和 gateway 已引入 `boost::program_options` 但只实现了 `--help`/`--version`，实际配置全走 confs + 环境变量
- aibox 已 `import argparse` 但从未调用，是死代码
- waf 是唯一有完整 CLI 接口的服务（Go `flag` 包）
- 8/11 个服务零 CLI 参数支持

### 2.4 配置加载模式分类

当前各服务的配置获取有三种模式：

| 模式 | 服务 | 说明 |
|------|------|------|
| **confs + 环境变量** | ca, gateway, dnfs2 | 从 confs 服务拉取 ini 配置，环境变量可覆盖；`CONFS_HOST`/`CONFS_PORT` 引导 |
| **本地配置文件** | waf | `-c config.yaml` 指定 YAML 配置 |
| **硬编码** | db2, fsys, captcha2, aibox, ai_review, smtp, redirect80 | 常量直接写在源码中 |

CLI 标准化后，三种模式可统一为：**CLI 参数（最高优先级）> 环境变量 > confs/配置文件 > 硬编码默认值**。

---

## 3. 三步改造方案

### 3.1 总览

```
Step 1: init.txt → services.yaml          Step 2: _run.sh 参数透传          Step 3: CLI 参数标准化
┌──────────────────────────────┐          ┌──────────────────────┐          ┌──────────────────────┐
│  • 引入 yaml-cpp (vendored)   │          │  • 11 个 _run.sh 统一  │          │  • 定义通用参数约定    │
│  • 设计 services.yaml 格式    │  ──▶    │    末尾 exec 加 "$@"   │  ──▶    │  • _run.sh 注入默认值  │
│  • core 解析 YAML 并启动      │          │  • 移除硬编码参数      │          │  • 各服务逐步适配      │
│  • 回退兼容 init.txt          │          │  • sleep 移至 YAML     │          │  • 环境变量桥接        │
└──────────────────────────────┘          └──────────────────────┘          └──────────────────────┘
```

---

## 4. Step 1：init.txt → services.yaml

### 4.1 yaml-cpp 引入

采用系统包安装方式（已写入 `_setup.deb.sh` 和 `_setup.rhel.sh`）：

| 发行版 | 包名 | 安装命令 |
|--------|------|----------|
| Debian/Ubuntu | `libyaml-cpp-dev` | `apt install libyaml-cpp-dev` |
| RHEL/CentOS/Rocky | `yaml-cpp-devel` | `dnf install yaml-cpp-devel` |

**Makefile 集成**（已完成）：

```makefile
# 链接 yaml-cpp 系统库
LIBS := ... -lyaml-cpp

# InitParser 编译目标
$(BUILD_DIR)/InitParser.o: src/registry/InitParser.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

# 链接时追加
$(APP_NAME).out: ... $(BUILD_DIR)/InitParser.o
```

### 4.2 services.yaml 格式设计

```yaml
# core/services.yaml
# 声明式服务启动配置（替代 init.txt）
# 如果本文件存在则优先使用，否则回退 init.txt

services:
  # --- 数据层 ---
  - name: db2
    cmd: bash /opt/micro_servers/db2/_run.sh
    args: []
    env: {}
    depends_on: []
    sleep_before: 0

  # --- AI 服务 ---
  - name: ai_review
    cmd: bash /opt/micro_servers/ai_review/_run.sh
    args: ["--port", "8007", "--log-level", "info"]
    env:
      PYTHONUNBUFFERED: "1"
    depends_on: [db2]
    sleep_before: 2

  # --- 文件服务 ---
  - name: dnfs2
    cmd: bash /opt/micro_servers/dnfs2/_run.sh
    args: []
    env: {}
    depends_on: [db2, ai_review]
    sleep_before: 0

  # --- 邮件服务 ---
  - name: smtp
    cmd: bash /opt/micro_servers/smtp/_run.sh
    args: []
    env: {}
    depends_on: []
    sleep_before: 2

  # --- 前端静态资源 ---
  - name: fsys
    cmd: bash /opt/micro_servers/fsys/_run.sh
    args: []
    env: {}
    depends_on: []
    sleep_before: 0

  # --- 安全链路 ---
  - name: waf
    cmd: bash /opt/micro_servers/waf/_run.sh
    args: ["-c", "config.yaml"]
    env: {}
    depends_on: [ca, gateway]
    sleep_before: 0

  - name: ca
    cmd: bash /opt/micro_servers/ca/_run.sh
    args: []
    env: {}
    depends_on: [db2]
    sleep_before: 2

  - name: gateway
    cmd: bash /opt/micro_servers/gateway/_run.sh
    args: []
    env: {}
    depends_on: [db2]
    sleep_before: 2

  # --- 验证码 ---
  - name: captcha2
    cmd: bash /opt/micro_servers/captcha2/_run.sh
    args: []
    env: {}
    depends_on: []
    sleep_before: 1

  # --- AI 工具箱 ---
  - name: aibox
    cmd: bash /opt/micro_servers/aibox/_run.sh
    args: []
    env:
      PATH: "${PATH}:~/.aibox/skills"
    depends_on: []
    sleep_before: 0

  # --- 端口重定向 ---
  - name: redirect80
    cmd: bash /opt/micro_servers/redirect80/_run.sh
    args: []
    env: {}
    depends_on: [waf]
    sleep_before: 0
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 服务唯一标识，用于日志和未来注册表 |
| `cmd` | string | 是 | 启动命令（与 init.txt 每行格式一致） |
| `args` | string[] | 否 | 传递给 `_run.sh` 的命令行参数列表 |
| `env` | map<string,string> | 否 | 启动前设置的环境变量 |
| `depends_on` | string[] | 否 | 依赖的服务名列表（未来 DAG 排序用，当前仅记录） |
| `sleep_before` | int | 否 | 启动前等待秒数（替代散落在 _run.sh 中的 sleep） |

### 4.3 core 解析逻辑

新增 `include/registry/InitParser.hh` + `src/registry/InitParser.cpp`：

```cpp
// include/registry/InitParser.hh
#pragma once
#include <string>
#include <vector>
#include <map>

namespace qing::registry {

struct ServiceConfig {
    std::string name;
    std::string cmd;
    std::vector<std::string> args;
    std::map<std::string, std::string> env;
    std::vector<std::string> depends_on;
    int sleep_before = 0;
};

class InitParser {
public:
    // 优先读 services.yaml，不存在则回退 init.txt
    static std::vector<ServiceConfig> load(const std::string& conf_dir);

    // 解析 services.yaml
    static std::vector<ServiceConfig> parseYaml(const std::string& path);

    // 解析 init.txt（向后兼容）
    static std::vector<ServiceConfig> parseTxt(const std::string& path);

    // 将 ServiceConfig 拼成完整命令字符串（cmd + " " + args.join(" ")）
    static std::string toCommand(const ServiceConfig& svc);
};

} // namespace qing::registry
```

**解析流程**：

```
load(conf_dir):
  1. 检查 conf_dir/services.yaml 是否存在
  2. 存在 → parseYaml() → 返回 ServiceConfig 列表
  3. 不存在 → 检查 conf_dir/init.txt
  4. 存在 → parseTxt() → 每行作为 ServiceConfig{name="", cmd=line, args=[]}
  5. 都不存在 → 报错退出
```

**parseYaml 核心逻辑**（使用 yaml-cpp）：

```cpp
#include <yaml-cpp/yaml.h>

std::vector<ServiceConfig> InitParser::parseYaml(const std::string& path) {
    YAML::Node root = YAML::LoadFile(path);
    std::vector<ServiceConfig> result;

    for (const auto& node : root["services"]) {
        ServiceConfig svc;
        svc.name = node["name"].as<std::string>("");
        svc.cmd  = node["cmd"].as<std::string>();

        if (node["args"])
            for (const auto& a : node["args"])
                svc.args.push_back(a.as<std::string>());

        if (node["env"])
            for (const auto& kv : node["env"])
                svc.env[kv.first.as<std::string>()] = kv.second.as<std::string>();

        if (node["depends_on"])
            for (const auto& d : node["depends_on"])
                svc.depends_on.push_back(d.as<std::string>());

        if (node["sleep_before"])
            svc.sleep_before = node["sleep_before"].as<int>(0);

        result.push_back(std::move(svc));
    }
    return result;
}
```

**启动执行逻辑**（改动 `pmc_mtd::parse_self_init_list`）：

```cpp
// pmc_mtd.cpp 中改造
void pmc_mtd::init_from_config(const std::string& conf_dir) {
    auto services = InitParser::load(conf_dir);

    for (const auto& svc : services) {
        // 1. sleep_before 等待
        if (svc.sleep_before > 0)
            std::this_thread::sleep_for(std::chrono::seconds(svc.sleep_before));

        // 2. 设置环境变量
        for (const auto& [k, v] : svc.env)
            setenv(k.c_str(), v.c_str(), 1);

        // 3. 拼接完整命令并启动
        std::string full_cmd = InitParser::toCommand(svc);
        // full_cmd = "bash /opt/micro_servers/db2/_run.sh --port 8007 --log-level info"
        ppool.crtp(full_cmd);
    }
}
```

### 4.4 向后兼容策略

```
                   services.yaml 存在？
                        │
              ┌─── 是 ──┤── 否 ───┐
              ▼                   ▼
        parseYaml()          init.txt 存在？
              │                   │
              │           ┌── 是 ──┤── 否 ───┐
              │           ▼                   ▼
              │       parseTxt()          报错退出
              │           │
              └─────┬─────┘
                    ▼
            ServiceConfig[]
            逐个启动
```

- `init.txt` 模式下，每行解析为 `{name="", cmd=line, args=[], sleep_before=0}`，行为与当前完全一致
- `services.yaml` 模式下，享受参数透传和 sleep 管理能力
- Makefile 的 `CONF_NAME` 可逐步从 `init.txt` 切换为 `services.yaml`

---

## 5. Step 2：_run.sh 参数透传

### 5.1 改造规则

每个 `_run.sh` 的末尾 `exec` 行必须带 `"$@"`，同时移除硬编码参数和散落的 `sleep`。

**改造模板**：

```bash
#!/bin/bash
# <service>/_run.sh — 标准化启动脚本
cd "$(dirname "$0")" || exit

# --- 服务特有前置（venv / setcap 等）保持不变 ---

# 启动："$@" 透传 core/services.yaml 传入的参数
exec <binary_or_python> "$@"
```

### 5.2 逐服务改造清单

| 服务 | 当前 exec 行 | 改造后 | 额外改动 |
|------|-------------|--------|----------|
| db2 | `exec ./build/db2 "$@"` | ✅ 无需改 | — |
| dnfs2 | `exec ./build/dnfs2 "$@"` | ✅ 无需改 | — |
| fsys | `exec ./build/fsys "$@"` | ✅ 无需改 | — |
| ca | `exec ./build/ca.out "$@"` | ✅ 无需改 | 移除 `sleep 2`（移交 YAML） |
| gateway | `exec ./build/gateway.out "$@"` | ✅ 无需改 | 移除 `sleep 2`（移交 YAML） |
| waf | `exec ./waf.bin -c config.yaml` | `exec ./waf.bin -c config.yaml "$@"` | 保留 `-c config.yaml` 作为脚本默认值，services.yaml `args: []` |
| captcha2 | `exec ./captcha2.bin` | `exec ./captcha2.bin "$@"` | 移除 `sleep 1`（移交 YAML） |
| aibox | `exec python aibox.py $@` | `exec python aibox.py "$@"` | `$@` → `"$@"` 加引号 |
| ai_review | `exec python3 ai_review.py` | `exec python3 ai_review.py "$@"` | — |
| smtp | `exec python3 smtp_v2.py` | `exec python3 smtp_v2.py "$@"` | 移除 `sleep 2`（移交 YAML） |
| redirect80 | `exec python3 redirect80.py` | `exec python3 redirect80.py "$@"` | — |

**统计**：6 个无需改、5 个需改 exec 行、4 个需移除 sleep。

### 5.3 sleep 迁移对照

| 服务 | 当前 sleep 位置 | 迁移到 services.yaml |
|------|----------------|---------------------|
| ca | `_run.sh` 中 `sleep 2` | `sleep_before: 2` |
| gateway | `_run.sh` 中 `sleep 2` | `sleep_before: 2` |
| captcha2 | `_run.sh` 中 `sleep 1` | `sleep_before: 1` |
| smtp | `_run.sh` 中 `sleep 2` | `sleep_before: 2` |

移除后 `_run.sh` 只负责"切换目录 + 前置准备 + exec"，不负责时序控制。

---

## 6. Step 3：CLI 参数标准化

### 6.1 通用参数约定

定义一组所有服务都应支持的通用 CLI 参数：

| 参数 | 短选项 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--host` | `-H` | string | `127.0.0.1` | 绑定地址 |
| `--port` | `-p` | int | 服务特定 | 监听端口 |
| `--config` | `-c` | string | 服务特定 | 配置文件路径 |
| `--log-level` | `-l` | enum | `info` | `debug\|info\|warn\|error` |
| `--instance` | `-i` | string | 服务名 | 实例标识（未来多实例/注册中心用） |
| `--help` | `-h` | flag | — | 输出帮助 |
| `--version` | `-v` | flag | — | 输出版本 |

**优先级规则**：`CLI 参数 > 环境变量 > confs/配置文件 > 硬编码默认值`

### 6.2 _run.sh 默认值注入

在服务二进制还不支持所有参数的过渡期，`_run.sh` 可注入默认值：

```bash
#!/bin/bash
# waf/_run.sh — 标准化后
cd "$(dirname "$0")" || exit

# 默认参数（可被 services.yaml args 覆盖）
DEFAULT_ARGS=(-c config.yaml)

exec ./waf.bin "${DEFAULT_ARGS[@]}" "$@"
```

```bash
#!/bin/bash
# db2/_run.sh — 标准化后
cd "$(dirname "$0")" || exit
set -e

# 默认参数
DEFAULT_ARGS=(--host 127.0.0.1 --port 8005 --log-level info)

exec ./build/db2 "${DEFAULT_ARGS[@]}" "$@"
```

**原理**：`"$@"` 追加在 `DEFAULT_ARGS` 之后，如果用户通过 services.yaml 传了同名参数，取决于各服务的解析逻辑（`boost::program_options` 默认后者覆盖前者；Python `argparse` 同理）。

### 6.3 各语言适配指南

#### C++ 服务（db2, dnfs2, fsys, captcha2）

当前这 4 个服务无 CLI 框架。建议引入 `boost::program_options`（ca/gateway 已有先例）：

```cpp
// db2/src/db2.cpp — 新增 CLI 解析
#include <boost/program_options.hpp>
namespace po = boost::program_options;

int main(int argc, char** argv) {
    po::options_description desc("db2 — database RPC service");
    desc.add_options()
        ("help,h", "output help message")
        ("version,v", "output version message")
        ("host,H", po::value<std::string>()->default_value("127.0.0.1"), "bind address")
        ("port,p", po::value<int>()->default_value(8005), "listen port")
        ("log-level,l", po::value<std::string>()->default_value("info"),
         "debug|info|warn|error")
        ("instance,i", po::value<std::string>()->default_value("db2"), "instance name")
    ;

    po::variables_map vm;
    try {
        po::store(po::parse_command_line(argc, argv, desc), vm);
        po::notify(vm);
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << std::endl;
        std::cerr << desc << std::endl;
        return 1;
    }

    if (vm.count("help")) { std::cout << desc << std::endl; return 0; }
    if (vm.count("version")) { std::cout << "db2 1.0.0" << std::endl; return 0; }

    std::string host = vm["host"].as<std::string>();
    int port = vm["port"].as<int>();
    std::string log_level = vm["log-level"].as<std::string>();

    // 后续使用 host/port/log_level 启动服务...
}
```

#### C++ 服务（ca, gateway）— 扩展现有 program_options

ca 和 gateway已有 `po::options_description` 但只注册了 `--help`/`--version`，需追加通用参数：

```cpp
// ca/src/main.cpp — 扩展
desc.add_options()
    ("help,h",     "output help message")
    ("version,v",  "output version message")
    // 新增通用参数
    ("host,H",     po::value<std::string>()->default_value("127.0.0.1"), "bind address")
    ("port,p",     po::value<int>()->default_value(8443), "listen port")
    ("log-level,l", po::value<std::string>()->default_value("info"),
     "debug|info|warn|error")
    ("instance,i", po::value<std::string>()->default_value("ca"), "instance name")
;
```

注意：ca/gateway 当前配置走 confs + 环境变量，CLI 参数应作为**覆盖层**，不替换 confs 机制。

#### Go 服务（waf）— 扩展现有 flag

waf 已有 `flag` 包，追加通用参数：

```go
func main() {
    var (
        configFile = flag.String("c", "config.yaml", "配置文件路径")
        host       = flag.String("H", "127.0.0.1", "绑定地址")
        port       = flag.Int("p", 443, "监听端口")
        logLevel   = flag.String("l", "info", "debug|info|warn|error")
        instance   = flag.String("i", "waf", "实例名")
        doList     = flag.Bool("list", false, "列出所有规则后退出")
        doTest     = flag.String("test", "", "校验单个规则文件语法后退出")
        doCheck    = flag.String("check", "", "用当前规则测试输入字符串后退出")
    )
    flag.Parse()
    // ...
}
```

注意：Go `flag` 包不支持短选项连写（`-H 127.0.0.1`），与 C++ `program_options` 的 `-H` 语法兼容。

#### Python 服务（aibox, ai_review, smtp, redirect80）

建议统一使用 `argparse`：

```python
#!/usr/bin/env python3
"""ai_review — AI 审核服务"""
import argparse

def parse_args():
    parser = argparse.ArgumentParser(description="ai_review — AI 审核服务")
    parser.add_argument("-H", "--host", default="127.0.0.1", help="绑定地址")
    parser.add_argument("-p", "--port", type=int, default=8007, help="监听端口")
    parser.add_argument("-l", "--log-level", default="info",
                        choices=["debug", "info", "warn", "error"], help="日志级别")
    parser.add_argument("-i", "--instance", default="ai_review", help="实例名")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    # 后续使用 args.host, args.port, args.log_level...
```

aibox 已有 `import argparse`（死代码），激活即可。

### 6.4 环境变量桥接

对于走 confs + 环境变量的服务（ca, gateway, dnfs2），CLI 参数应映射到环境变量，保持与现有 confs 机制兼容：

```bash
# ca/_run.sh — 环境变量桥接模式
cd "$(dirname "$0")" || exit

# 从命令行参数提取并导出环境变量（供 confs 加载逻辑使用）
while [[ $# -gt 0 ]]; do
    case "$1" in
        -H|--host)    export CA_HOST="$2"; shift 2 ;;
        -p|--port)    export CA_PORT="$2"; shift 2 ;;
        -l|--log-level) export LOG_LEVEL="$2"; shift 2 ;;
        *)            EXTRA_ARGS+=("$1"); shift ;;
    esac
done

exec ./build/ca.out "${EXTRA_ARGS[@]}"
```

这种桥接方式不需要立刻改 ca 的 C++ 源码——`_run.sh` 先把 CLI 参数翻译成环境变量，ca 现有的环境变量读取逻辑自然生效。

---

## 7. 实施计划

### 7.1 分步交付

| 步骤 | 改动范围 | 涉及文件 | 预估工时 | 风险 |
|------|----------|----------|----------|------|
| **S1-a** | yaml-cpp vendored + Makefile 集成 | `core/third_party/yaml-cpp/`、`core/Makefile` | 1h | 低 |
| **S1-b** | InitParser 实现 | `core/include/registry/InitParser.hh`、`core/src/registry/InitParser.cpp` | 2h | 低 |
| **S1-c** | pmc_mtd 改造（调用 InitParser） | `core/src/main/pmc_mtd.cpp` | 1h | 中（改核心启动路径） |
| **S1-d** | 创建 services.yaml | `core/services.yaml` | 0.5h | 低 |
| **S1-e** | 回退测试（删除 services.yaml 验证 init.txt 兼容） | — | 0.5h | 低 |
| **S2-a** | 5 个 _run.sh 改 exec 行 | `waf/`、`captcha2/`、`aibox/`、`ai_review/`、`smtp/`、`redirect80/` 的 `_run.sh` | 0.5h | 极低 |
| **S2-b** | 4 个 _run.sh 移除 sleep | `ca/`、`gateway/`、`captcha2/`、`smtp/` 的 `_run.sh` | 0.2h | 低（sleep 已移交 YAML） |
| **S3-a** | 定义 CLI 标准文档（本文档 §6） | — | 0h（已完成） | — |
| **S3-b** | C++ 服务适配（ca/gateway 扩展，db2/dnfs2/fsys/captcha2 新增） | 各服务 `src/*.cpp` | 4h | 中 |
| **S3-c** | Python 服务适配（argparse） | `aibox.py`、`ai_review.py`、`smtp_v2.py`、`redirect80.py` | 2h | 低 |
| **S3-d** | Go 服务适配（waf flag 扩展） | `waf/waf.go` | 0.5h | 低 |
| **S3-e** | _run.sh 默认值注入 | 各服务 `_run.sh` | 0.5h | 低 |
| **S4** | 端到端测试 | — | 1h | — |

**总计预估：~14 小时**

### 7.2 推荐实施顺序

```
S1-a (yaml-cpp 集成)
  └─▶ S1-b (InitParser)
        └─▶ S1-c (pmc_mtd 改造)
              └─▶ S1-d (services.yaml)
                    └─▶ S1-e (回退测试) ──▶ Step 1 完成
                          │
                          ▼
                    S2-a + S2-b ( _run.sh 改造) ──▶ Step 2 完成
                          │
                          ▼
                    S3-b/c/d (各服务 CLI 适配)
                          └─▶ S3-e (默认值注入) ──▶ Step 3 完成
                                │
                                ▼
                          S4 (端到端测试)
```

### 7.3 新增/改动文件清单

```
core/
├── include/registry/
│   └── InitParser.hh                  # 【新增】YAML/TXT 解析器头文件
├── src/registry/
│   └── InitParser.cpp                 # 【新增】YAML/TXT 解析器实现
├── src/main/pmc_mtd.cpp               # 【改动】调用 InitParser 替代 parse_self_init_list
├── src/main.cpp                       # 【改动】--init 帮助文本更新
├── Makefile                           # 【改动】追加 InitParser.o 编译目标 + -lyaml-cpp 链接
├── _run.sh                            # 【改动】-i init.txt → -i .（目录模式自动查找）
├── services.yaml                      # 【新增】声明式服务启动配置
└── init.txt                           # 【保留】向后兼容回退

项目根目录/
├── _setup.deb.sh                      # 【改动】追加 libyaml-cpp-dev
└── _setup.rhel.sh                     # 【改动】追加 yaml-cpp-devel

各服务 _run.sh（11 个）                  # 【已完成】Step 2 统一 "$@" 透传 + 移除 sleep

C++ 服务源码（6 个）                     # 【待改】Step 3 新增/扩展 program_options
Python 服务源码（4 个）                  # 【待改】Step 3 新增 argparse
Go 服务源码（1 个）                      # 【待改】Step 3 扩展 flag
```

---

## 8. 验证清单

### 8.1 Step 1 验证

- [ ] `make` 编译成功，yaml-cpp 源文件正确编译链接
- [ ] `services.yaml` 存在时，core 按 YAML 声明启动全部 11 个服务
- [ ] `services.yaml` 不存在时，core 回退 `init.txt`，行为与改造前完全一致
- [ ] `args` 字段的参数正确透传到 `_run.sh`
- [ ] `env` 字段的环境变量在子进程启动前生效
- [ ] `sleep_before` 字段正确控制启动间隔

### 8.2 Step 2 验证

- [x] 全部 11 个 `_run.sh` 末尾 `exec` 行带 `"$@"`
- [x] waf 保留 `-c config.yaml` 作为脚本默认值，追加 `"$@"` 透传，services.yaml `args: []`
- [x] aibox 的 `$@` 改为 `"$@"`（加引号，防空格路径问题）
- [x] ca/gateway/captcha2/smtp 的 `sleep` 从 `_run.sh` 移除，由 `services.yaml` 的 `sleep_before` 管理
- [ ] 手动执行 `_run.sh --help` 能透传到二进制（依赖 Step 3 CLI 适配）

### 8.3 Step 3 验证

- [ ] 全部服务支持 `--help` 输出用法
- [ ] 全部服务支持 `--version` 输出版本
- [ ] `--port` 能覆盖默认端口并生效
- [ ] `--log-level` 能控制日志输出级别
- [ ] `_run.sh` 默认值注入 + `services.yaml` 覆盖，两者叠加不冲突
- [ ] ca/gateway 的环境变量桥接模式正确工作

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| yaml-cpp 编译增加构建时间 | 低（~10s） | 可接受；如需优化可预编译为静态库 |
| pmc_mtd 改造影响核心启动路径 | 高 | 保留 `init.txt` 回退路径；先在测试环境验证 |
| _run.sh 移除 sleep 后启动时序变化 | 中 | `services.yaml` 的 `sleep_before` 提供等价等待 |
| 各服务 CLI 参数适配工作量大 | 中 | 分批适配；_run.sh 默认值注入可过渡 |
| Go flag 与 C++ program_options 语法差异 | 低 | 两者都支持 `-H value` 和 `--host value` 格式 |
| Python argparse 与 C++ program_options 行为差异 | 低 | 测试覆盖；argparse 默认不覆盖已设默认值 |

---

## 10. 附录

### A. 各服务端口与配置对照

| 服务 | 端口 | 协议 | 配置来源 | CLI 框架现状 |
|------|------|------|----------|-------------|
| db2 | 8005 | pymsg | 硬编码 | 无 |
| ai_review | 8007 | http | 硬编码 | 无 |
| dnfs2 | 8006 | http | 硬编码 + confs | 无 |
| smtp | 8008 | pymsg | 硬编码 | 无 |
| fsys | 8090 | http | 硬编码 | 无 |
| waf | 443 | https | YAML 配置文件 | Go flag ✅ |
| ca | 8443 | https | confs + 环境变量 | program_options（仅 --help/--version） |
| gateway | 8445 | http | confs + 环境变量 | program_options（仅 --help/--version） |
| captcha2 | 8009 | pymsg | 硬编码 | 无 |
| aibox | 8765 | websocket | _setenv.sh + 硬编码 | argparse（已 import 未使用） |
| redirect80 | 80 | http | 硬编码 | 无 |

### B. yaml-cpp 集成验证

已采用系统包方式（非 vendored），在 `_setup.deb.sh` / `_setup.rhel.sh` 中声明依赖：

```bash
# Debian/Ubuntu
sudo apt install libyaml-cpp-dev

# RHEL/CentOS
sudo dnf install yaml-cpp-devel
```

Makefile 中 `-lyaml-cpp` 链接，`InitParser.cpp` 中 `#include <yaml-cpp/yaml.h>` 引用。

### C. 环境变量约定（现有 + 新增）

| 环境变量 | 使用方 | 当前/新增 | 说明 |
|----------|--------|-----------|------|
| `CONFS_HOST` | ca, gateway, dnfs2 | 现有 | confs 服务地址，默认 `127.0.0.1` |
| `CONFS_PORT` | ca, gateway, dnfs2 | 现有 | confs 服务端口，默认 `8010` |
| `CORE_HOST` | 未来 RegistrationClient | 新增 | core 注册中心地址，默认 `127.0.0.1` |
| `CORE_PORT` | 未来 RegistrationClient | 新增 | core 注册中心端口，默认 `5200` |
| `LOG_LEVEL` | 全部服务（可选） | 新增 | 日志级别，可被 `--log-level` 覆盖 |

### D. 与注册中心计划的衔接点

| 本方案产出 | 注册中心计划消费点 |
|-----------|-------------------|
| `services.yaml` 格式 | P7 直接复用，追加 `health_endpoint`/`protocol`/`restart_max` 等字段 |
| `InitParser` 解析器 | P2 调用 `parseYaml()` 后自动填充 `ServiceRegistry` |
| `ServiceConfig.depends_on` | P7 DAG 拓扑排序的输入 |
| `ServiceConfig.args` | 各服务通过 CLI 参数接收注册中心分配的端口/实例名 |
| CLI `--instance` 参数 | P5 RegistrationClient SDK 用此值作为注册服务名 |
| CLI `--port` 参数 | P0 ServiceEntry 的 port 字段来源 |
