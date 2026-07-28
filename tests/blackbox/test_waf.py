"""
waf 黑盒用户模拟测试 — 正常请求通行 vs 攻击载荷拦截。

用户旅程:
  1. 正常请求 → 通过
  2. SQL 注入载荷 → 403 blocked by WAF
  3. XSS 跨站脚本载荷 → 403
  4. 命令注入载荷 → 403
  5. 路径遍历载荷 → 403
  6. 扫描器 User-Agent → 403

注意: WAF 监听 443 端口 (TLS)，测试时需注意证书验证。
"""

import ssl
import urllib.request
import urllib.error

from helpers import (
    http_get,
    assert_eq, assert_status, assert_true, assert_contains,
    skip, Counter, check_service,
)
from config import get_url

WAF_URL = "https://127.0.0.1:443"  # WAF 监听 443 需要 HTTPS


def _waf_get(base_url: str, path: str, headers: dict = None):
    """通过 WAF 发送 HTTPS GET 请求（跳过证书验证）。"""
    ctx = ssl._create_unverified_context()
    url = f"{base_url}{path}"
    hdrs = headers or {}
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10.0, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body, dict(e.headers) if e.headers else {}
    except Exception as e:
        return 0, str(e), {}


def run(url: str = None):
    """waf 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    wu = url or WAF_URL
    c = Counter()

    print(f"\n--- waf 安全防护流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性
    # ------------------------------------------------------------------
    reachable, detail = check_service(wu)
    if not reachable:
        skip("全部测试", f"WAF 不可达: {detail}", c)
        return c.passed, c.failed, c.skipped

    # ------------------------------------------------------------------
    # 场景 1: 正常请求应通过
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/")
    assert_true(
        status != 403,
        "正常请求-通过", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 场景 2: SQL 注入 — UNION SELECT
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/?id=1 UNION SELECT * FROM users")
    assert_eq(status, 403, "SQLi-UNION SELECT-拦截 (403)", c)
    assert_contains(body, "blocked by WAF", "SQLi-响应体 blocked", c)

    # ------------------------------------------------------------------
    # 场景 3: SQL 注入 — SQL 注释符
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/?user=admin'--")
    assert_eq(status, 403, "SQLi-注释符-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 4: XSS — script 标签
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/?q=<script>alert(1)</script>")
    assert_eq(status, 403, "XSS-script标签-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 5: XSS — onerror 事件
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/?q=<img onerror=alert(1)>")
    assert_eq(status, 403, "XSS-onerror-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 6: 命令注入 — cat /etc/passwd
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/?cmd=; cat /etc/passwd")
    assert_eq(status, 403, "命令注入-cat-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 7: 命令注入 — id 命令
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/?x=| id")
    assert_eq(status, 403, "命令注入-id-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 8: 路径遍历
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/?file=../../../etc/passwd")
    assert_eq(status, 403, "路径遍历-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 9: 路径遍历 — URL 编码
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/?file=..%2f..%2f..%2fetc%2fpasswd")
    assert_eq(status, 403, "路径遍历-URL编码-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 10: 扫描器 User-Agent
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/", headers={"User-Agent": "sqlmap/1.0"})
    assert_eq(status, 403, "扫描器UA-sqlmap-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 11: 扫描器 User-Agent — Nikto
    # ------------------------------------------------------------------
    status, body, headers = _waf_get(wu, "/", headers={"User-Agent": "Nikto"})
    assert_eq(status, 403, "扫描器UA-nikto-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  waf 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
