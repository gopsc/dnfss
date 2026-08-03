# Core 模块代码审查日志

> 审查日期：2026-07-30 14:30:00 (Asia/Shanghai)
> 审查人：Automated Review
> 审查范围：`core/` 全模块（C++20，PMC/Nidus 进程托管服务）

---

## 一、模块概述

| 属性 | 值 |
|------|-----|
| 定位 | 微服务进程托管控制器（Process Machine Controller） |
| 语言 | C++20 |
| 构建 | Makefile（GCC）+ systemd 部署 |
| 入口 | `nidus.out` |
| 端口 | HTTP API: 5200（仅本机 `127.0.0.1`） |
| 启动列表 | `init.txt`（9 个子服务） |

### 依赖项

| 库 | 用途 | 版本要求 |
|----|------|---------|
| Boost.Process | 子进程创建/管理 | — |
| Boost.JSON | JSON 解析与序列化 | — |
| Boost.Interprocess | POSIX 消息队列 | — |
| Boost.Program.Options | CLI 参数解析 | — |
| UniServe_libth | 自定义线程/HTTP 服务器封装 | 自研 |
| OpenSSL | TLS（HTTP 服务器证书） | — |

### 目录结构

```
core/
├── include/
│   ├── cn/              # 中文代码库（自研）
│   │   ├── 中文化.hpp   # 保留字映射 + 中文字符串/IO 类
│   │   ├── 链串.hpp    # 分块链表
│   │   ├── 顺序表.hpp  # 数组封装
│   │   ├── 向量.hpp    # 动态数组
│   │   └── 异常.hpp    # 异常层次
│   ├── logs/
│   │   └── Logger.hpp  # 单例日志（控制台输出）
│   ├── main/
│   │   ├── HttpTask.hh    # HTTP 服务器任务
│   │   ├── PPool.hh       # 进程池
│   │   ├── ProcessTask.hh # 子进程任务
│   │   ├── Tttask.hh      # 通信任务（消息队列监听）
│   │   ├── pmc_mtd.hh     # 进程管理方法集
│   │   └── subsys_call.hh # 子系统调用封装
│   └── tt/
│       ├── Mq.h          # POSIX 消息队列封装
│       └── Pipe.h        # FIFO 管道封装
├── src/
│   ├── main.cpp              # 主入口
│   └── main/                 # 实现文件
├── init.txt            # 自启动列表
├── Makefile            # 构建脚本
├── Nidus.service       # systemd 单元
├── README.md
├── _set.sh             # 编译+安装
└── _run.sh             # 运行脚本
```

---

## 二、审查发现

### 🔴 严重问题 (P0)

#### [CORE-S01] 命令注入漏洞

- **文件**: `src/main/HttpTask.cpp:230-247`
- **时间**: 2026-07-30 14:32:15
- **描述**: `exec` 路由直接将 URL 查询参数 `command` 传递给 `boost::process::child`，无任何过滤或校验。攻击者可构造恶意请求执行任意系统命令。

```cpp
// 危险代码
std::string result = execCommand(command, target);
// command 直接来自 HTTP 请求参数
```

- **风险**: 远程代码执行（RCE）
- **严重等级**: 🔴 Critical
- **修复建议**: 
  1. 实现命令白名单机制
  2. 使用 `execvp` 而非 `system`/shell 模式
  3. 限制可执行命令的路径范围
  4. 添加参数长度和字符集校验

#### [CORE-S02] HTTP API 无任何鉴权

- **文件**: `src/main/HttpTask.cpp:130-340`
- **时间**: 2026-07-30 14:33:00
- **描述**: HTTP API (`/list`, `/exec`, `/kill`) 完全开放，无认证、无授权、无访问控制。任何人（本地）都可以：
  - 列出所有子进程
  - 启动任意进程
  - 终止任意进程

- **风险**: 权限绕过、未授权操作
- **严重等级**: 🔴 Critical
- **修复建议**:
  1. 添加 API Token 认证（请求头 `Authorization: Bearer <token>`）
  2. 实现 IP 白名单（仅允许 `127.0.0.1`）
  3. 添加请求速率限制
  4. 记录所有管理操作审计日志

#### [CORE-S03] ProcessTask 空指针解引用

- **文件**: `include/main/ProcessTask.hh:19,36`
- **时间**: 2026-07-30 14:35:22
- **描述**: `isRunning()` 和 `pid()` 方法未检查 `p` 是否为空。如果在 `start()` 之前调用这些方法，会导致空指针解引用（段错误）。

```cpp
bool isRunning() override { return p && p->running(); }  // 应加 p 非空检查
pid_t pid() { return p->id(); }  // 危险！p 可能为 nullptr
```

- **风险**: 程序崩溃
- **严重等级**: 🔴 Critical
- **修复建议**:
  ```cpp
  bool isRunning() override { return p != nullptr && p->running(); }
  pid_t pid() { return p ? p->id() : -1; }
  ```

#### [CORE-S04] exec 路由路径穿越

- **文件**: `src/main/HttpTask.cpp:232-236`
- **时间**: 2026-07-30 14:37:10
- **描述**: `getExecCommand` 函数从 URL 参数提取命令，允许包含 `..` 路径穿越符，可能执行预期外的脚本。

- **风险**: 路径遍历
- **严重等级**: 🔴 High
- **修复建议**:
  - 白名单校验，仅允许执行 `init.txt` 中列出的命令
  - 规范化路径后检查是否在允许目录内

### 🟡 中等问题 (P1)

#### [CORE-S05] 进程池无线程安全保护

- **文件**: `src/main/PPool.cpp`
- **时间**: 2026-07-30 14:40:05
- **描述**: `PPool` 类使用 `std::list<std::shared_ptr<ProcessTask>>` 作为内部存储，但无互斥锁保护。`HttpTask` 和 `Tttask` 可能并发调用 `crtp()`、`kill()`、`clr()` 等方法，导致数据竞争。

- **风险**: 数据竞争、未定义行为
- **严重等级**: 🟡 Medium
- **修复建议**: 添加 `std::mutex` 保护所有公共方法

#### [CORE-S06] 消息队列命名冲突

- **文件**: `src/main/Tttask.cpp:41-42`
- **时间**: 2026-07-30 14:42:30
- **描述**: 使用 `getpid()` 作为消息队列名。同一台机器上启动多个 Nidus 实例时，可能发生队列名冲突。

```cpp
name = std::to_string(getpid());  // 仅用 PID，无唯一化
```

- **风险**: 多实例冲突
- **严重等级**: 🟡 Medium
- **修复建议**: 使用 `PID + 时间戳` 或 `PID + 随机后缀` 组合命名

#### [CORE-S07] 管道文件残留

- **文件**: `src/main/subsys_call.cpp`
- **时间**: 2026-07-30 14:44:18
- **描述**: 在异常路径中，`Mq` 和 `Pipe` 对象的析构器可能未正确执行（依赖栈展开），导致 `/tmp/pst-*` 文件残留，长期运行会占满 `/tmp` 空间。

- **风险**: 磁盘空间耗尽
- **严重等级**: 🟡 Medium
- **修复建议**: 
  1. 在 `catch` 块中显式清理管道文件
  2. 添加定期清理脚本
  3. 使用 `std::filesystem::remove` 在构造器前清理旧文件

#### [CORE-S08] kill/exec IPC 路径已废弃

- **文件**: `src/main.cpp:282-296`
- **时间**: 2026-07-30 14:46:00
- **描述**: `parse_data_from_mq()` 中 `kill` 和 `exec` 的 IPC 处理路径被注释，仅 `list` 可用。HTTP 路由 `/exec` 和 `/kill` 虽然存在，但远端调用时会失败。

```cpp
// 已注释的代码
//else if (typ == "kill") {
//    int trg = jv.at("kill").as_int64();
//    auto flag = pmc_mtd::kill(trg);
//    ...
//}
```

- **风险**: 功能缺失
- **严重等级**: 🟡 Medium
- **修复建议**: 恢复被注释的代码，或在文档中明确说明限制

#### [CORE-S09] PPool::clr() 边界条件错误

- **文件**: `src/main/PPool.cpp:20-32`
- **时间**: 2026-07-30 14:48:22
- **描述**: `clr()` 方法从尾部反向遍历删除已终止的进程，但边界条件处理复杂：
  - 当 `pool.size() == 1` 时，循环条件 `it != pool.begin()` 直接跳过
  - 最后的 `if (pool.size() > 0 && !(*it)->isRunning())` 可能导致双删或漏删

```cpp
auto it = --pool.end();
for (; it != pool.begin(); --it)
    if (!(*it)->isRunning()) { /* erase */ }
if (pool.size() > 0 && !(*it)->isRunning())  // 可能越界
    pool.erase(it);
```

- **风险**: 迭代器失效、崩溃
- **严重等级**: 🟡 Medium
- **修复建议**: 简化为正向前遍历，或使用 `std::list::remove_if`

#### [CORE-S10] 日志仅输出到控制台

- **文件**: `include/logs/Logger.hpp`
- **时间**: 2026-07-30 14:50:00
- **描述**: `Logger` 类仅支持控制台输出，日志不持久化。服务重启后日志丢失，无法进行事后审计或问题追溯。

- **风险**: 可观测性缺失
- **严重等级**: 🟡 Medium
- **修复建议**:
  1. 实现日志文件轮转
  2. 添加远程日志推送（syslog、Elasticsearch）
  3. 集成到微服务体系的集中日志（db:8005 Logs 表）

### 🟢 轻微问题 (P2)

#### [CORE-Q01] 版本号不一致

- **文件**: `src/main.cpp:42` vs `Makefile:1`
- **时间**: 2026-07-30 14:52:10
- **描述**: `PMC_VERSION = "0.0.10"` 与 `Makefile` 中 `VERSION := 0.0.4` 不一致。

- **严重等级**: 🟢 Low
- **修复建议**: 统一版本管理，或在 Makefile 中定义并编译时注入

#### [CORE-Q02] parse_data_from_mq 语法存疑

- **文件**: `src/main.cpp:267-304`
- **时间**: 2026-07-30 14:53:45
- **描述**: 函数声明缺少 `try` 关键字（`void parse_data_from_mq(std::string& json_str) try {`），但代码能编译通过，可能使用了 C++11 的 `function-try-block` 语法。代码可读性较差，建议显式标注。

- **严重等级**: 🟢 Low
- **修复建议**: 添加注释说明使用的是函数 try-catch 语法

#### [CORE-Q03] PPool::kill 参数类型不匹配

- **文件**: `include/main/PPool.hh:35` vs `src/main/pmc_mtd.cpp:53`
- **时间**: 2026-07-30 14:55:00
- **描述**: `PPool::kill(int idx)` 接受下标参数，但 `pmc_mtd::kill(pid)` 传入的是 PID。需要遍历查找，O(n) 复杂度。

- **严重等级**: 🟢 Low
- **修复建议**: 
  1. 改为 `killByPid(pid_t pid)`
  2. 或维护 PID → 迭代器 的映射表

#### [CORE-Q04] 缺少配置加载能力

- **文件**: 全局
- **时间**: 2026-07-30 14:56:30
- **描述**: 当前无 `config.yaml` 或类似配置文件支持，所有参数硬编码：
  - HTTP 端口 5200
  - 消息队列大小 2048/100
  - 证书路径 `server.crt/server.key`

- **严重等级**: 🟢 Low
- **修复建议**: 引入 `config.yaml` 支持，至少分离端口和证书路径配置

#### [CORE-Q05] 中文代码库未被实际使用

- **文件**: `include/cn/`
- **时间**: 2026-07-30 14:58:00
- **描述**: `中文化.hpp`、`链串.hpp`、`顺序表.hpp`、`向量.hpp`、`异常.hpp` 构成了一套完整的中文 API，但在 `src/` 代码中未被引用。这些文件更像是演示项目，而非生产代码。

- **严重等级**: 🟢 Low
- **修复建议**: 
  1. 删除未使用的中文代码库
  2. 或在 README 中明确标注为"实验性功能"
  3. 迁移到独立仓库

#### [CORE-Q06] 缺少单元测试

- **文件**: 全局
- **时间**: 2026-07-30 14:59:30
- **描述**: 项目无任何测试代码。核心功能（进程管理、消息队列、管道通信）完全依赖手工测试。

- **严重等级**: 🟢 Low
- **修复建议**: 引入 Google Test，覆盖：
  - PPool 的增删查改
  - Pipe 的读写超时
  - Mq 的发送接收
  - 中文代码库的基础功能

---

## 三、统计汇总

| 等级 | 数量 | 问题 ID |
|------|------|---------|
| 🔴 Critical | 4 | S01, S02, S03, S04 |
| 🟡 Medium | 6 | S05, S06, S07, S08, S09, S10 |
| 🟢 Low | 6 | Q01, Q02, Q03, Q04, Q05, Q06 |
| **合计** | **16** | |

### 按类别统计

| 类别 | 问题数 | 占比 |
|------|--------|------|
| 安全（注入、鉴权） | 3 | 19% |
| 稳定性（崩溃、竞态） | 4 | 25% |
| 功能缺失 | 2 | 13% |
| 代码质量 | 5 | 31% |
| 可观测性 | 2 | 12% |

---

## 四、修复优先级建议

### 第一阶段（紧急，预计 1 天）

1. **CORE-S03**: 修复 `ProcessTask` 空指针检查（最简单，影响启动）
2. **CORE-S01**: 实现命令白名单 + 路径校验（核心安全）
3. **CORE-S02**: 添加 API Token 鉴权（核心安全）

### 第二阶段（重要，预计 3 天）

4. **CORE-S05**: 添加 PPool 互斥锁（并发安全）
5. **CORE-S08**: 恢复 kill/exec IPC 路径（功能补全）
6. **CORE-S09**: 简化 PPool::clr() 实现（稳定性）
7. **CORE-S10**: 添加日志文件持久化（可观测性）

### 第三阶段（优化，预计 1 周）

8. **CORE-S04**: 完善路径穿越防护
9. **CORE-S06**: 改进消息队列命名
10. **CORE-S07**: 添加管道清理机制
11. **CORE-Q01 ~ Q06**: 代码质量优化

---

## 五、亮点

尽管问题较多，项目仍有值得肯定之处：

| 亮点 | 说明 |
|------|------|
| 清晰的模块划分 | 进程池、线程、通信、HTTP 各司其职 |
| 双轨 IPC 设计 | 消息队列 + 管道的请求-响应模式简洁高效 |
| 中文代码实验 | 对中文编程的探索有教育意义 |
| systemd 集成 | 标准部署方式，便于运维 |
| 最小依赖 | 仅依赖 Boost，无过度设计 |

---

## 六、附录

### 审查工具

- 人工静态分析
- grep 模式匹配
- 代码路径追踪

### 参考文档

- [Boost.Process 文档](https://www.boost.org/doc/libs/release/libs/process/)
- [Boost.Interprocess 文档](https://www.boost.org/doc/libs/release/libs/interprocess/)
- [POSIX 消息队列](https://man7.org/linux/man-pages/man7/mq_overview.7.html)
- [FIFO 管道](https://man7.org/linux/man-pages/man7/fifo.7.html)

### 审查人

- 自动化审查助手
- 审查时间：2026-07-30

---

*本日志由自动化代码审查工具生成，供开发团队参考。修复建议需结合实际场景评估。*
