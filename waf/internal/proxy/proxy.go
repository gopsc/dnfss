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

// maxRequestBodySize 请求体最大大小（10MB），与 rules/waf.conf 中 SecRequestBodyLimit 保持一致。
// 超过此限制的请求直接返回 413 Request Entity Too Large，防止 OOM。
const maxRequestBodySize = 10 * 1024 * 1024

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
		setSecurityHeaders(w.Header())
		http.Error(w, "upstream unavailable", http.StatusBadGateway)
	}
	// 统一注入安全响应头：在边缘单点覆盖所有出站响应。
	// Header.Set 覆盖下游同名头，不会产生重复字段
	rp.ModifyResponse = func(res *http.Response) error {
		setSecurityHeaders(res.Header)
		return nil
	}
	return p, nil
}

// setSecurityHeaders 设置安全响应头基线（与 static/security_middleware.py 保持一致）。
// CSP 暂保留 unsafe-inline：forgot.html 与 games/*.html 含内联脚本。
// HSTS 半年期、不含子域，站点全量 HTTPS 后可按需收紧。
func setSecurityHeaders(h http.Header) {
	// SAMEORIGIN：允许本站 iframe 嵌套（登录页条例弹窗），仍防跨站点击劫持
	h.Set("X-Frame-Options", "SAMEORIGIN")
	h.Set("X-Content-Type-Options", "nosniff")
	h.Set("X-XSS-Protection", "1; mode=block")
	h.Set("Referrer-Policy", "strict-origin-when-cross-origin")
	h.Set("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
	h.Set("Strict-Transport-Security", "max-age=15552000")
	h.Set("Content-Security-Policy",
		"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "+
			"img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'self'")
}

// ServeHTTP 实现 http.Handler。
// 流程：ProcessRequest → 命中则 403 → 否则转发 → ProcessResponseHeaders。
func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	tx := p.waf.NewTransaction()
	defer func() {
		tx.ProcessLogging()
		_ = tx.Close()
		// 确保 r.Body 被关闭并耗尽，保证 TCP 连接可复用
		if r.Body != nil {
			_, _ = io.Copy(io.Discard, io.LimitReader(r.Body, 1024))
			_ = r.Body.Close()
		}
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

	// 请求体：读取时限制大小（maxRequestBodySize + 1 字节用于判断超限），防止 OOM
	if r.Body != nil && r.Body != http.NoBody {
		// 限制读取大小：多读 1 字节，若能读到说明超限
		limitReader := io.LimitReader(r.Body, maxRequestBodySize+1)
		bodyBytes, err := io.ReadAll(limitReader)
		if err != nil {
			// 客户端中途断开或读取错误，直接返回无需继续处理
			logclient.Info("waf", fmt.Sprintf("body_read_error path=%s err=%v", r.URL.Path, err),
				map[string]any{"ip": r.RemoteAddr})
			return
		}
		if len(bodyBytes) > maxRequestBodySize {
			// 请求体超限，返回 413
			logclient.Warning("waf", fmt.Sprintf("body_too_large %s %s size=%d", r.Method, r.URL.Path, len(bodyBytes)),
				map[string]any{
					"ip":      r.RemoteAddr,
					"method":  r.Method,
					"path":    r.URL.Path,
					"size":    len(bodyBytes),
					"limit":   maxRequestBodySize,
					"cost_ms": time.Since(start).Milliseconds(),
				})
			setSecurityHeaders(w.Header())
			w.WriteHeader(http.StatusRequestEntityTooLarge)
			_, _ = w.Write([]byte("request body too large\n"))
			return
		}
		// 喂给 coraza 进行请求体检测
		if _, _, err := tx.ReadRequestBodyFrom(bytes.NewReader(bodyBytes)); err != nil {
			logclient.Warning("waf", fmt.Sprintf("body_parse_error path=%s err=%v", r.URL.Path, err),
				map[string]any{"ip": r.RemoteAddr})
		}
		// 恢复 r.Body 供 ReverseProxy 转发
		r.Body = io.NopCloser(bytes.NewReader(bodyBytes))

		it, err := tx.ProcessRequestBody()
		if err != nil {
			logclient.Warning("waf", fmt.Sprintf("body_process_error path=%s err=%v", r.URL.Path, err),
				map[string]any{"ip": r.RemoteAddr})
		}
		if it != nil {
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
	setSecurityHeaders(w.Header())
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
