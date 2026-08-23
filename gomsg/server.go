package gomsg

import (
	"net"
	"sync"
	"time"
)

// MsgHandler 处理一条完整消息，返回回复载荷；返回 nil 表示不回复。
type MsgHandler func(payload []byte) []byte

// MsgServer pymsg 协议服务端：accept 循环 + 每连接一个 goroutine 串行处理。
// 对应 Python pymsg.MsgServer / C++ libmsg.MsgServer。
type MsgServer struct {
	addr    string
	handler MsgHandler
	// ReadTimeout 单条消息读取总超时；0 表示不设超时（默认，与 Python 行为一致）。
	ReadTimeout time.Duration

	mu     sync.Mutex
	ln     net.Listener
	conns  map[net.Conn]struct{}
	closed bool
	wg     sync.WaitGroup
}

// NewMsgServer 创建服务端（不监听，需调用 Start）。
func NewMsgServer(addr string, handler MsgHandler) *MsgServer {
	return &MsgServer{
		addr:    addr,
		handler: handler,
		conns:   make(map[net.Conn]struct{}),
	}
}

// Addr 返回监听地址（Start 后可用，便于测试取随机端口）。
func (s *MsgServer) Addr() net.Addr {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ln == nil {
		return nil
	}
	return s.ln.Addr()
}

// Start 开始监听并启动 accept 循环（非阻塞）。
func (s *MsgServer) Start() error {
	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return err
	}
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		_ = ln.Close()
		return nil
	}
	s.ln = ln
	s.mu.Unlock()

	s.wg.Add(1)
	go s.acceptLoop(ln)
	return nil
}

// Stop 关闭监听与全部连接，等待处理中的请求结束。
func (s *MsgServer) Stop() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	if s.ln != nil {
		_ = s.ln.Close()
	}
	for conn := range s.conns {
		_ = conn.Close()
	}
	s.mu.Unlock()
	s.wg.Wait()
}

func (s *MsgServer) acceptLoop(ln net.Listener) {
	defer s.wg.Done()
	for {
		conn, err := ln.Accept()
		if err != nil {
			return // 监听关闭
		}
		s.mu.Lock()
		if s.closed {
			s.mu.Unlock()
			_ = conn.Close()
			return
		}
		s.conns[conn] = struct{}{}
		s.mu.Unlock()

		s.wg.Add(1)
		go s.handleConn(conn)
	}
}

func (s *MsgServer) handleConn(conn net.Conn) {
	defer s.wg.Done()
	defer func() {
		s.mu.Lock()
		delete(s.conns, conn)
		s.mu.Unlock()
		_ = conn.Close()
	}()

	for {
		if s.ReadTimeout > 0 {
			_ = conn.SetReadDeadline(time.Now().Add(s.ReadTimeout))
		}
		payload, err := ReadFrame(conn, 0)
		if err != nil {
			return
		}
		reply := s.handler(payload)
		if reply == nil {
			continue
		}
		if err := WriteFrame(conn, reply); err != nil {
			return
		}
	}
}
