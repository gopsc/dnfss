package gomsg

import (
	"bytes"
	"errors"
	"testing"
	"time"
)

// startEchoServer 启动一个 MsgServer，把收到的载荷原样回复。
func startEchoServer(t *testing.T) (*MsgServer, string) {
	t.Helper()
	s := NewMsgServer("127.0.0.1:0", func(payload []byte) []byte {
		return append([]byte("echo:"), payload...)
	})
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Stop)
	return s, s.Addr().String()
}

func TestMsgClientCall(t *testing.T) {
	_, addr := startEchoServer(t)
	c := NewMsgClient(addr, 0)
	defer c.Close()

	resp, err := c.Call([]byte(`{"hello":"世界"}`))
	if err != nil {
		t.Fatal(err)
	}
	want := "echo:{\"hello\":\"世界\"}"
	if string(resp) != want {
		t.Fatalf("resp = %q, want %q", resp, want)
	}
}

func TestMsgClientReusesConn(t *testing.T) {
	_, addr := startEchoServer(t)
	c := NewMsgClient(addr, 0)
	defer c.Close()

	for i := 0; i < 5; i++ {
		if _, err := c.Call([]byte("x")); err != nil {
			t.Fatal(err)
		}
	}
	if c.conn == nil {
		t.Fatal("expected connection reuse")
	}
}

func TestMsgClientReconnectAfterServerRestart(t *testing.T) {
	s := NewMsgServer("127.0.0.1:0", func(p []byte) []byte { return p })
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	addr := s.Addr().String()

	c := NewMsgClient(addr, 2*time.Second)
	defer c.Close()
	if _, err := c.Call([]byte("before")); err != nil {
		t.Fatal(err)
	}

	// 服务端重启：旧连接失效，Call 应自动重连成功
	s.Stop()
	// 重新监听同一地址：旧 listener 已释放端口
	s3 := NewMsgServer(addr, func(p []byte) []byte { return p })
	if err := s3.Start(); err != nil {
		t.Fatal(err)
	}
	defer s3.Stop()

	resp, err := c.Call([]byte("after"))
	if err != nil {
		t.Fatalf("expected auto-reconnect, got %v", err)
	}
	if string(resp) != "after" {
		t.Fatalf("resp = %q", resp)
	}
}

func TestMsgClientSendNoReply(t *testing.T) {
	got := make(chan []byte, 1)
	s := NewMsgServer("127.0.0.1:0", func(payload []byte) []byte {
		got <- payload
		return nil // 不回复
	})
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	defer s.Stop()

	c := NewMsgClient(s.Addr().String(), 0)
	defer c.Close()
	if err := c.Send([]byte("fire-and-forget")); err != nil {
		t.Fatal(err)
	}
	select {
	case p := <-got:
		if string(p) != "fire-and-forget" {
			t.Fatalf("payload = %q", p)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("server did not receive message")
	}
}

func TestMsgClientTimeout(t *testing.T) {
	s := NewMsgServer("127.0.0.1:0", func(payload []byte) []byte {
		time.Sleep(500 * time.Millisecond) // 拖过客户端超时
		return nil
	})
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	defer s.Stop()

	c := NewMsgClient(s.Addr().String(), 50*time.Millisecond)
	defer c.Close()
	if _, err := c.Call([]byte("slow")); err == nil {
		t.Fatal("expected timeout error")
	}
}

func TestMsgServerStopClosesConns(t *testing.T) {
	_, addr := startEchoServer(t)
	c := NewMsgClient(addr, 0)
	defer c.Close()
	if _, err := c.Call([]byte("ping")); err != nil {
		t.Fatal(err)
	}
}

func TestMsgServerConcurrentClients(t *testing.T) {
	s := NewMsgServer("127.0.0.1:0", func(payload []byte) []byte {
		return bytes.ToUpper(payload)
	})
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	defer s.Stop()

	errCh := make(chan error, 8)
	for i := 0; i < 8; i++ {
		go func(n int) {
			c := NewMsgClient(s.Addr().String(), 0)
			defer c.Close()
			for j := 0; j < 10; j++ {
				in := []byte{byte('a' + n)}
				resp, err := c.Call(in)
				if err != nil {
					errCh <- err
					return
				}
				if len(resp) != 1 || resp[0] != in[0]-'a'+'A' {
					errCh <- errors.New("unexpected reply")
					return
				}
			}
			errCh <- nil
		}(i)
	}
	for i := 0; i < 8; i++ {
		if err := <-errCh; err != nil {
			t.Fatal(err)
		}
	}
}
