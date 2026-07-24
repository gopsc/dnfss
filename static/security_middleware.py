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
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'DENY'
        # XSS protection (legacy browsers)
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Permissions policy (disable camera, mic, geolocation)
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

        # Content Security Policy
        if csp_policy:
            response.headers['Content-Security-Policy'] = csp_policy
        else:
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )

        # Remove server header info
        response.headers.pop('Server', None)

        return response
