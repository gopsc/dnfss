// Package gomsg 是 pymsg（Python）/ libmsg（C++）协议的 Go 实现。
//
// 线协议（与 Python / C++ 字节级兼容）：
//
//	[4 字节 big-endian uint32 长度][UTF-8 载荷]
//
// 消息大小上限 64 MiB（与 Python pymsg / C++ libmsg 对齐）。
//
// 覆盖功能：帧编解码（本文件）、MsgClient/MsgServer（client.go / server.go）、
// JSON RPC 约定层（rpc.go）、通用异步批量队列（asyncqueue.go）与
// 集中式日志客户端（logclient.go，对应各服务目录下的 log_client.py / liblogc）。
package gomsg

import (
	"encoding/binary"
	"fmt"
	"io"
)

// MaxMsgSize 单条消息载荷大小上限，与 Python pymsg / C++ libmsg 保持一致。
const MaxMsgSize = 64 * 1024 * 1024

var headerSize = 4

// Encode 将载荷封装为 pymsg 帧：4 字节大端长度前缀 + 载荷。
func Encode(payload []byte) ([]byte, error) {
	if len(payload) > MaxMsgSize {
		return nil, fmt.Errorf("payload too large: %d bytes (max %d)", len(payload), MaxMsgSize)
	}
	frame := make([]byte, headerSize+len(payload))
	binary.BigEndian.PutUint32(frame, uint32(len(payload)))
	copy(frame[headerSize:], payload)
	return frame, nil
}

// WriteFrame 将载荷以 pymsg 帧形式写入 w。
func WriteFrame(w io.Writer, payload []byte) error {
	frame, err := Encode(payload)
	if err != nil {
		return err
	}
	_, err = w.Write(frame)
	return err
}

// ReadFrame 从 r 读取一条 pymsg 帧，返回载荷。
// max 为单条消息大小上限（传 0 表示使用 MaxMsgSize）。
// 长度前缀超过上限视为协议错误（防止恶意长度导致内存耗尽）。
func ReadFrame(r io.Reader, max int) ([]byte, error) {
	if max <= 0 || max > MaxMsgSize {
		max = MaxMsgSize
	}
	header := make([]byte, headerSize)
	if _, err := io.ReadFull(r, header); err != nil {
		return nil, err
	}
	length := binary.BigEndian.Uint32(header)
	if length > uint32(max) {
		return nil, fmt.Errorf("message too large: %d bytes (max %d)", length, max)
	}
	payload := make([]byte, length)
	if _, err := io.ReadFull(r, payload); err != nil {
		return nil, err
	}
	return payload, nil
}
