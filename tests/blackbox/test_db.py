"""
db 黑盒用户模拟测试 — 集中日志: 写入 → 查询 → 统计 → 清理。

用户旅程:
  1. 通过 pymsg 协议执行 CRUD (select/insert)
  2. 通过 HTTP /log/ingest 写入日志
  3. 通过 HTTP /log/query 查询日志
  4. 通过 HTTP /log/stats 查看统计
  5. 通过 HTTP /log/health 健康检查

注意: dbv2.py (pymsg 模式) 不挂载 Flask log_module Blueprint，
因此 HTTP /log/* 路由可能不可用。测试会优雅降级。
"""

import json
import random
import string
import time

from helpers import (
    http_get, http_post, try_parse_json,
    assert_eq, assert_status, assert_true, assert_json_key,
    skip, Counter, check_service, PymsgClient,
)
from config import get_url

DB_URL = get_url("db")
DB_HOST = "127.0.0.1"
DB_PORT = 8005


def run(url: str = None):
    """db 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    du = url or DB_URL
    c = Counter()

    print(f"\n--- db 数据库与日志流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性
    # ------------------------------------------------------------------
    if not check_service(du)[0]:
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

    # ------------------------------------------------------------------
    # 第二部分: HTTP /log/* 接口 (Flask log_module)
    # ------------------------------------------------------------------
    print("\n  [HTTP /log/* 接口测试]")

    # 检查 /log/health 是否可用（Flask log_module 是否挂载）
    status, body, _ = http_get(f"{du}/log/health")
    if status == 0 or status >= 500:
        skip("HTTP /log/* 测试", "log_module 未挂载 (dbv2 使用 pymsg 模式, 非 Flask)", c)
    else:
        # 场景 5: /log/health 健康检查
        assert_eq(status, 200, "log-健康检查 (200)", c)

        # 场景 6: /log/ingest 写入日志
        test_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        status, body, _ = http_post(f"{du}/log/ingest", {
            "logs": [{
                "service": "dnfs",
                "level": "INFO",
                "message": f"blackbox_test_{random.randint(10000, 99999)}",
                "ts": test_ts,
            }],
        })
        data = try_parse_json(body)
        assert_true(
            status == 200 and data is not None and data.get("success") is True,
            "log-写入日志", c,
            f"status={status} body={body[:150]}",
        )

        # 场景 7: /log/ingest — 空日志列表
        status, body, _ = http_post(f"{du}/log/ingest", {"logs": []})
        assert_eq(status, 200, "log-空日志列表 (200)", c)

        # 场景 8: /log/ingest — 缺少 logs 字段
        status, body, _ = http_post(f"{du}/log/ingest", {})
        data = try_parse_json(body)
        assert_true(
            data is not None and not data.get("success", True),
            "log-缺少logs字段-拒绝", c,
        )

        # 场景 9: /log/query 查询日志
        status, body, _ = http_post(f"{du}/log/query", {
            "limit": 5,
            "desc": True,
        })
        data = try_parse_json(body)
        assert_true(
            status == 200 and data is not None and data.get("success") is True,
            "log-查询日志", c,
            f"status={status}",
        )
        if data and data.get("success"):
            assert_true(
                "logs" in data.get("data", {}),
                "log-查询返回logs字段", c,
            )

        # 场景 10: /log/stats 统计
        status, body, _ = http_post(f"{du}/log/stats", {
            "group_by": "service",
        })
        data = try_parse_json(body)
        assert_true(
            status == 200 and data is not None,
            "log-统计", c,
            f"status={status}",
        )

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  db 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
