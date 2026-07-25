// Package proxy 封装到 gateway 的反向代理，并在请求/响应阶段接入 WAF 事务。
package proxy

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/micro_servers/waf/internal/engine"
	"github.com/micro_servers/waf/internal/logclient"
)

// Proxy 持有 ReverseProxy 与 WAF 引擎引用。
type Proxy struct {
	rp  *httputil.ReverseProxy
	waf *engine.Engine
}

// New 创建代理。upstream 形如 "http://127.0.0.1:8443"。
func New(upstream string, waf *engine.Engine) (*Proxy, error) {
	target, err := url.Parse(upstream)
	if err != nil {
		return nil, err
	}
	rp := httputil.NewSingleHostReverseProxy(target)
	p := &Proxy{rp: rp, waf: waf}

	// 自定义 Director：保留原 Host / X-Forwarded-For
	origDirector := rp.Director
	rp.Director = func(req *http.Request) {
		origDirector(req)
		// RemoteAddr 形如 "1.2.3.4:54321"，剥离端口只保留 IP，
		// 否则 gateway RateLimiter 会把同 IP 不同端口的连接当成不同客户端
		clientIP := req.RemoteAddr
		if idx := strings.LastIndexByte(req.RemoteAddr, ':'); idx != -1 {
			clientIP = req.RemoteAddr[:idx]
		}
		req.Header.Set("X-Forwarded-For", clientIP)
		req.Header.Set("X-Forwarded-Proto", "https")
	}
	// 透传错误，不暴露内部堆栈
	rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		logclient.Error("waf", fmt.Sprintf("upstream_error path=%s err=%v", r.URL.Path, err),
			map[string]any{"ip": r.RemoteAddr})
		http.Error(w, "upstream unavailable", http.StatusBadGateway)
	}
	return p, nil
}

// ServeHTTP 实现 http.Handler。
// 流程：ProcessRequest → 命中则 403 → 否则转发 → ProcessResponseHeaders。
func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	tx := p.waf.NewTransaction()
	defer func() {
		tx.ProcessLogging()
		_ = tx.Close()
	}()

	// 解析 RemoteAddr 中的 IP 与端口
	client := r.RemoteAddr
	cport := 0
	if idx := strings.LastIndexByte(r.RemoteAddr, ':'); idx != -1 {
		client = r.RemoteAddr[:idx]
		cport, _ = strconv.Atoi(r.RemoteAddr[idx+1:])
	}

	// 请求阶段：照搬 coraza/http/middleware.go 的 processRequest 流程
	tx.ProcessConnection(client, cport, "", 0)
	tx.ProcessURI(r.URL.String(), r.Method, r.Proto)
	for k, vs := range r.Header {
		for _, v := range vs {
			tx.AddRequestHeader(k, v)
		}
	}
	if r.Host != "" {
		tx.AddRequestHeader("Host", r.Host)
		tx.SetServerName(r.Host)
	}
	if it := tx.ProcessRequestHeaders(); it != nil {
		p.block(w, r, it.RuleID, start)
		return
	}

	// 请求体：读全量喂给 coraza，再用 buffer 恢复 r.Body 给 proxy 转发
	if r.Body != nil && r.Body != http.NoBody {
		bodyBytes, err := io.ReadAll(r.Body)
		if err == nil {
			_, _, _ = tx.ReadRequestBodyFrom(bytes.NewReader(bodyBytes))
			r.Body = io.NopCloser(bytes.NewReader(bodyBytes))
		}
		if it, _ := tx.ProcessRequestBody(); it != nil {
			p.block(w, r, it.RuleID, start)
			return
		}
	}

	// 包装 ResponseWriter 以捕获状态码与响应头
	rw := &responseRecorder{ResponseWriter: w, status: 200}
	p.rp.ServeHTTP(rw, r)

	// 响应阶段：把 upstream 的 header 喂给 WAF
	for k, vs := range rw.headers {
		for _, v := range vs {
			tx.AddResponseHeader(k, v)
		}
	}
	tx.ProcessResponseHeaders(rw.status, "HTTP/1.1")
}

// block 命中规则时统一返回 403，并将关键输入推送到日志库。
func (p *Proxy) block(w http.ResponseWriter, r *http.Request, ruleID int, start time.Time) {
	logclient.Warning("waf", fmt.Sprintf("block rule_id=%d %s %s", ruleID, r.Method, r.URL.Path),
		map[string]any{
			"rule_id": ruleID,
			"ip":      r.RemoteAddr,
			"method":  r.Method,
			"path":    r.URL.Path,
			"query":   r.URL.RawQuery,
			"ua":      r.UserAgent(),
			"cost_ms": time.Since(start).Milliseconds(),
		})
	w.WriteHeader(http.StatusForbidden)
	_, _ = w.Write([]byte("blocked by WAF\n"))
}

// responseRecorder 记录状态码与响应头，供响应阶段 WAF 检测使用。
type responseRecorder struct {
	http.ResponseWriter
	status  int
	headers http.Header
}

func (r *responseRecorder) WriteHeader(code int) {
	r.status = code
	if r.headers == nil {
		r.headers = http.Header{}
	}
	// 拷贝上游响应头
	for k, vs := range r.Header() {
		r.headers[k] = vs
	}
	r.ResponseWriter.WriteHeader(code)
}

func (r *responseRecorder) Flush() {
	if f, ok := r.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}
