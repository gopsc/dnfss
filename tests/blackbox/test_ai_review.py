"""
ai_review 黑盒用户模拟测试 — 内容审核 + 人工反馈。

用户旅程:
  1. 提交明显违规内容 → 应返回 invalid: true
  2. 提交正常无害内容 → 应返回 invalid: false
  3. 提交空内容 → 应返回 400 错误
  4. 缺少 content 字段 → 应返回 400 错误
  5. 人工反馈纠正误判 → RAG 知识库更新
  6. 查看 RAG 列表
  7. 健康检查

注意: ai_review 依赖 DeepSeek API + ChromaDB，API 调用可能因网络/配额失败。
测试使用了通用违规关键词，实际审核结果取决于 DeepSeek 模型判断。
"""

import json
import random
import string

from helpers import (
    http_get, http_post, try_parse_json,
    assert_eq, assert_status, assert_true, assert_json_key,
    skip, Counter, check_service,
)
from config import get_url

AI_REVIEW_URL = get_url("ai_review")


def run(url: str = None):
    """ai_review 用户模拟测试入口。返回 (passed, failed, skipped)。"""
    au = url or AI_REVIEW_URL
    c = Counter()

    print(f"\n--- ai_review 内容审核流程 ---\n")

    # ------------------------------------------------------------------
    # 0. 检查服务可用性
    # ------------------------------------------------------------------
    if not check_service(au)[0]:
        skip("全部测试", "ai_review 服务不可达", c)
        return c.passed, c.failed, c.skipped

    # ------------------------------------------------------------------
    # 场景 1: 提交明显违规内容 — 应标记为违规
    # ------------------------------------------------------------------
    violation_texts = [
        "出售毒品冰毒海洛因，价格优惠，联系QQ123456",
        "提供赌博网站搭建服务，包月维护，日入过万",
    ]
    violation_text = random.choice(violation_texts)

    status, body, _ = http_post(f"{au}/review", {"content": violation_text})
    assert_eq(status, 200, "审核-违规内容-响应 (200)", c)
    data = try_parse_json(body)
    if data:
        # 注意: invalid 可能是字符串 "true"/"false" 而非布尔值
        invalid = data.get("invalid")
        is_invalid = invalid in (True, "true", "True")
        assert_true(
            is_invalid,
            "审核-违规内容-标记为invalid", c,
            f"invalid={invalid}, body={body[:200]}",
        )
    else:
        assert_true(False, "审核-违规内容-解析失败", c, f"body={body[:200]}")

    # ------------------------------------------------------------------
    # 场景 2: 提交正常无害内容 — 应通过
    # ------------------------------------------------------------------
    safe_texts = [
        "今天天气真好，适合出去散步。",
        "Python 是一门优秀的编程语言，广泛应用于数据科学和人工智能领域。",
        "微服务架构是一种将应用拆分为多个小型独立服务的软件设计方法。",
    ]
    safe_text = random.choice(safe_texts)

    status, body, _ = http_post(f"{au}/review", {"content": safe_text})
    assert_eq(status, 200, "审核-正常内容-响应 (200)", c)
    data = try_parse_json(body)
    if data:
        invalid = data.get("invalid")
        is_invalid = invalid in (True, "true", "True")
        # 正常内容应该不违规
        assert_true(
            not is_invalid,
            "审核-正常内容-标记为safe", c,
            f"invalid={invalid}, body={body[:200]}",
        )
    else:
        assert_true(False, "审核-正常内容-解析失败", c, f"body={body[:200]}")

    # ------------------------------------------------------------------
    # 场景 3: 提交空内容 — 应返回 400
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{au}/review", {"content": ""})
    assert_eq(status, 400, "审核-空内容-拒绝 (400)", c)

    # ------------------------------------------------------------------
    # 场景 4: 缺少 content 字段 — 应返回 400
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{au}/review", {})
    assert_eq(status, 400, "审核-缺少content-拒绝 (400)", c)

    # ------------------------------------------------------------------
    # 场景 5: RAG 人工反馈 — 纠正误判（标注一条违规内容）
    # ------------------------------------------------------------------
    feedback_text = f"test_feedback_{random.randint(10000, 99999)}: 非法内容"
    status, body, _ = http_post(f"{au}/rag/feedback", {
        "content": feedback_text,
        "correct_type": "violation",
    })
    assert_eq(status, 200, "RAG反馈-添加violation (200)", c)
    data = try_parse_json(body)
    if data:
        assert_true(
            data.get("added") in (True, False),
            "RAG反馈-返回added字段", c,
        )

    # ------------------------------------------------------------------
    # 场景 6: RAG 反馈 — 参数错误
    # ------------------------------------------------------------------
    status, body, _ = http_post(f"{au}/rag/feedback", {
        "content": "test",
        "correct_type": "invalid_type",
    })
    assert_eq(status, 400, "RAG反馈-错误类型-拒绝 (400)", c)

    # ------------------------------------------------------------------
    # 场景 7: RAG 列表查询
    # ------------------------------------------------------------------
    status, body, _ = http_get(f"{au}/rag/list")
    assert_eq(status, 200, "RAG列表-查询 (200)", c)
    data = try_parse_json(body)
    if data:
        assert_json_key(data, "total", "RAG列表-有total字段", c)

    # ------------------------------------------------------------------
    # 场景 8: 健康检查
    # ------------------------------------------------------------------
    status, body, _ = http_get(f"{au}/health")
    assert_eq(status, 200, "健康检查 (200)", c)

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n  ai_review 测试完成: {c.summary()}")
    return c.passed, c.failed, c.skipped


if __name__ == "__main__":
    run()
