package gomsg

import (
	"encoding/json"
	"sync"
	"testing"
	"time"
)

// fakeDB2 模拟 db2 的 execute 路由：校验 Logs INSERT 请求格式并返回成功。
type fakeDB2 struct {
	server   *MsgServer
	mu       sync.Mutex
	inserts  []map[string]any
	failures int
}

func newFakeDB2(t *testing.T) *fakeDB2 {
	f := &fakeDB2{}
	f.server = NewMsgServer("127.0.0.1:0", func(payload []byte) []byte {
		var req map[string]any
		if err := json.Unmarshal(payload, &req); err != nil {
			return []byte(`{"success":false,"error":"invalid JSON request"}`)
		}
		// db2 真实语义：非法 JSON 返回 success=false；其余按通用响应
		if req["_route"] != "execute" {
			return []byte(`{"success":false,"error":"未知路由"}`)
		}
		f.mu.Lock()
		f.inserts = append(f.inserts, req)
		f.mu.Unlock()
		return []byte(`{"success":true,"data":{}}`)
	})
	if err := f.server.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(f.server.Stop)
	return f
}

func (f *fakeDB2) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.inserts)
}

func TestLogClientPushesToDB2(t *testing.T) {
	db := newFakeDB2(t)
	lc := NewLogClient(db.server.Addr().String())
	defer lc.Flush()

	lc.SetLevel("debug")
	lc.Debug("test-svc", "debug msg", nil)
	lc.Info("test-svc", "hello", map[string]any{"k": "v"})
	lc.Error("test-svc", "boom", nil)

	deadline := time.Now().Add(3 * time.Second)
	for db.count() < 3 {
		if time.Now().After(deadline) {
			t.Fatal("logs not pushed in time")
		}
		time.Sleep(20 * time.Millisecond)
	}

	db.mu.Lock()
	defer db.mu.Unlock()
	first := db.inserts[0]
	if first["_route"] != "execute" {
		t.Fatalf("_route = %v", first["_route"])
	}
	vals := first["values"].(map[string]any)
	if vals["service"] != "test-svc" || vals["level"] != "DEBUG" || vals["message"] != "debug msg" {
		t.Fatalf("values = %v", vals)
	}
	// ts 必须是 ISO 8601 字符串
	if _, ok := vals["ts"].(string); !ok {
		t.Fatalf("ts missing: %v", vals)
	}
	// 第二条（Info）的 extra 应序列化为 JSON 字符串
	vals2 := db.inserts[1]["values"].(map[string]any)
	if vals2["extra"] != `{"k":"v"}` {
		t.Fatalf("extra = %v", vals2["extra"])
	}
}

func TestLogClientLevelFilter(t *testing.T) {
	db := newFakeDB2(t)
	lc := NewLogClient(db.server.Addr().String())
	defer lc.Flush()

	lc.Info("svc", "visible", nil)
	lc.Debug("svc", "hidden", nil) // 默认 minLevel=info，debug 应被过滤
	lc.Push(Entry{Service: "svc", Level: "NOPE", Message: "hidden"})

	deadline := time.Now().Add(3 * time.Second)
	for db.count() < 1 {
		if time.Now().After(deadline) {
			t.Fatal("expected exactly 1 log")
		}
		time.Sleep(20 * time.Millisecond)
	}
	time.Sleep(100 * time.Millisecond) // 确认没有多余的推送
	if db.count() != 1 {
		t.Fatalf("expected 1 insert, got %d", db.count())
	}
}

func TestLogClientFailSafeWhenDBDown(t *testing.T) {
	lc := NewLogClient("127.0.0.1:1") // 不可达地址
	defer lc.Flush()
	lc.Info("svc", "will be dropped", nil)
	time.Sleep(200 * time.Millisecond)
	// 不 panic、不阻塞即视为通过（fail-safe 语义）
}
