"""
dnfs 黑盒用户模拟测试 — 创建笔记 → 查询 → 更新 → 删除（含内容审核）。

用户旅程:
  已登录用户携带 Bearer Token:
  1. 创建根节点笔记（内容经 ai_review 审核）
  2. 创建子节点笔记
  3. 查询节点列表
  4. 更新笔记标题和内容
  5. 删除叶子节点（无子节点）
  6. 删除非空节点（应拒绝）
  7. 无 Token 操作（应拒绝）
  8. 创建时缺少必填参数（应报错）

前提: gateway 已启动，有已注册用户和有效 Token。
"""

import json
import random
import string

from helpers import (
    http_post, try_parse_json,
    assert_eq, assert_status, assert_true, assert_json_key,
    skip, Counter, check_service,
)
from config import get_url, SERVICES

DNFS_URL = get_url("dnfs")
GATEWAY_URL = get_url("gateway")


def _random_test_user():
    suffix = ''.join(random.choices(string.ascii_lowercase, k=6))
    return f"dtest_{suffix}@test.local"


def _register_and_login(gw_url: str, counter: Counter):
    """通过 gateway 注册并登录，返回 (token, email)。失败返回 (None, None)。"""
    email = _random_test_user()
    password = "TestP@ss123"

    # 注册
    status, body, _ = http_post(f"{gw_url}/register", {
        "username": email,
        "password": password,
    })
    if status != 200:
        return None, None

    data = try_parse_json(body)
    if not data or not data.get("success"):
        return None, None

    # 登录
    status, body, _ = http_post(f"{gw_url}/login", {
        "username": email,
        "password": password,
    })
    if status != 200:
        return None, None

    data = try_parse_json(body)
    if not data or not data.get("success"):
        return None, None

    token = (data.get("data") or {}).get("token", "")
    return token, email


def run(url: str = None):
    """dnfs 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    du = url or DNFS_URL
    c = Counter()

    print(f"\n--- dnfs 笔记管理流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性
    # ------------------------------------------------------------------
    if not check_service(du)[0]:
        skip("全部测试", "dnfs 服务不可达", c)
        return c.passed, c.failed, c.skipped

    # ------------------------------------------------------------------
    # 准备: 注册用户并获取 Token
    # ------------------------------------------------------------------
    gw_available = check_service(GATEWAY_URL)[0]
    if not gw_available:
        skip("全部测试", "gateway 服务不可达，无法获取 Token", c)
        return c.passed, c.failed, c.skipped

    token, email = _register_and_login(GATEWAY_URL, c)
    if not token:
        skip("全部测试", "无法完成注册/登录获取 Token", c)
        return c.passed, c.failed, c.skipped

    auth_headers = {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # 场景 1: 无 Token 创建笔记 — 应拒绝
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{du}/add_note", {
        "pid": 0,
        "title": "test",
        "content": "test content",
    })
    assert_eq(status, 401, "创建笔记-无Token-拒绝 (401)", c)

    # ------------------------------------------------------------------
    # 场景 2: 创建笔记 — 缺少参数
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{du}/add_note", {
        "pid": 0,
    }, headers=auth_headers)
    assert_eq(status, 400, "创建笔记-缺少title/content (400)", c)

    # ------------------------------------------------------------------
    # 场景 3: 创建根节点笔记
    # ------------------------------------------------------------------
    note_title = f"测试笔记_{random.randint(1000, 9999)}"
    note_content = "这是一段正常的测试内容，用于验证笔记创建功能。"
    status, body, _ = http_post(f"{du}/add_note", {
        "pid": 0,
        "title": note_title,
        "content": note_content,
    }, headers=auth_headers)
    # 期望 200（审核通过则成功）
    data = try_parse_json(body)
    assert_true(
        status == 200 and data is not None and data.get("success") is True,
        "创建笔记-根节点", c,
        f"status={status} body={body[:150]}",
    )

    # ------------------------------------------------------------------
    # 场景 4: 查询根节点下的笔记列表
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{du}/get_notes", {"pid": 0})
    assert_eq(status, 200, "查询笔记-根节点 (200)", c)
    notes = try_parse_json(body)
    assert_true(
        isinstance(notes, list),
        "查询笔记-返回列表", c,
    )

    # ------------------------------------------------------------------
    # 场景 5: 创建子节点笔记
    # ------------------------------------------------------------------
    # 先获取刚创建的笔记 ID
    child_note_id = None
    if notes and len(notes) > 0:
        # 找到我们刚创建的笔记
        for note in notes:
            if note.get("title") == note_title:
                child_note_id = note.get("id")
                break

    if child_note_id:
        child_title = f"子笔记_{random.randint(1000, 9999)}"
        child_content = "这是子节点的测试内容。"
        status, body, _ = http_post(f"{du}/add_note", {
            "pid": child_note_id,
            "title": child_title,
            "content": child_content,
        }, headers=auth_headers)
        data = try_parse_json(body)
        assert_true(
            status == 200 and data is not None,
            "创建笔记-子节点", c,
            f"status={status} body={body[:150]}",
        )

    # ------------------------------------------------------------------
    # 场景 6: 删除非空节点 — 应拒绝（有子节点）
    # ------------------------------------------------------------------
    if child_note_id:
        status, body, _ = http_post(f"{du}/delete_note", {
            "id": child_note_id,
        }, headers=auth_headers)
        data = try_parse_json(body)
        assert_true(
            status in (400, 403) or (data and not data.get("success")),
            "删除笔记-非空节点-拒绝", c,
            f"status={status} body={body[:150]}",
        )

    # ------------------------------------------------------------------
    # 场景 7: 更新笔记内容
    # ------------------------------------------------------------------
    if child_note_id:
        updated_title = f"更新_{random.randint(1000, 9999)}"
        updated_content = "更新后的测试内容，依然正常无害。"
        status, body, _ = http_post(f"{du}/update_note", {
            "id": child_note_id,
            "pid": 0,
            "title": updated_title,
            "content": updated_content,
        }, headers=auth_headers)
        data = try_parse_json(body)
        assert_true(
            status == 200 and data is not None and data.get("success") is True,
            "更新笔记-内容", c,
            f"status={status} body={body[:150]}",
        )

    # ------------------------------------------------------------------
    # 场景 8: 更新笔记 — 缺少参数
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{du}/update_note", {
        "id": child_note_id,
    }, headers=auth_headers)
    assert_eq(status, 400, "更新笔记-缺少参数 (400)", c)

    # ------------------------------------------------------------------
    # 场景 9: 删除笔记 — 缺少 id 参数
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{du}/delete_note", {}, headers=auth_headers)
    assert_eq(status, 400, "删除笔记-缺少id (400)", c)

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  dnfs 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
