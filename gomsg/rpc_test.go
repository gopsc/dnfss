package gomsg

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

// startRPCServer 启动带两路由的 RPC 服务端。
func startRPCServer(t *testing.T) (*RpcServer, string) {
	t.Helper()
	s := NewRpcServer("127.0.0.1:0")
	s.AddRoute("echo", func(req map[string]any) (any, error) {
		return req, nil
	})
	s.AddRoute("sum", func(req map[string]any) (any, error) {
		a := req["a"].(json.Number)
		b := req["b"].(json.Number)
		ai, _ := a.Int64()
		bi, _ := b.Int64()
		return ai + bi, nil
	})
	s.AddRoute("fail", func(req map[string]any) (any, error) {
		return nil, errors.New("boom: /opt/micro_servers/db2/xxx.py failed")
	})
	s.AddRoute("panic", func(req map[string]any) (any, error) {
		panic("panic in handler")
	})
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Stop)
	return s, s.Addr().String()
}

func TestRpcClientCallSuccess(t *testing.T) {
	_, addr := startRPCServer(t)
	c := NewRpcClient(addr, 0)
	defer c.Close()

	data, err := c.Call("echo", map[string]any{"hello": "world"})
	if err != nil {
		t.Fatal(err)
	}
	m := data.(map[string]any)
	if m["hello"] != "world" {
		t.Fatalf("data = %v", data)
	}
	if _, has := m["_route"]; has {
		t.Fatal("_route should be stripped before handler")
	}
}

func TestRpcClientNumberPrecision(t *testing.T) {
	_, addr := startRPCServer(t)
	c := NewRpcClient(addr, 0)
	defer c.Close()

	data, err := c.Call("sum", map[string]any{"a": json.Number("9007199254740993"), "b": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	if got := data.(json.Number).String(); got != "9007199254740994" {
		t.Fatalf("sum = %s, want exact int64 precision", got)
	}
}

func TestRpcClientBusinessError(t *testing.T) {
	_, addr := startRPCServer(t)
	c := NewRpcClient(addr, 0)
	defer c.Close()

	_, err := c.Call("fail", nil)
	var rpcErr *RpcError
	if !errors.As(err, &rpcErr) {
		t.Fatalf("expected *RpcError, got %T: %v", err, err)
	}
	// 绝对路径应被脱敏
	if strings.Contains(rpcErr.Error(), "/opt/") {
		t.Fatalf("error not sanitized: %v", rpcErr)
	}
	if !strings.Contains(rpcErr.Error(), "<redacted>") {
		t.Fatalf("expected <redacted> in error: %v", rpcErr)
	}
}

func TestRpcUnknownRoute(t *testing.T) {
	_, addr := startRPCServer(t)
	c := NewRpcClient(addr, 0)
	defer c.Close()

	_, err := c.Call("no_such_route", nil)
	if err == nil || !strings.Contains(err.Error(), "未知路由") {
		t.Fatalf("expected 未知路由 error, got %v", err)
	}
}

func TestRpcServerInvalidJSON(t *testing.T) {
	_, addr := startRPCServer(t)
	c := NewMsgClient(addr, 0)
	defer c.Close()

	resp, err := c.Call([]byte("{not json"))
	if err != nil {
		t.Fatal(err)
	}
	var r map[string]any
	if json.Unmarshal(resp, &r) != nil {
		t.Fatalf("invalid response: %q", resp)
	}
	if r["success"] != false || !strings.Contains(r["error"].(string), "invalid JSON request") {
		t.Fatalf("unexpected response: %v", r)
	}
}

func TestRpcServerPanicHandler(t *testing.T) {
	_, addr := startRPCServer(t)
	c := NewRpcClient(addr, 0)
	defer c.Close()

	// handler 内 panic 不应打崩服务，后续请求仍可用
	if _, err := c.Call("panic", nil); err == nil {
		t.Fatal("expected error from panicking handler")
	}
	if _, err := c.Call("echo", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
}

func TestRpcServerOnErrorHook(t *testing.T) {
	s := NewRpcServer("127.0.0.1:0")
	var hookRoute string
	s.OnError = func(route string, err error) { hookRoute = route }
	s.AddRoute("fail", func(req map[string]any) (any, error) {
		return nil, errors.New("boom")
	})
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	defer s.Stop()

	c := NewRpcClient(s.Addr().String(), 0)
	defer c.Close()
	_, _ = c.Call("fail", nil)
	if hookRoute != "fail" {
		t.Fatalf("OnError hook route = %q", hookRoute)
	}
}

func TestRpcServerNonDictRequest(t *testing.T) {
	s := NewRpcServer("127.0.0.1:0")
	s.AddRoute("echo", func(req map[string]any) (any, error) { return req, nil })
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	defer s.Stop()

	c := NewMsgClient(s.Addr().String(), 0)
	defer c.Close()
	resp, err := c.Call([]byte(`["not","a","dict"]`))
	if err != nil {
		t.Fatal(err)
	}
	var r map[string]any
	if json.Unmarshal(resp, &r) != nil || r["success"] != false {
		t.Fatalf("expected failure response, got %q", resp)
	}
}

func TestSanitizeError(t *testing.T) {
	cases := []struct{ in, want string }{
		{"", ""},
		{"boom", "boom"},
		{"failed: /opt/micro_servers/db2/xxx.py", "failed: <redacted>"},
		{"no abs path here", "no abs path here"},
		{"abc/def 保持原样", "abc/def 保持原样"}, // 前导字符为单词字符时不匹配（对齐 Python lookbehind）
	}
	for _, c := range cases {
		if got := sanitizeError(c.in); got != c.want {
			t.Errorf("sanitizeError(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestRpcClientTimeout(t *testing.T) {
	s := NewRpcServer("127.0.0.1:0")
	s.AddRoute("slow", func(req map[string]any) (any, error) {
		time.Sleep(300 * time.Millisecond)
		return nil, nil
	})
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	defer s.Stop()

	c := NewRpcClient(s.Addr().String(), 50*time.Millisecond)
	defer c.Close()
	if _, err := c.Call("slow", nil); err == nil {
		t.Fatal("expected timeout error")
	}
}
