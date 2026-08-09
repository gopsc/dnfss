"""
gateway 黑盒用户模拟测试（v2 — 对齐 gateway v0.8.0 契约）

覆盖范围（P0，无需测试钩子）:
  A. 输入校验与错误语义（v0.8.0 新契约：/login 强制 CAPTCHA、/register 强制 email_code）
  B. /whoami 与 /admin/verify（fail-closed 契约）
  C. /captcha/image 代理路由（captcha2 在线时）
  D. 反向代理与路径安全（路径遍历矩阵，含 URL 编码变体）
  E. 速率限制（随机 XFF 隔离限流桶，避免用例互斥）
  F. CORS 预检现状固化 + 错误响应脱敏回归（P0-2/P0-3）

已知待修复缺陷（断言目标行为，gateway 修复前会 FAIL）:
  - A3: 缺失 JSON 字段 → 当前 500，应为 400（场景 A5/A8）
  - A2: 白名单外 Target-Url → 当前 500，应为 403（需有效 Token 才能触达，待测试钩子）

认证 happy path（注册/登录/Token 生命周期/Cookie/jti 吊销/refresh）依赖
captcha2/smtp 测试模式（固定验证码答案钩子），落地前黑盒无法覆盖（P1 清单）。

依赖说明:
  - gateway 必须运行，否则全部 SKIP
  - captcha2 (pymsg:8009) 不在线时 C/E5 段 SKIP（其余用例不受影响）
  - static (:9200) 不在线时 D3 段 SKIP
  - smtp 不在线不影响本文件（相关用例均为 fail-closed 400 路径）
"""

import http.client
import random
import re
import string
import urllib.parse

from helpers import (
    http_get, http_post, http_request, try_parse_json,
    assert_eq, assert_true, assert_no_internal_leak,
    skip, Counter, check_service, check_tcp_service,
    random_xff, db_execute,
)
from config import SERVICES, get_url

GATEWAY_URL = get_url("gateway")
STATIC_URL = get_url("static")

# 本测试文件使用的账号前缀（P0 用例不会真正创建用户，清理为防御性执行）
TEST_USER_PREFIX = "testgw_"


def _random_email():
    """生成随机测试邮箱（带本文件专属前缀，便于清理）。"""
    suffix = ''.join(random.choices(string.ascii_lowercase, k=8))
    return f"{TEST_USER_PREFIX}{suffix}@example.com"


def _strong_password():
    """生成必过强度校验的密码（含大写/小写/数字/特殊四类）。"""
    body = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    return f"Gw{body}!1"


def _raw_get_status(host: str, port: int, raw_path: str, headers: dict = None):
    """发送原始 GET（不做任何路径规范化），返回 (status, body)。

    urllib/curl 默认会折叠 ".." 路径段，路径遍历测试必须用原始请求行。
    """
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.putrequest("GET", raw_path, skip_accept_encoding=True)
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body
    finally:
        conn.close()


def run(url: str = None):
    """gateway 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    gw = url or GATEWAY_URL
    c = Counter()
    err_bodies = []  # 收集错误响应体，末尾统一做脱敏回归（F2）

    print(f"\n--- gateway 输入校验/Token契约/代理/限流 测试 ---\n")

    parsed = urllib.parse.urlparse(gw)
    gw_host, gw_port = parsed.hostname, parsed.port or 80

    # ------------------------------------------------------------------
    # 0. 依赖可用性探测
    # ------------------------------------------------------------------
    if not check_service(gw)[0]:
        print("  gateway 服务不可达，跳过所有测试")
        c.add_skip()
        return c.passed, c.failed, c.skipped

    cap_svc = SERVICES["captcha"]
    captcha_up = check_tcp_service(cap_svc["host"], cap_svc["port"])[0]
    if not captcha_up:
        print("  [WARN] captcha 服务不可达，/captcha/image 相关用例将 SKIP")

    static_up = check_service(STATIC_URL)[0]
    if not static_up:
        print("  [WARN] static 服务不可达，默认代理转发用例将 SKIP")

    # ==================================================================
    # A. 输入校验与错误语义（v0.8.0 新契约）
    # ==================================================================
    print("  [A] 输入校验与错误语义")

    # A1: 注册-密码太短 → 401（密码强度校验先于邮箱验证码校验）
    status, body, _ = http_post(
        f"{gw}/register",
        {"username": _random_email(), "password": "123"},
        headers={"X-Forwarded-For": random_xff()})
    err_bodies.append(body)
    assert_eq(status, 401, "注册-密码太短 (401)", c)

    # A2: 注册-邮箱格式错误 → 400（须先过密码强度校验）
    status, body, _ = http_post(
        f"{gw}/register",
        {"username": "not-an-email", "password": _strong_password()},
        headers={"X-Forwarded-For": random_xff()})
    err_bodies.append(body)
    assert_eq(status, 400, "注册-邮箱格式错误 (400)", c)

    # A3: 注册-缺 email_code → 400（v0.8.0 强制邮箱验证码）
    status, body, _ = http_post(
        f"{gw}/register",
        {"username": _random_email(), "password": _strong_password()},
        headers={"X-Forwarded-For": random_xff()})
    err_bodies.append(body)
    assert_eq(status, 400, "注册-缺email_code (400)", c)

    # A4: 注册-错误 email_code → 400（smtp 不在线时 fail-closed 同为 400）
    status, body, _ = http_post(
        f"{gw}/register",
        {"username": _random_email(), "password": _strong_password(),
         "email_code": "000000"},
        headers={"X-Forwarded-For": random_xff()})
    err_bodies.append(body)
    assert_eq(status, 400, "注册-错误email_code (400)", c)

    # A5: 登录-空 JSON → 400（审计 A3：当前实现返回 500，修复前 FAIL）
    status, body, _ = http_post(
        f"{gw}/login", {},
        headers={"X-Forwarded-For": random_xff()})
    err_bodies.append(body)
    assert_eq(status, 400, "登录-缺字段 (400) [A3: 当前500,待修复]", c)

    # A6: 登录-缺 captcha 字段 → 400（v0.8.0 强制 CAPTCHA）
    status, body, _ = http_post(
        f"{gw}/login",
        {"username": _random_email(), "password": _strong_password()},
        headers={"X-Forwarded-For": random_xff()})
    err_bodies.append(body)
    assert_eq(status, 400, "登录-缺captcha (400)", c)

    # A7: 登录-错误 captcha → 400（captcha 不可用 fail-closed 同为 400）
    status, body, _ = http_post(
        f"{gw}/login",
        {"username": _random_email(), "password": _strong_password(),
         "captcha_token": "0" * 64, "captcha_answer": "AAAAA"},
        headers={"X-Forwarded-For": random_xff()})
    err_bodies.append(body)
    assert_eq(status, 400, "登录-错误captcha (400)", c)

    # A8: 注册-空 JSON → 400（审计 A3：当前实现返回 500，修复前 FAIL）
    status, body, _ = http_post(
        f"{gw}/register", {},
        headers={"X-Forwarded-For": random_xff()})
    err_bodies.append(body)
    assert_eq(status, 400, "注册-缺字段 (400) [A3: 当前500,待修复]", c)

    # ==================================================================
    # B. /whoami 与 /admin/verify（fail-closed 契约）
    # ==================================================================
    print("  [B] whoami / admin.verify")

    # B1: whoami 无 Token → 200 + {"login": false}
    status, body, _ = http_post(f"{gw}/whoami")
    data = try_parse_json(body)
    assert_true(
        status == 200 and data is not None and data.get("login") is False,
        "whoami-无Token (200, login:false)", c, f"status={status} body={body[:80]}")

    # B2: whoami 伪造 Token → 200 + {"login": false}（不泄露验签细节）
    status, body, _ = http_post(
        f"{gw}/whoami", headers={"Authorization": f"Bearer {'A'*40}_{'B'*40}"})
    data = try_parse_json(body)
    assert_true(
        status == 200 and data is not None and data.get("login") is False,
        "whoami-伪造Token (login:false)", c, f"status={status} body={body[:80]}")

    # B3: admin/verify 无 Token → 401 fail-closed
    status, body, _ = http_post(f"{gw}/admin/verify")
    data = try_parse_json(body)
    assert_true(
        status == 401 and data is not None and data.get("success") is False,
        "admin/verify-无Token (401)", c, f"status={status}")

    # B4: admin/verify 伪造 Token → 401
    status, body, _ = http_post(
        f"{gw}/admin/verify",
        headers={"Authorization": f"Bearer {'A'*40}_{'B'*40}"})
    data = try_parse_json(body)
    assert_true(
        status == 401 and data is not None and data.get("success") is False,
        "admin/verify-伪造Token (401)", c, f"status={status}")

    # ==================================================================
    # C. /captcha/image 代理路由（captcha2 在线时）
    # ==================================================================
    print("  [C] /captcha/image 代理")
    if captcha_up:
        status, body, headers = http_get(
            f"{gw}/captcha/image",
            headers={"X-Forwarded-For": random_xff()})
        assert_eq(status, 200, "captcha/image-代理 (200)", c)
        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        assert_true("image/png" in content_type,
                    "captcha/image-Content-Type(png)", c, f"ct={content_type}")
        cap_token = headers.get("X-Captcha-Token", headers.get("x-captcha-token", ""))
        assert_true(
            bool(re.fullmatch(r"[0-9a-f]{64}", cap_token)),
            "captcha/image-Token(64位hex)", c, f"token={cap_token[:16]}...")
        cache_ctl = headers.get("Cache-Control", headers.get("cache-control", ""))
        assert_true("no-store" in cache_ctl,
                    "captcha/image-禁缓存(no-store)", c, f"cc={cache_ctl}")
    else:
        skip("captcha/image 代理", "captcha 服务不可达", c)

    # ==================================================================
    # D. 反向代理与路径安全
    # ==================================================================
    print("  [D] 反向代理与路径安全")

    # D1: 显式 Target-Url + 无 Token → 401（Token 校验先于转发，不依赖 static）
    status, body, _ = http_get(f"{gw}/some-path",
                               headers={"Target-Url": STATIC_URL})
    err_bodies.append(body)
    assert_eq(status, 401, "代理-无Token拒绝 (401)", c)

    # D2: 路径遍历矩阵 → 400（路径校验先于 Token 校验；原始请求行防客户端折叠）
    traversal_paths = [
        "/../../../etc/passwd",           # 明文 ..
        "/%2e%2e/%2e%2e/etc/passwd",      # URL 编码 .
        "/%2f..%2f..%2fetc",              # 编码 /
        "/%5c..%5cwindows",               # 编码反斜杠
    ]
    for p in traversal_paths:
        status, body = _raw_get_status(gw_host, gw_port, p,
                                       headers={"Target-Url": STATIC_URL})
        err_bodies.append(body)
        assert_eq(status, 400, f"代理-路径遍历拒绝 {p[:24]} (400)", c)

    # D3: 默认代理（无 Target-Url，公开目标不需 Token）
    if static_up:
        status, body, _ = http_get(f"{gw}/")
        assert_true(status in (200, 301, 302),
                    "代理-默认路由(static)", c, f"status={status}")
    else:
        skip("代理-默认路由", "static 服务不可达", c)

    # ==================================================================
    # E. 速率限制（随机 XFF 隔离限流桶）
    # ==================================================================
    print("  [E] 速率限制")

    # E1: 登录限流 5 次/5 分钟/IP —— 同 XFF 前 5 次 400，第 6 次 429
    xff = random_xff()
    results = []
    for _ in range(6):
        status, body, _ = http_post(
            f"{gw}/login",
            {"username": _random_email(), "password": _strong_password()},
            headers={"X-Forwarded-For": xff})
        results.append(status)
    err_bodies.append(body)
    assert_true(all(s == 400 for s in results[:5]),
                "限流-登录前5次放行(400)", c, f"results={results}")
    assert_eq(results[5], 429, "限流-登录第6次 (429)", c)

    # E2: 限流桶隔离 —— 换 XFF 后不再 429（固化审计 A4：直连模式 XFF 客户端可控）
    status, _, _ = http_post(
        f"{gw}/login",
        {"username": _random_email(), "password": _strong_password()},
        headers={"X-Forwarded-For": random_xff()})
    assert_eq(status, 400, "限流-换XFF重置桶 (400≠429)", c)

    # E3: 注册限流 3 次/10 分钟/IP —— 前 3 次 400，第 4 次 429
    xff = random_xff()
    results = []
    for _ in range(4):
        status, body, _ = http_post(
            f"{gw}/register",
            {"username": _random_email(), "password": _strong_password()},
            headers={"X-Forwarded-For": xff})
        results.append(status)
    err_bodies.append(body)
    assert_true(all(s == 400 for s in results[:3]),
                "限流-注册前3次放行(400)", c, f"results={results}")
    assert_eq(results[3], 429, "限流-注册第4次 (429)", c)

    # E4: 发码限流 5 次/10 分钟/IP —— 缺 captcha 每次 400，第 6 次 429
    xff = random_xff()
    results = []
    for _ in range(6):
        status, body, _ = http_post(
            f"{gw}/register/send_code",
            {"email": _random_email()},
            headers={"X-Forwarded-For": xff})
        results.append(status)
    err_bodies.append(body)
    assert_true(all(s == 400 for s in results[:5]),
                "限流-发码前5次放行(400)", c, f"results={results}")
    assert_eq(results[5], 429, "限流-发码第6次 (429)", c)

    # E5: /captcha/image 限流 10 次/分钟/IP —— 前 10 次 200，第 11 次 429
    # （captcha2 全局限流 600/分钟，不干扰本用例）
    if captcha_up:
        xff = random_xff()
        results = []
        for _ in range(11):
            status, _, _ = http_get(f"{gw}/captcha/image",
                                    headers={"X-Forwarded-For": xff})
            results.append(status)
        assert_true(all(s == 200 for s in results[:10]),
                    "限流-验证码前10次放行(200)", c, f"results={results}")
        assert_eq(results[10], 429, "限流-验证码第11次 (429)", c)
    else:
        skip("限流-验证码", "captcha 服务不可达", c)

    # ==================================================================
    # F. CORS 预检现状 + 错误响应脱敏回归
    # ==================================================================
    print("  [F] CORS 与错误脱敏")

    # F1: OPTIONS 预检 —— 固化当前行为：反射任意 Origin（P2-3 未修复）。
    # TODO(P2-3): 白名单生效后应翻转断言为「白名单外 Origin 无 ACAO 头」。
    status, _, headers = http_request(
        "OPTIONS", f"{gw}/login",
        headers={"Origin": "https://test-orig.example",
                 "Access-Control-Request-Method": "POST"})
    acao = headers.get("Access-Control-Allow-Origin",
                       headers.get("access-control-allow-origin", ""))
    assert_true(
        acao == "https://test-orig.example",
        "CORS-预检反射Origin(现状,P2-3待修)", c, f"ACAO={acao!r} status={status}")

    # F2: 错误响应脱敏 —— 所有收集到的 4xx/5xx 响应体不得含源码路径/类型名
    assert_no_internal_leak("\n".join(err_bodies),
                            f"错误响应脱敏(共{len(err_bodies)}条样本)", c)

    # ==================================================================
    # 测试后清理（P0 用例不创建用户，防御性执行；db 不可达则跳过）
    # ==================================================================
    db_svc = SERVICES["db"]
    if check_tcp_service(db_svc["host"], db_svc["port"])[0]:
        resp = db_execute(
            "delete", "Users",
            where={"username": {"like": f"{TEST_USER_PREFIX}%"}},
            host=db_svc["host"], port=db_svc["port"])
        if not resp.get("success") and "table not found" in str(resp.get("error", "")):
            # 当前环境 db 无 Users 表（如本地开发库），清理无意义
            skip("清理-删除本测试用户记录", "当前环境无 Users 表", c)
        else:
            assert_true(resp.get("success") is True,
                        "清理-删除本测试用户记录", c, f"resp={str(resp)[:100]}")
            # 旧版测试遗留污染（test_%@example.com）仅报告数量，不自动删除
            legacy = db_execute(
                "select", "Users",
                where={"username": {"like": "test_%@example.com"}},
                host=db_svc["host"], port=db_svc["port"])
            if legacy.get("success") and legacy.get("data"):
                print(f"  [WARN] 发现 {len(legacy['data'])} 条旧版测试遗留用户 "
                      f"(test_*@example.com)，建议人工清理")
    else:
        skip("清理-测试用户", "db 服务不可达", c)

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  gateway 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
