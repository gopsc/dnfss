# memdb — 内存缓存数据库

基于 `libmemdb` 模板库的 JSON RPC 缓存服务，提供键值存储、TTL 过期、并发安全的内存缓存能力。

## 架构

```
Memdb<std::string, std::string>        (libmemdb 模板：LRU 链表 + 哈希索引 + TTL 过期)
    └── MemdbServer                     (RPC 服务端：路由注册 + 互斥锁线程安全)
        └── pmc::net::RpcServer         (libmsg JSON RPC 服务端：多客户端 select + 线程池)
```

## 路由

| 路由 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `ping` | — | `"pong"` | 健康检查 |
| `set` | `key`, `value` | `true/false` | 插入键值对（key 已存在返回 false） |
| `get` | `key` | `"value"` / `null` | 查询键值 |
| `del` | `key` | `true/false` | 删除键 |
| `update` | `key`, `value`, `refresh_ttl?` | `true/false` | 更新值，可选刷新 TTL |
| `exists` | `key` | `true/false` | 键是否存在 |
| `size` | — | `int` | 当前条目数 |
| `expire` | `key`, `value` | `true/false` | 更新已有键的值并重置 TTL（键不存在返回 false） |
| `show` | — | `[{key,value},...]` | 导出所有条目 |

## 配置

`ServerConfig` 结构体（`src/memdb.cpp`）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `listen_host` | `127.0.0.1` | 监听地址 |
| `listen_port` | `8011` | 监听端口 |
| `workers` | `4` | 线程池大小 |
| `max_time` | `600` | 默认 TTL（秒），10 分钟 |
| `backlog` | `128` | listen() backlog（libmsg 默认 10，短连接突刺易丢连接） |

## 构建与运行

**前置依赖**：g++（C++20）、libmsg、libmemdb、boost::json

```bash
# 构建
bash _set.sh

# 运行
bash _run.sh
```

## 客户端示例

```cpp
#include <us/RpcClient.hpp>

pmc::net::RpcClient rpc("127.0.0.1", 8011, "memdb-client");

// 插入
json::object params;
params["key"]   = "session:abc123";
params["value"] = R"({"user":"admin","role":"root"})";
rpc.call("set", params);  // → true

// 查询
rpc.call("get", {{"key", "session:abc123"}});  // → "{\"user\":\"admin\",...}"

// 检查存在
rpc.call("exists", {{"key", "session:abc123"}});  // → true

// 查看大小
rpc.call("size");  // → 1
```

## 项目结构

```
memdb/
├── src/
│   └── memdb.cpp      # MemdbServer + main()
├── Makefile
├── _set.sh
├── _run.sh
└── README.md
```