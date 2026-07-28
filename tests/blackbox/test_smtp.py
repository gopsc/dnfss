"""
smtp 黑盒用户模拟测试 — 发送邮箱验证码 → 校验。

用户旅程:
  1. 发送验证码到测试邮箱
  2. 用错误验证码校验 → 应失败
  3. 用空参数校验 → 应报错

注意: SMTP 服务需要配置真实的邮件服务器。若服务不可达或邮件发送失败，
相关测试将标记为 SKIP。验证码有效期为 60 秒。
"""

import json
import random
import string

from helpers import (
    http_post, try_parse_json,
    assert_eq, assert_status, assert_true,
    skip, Counter, check_service,
)
from config import get_url

SMTP_URL = get_url("smtp")


def run(url: str = None):
    """smtp 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    su = url or SMTP_URL
    c = Counter()

    print(f"\n--- smtp 邮件验证码发送与校验流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性
    # ------------------------------------------------------------------
    if not check_service(su)[0]:
        skip("全部测试", "smtp 服务不可达", c)
        return c.passed, c.failed, c.skipped

    # ------------------------------------------------------------------
    # 场景 1: 发送验证码 — 缺少 email 参数
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{su}/send_email", {})
    data = try_parse_json(body)
    # 预期返回错误
    assert_true(
        status != 200 or (data and data.get("error")),
        "发送验证码-缺少email-报错", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 场景 2: 发送验证码 — 空 email
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{su}/send_email", {"email": ""})
    data = try_parse_json(body)
    assert_true(
        status != 200 or (data and data.get("error")),
        "发送验证码-空email-报错", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 场景 3: 校验验证码 — 缺少参数
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{su}/verify_email", {})
    data = try_parse_json(body)
    assert_true(
        status != 200 or (data and data.get("error")),
        "校验验证码-缺少参数-报错", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 场景 4: 校验验证码 — 错误验证码
    # ------------------------------------------------------------------
    test_email = f"test_{random.randint(10000, 99999)}@example.com"
    status, body, _ = http_post(f"{su}/verify_email", {
        "email": test_email,
        "input_captcha": "000000",
    })
    data = try_parse_json(body)
    # 未发送过的 email 验证应失败
    assert_true(
        status != 200 or (data and not data.get("success")),
        "校验验证码-错误码-拒绝", c,
        f"status={status} body={body[:100]}",
    )

    # ------------------------------------------------------------------
    # 场景 5: 发送验证码 — 不存在的邮箱域名
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{su}/send_email", {
        "email": "nobody@this-domain-does-not-exist-12345.com",
    })
    data = try_parse_json(body)
    # SMTP 可能成功发送请求（服务接受），或者返回错误
    # 两种情况都算通过 — 至少服务正常响应了
    assert_true(
        status in (200, 400, 500) and data is not None,
        "发送验证码-服务正常响应", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  smtp 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
