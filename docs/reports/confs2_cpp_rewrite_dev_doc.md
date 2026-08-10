# confs 模块 C++20 重写开发方案

> 文档类型：功能开发计划
> 模块：confs → confs2（C++20 重写）
> 作者：自动生成
> 日期：2026-08-09

---

## 1. 概述

### 1.1 背景与动机

`confs` 统一配置服务当前以 Python 实现，基于 `pymsg RpcServer` 在 `127.0.0.1:8010` 提供配置文件集中托管与分发。5 个内部服务（static、dnfs、db、captcha、redirect80）通过 `pymsg RpcClient` 消费配置。

本项目已完成 db2、captcha2、smtp3、dnfs2 等 C++20 重写，遵循"原版与重写版并存 → 重写版验证通过后切换"的渐进策略。confs 作为最后一个纯 Python 基础设施服务，其 C++ 重写可：

| 收益 | 说明 |
|------|------|
| 内存占用降低 | Python 进程 ~50-100MB RSS → C++ 预估 < 5MB |
| 启动速度提升 | Python 解释器启动 ~200ms → C++ 瞬时 |
| 依赖链简化 | 消除 Python venv / pip / nuitka 工具链 |
| 安全审计遗留修复 | 重写时一并修复 Python 版 7 项遗留审计发现 |
| 未来合入 core 的基础 | C++ 同构后，未来可评估合入 core 注册中心 |

### 1.2 设计目标

1. **协议零改动**：pymsg RPC 协议、端口 `8010`、路由名（`get`/`list`/`reload`/`health`）、响应格式全部不变，5 个消费方零改动。
2. **功能完全对等**：ConfigStore 内存缓存 + mtime 自动重载、INI/JSON 按键取值、扩展名白名单、TOCTOU 防护、文件大小限制——逐项 C++ 实现。
3. **零新增系统依赖**：仅链接 `libmsg`（`-lus_msg`）、`boost_json`、`boost_program_options`、`pthread`——均为系统已安装库。
4. **安全审计遗留修复**：Python 版 7 项未修复发现（P2-3/P2-4/P3-1~P3-6）在重写中一并解决。
5. **配置目录兼容**：复用现有 `confs/configs/` 目录，配置文件零迁移。
6. **可演进**：保留向 core 注册中心注册的接口预留，未来合入评估时改动最小。

### 1.3 不做什么

- **不改协议**：不引入 HTTP，不改变 pymsg 帧格式，不增减路由。
- **不改消费方**：5 个 Python 服务的 `RpcClient` 调用代码不改。
- **不引入新解析库**：INI 解析手写（与 captcha2 的 `merge_ini` 风格一致），不引入 `boost::property_tree::ini_parser`（其大小写语义与 Python ConfigParser 不一致）。
- **不实现 YAML/TOML 按键取值**：与 Python 版一致，仅返回全文。

---

## 2. 现状分析

### 2.1 Python confs 源码结构

```
confs/
├── confs.py              # 服务主程序（ConfigStore + ConfsServer，361 行）
├── log_client.py         # 异步日志客户端 SDK（160 行）
├── configs/              # 配置文件存放目录（9 个 .ini 文件）
│   ├── ca.ini
│   ├── captcha.ini
│   ├── db.ini
│   ├── dnfs.ini
│   ├── example.ini
│   ├── gateway.ini
│   ├── redirect80.ini
│   ├── smtp.ini
│   └── static.ini
├── _run.sh               # 启动脚本（venv 中 exec python3 confs.py）
├── _set.sh               # 初始化脚本（建 venv + pip install）
├── requirements.txt      # 依赖（pymsg + nuitka）
└── README.md
```

### 2.2 关键组件

| 组件 | 职责 | 行数 |
|------|------|------|
| `ConfigStore` | 磁盘 configs/ 目录 + 内存缓存 + mtime 自动重载 + 路径安全 | ~120 行 |
| `ConfsServer` | RpcServer 入口，路由分发（get/list/reload/health） | ~90 行 |
| `_extract_key()` | INI `Section.option` 取值 + JSON 点路径取值 | ~40 行 |
| `log_client.py` | 异步批量推送日志到 db:8005 | 160 行 |

### 2.3 现有路由

| 路由 | 方法 | 说明 |
|------|------|------|
| `get` | `{"name": "db.ini"}` | 取配置全文；带 `key` 时返回单键值（仅 ini/json） |
| `list` | `{}` | 列出配置目录全部文件及元信息 |
| `reload` | `{"name": "db.ini"}` 或 `{}` | 重载指定配置；省略 name 清空全部缓存 |
| `health` | `{}` | 健康检查 |

### 2.4 消费方清单

| 服务 | 配置文件 | 接入方式 |
|------|---------|---------|
| static | `static.ini` | pymsg `RpcClient` + `retry_on_refused=True` |
| dnfs | `dnfs.ini` | pymsg `RpcClient` + `retry_on_refused=True` |
| db | `db.ini` | pymsg `RpcClient` + `retry_on_refused=True` |
| captcha | `captcha.ini` | pymsg `RpcClient` + `retry_on_refused=True` |
| redirect80 | `redirect80.ini` | pymsg `RpcClient` + `retry_on_refused=True` |

### 2.5 Python 版安全审计遗留（7 项）

| 编号 | 级别 | 问题 | 重写修复方案 |
|------|------|------|-------------|
| P2-3 | P2 | 配置名/key 直接拼入日志，未过滤控制字符 | 日志消息过滤 `\n`/`\r`/ANSI 转义 |
| P2-4 | P2 | `list()` 持锁遍历目录+stat | 拷贝文件名列表后释放锁再 stat |
| P3-1 | P3 | `_run.sh` 有 `sleep` hack | 移除 sleep，靠 init.txt 启动顺序 |
| P3-2 | P3 | `_set.sh` 硬编码 pip 镜像源 | C++ 重写无需 pip，`_set.sh` 改为 `make` |
| P3-3 | P3 | `_set.sh` 缺少 `set -e` | `_set.sh` 加 `set -euo pipefail` |
| P3-4 | P3 | requirements.txt 含运行时不需要的 nuitka | C++ 无 requirements.txt |
| P3-5 | P3 | ConfigParser 大小写隐式行为未文档化 | INI 解析器明确定义大小写策略 |
| P3-6 | P3 | 错误消息暴露底层 IO 异常 | 异常消息脱敏，不返回系统路径 |

---

## 3. 整体设计

### 3.1 架构总览

```
┌──────────────────────────────────────────────────────────┐
│              confs2 (C++20, :8010)                       │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  ConfigStore (线程安全)                             │  │
│  │  ├── 磁盘: configs/ 目录                           │  │
│  │  ├── 内存缓存: {name → {content, mtime, size}}    │  │
│  │  ├── mtime 自动重载                                │  │
│  │  └── 安全: 路径遍历/TOCTOU/扩展名白名单/大小限制  │  │
│  └───────────────────────┬────────────────────────────┘  │
│                          │                               │
│  ┌───────────────────────▼────────────────────────────┐  │
│  │  RpcServer (libmsg, :8010, pymsg 协议)              │  │
│  │  ├── get    → ConfigStore.get() + _extract_key()   │  │
│  │  ├── list   → ConfigStore.list()                  │  │
│  │  ├── reload → ConfigStore.reload()                │  │
│  │  └── health → 静态响应                              │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  LogC (异步日志, libmsg AsyncQueue → db:8005)      │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
          ▲
          │ pymsg RPC (TCP, 127.0.0.1:8010)
          │
    ┌─────┴─────┬──────┬──────┬──────┬──────┐
    ▼           ▼      ▼      ▼      ▼      ▼
  static      dnfs    db   captcha  redirect80
  (RpcClient, 消费方零改动)
```

### 3.2 核心组件

| 组件 | 职责 | 对应 Python |
|------|------|-------------|
| **ConfigStore** | 配置文件仓库（磁盘+缓存+安全） | `ConfigStore` 类 |
| **IniParser** | INI 解析（Section.option 取值） | `configparser.ConfigParser` |
| **JsonKeyExtractor** | JSON 点路径取值 | `_extract_key` JSON 分支 |
| **LogC** | 异步日志推送 | `log_client.py` |
| **RpcServer** | pymsg RPC 服务端（libmsg） | `pymsg.RpcServer` |

### 3.3 目录结构

```
confs2/
├── src/
│   └── main.cpp                 # 全部源码（ConfigStore + 路由 + main）
├── inc/
│   └── LogC.hpp                 # 异步日志客户端（从 captcha2 改编）
├── configs/                     # → 符号链接到 ../confs/configs/（配置文件零迁移）
├── Makefile                     # 构建系统（g++ -std=c++20）
├── _run.sh                      # 启动脚本
├── _set.sh                      # 编译脚本
└── README.md                    # 模块文档
```

---

## 4. 详细设计

### 4.1 ConfigStore — 配置文件仓库

```cpp
// 核心数据结构
struct CacheEntry {
    std::string content;
    std::filesystem::file_time_type mtime;
    std::size_t size;
};

class ConfigStore {
public:
    explicit ConfigStore(const std::string& root);

    // 取配置全文（带缓存 + mtime 自动重载）
    // 返回 CacheEntry 的拷贝（防缓存污染，与 Python 版 dict(entry) 语义一致）
    CacheEntry get(const std::string& name);

    // 列出配置目录全部文件元信息
    std::vector<ConfigMeta> list();

    // 重载指定配置 / 清空全部缓存
    std::vector<std::string> reload(const std::string* name);

    std::size_t cached_count() const;

private:
    // 扩展名白名单校验（纯字符串操作，不访问文件系统）
    static void validate_name(const std::string& name);

    // 持锁解析路径（realpath 前缀校验 + 文件存在性）
    std::string resolve_locked(const std::string& name);

    std::filesystem::path root_;
    std::unordered_map<std::string, CacheEntry> cache_;
    mutable std::mutex mtx_;
};
```

#### 安全机制（逐项 C++ 实现）

| 安全机制 | Python 实现 | C++ 实现 |
|----------|-------------|----------|
| 扩展名白名单 | `ALLOWED_EXT = {".ini", ...}` | `static const std::unordered_set<std::string> kAllowedExt` |
| 路径遍历防护 | `os.path.basename` + `os.path.realpath` 前缀校验 | `std::filesystem::path::filename()` + `canonical()` 前缀校验 |
| TOCTOU 防护 | 锁内 `realpath→stat→open` | 锁内 `canonical→file_size→ifstream` |
| 文件大小限制 | `MAX_CONFIG_SIZE = 1MiB`，stat 后校验 | `file_size()` 后校验 `kMaxConfigSize` |
| 缓存防污染 | 返回 `dict(entry)` 浅拷贝 | 返回 `CacheEntry` 值拷贝 |
| mtime 自动重载 | `os.stat().st_mtime` 比对 | `last_write_time()` 比对 |
| list 符号链接防护 | 遍历时 `realpath` 前缀校验 | 遍历时 `canonical()` 前缀校验，逃逸链接跳过 |
| 日志注入防护 | **未实现（P2-3 遗留）** | **新增**：日志消息过滤 `\n`/`\r`/`\x1b` |

#### TOCTOU 防护流程（锁内原子操作）

```cpp
CacheEntry ConfigStore::get(const std::string& name) {
    validate_name(name);                    // 锁外快速拒绝非法名
    std::lock_guard<std::mutex> lock(mtx_);

    auto path = resolve_locked(name);       // 锁内: canonical() 解析符号链接 + 前缀校验

    // 锁内: stat → mtime 比对 → open → read，无 TOCTOU 窗口
    auto mtime = std::filesystem::last_write_time(path);
    auto size  = std::filesystem::file_size(path);

    auto it = cache_.find(name);
    if (it != cache_.end() && it->second.mtime == mtime)
        return it->second;                  // 缓存命中，返回拷贝

    if (size > kMaxConfigSize)
        throw ConfigError("配置文件过大");

    std::ifstream f(path);
    std::string content((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());

    CacheEntry entry{content, mtime, content.size()};
    cache_[name] = entry;
    return entry;                           // 返回拷贝
}
```

### 4.2 IniParser — INI 按键取值

与 Python `ConfigParser(interpolation=None)` 行为对等的 C++ 手写解析器：

| Python ConfigParser 行为 | C++ 实现策略 |
|--------------------------|-------------|
| Section 大小写敏感 | `std::string` 原样比较 |
| Option 大小写不敏感 | 比较前 `std::tolower` 归一化 |
| `;` 和 `#` 注释 | 行首匹配跳过 |
| `=` 和 `:` 分隔符 | 仅支持 `=`（与现有 configs/ 一致） |
| 禁用 `%(key)s` 插值 | 不实现插值，原样返回 |
| 多行续行（前导空格） | 支持：下一行以空格/Tab 开头则追加 |

```cpp
// 从 INI 文本中提取 Section.option 的值
// 键格式: "Section.option"
// 大小写: section 敏感, option 不敏感（与 Python 一致）
// 插值: 禁用，原样返回（与 Python interpolation=None 一致）
static std::optional<std::string> extract_ini_key(
    const std::string& content, const std::string& key);
```

### 4.3 JsonKeyExtractor — JSON 点路径取值

```cpp
// 从 JSON 文本中按点路径提取值
// 键格式: "server.port" 或 "servers.0.host"（数字下标）
// 返回 boost::json::value（可为 string/int/double/bool/null/array/object）
static json::value extract_json_key(
    const std::string& content, const std::string& key);
```

### 4.4 路由实现

#### `get` — 取配置

```cpp
rpc.add_route("get", [](const json::object& req) -> json::value {
    // 参数校验
    auto name_it = req.find("name");
    if (name_it == req.end() || !name_it->value().is_string())
        throw ConfigError("缺少 name 字段");

    std::string name = json::value_to<std::string>(name_it->value());
    auto entry = g_store.get(name);

    // 无 key：返回全文
    auto key_it = req.find("key");
    if (key_it == req.end())
        return json::object{
            {"name", name},
            {"content", entry.content},
            {"size", (std::int64_t)entry.size},
            {"mtime", iso_timestamp(entry.mtime)}
        };

    // 有 key：按键取值
    std::string key = json::value_to<std::string>(key_it->value());
    auto ext = get_extension(name);

    if (ext == ".ini" || ext == ".conf" || ext == ".cfg") {
        auto val = extract_ini_key(entry.content, key);
        if (!val) throw KeyNotFound("键不存在: " + name + " -> " + key);
        return json::object{{"name", name}, {"key", key}, {"value", *val}};
    }
    if (ext == ".json") {
        auto val = extract_json_key(entry.content, key);
        return json::object{{"name", name}, {"key", key}, {"value", val}};
    }
    throw ConfigError(get_extension(name) + " 类型不支持按键查询");
});
```

#### `list` — 列出配置

```cpp
rpc.add_route("list", [](const json::object&) -> json::value {
    auto configs = g_store.list();
    json::array arr;
    for (const auto& c : configs)
        arr.push_back(json::object{
            {"name", c.name}, {"size", (std::int64_t)c.size},
            {"mtime", c.mtime_iso}, {"cached", c.cached}
        });
    return json::object{
        {"config_dir", g_store.root_string()},
        {"configs", arr}
    };
});
```

#### `reload` — 重载配置

```cpp
rpc.add_route("reload", [](const json::object& req) -> json::value {
    auto it = req.find("name");

    // 省略 name 或 null → 清空全部缓存
    if (it == req.end() || it->value().is_null()) {
        auto names = g_store.reload(nullptr);
        return json::object{{"reloaded", make_json_array(names)}};
    }

    // 非字符串 → 报错
    if (!it->value().is_string())
        throw ConfigError("name 字段必须是字符串");

    std::string name = json::value_to<std::string>(it->value());
    // 空字符串 → 报错（不误清全部缓存，与 Python 版一致）
    if (name.empty())
        throw ConfigError("name 字段不能为空（要清空全部缓存请省略 name 字段）");

    auto names = g_store.reload(&name);
    return json::object{{"reloaded", make_json_array(names)}};
});
```

#### `health` — 健康检查

```cpp
rpc.add_route("health", [](const json::object&) -> json::value {
    return json::object{
        {"status", "ok"},
        {"service", "confs"},
        {"config_dir", g_store.root_string()},
        {"cached", (std::int64_t)g_store.cached_count()},
        {"uptime", uptime_seconds()}
    };
});
```

### 4.5 配置加载（confs 自身的引导配置）

confs 自身不需要从 confs 获取配置（避免循环依赖），使用硬编码默认值 + 环境变量覆盖 + 命令行参数覆盖：

```cpp
struct ConfsConfig {
    std::string host = "127.0.0.1";
    int port = 8010;
    int workers = 4;
    std::string config_dir;  // 默认: 同级 configs/ 目录
};

// 加载顺序: 硬编码默认 → 环境变量覆盖 → 命令行覆盖
// 环境变量: LISTEN_ADDR, LISTEN_PORT, WORKERS, CONFS_DIR
```

### 4.6 日志客户端（LogC.hpp）

从 [liblogc/inc/us/LogC.hpp](../../liblogc/inc/us/LogC.hpp)（原 captcha2/inc/LogC.hpp，后独立为 liblogc 模块）改编，仅改 namespace 和服务名：

```cpp
namespace confs2 {
// ... 与 captcha2::LogC 结构完全一致
// 仅改动:
// - namespace: captcha2 → confs2
// - 日志中服务名: "captcha2" → "confs"
// - 客户端名: "captcha2-log" → "confs-log"
}
```

#### 日志注入防护（修复 P2-3）

```cpp
// 过滤日志消息中的控制字符（\n, \r, \x1b ANSI 转义等）
static std::string sanitize_for_log(std::string s) {
    s.erase(std::remove_if(s.begin(), s.end(), [](unsigned char c) {
        return c < 0x20 && c != '\t';  // 保留 Tab，过滤其他控制字符
    }), s.end());
    return s;
}
```

### 4.7 异常消息脱敏（修复 P3-6）

```cpp
// 异常消息脱敏：不返回系统路径、权限错误等底层信息
static std::string sanitize_error_msg(const std::string& msg) {
    // 替换绝对路径为 <redacted>
    static const std::regex path_re(R"(/\S+(?:/\S*)*)");
    std::string result = std::regex_replace(msg, path_re, "<redacted>");
    // 过滤 Permission denied 等系统错误
    static const std::regex perm_re(R"(Permission denied|No such file)");
    result = std::regex_replace(result, perm_re, "access error");
    return result;
}
```

> 注意：RpcServer 已内置 `_sanitize_error()` 方法（见 [RpcServer.hpp](../../libmsg/inc/us/RpcServer.hpp#L188-L197)），会自动替换绝对路径。confs2 的 handler 抛出的异常会先经 RpcServer 脱敏。但 confs2 仍应在业务层对底层 IO 异常做一层包装，将 `Permission denied` 等系统错误转为业务语义错误。

---

## 5. 实现计划

### 5.1 分阶段交付

| 阶段 | 里程碑 | 涉及文件 | 预估工时 |
|------|--------|----------|----------|
| **P0 — 骨架搭建** | Makefile + _run.sh + _set.sh + 空 main.cpp（RpcServer 启动 + health 路由） | 新增 `confs2/Makefile`、`_run.sh`、`_set.sh`、`src/main.cpp`、`inc/LogC.hpp` | 1h |
| **P1 — ConfigStore 核心** | ConfigStore 类：磁盘读取 + 内存缓存 + mtime 重载 + 安全机制 | `src/main.cpp` | 3h |
| **P2 — 路由实现** | get / list / reload / health 四路由完整实现 | `src/main.cpp` | 2h |
| **P3 — INI 按键取值** | IniParser 手写 + JSON 点路径取值 | `src/main.cpp` | 2h |
| **P4 — 日志客户端** | LogC.hpp 改编 + 日志注入防护 | `inc/LogC.hpp`、`src/main.cpp` | 1h |
| **P5 — 配置加载** | 环境变量 + 命令行参数解析 | `src/main.cpp` | 1h |
| **P6 — 编译验证** | 零警告编译 + ldd 依赖检查 + --help 验证 | `Makefile` | 0.5h |
| **P7 — 功能测试** | 逐路由对比 Python 版响应一致性 | `tests/` 新增 | 2h |
| **P8 — 切换 init.txt** | init.txt 改为 confs2/_run.sh + 生产验证 | `core/init.txt` | 0.5h |

**总计预估：~13 小时**

### 5.2 Makefile

```makefile
CXX      := g++
CXXFLAGS := -std=c++20 -Wall -Wextra -O2
INCS     := -I./inc
LIBS     := -lus_msg -lboost_json -lboost_program_options -lpthread

TARGET   := confs2.bin
SRC      := src/main.cpp

.PHONY: all clean install

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) $(INCS) $^ $(LIBS) -o $@

install: $(TARGET)
	cp $(TARGET) /usr/local/bin/

clean:
	rm -f $(TARGET)
```

### 5.3 _run.sh

```bash
#!/bin/bash
cd "$(dirname "$0")" || exit
sleep 1
exec ./confs2.bin
```

### 5.4 _set.sh

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit

make clean 2>/dev/null || true
make
echo "[confs2] 编译完成 → ./confs2.bin"

# 创建 configs 符号链接（指向 ../confs/configs/，配置文件零迁移）
if [ ! -e configs ]; then
    ln -s ../confs/configs configs
    echo "[confs2] 已创建 configs → ../confs/configs 符号链接"
fi
```

### 5.5 新增文件清单

```
confs2/
├── src/
│   └── main.cpp          # 全部业务源码（预估 ~500 行）
├── inc/
│   └── LogC.hpp          # 异步日志客户端（从 captcha2 改编，~110 行）
├── configs               # → 符号链接到 ../confs/configs/
├── Makefile              # 构建系统
├── _run.sh               # 启动脚本
├── _set.sh               # 编译 + 链接 configs/
└── README.md             # 模块文档
```

---

## 6. 关键依赖

| 依赖 | 用途 | 已安装 |
|------|------|--------|
| `libmsg` (`-lus_msg`) | RpcServer / RpcClient / MsgServer / AsyncQueue | ✅ |
| `boost::json` (`-lboost_json`) | JSON 序列化/反序列化 | ✅ |
| `boost::program_options` (`-lboost_program_options`) | 命令行参数解析 | ✅ |
| `pthread` | 多线程（mutex） | ✅ |
| `std::filesystem` | 文件系统操作（C++20 标准库） | ✅ |

**零新增系统依赖**——所有库均已被 captcha2 / smtp3 等模块使用。

---

## 7. 测试计划

### 7.1 功能测试（对比 Python 版响应一致性）

| 测试项 | 方法 | 通过标准 |
|--------|------|----------|
| `get` 取全文 | `RpcClient.call("get", {"name":"db.ini"})` | content/size/mtime 与 Python 版一致 |
| `get` INI 按键 | `RpcClient.call("get", {"name":"db.ini","key":"server.port"})` | value="8005" |
| `get` JSON 按键 | 构造 .json 配置，取 `server.port` | 值一致 |
| `get` 不存在 | `RpcClient.call("get", {"name":"nonexist.ini"})` | `success=false`，error 含"不存在" |
| `get` 路径遍历 | `{"name":"../../etc/passwd"}` | `success=false`，拒绝 |
| `get` 非法扩展名 | `{"name":"test.exe"}` | `success=false`，拒绝 |
| `list` | `RpcClient.call("list", {})` | 配置列表与 Python 版一致 |
| `reload` 指定 | `RpcClient.call("reload", {"name":"db.ini"})` | `success=true`，reloaded=["db.ini"] |
| `reload` 全部 | `RpcClient.call("reload", {})` | `success=true`，reloaded 含全部缓存名 |
| `reload` 空字符串 | `RpcClient.call("reload", {"name":""})` | `success=false`，error 含"不能为空" |
| `reload` 非字符串 | `RpcClient.call("reload", {"name":123})` | `success=false`，error 含"必须是字符串" |
| `health` | `RpcClient.call("health", {})` | status="ok"，service="confs" |
| mtime 重载 | 修改配置文件内容，再 `get` | 返回新内容 |
| 大文件拒绝 | 创建 > 1MiB 文件，`get` | `success=false`，error 含"过大" |
| 符号链接逃逸 | configs/ 内放逃逸符号链接 | `list` 中不出现 |

### 7.2 消费方回归测试

| 服务 | 测试方法 |
|------|----------|
| static | 启动 confs2 + static，验证静态文件服务正常（从 confs2 获取 static.ini） |
| dnfs | 启动 confs2 + dnfs，验证树状菜单 API 正常 |
| db | 启动 confs2 + db，验证数据库服务正常（从 confs2 获取 db.ini） |
| captcha | 启动 confs2 + captcha2，验证验证码生成正常（从 confs2 获取 captcha.ini） |
| redirect80 | 启动 confs2 + redirect80，验证重定向服务正常 |

### 7.3 安全测试

| 测试项 | 方法 |
|--------|------|
| 路径遍历 | `{"name":"../../../etc/passwd"}` → 拒绝 |
| 绝对路径 | `{"name":"/etc/passwd"}` → 拒绝 |
| 非法扩展名 | `{"name":"malicious.sh"}` → 拒绝 |
| 符号链接逃逸 | configs/ 内符号链接指向 /etc/ → list 跳过，get 拒绝 |
| 大文件 OOM | 创建 2MiB 配置 → 拒绝 |
| 日志注入 | 配置名含 `\n[FAKE]` → 日志中无伪造条目 |

---

## 8. 安全考量

### 8.1 安全机制对照表

| 机制 | Python 版状态 | C++ 版方案 |
|------|---------------|-------------|
| 扩展名白名单 | ✅ | ✅ `kAllowedExt` |
| 路径遍历防护 | ✅ basename + realpath 前缀 | ✅ `filename()` + `canonical()` 前缀 |
| TOCTOU 防护 | ✅ 锁内 realpath→stat→open | ✅ 锁内 canonical→file_size→ifstream |
| 文件大小限制 | ✅ 1MiB | ✅ `kMaxConfigSize = 1MiB` |
| Slowloris 防护 | ✅ pymsg 10s recv 超时 | ✅ libmsg MsgServer 同策略 |
| 缓存防污染 | ✅ 返回浅拷贝 | ✅ 返回值拷贝 |
| list 符号链接防护 | ✅ realpath 前缀校验 | ✅ canonical 前缀校验 |
| 参数类型校验 | ✅ 非字符串/空值报错 | ✅ 逐字段 is_string 校验 |
| INI 安全 | ✅ 禁用插值 | ✅ 不实现插值 |
| 仅监听本机 | ✅ 127.0.0.1 | ✅ 默认 127.0.0.1 |
| 异常隔离 | ✅ RpcServer 包装 | ✅ libmsg RpcServer 同机制 |
| 日志注入防护 | ❌ P2-3 遗留 | ✅ **新增** sanitize_for_log |
| 异常消息脱敏 | ❌ P3-6 遗留 | ✅ **新增** RpcServer 内置脱敏 + 业务层包装 |
| 鉴权 | ❌ 无 | ❌ 无（依赖仅本机信任边界，与 Python 版一致） |

### 8.2 INI 大小写策略文档化（修复 P3-5）

在代码注释和 README 中明确文档化：

```
INI 解析大小写策略（与 Python configparser(interpolation=None) 一致）：
- Section 名：大小写敏感（[Server] ≠ [server]）
- Option 名：大小写不敏感（port = Port = PORT）
- 值：原样返回，不做任何转换
- 插值：禁用，%(key)s 原样返回
```

---

## 9. 兼容性与迁移

### 9.1 零改动兼容

- **消费方零改动**：5 个 Python 服务继续用 `RpcClient("127.0.0.1", 8010, ...)` 调用，协议/端口/路由/响应格式全部不变。
- **配置文件零迁移**：`confs2/configs/` 符号链接到 `../confs/configs/`，现有 9 个 .ini 文件不动。
- **启动方式一致**：`init.txt` 中 `bash .../confs/_run.sh` 改为 `bash .../confs2/_run.sh`，一行改动。

### 9.2 迁移路径

```
阶段 1: 编译 confs2 + 功能测试（confs2 与 confs 并存）
    ↓
阶段 2: 消费方回归测试（逐个服务切到 confs2 验证）
    ↓
阶段 3: init.txt 切换 confs → confs2（一行改动）
    ↓
阶段 4: 生产运行观察 72h，确认无异常
    ↓
阶段 5: Python confs 目录归档（不立即删除，保留回退能力）
```

### 9.3 init.txt 切换

```
# 切换前
bash /opt/micro_servers/confs/_run.sh

# 切换后
bash /opt/micro_servers/confs2/_run.sh
```

---

## 10. 未来扩展：core 注册中心联动

当 core 注册中心（[core_service_registry_dev_doc](core_service_registry_dev_doc.md)）落地后，confs2 可作为注册中心的第一个自注册服务：

```cpp
// main() 启动后，RpcServer.start() 之前
register_to_core("confs", g_config.port, "pymsg", "health",
                 "bash /opt/micro_servers/confs2/_run.sh");
```

进一步地，当未来评估 confs2 合入 core 时（方案 A: HTTP 合入），因 confs2 已是 C++ 同构，迁移成本仅为：
1. 将 ConfigStore 代码移入 core 进程
2. HTTP :5200 增加 `/confs/*` 路由
3. 5 个消费方从 pymsg 改 HTTP（~50 行改动）

详见前序分析中的"confs2 合入 core 注册中心"可行性评估。

---

## 11. 附录

### A. 与已有 C++ 重写的模式对照

| 维度 | captcha2 | smtp3 | **confs2** |
|------|----------|-------|-----------|
| 原版语言 | Python | Python | Python |
| 重写语言 | C++20 | C++20 | C++20 |
| 协议 | pymsg(libmsg) | pymsg(libmsg) | pymsg(libmsg) |
| 端口 | 8009 | 8008 | 8010 |
| libmsg | ✅ RpcServer | ✅ RpcServer | ✅ RpcServer |
| boost_json | ✅ | ✅ | ✅ |
| LogC.hpp | ✅ 原创 | ✅ 改编 | ✅ 改编 |
| Makefile 模式 | g++ -std=c++20 | g++ -std=c++20 | g++ -std=c++20 |
| _run.sh 模式 | sleep 1 + exec .bin | sleep 1 + exec .bin | sleep 1 + exec .bin |
| _set.sh 模式 | set -euo + make | set -euo + make | set -euo + make + ln -s |
| 特殊依赖 | freetype, stb | openssl | 无（仅 libmsg + boost） |

### B. 配置文件目录现状

| 文件名 | 消费方 | 大小 |
|--------|--------|------|
| ca.ini | ca 模块 | 51 行 |
| captcha.ini | captcha2 | 11 行 |
| db.ini | db | 16 行 |
| dnfs.ini | dnfs | 16 行 |
| example.ini | 示例 | 10 行 |
| gateway.ini | gateway | 20 行 |
| redirect80.ini | redirect80 | 5 行 |
| smtp.ini | smtp3 | 19 行 |
| static.ini | static | 17 行 |

### C. libmsg RpcServer API 速查

```cpp
// 构造
pmc::net::RpcServer rpc("127.0.0.1", 8010,
    pmc::net::MsgServerConfig{.workers = 4},
    [](const std::string& route, const std::exception& e) {
        // on_error 回调
    });

// 注册路由
rpc.add_route("get", [](const json::object& req) -> json::value {
    // handler 签名: json::value(const json::object& req)
    // 返回值自动包装为 {"success": true, "data": ...}
    // 抛异常自动包装为 {"success": false, "error": "<脱敏>"}
    return json::object{{"content", "..."}};
});

// 启动（阻塞）
rpc.start();

// 停止（另一线程）
rpc.stop();
```
