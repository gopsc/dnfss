package gomsg

import (
	"net"
	"sync"
	"time"
)

// DefaultTimeout 单次请求-响应交互的总超时。
const DefaultTimeout = 5 * time.Second

// MsgClient pymsg 协议客户端：复用长连接 + 请求-响应调用。
// 连接断开时自动重建一次（对应 Python pymsg.MsgClient / C++ libmsg.MsgClient）。
type MsgClient struct {
	addr    string
	timeout time.Duration

	mu   sync.Mutex
	conn net.Conn
}

// NewMsgClient 创建客户端。timeout 为 0 时使用 DefaultTimeout。
func NewMsgClient(addr string, timeout time.Duration) *MsgClient {
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	return &MsgClient{addr: addr, timeout: timeout}
}

// Call 发送一条请求并读取响应（复用长连接，失败时关闭重连一次）。
// 返回响应载荷；超时 / 连接错误返回 error。
func (c *MsgClient) Call(payload []byte) ([]byte, error) {
	resp, err := c.call(payload)
	if err == nil {
		return resp, nil
	}
	// 旧连接可能已断开（服务端重启等），关闭后重连一次
	c.closeConn()
	return c.call(payload)
}

// Send 发送一条请求，不等待响应（fire-and-forget）。失败时重连一次。
func (c *MsgClient) Send(payload []byte) error {
	if err := c.sendOnly(payload); err != nil {
		c.closeConn()
		return c.sendOnly(payload)
	}
	return nil
}

// Close 关闭底层连接。
func (c *MsgClient) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.closeLocked()
}

func (c *MsgClient) call(payload []byte) ([]byte, error) {
	conn, err := c.getConn()
	if err != nil {
		return nil, err
	}
	_ = conn.SetDeadline(time.Now().Add(c.timeout))
	if err := WriteFrame(conn, payload); err != nil {
		return nil, err
	}
	return ReadFrame(conn, 0)
}

func (c *MsgClient) sendOnly(payload []byte) error {
	conn, err := c.getConn()
	if err != nil {
		return err
	}
	_ = conn.SetWriteDeadline(time.Now().Add(c.timeout))
	return WriteFrame(conn, payload)
}

func (c *MsgClient) getConn() (net.Conn, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn != nil {
		return c.conn, nil
	}
	conn, err := net.DialTimeout("tcp", c.addr, c.timeout)
	if err != nil {
		return nil, err
	}
	c.conn = conn
	return conn, nil
}

func (c *MsgClient) closeConn() {
	c.mu.Lock()
	defer c.mu.Unlock()
	_ = c.closeLocked()
}

func (c *MsgClient) closeLocked() error {
	if c.conn != nil {
		err := c.conn.Close()
		c.conn = nil
		return err
	}
	return nil
}
