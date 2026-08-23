package gomsg

import (
	"sync"
	"testing"
	"time"
)

func collectBatches(batches *[][]int, mu *sync.Mutex) func([]int) {
	return func(b []int) {
		mu.Lock()
		*batches = append(*batches, append([]int(nil), b...))
		mu.Unlock()
	}
}

func TestAsyncQueueFlushOnBatchSize(t *testing.T) {
	var batches [][]int
	var mu sync.Mutex
	q := NewAsyncQueue[int](collectBatches(&batches, &mu), AsyncQueueConfig{
		BatchSize:     5,
		FlushInterval: time.Hour, // 只测批量阈值，不测定时
	})
	for i := 1; i <= 5; i++ {
		if !q.Push(i) {
			t.Fatal("push should succeed")
		}
	}
	deadline := time.Now().Add(2 * time.Second)
	for {
		mu.Lock()
		n := len(batches)
		mu.Unlock()
		if n >= 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("batch not flushed in time")
		}
		time.Sleep(10 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(batches[0]) != 5 || batches[0][4] != 5 {
		t.Fatalf("batch = %v", batches[0])
	}
	q.Shutdown()
}

func TestAsyncQueueFlushOnInterval(t *testing.T) {
	var batches [][]int
	var mu sync.Mutex
	q := NewAsyncQueue[int](collectBatches(&batches, &mu), AsyncQueueConfig{
		BatchSize:     100,
		FlushInterval: 100 * time.Millisecond,
	})
	if !q.Push(42) {
		t.Fatal("push should succeed")
	}
	time.Sleep(300 * time.Millisecond)
	mu.Lock()
	n := len(batches)
	mu.Unlock()
	if n < 1 {
		t.Fatal("expected interval flush")
	}
	q.Shutdown()
}

func TestAsyncQueueShutdownDrains(t *testing.T) {
	var batches [][]int
	var mu sync.Mutex
	q := NewAsyncQueue[int](collectBatches(&batches, &mu), AsyncQueueConfig{
		BatchSize:     100,
		FlushInterval: time.Hour,
	})
	for i := 1; i <= 3; i++ {
		q.Push(i)
	}
	q.Shutdown()
	mu.Lock()
	defer mu.Unlock()
	if len(batches) != 1 || len(batches[0]) != 3 {
		t.Fatalf("expected drained batch [1 2 3], got %v", batches)
	}
}

func TestAsyncQueueDropOnFull(t *testing.T) {
	var batches [][]int
	var mu sync.Mutex
	q := NewAsyncQueue[int](collectBatches(&batches, &mu), AsyncQueueConfig{
		BatchSize:     1000,
		FlushInterval: time.Hour,
		MaxQueueSize:  4,
	})
	// 后台协程可能已取走部分，填满队列直到 Push 返回 false
	dropped := false
	for i := 0; i < 100; i++ {
		if !q.Push(i) {
			dropped = true
			break
		}
	}
	if !dropped {
		t.Fatal("expected drop when queue full")
	}
	d, _ := q.Stats()
	if d == 0 {
		t.Fatal("dropped counter should be > 0")
	}
	q.Shutdown()
}

func TestAsyncQueueRejectAfterShutdown(t *testing.T) {
	q := NewAsyncQueue[int](func([]int) {}, AsyncQueueConfig{})
	q.Shutdown()
	if q.Push(1) {
		t.Fatal("push after shutdown should fail")
	}
}
