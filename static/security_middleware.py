"""
Security headers middleware for Flask applications.
Usage:
    from security_middleware import apply_security_headers
    apply_security_headers(app)
"""
import secrets

def apply_security_headers(app, csp_policy=None):
    """Apply security response headers to a Flask app."""

    @app.after_request
    def set_security_headers(response):
        # Prevent clickjacking (SAMEORIGIN: 允许本站 iframe 嵌套，如登录页条例弹窗)
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        # XSS protection (legacy browsers)
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Permissions policy (disable camera, mic, geolocation)
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

        # Content Security Policy
        # 注：script-src 含 'unsafe-inline' 因 dnfs 特殊节点"前端代码"功能需要内联脚本。
        # NEW-11（已知限制）：若发生 HTML 注入，CSP 无法阻止 XSS 执行。
        # 后续改进方向：改用 CSP nonce（'nonce-{random}'），需同步改造 dnfs 前端注入逻辑。
        if csp_policy:
            response.headers['Content-Security-Policy'] = csp_policy
        else:
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'self'"
            )

        # Remove server header info
        response.headers.pop('Server', None)

        return response
