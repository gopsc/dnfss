package gomsg

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

// 集中式日志客户端：通过 pymsg/TCP 调用 db2（或 db）execute 路由，
// 逐条 INSERT 到 Logs 表。对应各服务目录下的 log_client.py 与 C++ liblogc。
//
// 设计要点（与 waf internal/logclient / Python log_client.py 对齐）：
//   - 异步非阻塞：AsyncQueue 批量推送，不拖慢业务
//   - fail-safe：db 服务不可用时静默丢弃，绝不影响业务
//   - 缓冲批量：满 50 条或超时 2 秒自动 flush

const defaultDBAddr = "127.0.0.1:8005"

// 日志级别数值（越小越详细）。
const (
	levelDebug = 0
	levelInfo  = 1
	levelWarn  = 2
	levelError = 3
)

var levelMap = map[string]int{
	"debug": levelDebug, "DEBUG": levelDebug,
	"info": levelInfo, "INFO": levelInfo,
	"warn": levelWarn, "warning": levelWarn, "WARN": levelWarn, "WARNING": levelWarn,
	"error": levelError, "ERROR": levelError,
}

// Entry 单条日志。
type Entry struct {
	Service string
	Level   string
	Message string
	TraceID string
	Extra   map[string]any
}

// LogClient 异步批量日志推送客户端。
type LogClient struct {
	queue    *AsyncQueue[Entry]
	client   *MsgClient
	minLevel int
}

// NewLogClient 创建日志客户端并启动后台批量推送。
// dbAddr 为空时依次取环境变量 LOG_DB_ADDR、默认 127.0.0.1:8005；
// 默认最低级别 info，可用 SetLevel 调整（环境变量 LOG_LEVEL 同样生效）。
func NewLogClient(dbAddr string) *LogClient {
	if dbAddr == "" {
		dbAddr = os.Getenv("LOG_DB_ADDR")
	}
	if dbAddr == "" {
		dbAddr = defaultDBAddr
	}
	lc := &LogClient{
		client:   NewMsgClient(dbAddr, 5*time.Second),
		minLevel: levelInfo,
	}
	lc.queue = NewAsyncQueue[Entry](lc.sendBatch, AsyncQueueConfig{})
	if lv := os.Getenv("LOG_LEVEL"); lv != "" {
		lc.SetLevel(lv)
	}
	return lc
}

// SetLevel 设置最低日志级别（debug|info|warn|error，大小写不敏感）。
func (c *LogClient) SetLevel(level string) {
	if n, ok := levelMap[level]; ok {
		c.minLevel = n
	}
}

// Push 推送一条日志（非阻塞，队列满或低于最低级别时静默丢弃）。
func (c *LogClient) Push(e Entry) bool {
	n, ok := levelMap[e.Level]
	if !ok || n < c.minLevel {
		return false
	}
	return c.queue.Push(e)
}

// Debug 推送 DEBUG 级别日志。
func (c *LogClient) Debug(service, message string, extra map[string]any) bool {
	return c.Push(Entry{Service: service, Level: "DEBUG", Message: message, Extra: extra})
}

// Info 推送 INFO 级别日志。
func (c *LogClient) Info(service, message string, extra map[string]any) bool {
	return c.Push(Entry{Service: service, Level: "INFO", Message: message, Extra: extra})
}

// Warning 推送 WARNING 级别日志。
func (c *LogClient) Warning(service, message string, extra map[string]any) bool {
	return c.Push(Entry{Service: service, Level: "WARNING", Message: message, Extra: extra})
}

// Error 推送 ERROR 级别日志。
func (c *LogClient) Error(service, message string, extra map[string]any) bool {
	return c.Push(Entry{Service: service, Level: "ERROR", Message: message, Extra: extra})
}

// Flush 停止队列并 flush 剩余日志（用于进程退出前）。
func (c *LogClient) Flush() {
	c.queue.Shutdown()
}

// dbV2Request execute 路由的 INSERT 请求体（与 Python/C++ 各 log 客户端一致）。
type dbV2Request struct {
	Route  string         `json:"_route"`
	Action string         `json:"action"`
	Table  string         `json:"table"`
	Values map[string]any `json:"values"`
}

// dbV2Response execute 路由的通用响应。
type dbV2Response struct {
	Success bool   `json:"success"`
	Error   string `json:"error,omitempty"`
}

func (c *LogClient) sendBatch(batch []Entry) {
	now := time.Now().UTC().Format(time.RFC3339)
	for _, e := range batch {
		c.sendOne(e, now)
	}
}

// sendOne 通过 pymsg TCP 协议发送单条日志到 db2（fail-safe：任何错误静默丢弃）。
func (c *LogClient) sendOne(e Entry, ts string) {
	extraJSON := ""
	if e.Extra != nil {
		b, err := json.Marshal(e.Extra)
		if err != nil {
			return
		}
		extraJSON = string(b)
	}

	values := map[string]any{
		"ts":      ts,
		"service": e.Service,
		"level":   e.Level,
		"message": e.Message,
	}
	if e.TraceID != "" {
		values["trace_id"] = e.TraceID
	}
	if extraJSON != "" {
		values["extra"] = extraJSON
	}

	req := dbV2Request{Route: "execute", Action: "insert", Table: "Logs", Values: values}
	data, err := json.Marshal(req)
	if err != nil {
		return
	}

	resp, err := c.client.Call(data)
	if err != nil {
		return // fail-safe
	}

	var r dbV2Response
	if json.Unmarshal(resp, &r) != nil || !r.Success {
		fmt.Fprintf(os.Stderr, "[gomsg:logclient] INSERT failed: %s\n", r.Error)
	}
}
