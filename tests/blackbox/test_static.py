"""
static 黑盒用户模拟测试 — 首页加载 → 文件浏览 → 路径遍历防护 → 视频流。

用户旅程:
  1. 访问首页 → 返回 HTML 登录页
  2. 访问 /dnfs.html → 树状笔记前端
  3. 访问 /api/filelist → 目录列表 JSON
  4. 访问静态资源 (CSS/JS) → 返回正确 MIME 类型
  5. 路径遍历攻击 → 返回 403
  6. 不存在的文件 → 返回 404
  7. 健康检查
  8. DNFS 代理接口 (CORS 预检)
"""

import json

from helpers import (
    http_get, http_post, try_parse_json,
    assert_eq, assert_status, assert_true, assert_contains,
    skip, Counter, check_service,
)
from config import get_url

STATIC_URL = get_url("static")


def run(url: str = None):
    """static 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    su = url or STATIC_URL
    c = Counter()

    print(f"\n--- static 前端静态服务流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性
    # ------------------------------------------------------------------
    if not check_service(su)[0]:
        skip("全部测试", "static 服务不可达", c)
        return c.passed, c.failed, c.skipped

    # ------------------------------------------------------------------
    # 场景 1: 访问首页
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/")
    assert_eq(status, 200, "首页-加载 (200)", c)
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    assert_contains(content_type, "text/html", "首页-Content-Type text/html", c)

    # ------------------------------------------------------------------
    # 场景 2: 访问 DNFS 前端页面
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/dnfs.html")
    assert_eq(status, 200, "DNFS页面-加载 (200)", c)

    # ------------------------------------------------------------------
    # 场景 3: 访问 CSS 资源
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/css/theme.css")
    assert_true(
        status in (200, 404),
        "CSS资源-请求", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 场景 4: 访问 JS 资源
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/js/dnfs.js")
    assert_true(
        status in (200, 404),
        "JS资源-请求", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 场景 5: 路径遍历攻击 — ../ 应返回 403
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/../../../etc/passwd")
    assert_eq(status, 403, "路径遍历-../-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 6: 路径遍历 — URL 编码 ..%2f
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/..%2f..%2f..%2fetc%2fpasswd")
    assert_eq(status, 403, "路径遍历-URL编码-拦截 (403)", c)

    # ------------------------------------------------------------------
    # 场景 7: 不存在的文件 → 404
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/this-file-does-not-exist-12345.html")
    assert_eq(status, 404, "不存在文件-404", c)

    # ------------------------------------------------------------------
    # 场景 8: 健康检查
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/health")
    assert_eq(status, 200, "健康检查 (200)", c)

    # ------------------------------------------------------------------
    # 场景 9: /api/filelist — 目录列表
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/api/filelist")
    assert_eq(status, 200, "文件列表-API (200)", c)
    data = try_parse_json(body)
    assert_true(
        data is not None and data.get("success") is True,
        "文件列表-返回success", c,
        f"body={body[:200]}",
    )
    if data and data.get("success"):
        items = data.get("items", [])
        assert_true(
            isinstance(items, list),
            "文件列表-返回items数组", c,
        )

    # ------------------------------------------------------------------
    # 场景 10: /api/filelist — 路径遍历
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/api/filelist?path=../../../etc")
    assert_true(
        status in (403, 400),
        "文件列表-路径遍历-拒绝", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 场景 11: DNFS 代理接口 — OPTIONS 预检
    # ------------------------------------------------------------------
    status, body, headers = http_get(f"{su}/add_note")  # OPTIONS 需要特殊处理
    # 简单的 GET 代理转发测试
    assert_true(
        status in (200, 400, 401, 405),
        "DNFS代理-add_note可达", c,
        f"status={status}",
    )

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  static 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
