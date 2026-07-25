// Package logclient 提供异步批量日志推送客户端，将日志发送到 db 服务的 /log/ingest 接口。
//
// 设计要点（与 Python log_client.py 对齐）：
//   - 异步非阻塞：内存 channel + 后台 goroutine 批量推送，不拖慢业务
//   - fail-safe：db 服务不可用时静默丢弃，绝不影响业务
//   - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush
package logclient

import (
	"bytes"
	"encoding/json"
	"net/http"
	"sync"
	"time"
)

const (
	defaultURL     = "http://127.0.0.1:8005/log/ingest"
	batchSize      = 50
	flushInterval  = 2 * time.Second
	requestTimeout = 5 * time.Second
)

// Entry 单条日志。
type Entry struct {
	Service string         `json:"service"`
	Level   string         `json:"level"`
	Message string         `json:"message"`
	TraceID string         `json:"trace_id,omitempty"`
	Extra   map[string]any `json:"extra,omitempty"`
}

// Client 异步批量日志推送客户端。
type Client struct {
	url     string
	ch      chan Entry
	client  *http.Client
	stop    chan struct{}
	wg      sync.WaitGroup
	stopped bool
	mu      sync.Mutex
}

// global 全局单例。
var global *Client

// Init 初始化全局日志客户端并启动后台 flush goroutine。
func Init() {
	global = New(defaultURL)
}

// New 创建客户端实例。
func New(url string) *Client {
	c := &Client{
		url:    url,
		ch:     make(chan Entry, 256),
		client: &http.Client{Timeout: requestTimeout},
		stop:   make(chan struct{}),
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
	payload := map[string]any{"logs": batch}
	data, err := json.Marshal(payload)
	if err != nil {
		return
	}
	req, err := http.NewRequest("POST", c.url, bytes.NewReader(data))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return // fail-safe：静默丢弃
	}
	resp.Body.Close()
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
