# liblogc — 统一集中式日志客户端（C++ 版）

C++20 统一日志客户端库，对应 Python 侧的 `log_client.py`：通过 pymsg/TCP 调用 `db2:8005` 的 `execute` 路由，逐条 INSERT 到 `Logs` 表，实现集中式日志收集。

命名空间 `qing`，库文件 `libus_logc.so`，头文件安装至 `/usr/include/us/LogC.hpp`。被 `ca` / `core` / `gateway` / `captcha2` / `smtp3` 等 C++ 服务依赖。

## 设计要点

- **异步非阻塞**：基于 `pmc::net::AsyncQueue` 内存队列 + 后台线程批量推送，不阻塞业务。
- **缓冲批量**：队列满 50 条或超时 2 秒自动 flush。
- **背压保护**：队列上限 10000 条，满时丢弃最旧日志，防止内存无限增长。
- **级别过滤**：`set_level()` 设定阈值，低于阈值的日志直接丢弃。
- **fail-safe**：db2 不可用时静默丢弃（仅 stderr 提示），绝不影响业务。
- **可配置**：`set_endpoint()` / `set_service_name()`，各服务复用同一份库。

## 用法

```cpp
#include <us/LogC.hpp>

// 启动时配置（默认 127.0.0.1:8005）
qing::LogC::instance().set_endpoint("127.0.0.1", 8005);
qing::LogC::instance().set_service_name("gateway-log");
qing::LogC::instance().set_level("warn");

qing::LogC::instance().info("gateway", "启动完成");
qing::LogC::instance().error("gateway", "连接失败", std::nullopt, R"({"errno":111})");

// 进程退出前调用（幂等）
qing::LogC::instance().shutdown();
```

## 构建安装

```bash
bash _set.sh
```
