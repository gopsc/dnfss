# Core 模块注册中心功能扩展 · 开发文档

> 文档类型：功能开发计划
> 模块：core（PMC / Nidus）
> 作者：自动生成
> 日期：2026-08-08

---

## 1. 概述

### 1.1 背景与动机

当前 `core` 模块（PMC / Nidus）作为进程托管服务，承担子进程的启动、列举与终止职责。但是，架构存在以下局限（项目 README 已明确指出"未引入服务注册发现"）：

| 现状 | 问题 |
|------|------|
| `init.txt` 仅记录启动命令，无服务元数据 | core 不知道子进程的名称、端口、协议、健康检查方式 |
| 服务地址静态配置在 `waf/config.yaml`、`gateway/example.ini` 中 | 新增/迁移服务需手动同步多处配置 |
| 子进程崩溃后依赖 systemd 重启 core 整体 | 无法单独重启故障服务，也无自动恢复机制 |
| 无集中式健康检查 | 无法感知子服务的存活状态，运维只能人工排查 |
| 无服务发现能力 | 服务间调用（如 dnfs → db、dnfs → ai_review）靠硬编码地址 |

**注册中心的目标**：让 core 从"进程托管器"升级为"服务注册与治理中心"——所有子服务自动注册、自动发现、自动健康守护。

### 1.2 设计目标

1. **自动注册**：服务启动后自动向 core 注册自身元信息（名称、端口、协议、健康端点等），无需人工维护服务列表。
2. **服务发现**：任意服务可通过 core 查询目标服务的地址，消除硬编码。
3. **健康守护**：core 周期性探活，发现故障服务自动重启。
4. **渐进式兼容**：不破坏现有 `init.txt` 启动流程和 HTTP 管理 API，现有服务零改动可继续运行。
5. **轻量自研**：不引入 etcd / Consul 等外部中间件，保持 DIY 轻量风格。

---

## 2. 现状分析

### 2.1 Core 模块源码结构

```
core/
├── include/
│   ├── main/
│   │   ├── HttpTask.hh          # HTTP 管理服务器任务（ITask 子类）
│   │   ├── PPool.hh             # 子进程池（std::list<shared_ptr<ProcessTask>>）
│   │   ├── ProcessTask.hh       # 单个子进程任务（boost::process::child）
│   │   ├── Tttask.hh            # 消息队列通信任务（ITask 子类）
│   │   ├── pmc_mtd.hh           # 进程操作方法集（list/kill/exec/parse_self_init_list）
│   │   └── subsys_call.hh       # 远程子系统调用（通过 Mq + Pipe IPC）
│   ├── tt/
│   │   ├── Pipe.h               # POSIX 命名管道封装
│   │   └── Mq.h                 # Boost.Interprocess 消息队列封装
│   ├── logs/
│   │   └── Logger.hpp           # 单例控制台日志
│   └── cn/                      # 中文化编程宏
├── src/
│   ├── main.cpp                 # 入口：信号处理、命令行解析、初始化、HTTP 伺服器
│   └── main/
│       ├── HttpTask.cpp         # HTTP 路由（/list /exec /kill）+ 管理员认证
│       ├── PPool.cpp            # 进程池 CRUD
│       ├── ProcessTask.cpp      # boost::process v2 适配
│       ├── Tttask.cpp           # 消息队列接收循环
│       ├── pmc_mtd.cpp          # 进程操作实现
│       └── subsys_call.cpp      # IPC 远程调用实现
├── init.txt                     # 自启动列表（每行一条 bash 命令）
├── Makefile                     # 构建系统（g++ -std=c++20）
└── Nidus.service                # systemd 单元文件
```

### 2.2 关键类关系

```
┌──────────────────────────────────────────────────────────┐
│                       main.cpp                           │
│                                                          │
│  taskPool: vector<shared_ptr<ITask>>                     │
│     ├── Tttask          (Mq 消息队列接收循环)              │
│     └── HttpTask        (HTTP :5200 管理接口)             │
│                                                          │
│  pmc_mtd::ppool: PPool                                   │
│     └── list<shared_ptr<ProcessTask>>                    │
│           └── ProcessTask: ITask                         │
│                 └── boost::process::child                │
└──────────────────────────────────────────────────────────┘
```

### 2.3 现有 IPC 机制

| 机制 | 实现 | 用途 |
|------|------|------|
| POSIX 消息队列 (Mq) | `boost::interprocess::message_queue`，以 PID 命名 | 外部进程向 core 发送 exec/list/kill 命令 |
| 命名管道 (Pipe) | `mkfifo()`，`/tmp/pst-<PID>` | core 向调用者返回执行结果 |
| HTTP (HttpTask) | `pmc::net::HttpServer`，`:5200` | 远程管理接口（/list /exec /kill） |

### 2.4 现有 `init.txt` 格式

```
bash /opt/micro_servers/db2/_run.sh
bash /opt/micro_servers/confs/_run.sh
bash /opt/micro_servers/ai_review/_run.sh
...
```

纯命令列表，无任何元数据。

---

## 3. 整体设计

### 3.1 架构总览

```
                          ┌───────────────────────────────────────────────────┐
                          │              core (PMC / Nidus)                   │
                          │                                                   │
                          │  ┌─────────────┐   ┌───────────────────────────┐  │
                          │  │ ServiceRegistry │ │ HealthMonitor            │  │
                          │  │  服务注册表      │ │  健康守护线程              │  │
                          │  │  (内存存储)      │ │  周期探活 + 自动重启       │  │
                          │  └──────┬────────┘   └─────────────┬─────────────┘  │
                          │         │                          │              │
                          │  ┌──────▼──────────────────────────▼─────────────┐ │
                          │  │           HTTP API :5200                       │ │
                          │  │  原有: /list /exec /kill                       │ │
                          │  │  新增: /registry/register                      │ │
                          │  │        /registry/deregister                    │ │
                          │  │        /registry/discover                      │ │
                          │  │        /registry/services                      │ │
                          │  │        /registry/health                        │ │
                          │  └────────────────────────────────────────────────┘ │
                          └───────────────────┬─────────────────────────────────┘
                                              │
        ┌───────────┬──────────┬──────────┬────┴─────┬──────────┬──────────┐
        ▼           ▼          ▼          ▼          ▼          ▼          ▼
      db2        confs    ai_review    dnfs2      gateway     waf      ...
   (自注册)    (自注册)    (自注册)    (自注册)    (自注册)   (自注册)
```

### 3.2 核心组件

| 组件 | 职责 | 新增/改动 |
|------|------|-----------|
| **ServiceRegistry** | 服务注册表（内存数据结构），存储服务元信息，提供注册/注销/查询接口 | 新增 |
| **HealthMonitor** | 后台线程，周期性对已注册服务做健康探测，故障时触发自动重启 | 新增 |
| **RegistrationClient** | 各服务端的轻量 SDK，启动时调用 core 注册接口，退出时注销 | 新增（Python SDK + C++ SDK） |
| **ServiceEntry** | 服务描述结构体，包含 name/port/protocol/health_endpoint/status 等 | 新增 |
| **init.txt 格式扩展** | 兼容旧格式，同时支持新的声明式配置（YAML/JSON） | 改动 |
| **HTTP API 扩展** | 在现有 `:5200` HTTP 服务器上增加注册中心路由 | 改动 |

---

## 4. 详细设计

### 4.1 服务元数据结构

#### C++ 定义

```cpp
// include/registry/ServiceEntry.hh
#pragma once
#include <string>
#include <chrono>
#include <boost/json.hpp>

namespace qing::registry {

enum class ServiceStatus {
    REGISTERING,    // 注册中（已注册但尚未通过首次健康检查）
    UP,             // 健康
    DOWN,           // 不健康（连续探活失败）
    STOPPED         // 已停止（主动注销或 core 终止）
};

enum class Protocol {
    HTTP,           // HTTP/REST 服务（如 gateway, static, dnfs, ai_review）
    PYMSG,          // pymsg/TCP 服务（如 db, smtp, captcha, confs）
    WEBSOCKET,      // WebSocket 服务（如 aibox）
    PROCESS_ONLY    // 纯进程（无网络端口，如 redirect80 的特殊情况）
};

struct ServiceEntry {
    std::string name;               // 服务唯一标识（如 "db", "gateway", "dnfs"）
    std::string host;               // 绑定地址（如 "127.0.0.1"）
    int         port;               // 监听端口
    Protocol    protocol;           // 通信协议
    std::string health_endpoint;    // 健康检查端点（HTTP: "/health"；pymsg: "health" 路由）
    int         health_interval_s;  // 健康检查间隔（秒），默认 15
    int         health_timeout_ms;  // 健康检查超时（毫秒），默认 3000
    int         restart_max;        // 最大连续重启次数，超过则放弃，默认 5
    int         restart_count;      // 当前连续重启计数
    pid_t       pid;                // 进程 PID（core 拉起时可知）
    std::string start_cmd;          // 启动命令（用于自动重启）
    ServiceStatus status;
    std::chrono::steady_clock::time_point last_heartbeat;  // 最后心跳时间
    std::chrono::steady_clock::time_point registered_at;   // 注册时间

    // 序列化为 JSON
    boost::json::object to_json() const;

    // 从 JSON 反序列化（用于 HTTP 注册请求）
    static ServiceEntry from_json(const boost::json::object& obj);
};

} // namespace qing::registry
```

#### JSON 表示

```json
{
    "name": "db",
    "host": "127.0.0.1",
    "port": 8005,
    "protocol": "pymsg",
    "health_endpoint": "health",
    "health_interval_s": 15,
    "health_timeout_ms": 3000,
    "restart_max": 5,
    "pid": 12345,
    "start_cmd": "bash /opt/micro_servers/db/_run.sh",
    "status": "UP",
    "registered_at": "2026-08-08T10:30:00Z"
}
```

### 4.2 ServiceRegistry 注册表

```cpp
// include/registry/ServiceRegistry.hh
#pragma once
#include <string>
#include <unordered_map>
#include <mutex>
#include <optional>
#include <vector>
#include <functional>
#include "registry/ServiceEntry.hh"

namespace qing::registry {

class ServiceRegistry {
public:
    // 注册服务（幂等：同名服务重复注册则更新）
    bool registerService(const ServiceEntry& entry);

    // 注销服务
    bool deregisterService(const std::string& name);

    // 查询单个服务
    std::optional<ServiceEntry> discover(const std::string& name) const;

    // 列出全部服务
    std::vector<ServiceEntry> listAll() const;

    // 按状态过滤
    std::vector<ServiceEntry> listByStatus(ServiceStatus status) const;

    // 更新服务状态
    void updateStatus(const std::string& name, ServiceStatus status);

    // 更新心跳时间
    void touchHeartbeat(const std::string& name);

    // 注册状态变更回调（用于通知 HealthMonitor 等）
    using StatusCallback = std::function<void(const std::string& name, ServiceStatus old_s, ServiceStatus new_s)>;
    void onStatusChange(StatusCallback cb);

private:
    mutable std::mutex mtx_;
    std::unordered_map<std::string, ServiceEntry> entries_;
    std::vector<StatusCallback> callbacks_;
};

} // namespace qing::registry
```

### 4.3 HealthMonitor 健康守护

```cpp
// include/registry/HealthMonitor.hh
#pragma once
#include <thread>
#include <atomic>
#include <memory>
#include "registry/ServiceRegistry.hh"

namespace qing::registry {

class HealthMonitor {
public:
    HealthMonitor(ServiceRegistry& registry, class PPool& ppool);

    void start();   // 启动探活线程
    void stop();    // 停止探活线程

private:
    void loop();                                // 主循环
    bool checkHttp(const ServiceEntry& entry);  // HTTP GET 健康检查
    bool checkPymsg(const ServiceEntry& entry); // pymsg TCP 健康检查
    bool checkTcp(const ServiceEntry& entry);   // 纯 TCP 连接检查（兜底）
    void handleFailure(ServiceEntry& entry);    // 故障处理：重启或放弃
    void restartService(ServiceEntry& entry);   // 重启服务（调用 PPool）

    ServiceRegistry& registry_;
    PPool& ppool_;
    std::atomic<bool> running_{false};
    std::thread worker_;
};

} // namespace qing::registry
```

**探活策略**：

| 协议 | 探活方式 | 判定标准 |
|------|----------|----------|
| HTTP | `GET http://host:port/<health_endpoint>` | 返回 2xx 即为健康 |
| pymsg | TCP 连接 + 发送 `{"_route":"health"}` | 收到有效 JSON 响应即为健康 |
| WebSocket | TCP 连接检查 | 连接成功即为健康 |
| PROCESS_ONLY | `kill(pid, 0)` 检查进程存活 | 进程存在即为健康 |

**重启策略**：

1. 连续探活失败（默认 3 次）后标记为 `DOWN`。
2. 自动重启：调用 `PPool::crtp(entry.start_cmd)` 重新拉起。
3. 每次成功重启重置 `restart_count`，如果再次失败则 `restart_count++`。
4. 当 `restart_count >= restart_max` 时标记为 `STOPPED` 并放弃重启，等待人工介入。
5. 重启间隔：指数退避 2s → 4s → 8s → 16s → 30s（封顶）。

### 4.4 HTTP API 扩展

在现有 `HttpTask::registerRoutes()` 中新增路由：

```
POST   /registry/register       注册/更新服务
POST   /registry/deregister     注销服务
GET    /registry/discover       查询单个服务（?name=xxx）
GET    /registry/services       列出全部已注册服务
GET    /registry/health         注册中心自身健康检查
```

#### 请求/响应示例

**注册服务** `POST /registry/register`

```json
// 请求
{
    "name": "dnfs",
    "host": "127.0.0.1",
    "port": 8006,
    "protocol": "http",
    "health_endpoint": "/get_notes",
    "health_interval_s": 15,
    "health_timeout_ms": 3000,
    "restart_max": 5
}

// 响应 200
{
    "success": true,
    "message": "Service registered: dnfs"
}
```

**服务发现** `GET /registry/discover?name=db`

```json
// 响应 200
{
    "success": true,
    "data": {
        "name": "db",
        "host": "127.0.0.1",
        "port": 8005,
        "protocol": "pymsg",
        "status": "UP"
    }
}
```

**列出全部** `GET /registry/services`

```json
// 响应 200
{
    "success": true,
    "data": [
        { "name": "db", "port": 8005, "status": "UP", ... },
        { "name": "gateway", "port": 8443, "status": "UP", ... },
        { "name": "dnfs", "port": 8006, "status": "DOWN", ... }
    ]
}
```

### 4.5 客户端 SDK

#### Python SDK（`registration_client.py`）

各 Python 服务（db, dnfs, confs, smtp, captcha, ai_review, static, redirect80）复制即用：

```python
"""
服务注册客户端 SDK
各服务启动时调用 register()，退出时调用 deregister()。
core 不可用时静默失败（fail-safe），不阻塞业务启动。
"""
import json
import socket
import atexit
import urllib.request
import urllib.error

class RegistrationClient:
    def __init__(self, core_host="127.0.0.1", core_port=5200):
        self.core_url = f"http://{core_host}:{core_port}"
        self._registered = False

    def register(self, name, host, port, protocol="http",
                 health_endpoint="/health", health_interval_s=15,
                 health_timeout_ms=3000, restart_max=5):
        """向 core 注册本服务（fail-safe）"""
        payload = json.dumps({
            "name": name, "host": host, "port": port,
            "protocol": protocol, "health_endpoint": health_endpoint,
            "health_interval_s": health_interval_s,
            "health_timeout_ms": health_timeout_ms,
            "restart_max": restart_max
        }).encode()
        try:
            req = urllib.request.Request(
                f"{self.core_url}/registry/register",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                self._registered = (resp.status == 200)
        except Exception:
            pass  # core 不可用时静默失败

        if self._registered:
            atexit.register(self.deregister)

    def deregister(self):
        """注销本服务（best-effort）"""
        if not self._registered:
            return
        try:
            req = urllib.request.Request(
                f"{self.core_url}/registry/deregister",
                data=json.dumps({"name": self.name}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

    def discover(self, service_name):
        """查询目标服务地址，返回 (host, port) 或 None"""
        try:
            url = f"{self.core_url}/registry/discover?name={service_name}"
            with urllib.request.urlopen(url, timeout=2) as resp:
                data = json.loads(resp.read())
                if data.get("success"):
                    svc = data["data"]
                    return svc["host"], svc["port"]
        except Exception:
            pass
        return None
```

#### C++ SDK 片段（利用现有 `libhttp::HttpClient`）

```cpp
// 服务启动后注册
#include <us/HttpClient.hpp>
#include <boost/json.hpp>

void register_to_core(const std::string& name, int port,
                      const std::string& protocol = "http",
                      const std::string& health_ep = "/health") {
    try {
        std::string host = "127.0.0.1";
        int core_port = 5200;
        if (auto h = std::getenv("CORE_HOST")) host = h;
        if (auto p = std::getenv("CORE_PORT")) core_port = std::atoi(p);

        boost::json::object body;
        body["name"] = name;
        body["host"] = "127.0.0.1";
        body["port"] = port;
        body["protocol"] = protocol;
        body["health_endpoint"] = health_ep;

        pmc::net::HttpClient client;
        client.set_header("Content-Type: application/json");
        std::string url = "http://" + host + ":" + std::to_string(core_port) + "/registry/register";
        std::string resp;
        client.post_stream_sync(url, boost::json::serialize(body),
            [&](const std::string& chunk) { resp += chunk; });
    }
    catch (...) { /* fail-safe */ }
}
```

### 4.6 `init.txt` 格式扩展

保留旧格式（纯命令列表）的向后兼容，同时支持新的**声明式注册格式** `services.yaml`：

```yaml
# core/services.yaml（新格式，可选）
# 如果 services.yaml 存在则优先使用，否则回退到 init.txt
services:
  - name: db
    command: bash /opt/micro_servers/db2/_run.sh
    port: 8005
    protocol: pymsg
    health_endpoint: health
    health_interval_s: 15
    restart_max: 5
    depends_on: []                    # 无依赖，最先启动

  - name: confs
    command: bash /opt/micro_servers/confs/_run.sh
    port: 8010
    protocol: pymsg
    health_endpoint: health
    depends_on: []

  - name: ai_review
    command: bash /opt/micro_servers/ai_review/_run.sh
    port: 8007
    protocol: http
    health_endpoint: /health
    depends_on: []

  - name: dnfs
    command: bash /opt/micro_servers/dnfs2/_run.sh
    port: 8006
    protocol: http
    health_endpoint: /get_notes
    depends_on: [db, ai_review]       # 等待依赖就绪再启动

  - name: smtp
    command: bash /opt/micro_servers/smtp/_run.sh
    port: 8008
    protocol: pymsg
    health_endpoint: health
    depends_on: []

  - name: gateway
    command: bash /opt/micro_servers/gateway/_run.sh
    port: 8443
    protocol: http
    health_endpoint: /health
    depends_on: [db, confs]

  - name: waf
    command: bash /opt/micro_servers/waf/_run.sh
    port: 443
    protocol: http
    health_endpoint: ""               # WAF 不做 HTTP 健康检查
    depends_on: [gateway]

  - name: captcha
    command: bash /opt/micro_servers/captcha/_run.sh
    port: 8009
    protocol: pymsg
    health_endpoint: health
    depends_on: []

  - name: redirect80
    command: bash /opt/micro_servers/redirect80/_run.sh
    port: 80
    protocol: http
    health_endpoint: ""
    depends_on: [waf]

  - name: aibox
    command: bash /opt/micro_servers/aibox/_run.sh
    port: 8765
    protocol: websocket
    health_endpoint: ""
    depends_on: []
```

**启动编排逻辑**：

1. 解析 `services.yaml`（如不存在则回退 `init.txt`）。
2. 按 `depends_on` 构建 DAG（有向无环图），拓扑排序确定启动顺序。
3. 依次启动服务，对于有依赖的服务等待其全部依赖状态变为 `UP` 后再启动。
4. 启动后自动注册到 `ServiceRegistry`。
5. 超时等待（默认 30s）依赖仍未就绪则跳过并记录日志。

---

## 5. 实现计划

### 5.1 分阶段交付

| 阶段 | 里程碑 | 涉及文件 | 预估工时 |
|------|--------|----------|----------|
| **P0 — 注册表内核** | ServiceEntry + ServiceRegistry 数据结构与 CRUD | 新增 `include/registry/ServiceEntry.hh`、`ServiceRegistry.hh`、`src/registry/ServiceEntry.cpp`、`ServiceRegistry.cpp` | 2h |
| **P1 — HTTP API** | 在 HttpTask 中注册 `/registry/*` 路由 | 改动 `src/main/HttpTask.cpp`、`include/main/HttpTask.hh` | 2h |
| **P2 — 自动注册** | init.txt 解析后自动填充注册表（进程名/端口从命令推断或从 services.yaml 读取） | 改动 `src/main/pmc_mtd.cpp`、新增 `src/registry/InitParser.cpp` | 3h |
| **P3 — 健康守护** | HealthMonitor 后台线程，HTTP/TCP/pymsg 三种探活 | 新增 `include/registry/HealthMonitor.hh`、`src/registry/HealthMonitor.cpp` | 4h |
| **P4 — 自动重启** | 故障服务自动重启，指数退避，最大次数限制 | 改动 `HealthMonitor.cpp`、联动 `PPool` | 2h |
| **P5 — 客户端 SDK** | Python `registration_client.py` + C++ 注册片段 | 新增 `registration_client.py`（复制到各服务） | 2h |
| **P6 — 服务发现集成** | gateway/waf 等消费方从注册表获取服务地址替代硬编码 | 改动 `gateway`、`waf` 配置读取逻辑 | 3h |
| **P7 — services.yaml** | 声明式配置解析，DAG 拓扑排序启动 | 新增 `src/registry/ServiceYaml.cpp`、改动 `pmc_mtd.cpp` | 4h |
| **P8 — 测试** | 单元测试 + 集成测试（注册/发现/健康检查/自动重启） | `tests/` 新增测试文件 | 3h |

**总计预估：~25 小时**

### 5.2 构建系统改动

`Makefile` 需新增编译目标：

```makefile
# 新增注册中心模块源文件
$(BUILD_DIR)/ServiceEntry.o: src/registry/ServiceEntry.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/ServiceRegistry.o: src/registry/ServiceRegistry.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/HealthMonitor.o: src/registry/HealthMonitor.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/InitParser.o: src/registry/InitParser.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

# 链接时追加上述 .o
nidus.out: ... $(BUILD_DIR)/ServiceEntry.o $(BUILD_DIR)/ServiceRegistry.o \
             $(BUILD_DIR)/HealthMonitor.o $(BUILD_DIR)/InitParser.o
```

### 5.3 新增文件清单

```
core/
├── include/registry/
│   ├── ServiceEntry.hh          # 服务元数据结构
│   ├── ServiceRegistry.hh       # 注册表（线程安全）
│   ├── HealthMonitor.hh         # 健康守护线程
│   └── InitParser.hh            # services.yaml 解析器
├── src/registry/
│   ├── ServiceEntry.cpp
│   ├── ServiceRegistry.cpp
│   ├── HealthMonitor.cpp
│   └── InitParser.cpp
├── services.yaml                # 声明式服务配置（可选，替代 init.txt）
└── registration_client.py       # Python 服务注册 SDK（复制到各服务）
```

---

## 6. 服务接入指南

### 6.1 Python 服务接入（以 dnfs 为例）

在 `dnfs/dnfs.py` 启动代码中加入注册：

```python
from registration_client import RegistrationClient

# 启动后注册
reg = RegistrationClient()
reg.register(
    name="dnfs",
    host="127.0.0.1",
    port=8006,
    protocol="http",
    health_endpoint="/get_notes",
    restart_max=5
)

# 使用服务发现替代硬编码
db_addr = reg.discover("db")
if db_addr:
    DB_HOST, DB_PORT = db_addr
else:
    DB_HOST, DB_PORT = "127.0.0.1", 8005  # 回退默认值
```

### 6.2 C++ 服务接入（以 dnfs2 为例）

在 `dnfs2/src/main.cpp` 启动后调用：

```cpp
#include "registry_client.hh"  // 封装 register_to_core

// 启动后注册
register_to_core("dnfs", 8006, "http", "/get_notes");

// 使用服务发现
auto [host, port] = discover_service("db");
// host="127.0.0.1", port=8005
```

### 6.3 Go 服务接入（waf）

waf 作为 Go 服务，可用类似方式调用 HTTP 注册：

```go
func registerToCore() {
    body := `{"name":"waf","host":"0.0.0.0","port":443,"protocol":"http","health_endpoint":""}`
    http.Post("http://127.0.0.1:5200/registry/register", "application/json",
              strings.NewReader(body))
}
```

---

## 7. 安全考量

### 7.1 注册接口认证

注册中心 API（`/registry/*`）的安全性至关重要：

| 威胁 | 防御 |
|------|------|
| 外部伪造注册请求 | `/registry/*` 仅监听 `127.0.0.1`（与现有 `:5200` 一致），不对外暴露 |
| 本地恶意进程注册虚假信息 | 可选：注册时校验 `start_cmd` 是否在白名单中（来自 `init.txt` / `services.yaml`） |
| 注册中心被恶意注销服务 | `deregister` 接口需验证请求来源 PID 与注册 PID 一致 |

### 7.2 健康检查安全

- 健康检查请求应设置合理超时（默认 3s），避免慢响应拖垮 HealthMonitor。
- pymsg 健康检查使用独立连接，不复用业务连接池。

### 7.3 日志

- 注册/注销/状态变更事件应写入日志（`LOG_INFO`）。
- 自动重启事件应写入日志（`LOG_WARNING`），达到重启上限时 `LOG_ERROR`。
- 后续可集成 `log_client` 推送到 db 的 `Logs` 表。

---

## 8. 兼容性与迁移

### 8.1 零改动兼容

- 现有 `init.txt` 格式继续有效，core 按旧逻辑拉起子进程。
- 旧模式下的子进程不会自动注册（注册表为空），但不影响现有 `/list` `/exec` `/kill` 功能。
- HTTP API 中 `/registry/*` 为新增路由，不与旧路由冲突。

### 8.2 渐进迁移路径

```
阶段 1: core 实现注册表 + HTTP API（P0-P1），服务零改动
        ↓
阶段 2: 各服务逐步集成 RegistrationClient SDK（P5），注册/发现可用
        ↓
阶段 3: 创建 services.yaml，启用 HealthMonitor + 自动重启（P3-P4, P7）
        ↓
阶段 4: gateway/waf 从注册表获取后端地址，消除硬编码（P6）
```

---

## 9. 未来扩展方向

| 方向 | 说明 |
|------|------|
| **事件总线** | 服务状态变更（UP/DOWN）时向订阅者推送事件，替代轮询 discover |
| **负载均衡** | 注册表支持同服务多实例，discover 返回实例列表 + 轮询/加权策略 |
| **优雅关停** | core 退出时按依赖反序逐个停止服务，发送 SIGTERM 等待优雅退出 |
| **持久化** | 注册表快照持久化到文件，core 重启后可恢复（当前为内存存储） |
| **Web 控制台** | 基于 `/registry/services` API 构建可视化服务状态面板 |
| **配置联动** | 注册中心与 confs 服务联动，服务注册后自动分发配置 |

---

## 10. 附录

### A. 端口与服务对照（注册表预期数据）

| name | port | protocol | health_endpoint | start_cmd |
|------|------|----------|-----------------|-----------|
| db | 8005 | pymsg | health | `bash /opt/micro_servers/db2/_run.sh` |
| confs | 8010 | pymsg | health | `bash /opt/micro_servers/confs/_run.sh` |
| ai_review | 8007 | http | /health | `bash /opt/micro_servers/ai_review/_run.sh` |
| dnfs | 8006 | http | /get_notes | `bash /opt/micro_servers/dnfs2/_run.sh` |
| smtp | 8008 | pymsg | health | `bash /opt/micro_servers/smtp/_run.sh` |
| gateway | 8443 | http | /health | `bash /opt/micro_servers/gateway/_run.sh` |
| waf | 443 | http | — | `bash /opt/micro_servers/waf/_run.sh` |
| captcha | 8009 | pymsg | health | `bash /opt/micro_servers/captcha/_run.sh` |
| redirect80 | 80 | http | — | `bash /opt/micro_servers/redirect80/_run.sh` |
| aibox | 8765 | websocket | — | `bash /opt/micro_servers/aibox/_run.sh` |

### B. 关键依赖

| 依赖 | 用途 | 已存在 |
|------|------|--------|
| `boost::json` | JSON 序列化/反序列化 | ✅ |
| `boost::beast::http` | HTTP 客户端（健康检查） | ✅ (libhttp) |
| `libhttp::HttpClient` | 服务注册 HTTP 调用 | ✅ |
| `libmsg::RpcClient` | pymsg 健康检查 | ✅ |
| `yaml-cpp` 或 `boost::property_tree` | services.yaml 解析 | 需新增 `yaml-cpp` 或使用 JSON 替代 |

### C. 与现有机制的关系

| 现有机制 | 注册中心的关系 |
|----------|--------------|
| `PPool` 进程池 | HealthMonitor 通过 PPool 重启故障服务 |
| `pmc_mtd::list()` | `/registry/services` 提供更丰富的服务元信息 |
| `init.txt` | 向后兼容，新增 `services.yaml` 作为增强替代 |
| `Mq + Pipe` IPC | 保留用于命令行子系统调用模式 |
| HTTP `:5200` | 注册中心路由复用现有 HTTP 服务器 |
| systemd `Nidus.service` | 保持不变，systemd 守护 core 进程本身 |
