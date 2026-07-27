// Package logclient 提供异步批量日志推送客户端。
//
// 通过 pymsg TCP 协议调用 dbv2 execute 路由，逐条 INSERT 到 Logs 表。
//
// 设计要点（与 Python example_log_client.py 对齐）：
//   - 异步非阻塞：内存 channel + 后台 goroutine 批量推送，不拖慢业务
//   - fail-safe：db 服务不可用时静默丢弃，绝不影响业务
//   - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush
//
// 协议：pymsg 长度前缀帧（4 字节 big-endian uint32 长度 + UTF-8 载荷）
package logclient

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"sync"
	"time"
)

const (
	dbAddr        = "127.0.0.1:8005"
	batchSize     = 50
	flushInterval = 2 * time.Second
	socketTimeout = 5 * time.Second
)

// Entry 单条日志。
type Entry struct {
	Service string         `json:"service"`
	Level   string         `json:"level"`
	Message string         `json:"message"`
	TraceID string         `json:"trace_id,omitempty"`
	Extra   map[string]any `json:"extra,omitempty"`
}

// dbV2Request execute 路由的 INSERT 请求体。
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

// Client 异步批量日志推送客户端。
type Client struct {
	ch      chan Entry
	stop    chan struct{}
	wg      sync.WaitGroup
	stopped bool
	mu      sync.Mutex
}

// global 全局单例。
var global *Client

// Init 初始化全局日志客户端并启动后台 flush goroutine。
func Init() {
	global = New()
}

// New 创建客户端实例。
func New() *Client {
	c := &Client{
		ch:   make(chan Entry, 256),
		stop: make(chan struct{}),
	}
	c.wg.Add(1)
	go c.flushLoop()
	return c
}

// Push 推送一条日志（非阻塞，队列满时丢弃）。
func Push(service, level, message string, extra map[string]any) {
	if global == nil {
		return
	}
	global.push(Entry{
		Service: service,
		Level:   level,
		Message: message,
		Extra:   extra,
	})
}

// Info 推送 INFO 级别日志。
func Info(service, message string, extra map[string]any) {
	Push(service, "INFO", message, extra)
}

// Warning 推送 WARNING 级别日志。
func Warning(service, message string, extra map[string]any) {
	Push(service, "WARNING", message, extra)
}

// Error 推送 ERROR 级别日志。
func Error(service, message string, extra map[string]any) {
	Push(service, "ERROR", message, extra)
}

// Flush 同步 flush 队列中剩余日志（用于进程退出前）。
func Flush() {
	if global == nil {
		return
	}
	global.flush()
}

func (c *Client) push(e Entry) {
	select {
	case c.ch <- e:
	default:
		// 队列满，静默丢弃（fail-safe）
	}
}

func (c *Client) flushLoop() {
	defer c.wg.Done()
	batch := make([]Entry, 0, batchSize)
	ticker := time.NewTicker(flushInterval)
	defer ticker.Stop()

	for {
		select {
		case e := <-c.ch:
			batch = append(batch, e)
			// 非阻塞地继续取，直到满 batchSize
			for len(batch) < batchSize {
				select {
				case e2 := <-c.ch:
					batch = append(batch, e2)
				default:
					goto send
				}
			}
		send:
			if len(batch) >= batchSize {
				c.send(batch)
				batch = batch[:0]
			}
		case <-ticker.C:
			if len(batch) > 0 {
				c.send(batch)
				batch = batch[:0]
			}
		case <-c.stop:
			// 退出前 drain channel
			for {
				select {
				case e := <-c.ch:
					batch = append(batch, e)
				default:
					if len(batch) > 0 {
						c.send(batch)
					}
					return
				}
			}
		}
	}
}

func (c *Client) send(batch []Entry) {
	now := time.Now().UTC().Format(time.RFC3339)
	for _, entry := range batch {
		c.sendOne(entry, now)
	}
}

// sendOne 通过 pymsg TCP 协议发送单条日志到 dbv2。
func (c *Client) sendOne(entry Entry, ts string) {
	extraJSON := ""
	if entry.Extra != nil {
		b, err := json.Marshal(entry.Extra)
		if err != nil {
			return
		}
		extraJSON = string(b)
	}

	values := map[string]any{
		"ts":      ts,
		"service": entry.Service,
		"level":   entry.Level,
		"message": entry.Message,
	}
	if entry.TraceID != "" {
		values["trace_id"] = entry.TraceID
	}
	if extraJSON != "" {
		values["extra"] = extraJSON
	}

	req := dbV2Request{
		Route:  "execute",
		Action: "insert",
		Table:  "Logs",
		Values: values,
	}
	data, err := json.Marshal(req)
	if err != nil {
		return
	}

	// pymsg 协议：4 字节 big-endian 长度 + UTF-8 载荷
	resp, err := pymsgCall(data)
	if err != nil {
		return // fail-safe
	}

	var r dbV2Response
	if json.Unmarshal(resp, &r) != nil || !r.Success {
		fmt.Printf("[logclient] INSERT failed: %s\n", r.Error)
	}
}

// pymsgCall 发送一条 pymsg 请求并读取响应（短连接模式）。
func pymsgCall(payload []byte) ([]byte, error) {
	conn, err := net.DialTimeout("tcp", dbAddr, socketTimeout)
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	conn.SetDeadline(time.Now().Add(socketTimeout))

	// 发送：4 字节长度前缀 + 载荷
	header := make([]byte, 4)
	binary.BigEndian.PutUint32(header, uint32(len(payload)))
	if _, err := conn.Write(header); err != nil {
		return nil, err
	}
	if _, err := conn.Write(payload); err != nil {
		return nil, err
	}

	// 接收：4 字节长度前缀 + 载荷
	if _, err := io.ReadFull(conn, header); err != nil {
		return nil, err
	}
	respLen := binary.BigEndian.Uint32(header)
	resp := make([]byte, respLen)
	if _, err := io.ReadFull(conn, resp); err != nil {
		return nil, err
	}

	return resp, nil
}

func (c *Client) flush() {
	c.mu.Lock()
	if c.stopped {
		c.mu.Unlock()
		return
	}
	c.stopped = true
	c.mu.Unlock()

	close(c.stop)
	c.wg.Wait()
}
