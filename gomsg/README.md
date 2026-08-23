# gomsg — pymsg / libmsg 协议的 Go 实现

Go 版消息通信库，对应 Python [pymsg](../pymsg/) 与 C++ [libmsg](../libmsg/)，线协议字节级兼容：

```
[4 字节 big-endian uint32 长度][UTF-8 载荷]        （单条上限 64 MiB）
```

## 模块组成

| 文件 | 内容 | 对应 |
|------|------|------|
| `frame.go` | 帧编解码：`Encode` / `WriteFrame` / `ReadFrame`（长度上限防护） | pymsg `send`/`recv` |
| `client.go` | `MsgClient`：复用长连接 + 请求-响应 + 失败重连一次 | pymsg `MsgClient` |
| `server.go` | `MsgServer`：accept 循环 + 每连接 goroutine 串行处理 | pymsg `MsgServer` |
| `rpc.go` | `RpcClient` / `RpcServer` / `RpcError`：`_route` 分发、错误脱敏、panic 恢复 | pymsg `rpc.py` |
| `asyncqueue.go` | `AsyncQueue[T]`：批量 + 定时 flush，满队列丢新数据（fail-safe） | libmsg `AsyncQueue` |
| `logclient.go` | `LogClient`：异步批量推送 db2 Logs 表（级别过滤 + fail-safe） | waf `internal/logclient`、`log_client.py`、liblogc |

## 使用

```go
// RPC 调用（如调 db2 execute 路由）
rpc := gomsg.NewRpcClient("127.0.0.1:8005", 5*time.Second)
data, err := rpc.Call("execute", map[string]any{
    "action": "select", "table": "Users",
})
// data 为 any：数字是 json.Number（保 int64 精度），需用 .Int64()/.Float64() 转换

// 提供 RPC 服务
srv := gomsg.NewRpcServer("127.0.0.1:8009")
srv.AddRoute("health", func(req map[string]any) (any, error) { return map[string]any{"ok": true}, nil })
if err := srv.Start(); err != nil { ... }
defer srv.Stop()

// 集中式日志（写入 db2 Logs 表）
lc := gomsg.NewLogClient("127.0.0.1:8005")
lc.SetLevel("info")
lc.Info("backend", "服务启动", nil)
defer lc.Flush() // 进程退出前 flush
```

## 与 Python / C++ 的兼容约定

- **请求**：`{"_route": "<路由名>", ...业务字段}`（`_route` 在分发前被弹出，handler 看不到）
- **响应**：`{"success": true, "data": ...}` / `{"success": false, "error": "..."}`
- **未知路由**：`{"success": false, "error": "未知路由: <route>"}`
- **非法 JSON**：`{"success": false, "error": "invalid JSON request"}`（不回显输入片段）
- **错误脱敏**：异常信息中的绝对路径替换为 `<redacted>`（对齐 Python `_sanitize_error`）
- **handler panic**：自动恢复并包装为 `success=false`（对应 Python 的 `except Exception`）

## 安全性

- 帧长度上限 64 MiB 在读写两侧强制（恶意长度前缀不会导致内存耗尽）
- 请求 JSON 解析：Go `encoding/json` 内置 10000 层嵌套上限；数字经 `UseNumber` 以字符串保存，
  超大数字不会触发 CPU 密集型解析；输入大小受帧上限约束
- 异常信息脱敏，不透传绝对路径等内部细节
- 数字一律 `json.Number` 保留原始精度，避免 int64 ID 经 float64 丢失

## 测试

```bash
go test ./... -count=1 -race
```

覆盖：帧往返/超限/截断、Client-Server 回环、自动重连、并发客户端、RPC 成功/失败/脱敏/
未知路由/非法 JSON/panic 恢复/超时、AsyncQueue 批量/定时/关停 drain/满队列丢弃、
LogClient 对假 db2 服务的推送与级别过滤、db2 不可用时 fail-safe。

## 来源

`MsgClient` 连接复用与 `LogClient` 逻辑抽取自 [waf/internal/logclient](../waf/internal/logclient/)；
API 与协议约定对齐 [pymsg](../pymsg/)（Python）与 [libmsg](../libmsg/)（C++）。
