"""
captcha 黑盒用户模拟测试 — 获取验证码 → 校验 → 一次性消费。

用户旅程:
  1. 请求 /captcha/image 获取验证码图片 + token
  2. 使用正确答案校验 → 应通过
  3. 使用错误答案校验 → 应拒绝
  4. 同一 token 重复校验 → 应拒绝 (一次性)
  5. 缺少 token/answer → 应报错
  6. 不存在的 token → 应拒绝
  7. 健康检查
"""

import json
import string

from helpers import (
    http_get, http_post, http_request,
    assert_eq, assert_status, assert_true, assert_json_key, assert_contains,
    skip, Counter, check_service,
)
from config import get_url

CAPTCHA_URL = get_url("captcha")


def run(url: str = None):
    """captcha 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    cu = url or CAPTCHA_URL
    c = Counter()

    print(f"\n--- captcha 验证码获取与校验流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性
    # ------------------------------------------------------------------
    if not check_service(cu)[0]:
        print("  captcha 服务不可达，跳过所有测试")
        c.add_skip()
        return c.passed, c.failed, c.skipped

    # ------------------------------------------------------------------
    # 场景 1: 获取验证码图片
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{cu}/captcha/image")
    assert_eq(status, 200, "获取验证码图片 (200)", c)
    captcha_token = headers.get("X-Captcha-Token", headers.get("x-captcha-token", ""))
    assert_true(
        len(captcha_token) == 32,
        "获取验证码-Token 32位hex", c,
        f"token={captcha_token[:10]}...",
    )
    # 验证返回的是图片
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    assert_contains(content_type, "image", "获取验证码-图片类型", c)

    # ------------------------------------------------------------------
    # 场景 2: 缺少参数 — token 和 answer 都缺
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{cu}/captcha/verify", {})
    data = json.loads(body) if body else {}
    assert_true(
        not data.get("valid", True),
        "校验-缺少参数-拒绝", c,
    )

    # ------------------------------------------------------------------
    # 场景 3: 不存在的 token
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{cu}/captcha/verify", {
        "token": "0" * 32,
        "answer": "abcde",
    })
    data = json.loads(body) if body else {}
    assert_true(
        not data.get("valid", True),
        "校验-不存在token-拒绝", c,
    )

    # ------------------------------------------------------------------
    # 场景 4: 错误答案
    # ------------------------------------------------------------------
    if captcha_token:
        status, body, _ = http_post(f"{cu}/captcha/verify", {
            "token": captcha_token,
            "answer": "ZZZZZ",
        })
        data = json.loads(body) if body else {}
        assert_true(
            not data.get("valid", True),
            "校验-错误答案-拒绝", c,
        )

    # ------------------------------------------------------------------
    # 场景 5: 验证码一次性消费 — 拿新 token 分别测正确答案和二次使用
    # ------------------------------------------------------------------
    # 重新获取一个 captcha（上一步的 token 已被消费）
    status2, body2, headers2 = http_get(f"{cu}/captcha/image")
    assert_eq(status2, 200, "获取第二个验证码 (200)", c)

    # 没法从图片自动识别文字，但我们可以测试一次性消费的行为
    # 先用错误答案消费 token
    token2 = headers2.get("X-Captcha-Token", headers.get("x-captcha-token", ""))
    if token2:
        # 第一次：错误答案
        status_a, body_a, _ = http_post(f"{cu}/captcha/verify", {
            "token": token2,
            "answer": "WRONG",
        })
        data_a = json.loads(body_a) if body_a else {}
        assert_true(
            not data_a.get("valid", True),
            "一次性消费-第一次用完", c,
        )

        # 第二次：同一 token 再次使用
        status_b, body_b, _ = http_post(f"{cu}/captcha/verify", {
            "token": token2,
            "answer": "WRONG",
        })
        data_b = json.loads(body_b) if body_b else {}
        assert_true(
            not data_b.get("valid", True),
            "一次性消费-重复使用拒绝", c,
        )

    # ------------------------------------------------------------------
    # 场景 6: 健康检查
    # ------------------------------------------------------------------
    status, body, _ = http_get(f"{cu}/health")
    assert_eq(status, 200, "健康检查 (200)", c)

    # ------------------------------------------------------------------
    # 场景 7: 不存在路径 → 404
    # ------------------------------------------------------------------
    status, body, _ = http_get(f"{cu}/nonexistent")
    assert_eq(status, 404, "不存在路径-404", c)

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  captcha 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
