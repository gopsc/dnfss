package gomsg

import (
	"sync"
	"time"
)

// AsyncQueueConfig 异步批量队列配置（语义对齐 C++ libmsg.AsyncQueueConfig）。
type AsyncQueueConfig struct {
	BatchSize     int           // 批量发送阈值，达到即 flush；必须 > 0，默认 50
	FlushInterval time.Duration // 定时 flush 间隔；必须 > 0 且 <= 300s，默认 2s
	MaxQueueSize  int           // 队列容量；必须 >= 1，默认 10000；满时丢新数据（对齐 waf logclient 的 fail-safe 语义）
}

func (c AsyncQueueConfig) withDefaults() AsyncQueueConfig {
	if c.BatchSize <= 0 {
		c.BatchSize = 50
	}
	if c.FlushInterval <= 0 || c.FlushInterval > 300*time.Second {
		c.FlushInterval = 2 * time.Second
	}
	if c.MaxQueueSize < 1 {
		c.MaxQueueSize = 10000
	}
	return c
}

// AsyncQueue 通用异步批量发送队列（对应 C++ libmsg.AsyncQueue）。
// Push 非阻塞；队列满时静默丢弃并计数（fail-safe，不影响业务）。
type AsyncQueue[T any] struct {
	cfg     AsyncQueueConfig
	handler func([]T)
	ch      chan T
	stop    chan struct{}
	wg      sync.WaitGroup

	mu      sync.Mutex
	stopped bool
	dropped uint64
	flushed uint64
}

// NewAsyncQueue 创建异步批量队列，handler 在单后台 goroutine 中按批调用。
func NewAsyncQueue[T any](handler func([]T), cfg AsyncQueueConfig) *AsyncQueue[T] {
	if handler == nil {
		panic("gomsg: AsyncQueue handler must not be nil")
	}
	cfg = cfg.withDefaults()
	q := &AsyncQueue[T]{
		cfg:     cfg,
		handler: handler,
		ch:      make(chan T, cfg.MaxQueueSize),
		stop:    make(chan struct{}),
	}
	q.wg.Add(1)
	go q.loop()
	return q
}

// Push 入队一条数据；队列满时返回 false（该条被丢弃）。
func (q *AsyncQueue[T]) Push(item T) bool {
	q.mu.Lock()
	if q.stopped {
		q.mu.Unlock()
		return false
	}
	q.mu.Unlock()
	select {
	case q.ch <- item:
		return true
	default:
		q.mu.Lock()
		q.dropped++
		q.mu.Unlock()
		return false
	}
}

// Shutdown 停止队列，flush 剩余数据后退出后台 goroutine。
func (q *AsyncQueue[T]) Shutdown() {
	q.mu.Lock()
	if q.stopped {
		q.mu.Unlock()
		return
	}
	q.stopped = true
	q.mu.Unlock()
	close(q.stop)
	q.wg.Wait()
}

// Stats 返回丢弃与已发送批次数（供监控/测试）。
func (q *AsyncQueue[T]) Stats() (dropped, flushed uint64) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.dropped, q.flushed
}

func (q *AsyncQueue[T]) loop() {
	defer q.wg.Done()
	batch := make([]T, 0, q.cfg.BatchSize)
	ticker := time.NewTicker(q.cfg.FlushInterval)
	defer ticker.Stop()

	for {
		select {
		case item := <-q.ch:
			batch = append(batch, item)
			// 非阻塞续取，直到凑满 batchSize 或暂时无数据
			for len(batch) < q.cfg.BatchSize {
				select {
				case item := <-q.ch:
					batch = append(batch, item)
				default:
					goto send
				}
			}
		send:
			if len(batch) >= q.cfg.BatchSize {
				q.send(batch)
				batch = batch[:0]
			}
		case <-ticker.C:
			if len(batch) > 0 {
				q.send(batch)
				batch = batch[:0]
			}
		case <-q.stop:
			// 退出前 drain 剩余数据
			for {
				select {
				case item := <-q.ch:
					batch = append(batch, item)
				default:
					if len(batch) > 0 {
						q.send(batch)
					}
					return
				}
			}
		}
	}
}

func (q *AsyncQueue[T]) send(batch []T) {
	q.handler(batch)
	q.mu.Lock()
	q.flushed++
	q.mu.Unlock()
}
