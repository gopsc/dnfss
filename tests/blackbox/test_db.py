"""
db 黑盒用户模拟测试 — pymsg 协议 CRUD。

用户旅程:
  1. 通过 pymsg 协议查询 Users 表
  2. 通过 pymsg 协议查询 Logs 表
  3. 通过 pymsg 协议带 where 条件查询
  4. 无效路由被拒绝
  5. 日期列 where（ISO 字符串 / $now 哨兵 / 非法值拒绝）

注意: dbv2.py 为纯 pymsg/TCP 服务（无 HTTP 接口），日志通过
log_client.py 走 execute 路由 INSERT 写入 Logs 表。
"""

from helpers import (
    assert_true, skip, Counter, PymsgClient, check_tcp_service,
)

DB_HOST = "127.0.0.1"
DB_PORT = 8005


def run(url: str = None):
    """db 用户模拟测试入口。返回 (passed, failed, skipped)。

    url 参数仅为保持与其他测试模块的签名一致，db 固定走 127.0.0.1:8005。
    """
    c = Counter()

    print(f"\n--- db 数据库与日志流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性（TCP 直连，db 无 HTTP 接口）
    # ------------------------------------------------------------------
    if not check_tcp_service(DB_HOST, DB_PORT)[0]:
        skip("全部测试", "db 服务不可达", c)
        return c.passed, c.failed, c.skipped

    # ------------------------------------------------------------------
    # 第一部分: pymsg 协议 CRUD
    # ------------------------------------------------------------------
    print("  [pymsg 协议测试]")

    client = PymsgClient(DB_HOST, DB_PORT, "test_blackbox")

    # 场景 1: pymsg 查询 Users 表
    result = client.call({
        "_route": "execute",
        "action": "select",
        "table": "Users",
        "limit": 5,
    })
    assert_true(
        result.get("success") is True,
        "pymsg-查询Users表", c,
        f"error={result.get('error', '')}",
    )
    if result.get("success"):
        data = result.get("data", [])
        assert_true(
            isinstance(data, list),
            "pymsg-返回列表格式", c,
        )

    # 场景 2: pymsg 查询 Logs 表
    result = client.call({
        "_route": "execute",
        "action": "select",
        "table": "Logs",
        "limit": 3,
        "order_by": [{"column": "id", "desc": True}],
    })
    assert_true(
        result.get("success") is True,
        "pymsg-查询Logs表", c,
        f"error={result.get('error', '')}",
    )

    # 场景 3: pymsg 使用 where 条件
    result = client.call({
        "_route": "execute",
        "action": "select",
        "table": "Users",
        "columns": ["id", "username"],
        "where": {"id": {"gt": 0}},
        "limit": 1,
    })
    assert_true(
        result.get("success") is True,
        "pymsg-条件查询", c,
        f"error={result.get('error', '')}",
    )

    # 场景 4: pymsg 无效路由
    result = client.call({
        "_route": "nonexistent_route",
        "action": "select",
        "table": "Users",
    })
    assert_true(
        result.get("success") is False,
        "pymsg-无效路由-拒绝", c,
    )

    # 场景 5: 日期列 where — ISO 8601 字符串（dbv2 日期适配层）
    result = client.call({
        "_route": "execute",
        "action": "select",
        "table": "Users",
        "columns": ["id", "username", "create_at"],
        "where": {"create_at": {"lt": "2999-01-01T00:00:00"}},
        "limit": 1,
    })
    assert_true(
        result.get("success") is True,
        "pymsg-日期列where-ISO字符串", c,
        f"error={result.get('error', '')}",
    )

    # 场景 6: 日期列 where — $now 哨兵
    result = client.call({
        "_route": "execute",
        "action": "select",
        "table": "Users",
        "columns": ["id", "username", "create_at"],
        "where": {"create_at": {"lt": "$now"}},
        "limit": 1,
    })
    assert_true(
        result.get("success") is True,
        "pymsg-日期列where-$now哨兵", c,
        f"error={result.get('error', '')}",
    )

    # 场景 7: 日期列 where — 非法日期字符串应报错
    result = client.call({
        "_route": "execute",
        "action": "select",
        "table": "Users",
        "where": {"create_at": {"lt": "not-a-date"}},
    })
    assert_true(
        result.get("success") is False,
        "pymsg-日期列where-非法值-拒绝", c,
    )

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  db 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
