"""
gateway 黑盒用户模拟测试 — 注册 → 登录 → Token → 代理转发。

用户旅程:
  1. 注册新账号（含密码强度校验、邮箱格式校验）
  2. 登录获取 Token
  3. Token 用于代理访问后端服务
  4. 重复注册（应失败）
  5. 错误密码登录（应失败）
  6. 无效 Token 代理（应拒绝）
  7. 密码强度不足（应拒绝）
  8. 邮箱格式错误（应拒绝）
"""

import json
import random
import string
import time

from helpers import (
    http_get, http_post, try_parse_json,
    assert_eq, assert_status, assert_true, assert_json_key,
    Counter, check_service,
)
from config import get_url

GATEWAY_URL = get_url("gateway")
STATIC_URL = get_url("static")


def _random_email():
    """生成随机测试邮箱。"""
    suffix = ''.join(random.choices(string.ascii_lowercase, k=8))
    return f"test_{suffix}@example.com"


def _random_password():
    """生成符合强度要求的随机密码。"""
    chars = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(random.choices(chars, k=12))


def run(url: str = None):
    """gateway 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    gw = url or GATEWAY_URL
    c = Counter()

    print(f"\n--- gateway 用户注册与登录流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性
    # ------------------------------------------------------------------
    if not check_service(gw)[0]:
        print("  gateway 服务不可达，跳过所有测试")
        c.add_skip()
        return c.passed, c.failed, c.skipped

    email = _random_email()
    password = _random_password()
    token = None

    # ------------------------------------------------------------------
    # 场景 1: 注册 — 密码强度不足
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{gw}/register", {
        "username": email,
        "password": "123",
    })
    assert_eq(status, 401, "注册-密码太短 (401)", c)
    data = try_parse_json(body)
    assert_true(
        data is not None and not data.get("success"),
        "注册-密码太短-拒绝", c,
    )

    # ------------------------------------------------------------------
    # 场景 2: 注册 — 邮箱格式错误
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{gw}/register", {
        "username": "not-an-email",
        "password": _random_password(),
    })
    assert_eq(status, 400, "注册-邮箱格式错误 (400)", c)
    data = try_parse_json(body)
    assert_true(
        data is not None and not data.get("success"),
        "注册-邮箱格式错误-拒绝", c,
    )

    # ------------------------------------------------------------------
    # 场景 3: 注册 — 成功
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{gw}/register", {
        "username": email,
        "password": password,
    })
    assert_eq(status, 200, "注册-成功 (200)", c)
    data = try_parse_json(body)
    assert_true(
        data is not None and data.get("success") is True,
        "注册-返回 success=true", c,
    )

    # ------------------------------------------------------------------
    # 场景 4: 注册 — 重复用户名
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{gw}/register", {
        "username": email,
        "password": password,
    })
    # 期望注册失败（用户名已存在）
    assert_true(
        status != 200 or (try_parse_json(body) or {}).get("success") is not True,
        "注册-重复用户名-拒绝", c,
        f"status={status}, body={body[:100]}",
    )

    # ------------------------------------------------------------------
    # 场景 5: 登录 — 错误密码
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{gw}/login", {
        "username": email,
        "password": "WrongPassword1",
    })
    assert_eq(status, 401, "登录-错误密码 (401)", c)
    data = try_parse_json(body)
    assert_true(
        data is not None and not data.get("success"),
        "登录-错误密码-拒绝", c,
    )

    # ------------------------------------------------------------------
    # 场景 6: 登录 — 成功获取 Token
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{gw}/login", {
        "username": email,
        "password": password,
    })
    assert_eq(status, 200, "登录-成功 (200)", c)
    data = try_parse_json(body)
    assert_true(
        data is not None and data.get("success") is True,
        "登录-返回 success=true", c,
    )
    token_data = (data or {}).get("data", {})
    token = token_data.get("token", "")
    assert_true(
        len(token) > 20 if token else False,
        "登录-Token 不为空", c,
    )

    # ------------------------------------------------------------------
    # 场景 7: 代理 — 使用 Token 访问静态服务
    # ------------------------------------------------------------------
    if token:
        status, body, headers = http_get(
            f"{gw}/",
            headers={
                "Authorization": f"Bearer {token}",
                "Target-Url": STATIC_URL,
            },
        )
        assert_true(
            status in (200, 302, 301),
            "代理-GET静态首页", c,
            f"status={status}",
        )

    # ------------------------------------------------------------------
    # 场景 8: 代理 — 无 Token 应拒绝
    # ------------------------------------------------------------------
    status, body, _ = http_get(
        f"{gw}/some-path",
        headers={"Target-Url": STATIC_URL},
    )
    # 缺少 Token 应返回 400/401
    assert_true(
        status in (400, 401, 403),
        "代理-无Token拒绝", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 场景 9: 代理 — 无效路径（路径遍历）
    # ------------------------------------------------------------------
    if token:
        status, body, _ = http_get(
            f"{gw}/../../../etc/passwd",
            headers={
                "Authorization": f"Bearer {token}",
                "Target-Url": STATIC_URL,
            },
        )
        assert_true(
            status in (400, 403),
            "代理-路径遍历拒绝", c,
            f"status={status}",
        )

    # ------------------------------------------------------------------
    # 场景 10: 默认代理 (无 Target-Url，无需 Token)
    # ------------------------------------------------------------------
    status, body, _ = http_get(f"{gw}/")
    assert_true(
        status in (200, 302, 301),
        "代理-默认路由", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  gateway 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
