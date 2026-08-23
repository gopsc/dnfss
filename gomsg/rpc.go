package gomsg

import (
	"bytes"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"sync"
	"time"
)

// JSON RPC 约定层（与 Python pymsg.rpc / C++ libmsg RpcClient/RpcServer 兼容）：
//
//	请求:  {"_route": "<路由名>", ...业务字段}
//	响应:  {"success": true,  "data": ...}
//	       {"success": false, "error": "..."}
//
// 数字一律以 json.Number 保留原始精度（避免 int64 经 float64 丢失）。

// RpcError 远端返回 success=false 的业务错误，或响应不符合 RPC 约定。
type RpcError struct {
	msg string
}

func (e *RpcError) Error() string { return e.msg }

// RpcHandler 处理一条 RPC 请求，返回值放入 data，错误包装为 success=false。
type RpcHandler func(req map[string]any) (any, error)

// RpcClient JSON RPC 客户端（复用 MsgClient 长连接）。
type RpcClient struct {
	client *MsgClient
}

// NewRpcClient 创建客户端。timeout 为 0 时使用 DefaultTimeout。
func NewRpcClient(addr string, timeout time.Duration) *RpcClient {
	return &RpcClient{client: NewMsgClient(addr, timeout)}
}

// Call 调用指定路由，返回响应中的 data 字段（可能为 nil）。
// success=false 或非法响应时返回 *RpcError。
func (c *RpcClient) Call(route string, payload map[string]any) (any, error) {
	req := make(map[string]any, len(payload)+1)
	for k, v := range payload {
		req[k] = v
	}
	req["_route"] = route
	body, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	resp, err := c.client.Call(body)
	if err != nil {
		return nil, err
	}

	var obj map[string]any
	dec := json.NewDecoder(bytes.NewReader(resp))
	dec.UseNumber()
	if err := dec.Decode(&obj); err != nil || obj == nil {
		return nil, &RpcError{msg: fmt.Sprintf("响应不是合法 JSON: %v", err)}
	}
	success, ok := obj["success"].(bool)
	if !ok {
		return nil, &RpcError{msg: fmt.Sprintf("非法 RPC 响应: %s", truncate(resp))}
	}
	if !success {
		errMsg, _ := obj["error"].(string)
		return nil, &RpcError{msg: errMsg}
	}
	return obj["data"], nil
}

// Close 关闭底层连接。
func (c *RpcClient) Close() error { return c.client.Close() }

// RpcServer JSON RPC 服务端：基于 MsgServer 的路由分发与响应包装。
type RpcServer struct {
	*MsgServer
	routes map[string]RpcHandler
	mu     sync.RWMutex
	// OnError 可选钩子：handler 抛错时调用（用于记日志）。
	OnError func(route string, err error)
	// JSONMarshal 响应序列化函数，默认 json.Marshal（对应 Python 的 json_default）。
	JSONMarshal func(v any) ([]byte, error)
}

// NewRpcServer 创建 RPC 服务端（不监听，需调用 Start）。
func NewRpcServer(addr string) *RpcServer {
	s := &RpcServer{routes: make(map[string]RpcHandler)}
	s.MsgServer = NewMsgServer(addr, s.dispatch)
	return s
}

// AddRoute 注册路由处理函数。
func (s *RpcServer) AddRoute(name string, fn RpcHandler) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.routes[name] = fn
}

// dispatch 解析请求、查找路由、包装响应；任何输入错误都不向客户端回显内部细节。
// handler 内 panic 会被恢复并包装为 success=false（对应 Python 的 except Exception）。
func (s *RpcServer) dispatch(payload []byte) (reply []byte) {
	defer func() {
		if r := recover(); r != nil {
			err := fmt.Errorf("handler panic: %v", r)
			if s.OnError != nil {
				s.OnError("", err)
			}
			reply = s.reply(false, nil, sanitizeError(err.Error()))
		}
	}()

	req, err := decodeRequest(payload)
	if err != nil {
		return s.reply(false, nil, "invalid JSON request")
	}

	routeVal, has := req["_route"]
	delete(req, "_route")
	route := ""
	switch r := routeVal.(type) {
	case string:
		route = r
	case nil:
		// 保持 Python 行为：缺省 _route 时按空路由查找（未知路由）
	default:
		if has {
			route = fmt.Sprintf("%v", r)
		}
	}

	s.mu.RLock()
	handler := s.routes[route]
	s.mu.RUnlock()
	if handler == nil {
		return s.reply(false, nil, "未知路由: "+route)
	}

	data, err := handler(req)
	if err != nil {
		if s.OnError != nil {
			s.OnError(route, err)
		}
		return s.reply(false, nil, sanitizeError(err.Error()))
	}
	return s.reply(true, data, "")
}

func (s *RpcServer) reply(success bool, data any, errMsg string) []byte {
	resp := map[string]any{"success": success}
	if data != nil {
		resp["data"] = data
	}
	if errMsg != "" {
		resp["error"] = errMsg
	}
	marshal := s.JSONMarshal
	if marshal == nil {
		marshal = json.Marshal
	}
	out, err := marshal(resp)
	if err != nil {
		out, _ = json.Marshal(map[string]any{
			"success": false,
			"error":   sanitizeError(err.Error()),
		})
	}
	return out
}

// decodeRequest 解析请求 JSON：UseNumber 保留整数精度；
// Go 的 encoding/json 内置 10000 层嵌套上限，数字位数字符串化保存，
// 输入大小又受帧协议 64 MiB 上限约束，天然规避深度/超大数字 DoS。
func decodeRequest(payload []byte) (map[string]any, error) {
	dec := json.NewDecoder(bytes.NewReader(payload))
	dec.UseNumber()
	var req map[string]any
	if err := dec.Decode(&req); err != nil {
		return nil, err
	}
	if req == nil {
		return nil, fmt.Errorf("empty request")
	}
	return req, nil
}

// pathRe 匹配绝对路径（如 /opt/micro_servers/db/xxx.py），用于脱敏异常信息。
var pathRe = regexp.MustCompile(`/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*`)

// sanitizeError 脱敏异常信息中的绝对路径等内部细节，保留业务错误语义
// （对齐 Python pymsg._sanitize_error：前导字符为单词字符时不匹配）。
func sanitizeError(msg string) string {
	if msg == "" {
		return msg
	}
	var b strings.Builder
	last := 0
	for _, idx := range pathRe.FindAllStringIndex(msg, -1) {
		start, end := idx[0], idx[1]
		if start > 0 && isWordChar(msg[start-1]) {
			continue
		}
		b.WriteString(msg[last:start])
		b.WriteString("<redacted>")
		last = end
	}
	b.WriteString(msg[last:])
	return b.String()
}

func isWordChar(c byte) bool {
	return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
}

func truncate(b []byte) string {
	const limit = 200
	if len(b) <= limit {
		return string(b)
	}
	return string(b[:limit]) + "..."
}
