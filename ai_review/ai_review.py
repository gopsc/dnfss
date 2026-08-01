#!/usr/bin/env python3

"""
AI Review Service

基于 DeepSeek + RAG 的 AI 内容审核微服务。
接收纯文本字符串，返回结构化审核结果。
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from waitress import serve
from dotenv import load_dotenv
import hashlib
import os
import sys
import threading
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from agent.review_agent import ReviewAgent
from security_middleware import apply_security_headers

app = Flask(__name__)
apply_security_headers(app)
CORS(app)

review_agent = ReviewAgent()

# ── 内容大小限制（10KB，审核文本不应超过此值）──
MAX_CONTENT_LENGTH = 10 * 1024

# ── 简易滑动窗口限流器 ──────────────────────────────────────────────
# /review: 30次/分钟/IP（正常审核请求）
# /rag/feedback: 5次/分钟/IP（人工纠偏低频操作）

_RATE_LIMIT_WINDOW = 60          # 窗口秒数
_RATE_LIMIT_REVIEW = 30          # /review 每分钟上限
_RATE_LIMIT_FEEDBACK = 5         # /rag/feedback 每分钟上限
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_BUCKETS: "dict[str, list[float]]" = defaultdict(list)


def _cleanup_buckets():
    """惰性清理过期时间戳，在线程锁内完成。"""
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW
    stale_keys = []
    for key, timestamps in _RATE_LIMIT_BUCKETS.items():
        timestamps[:] = [t for t in timestamps if t > cutoff]
        if not timestamps:
            stale_keys.append(key)
    for key in stale_keys:
        del _RATE_LIMIT_BUCKETS[key]


def _check_rate_limit(key: str, limit: int) -> bool:
    """检查是否超出限流，返回 True 表示放行，False 表示拒绝。"""
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW
    with _RATE_LIMIT_LOCK:
        bucket = _RATE_LIMIT_BUCKETS[key]
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def _client_ip() -> str:
    """获取客户端真实 IP（优先 X-Forwarded-For）。"""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


@app.route('/review', methods=['POST'])
def review():
    """
    审核接口
    Request:  {"content": "待审核的字符串"}
    Response: {"invalid": "true"}  或  {"invalid": "false"}
    """
    # 频率限制
    ip = _client_ip()
    if not _check_rate_limit(f"review:{ip}", _RATE_LIMIT_REVIEW):
        return jsonify({"error": "rate limit exceeded", "retry_after": _RATE_LIMIT_WINDOW}), 429

    data = request.get_json()

    if not data or 'content' not in data:
        return jsonify({"error": "content is required"}), 400

    content = data.get('content', '').strip()

    if not content:
        return jsonify({"error": "content cannot be empty"}), 400

    # 内容大小限制
    if len(content) > MAX_CONTENT_LENGTH:
        return jsonify({"error": f"content too large, max {MAX_CONTENT_LENGTH} bytes"}), 413

    result = review_agent.review(content)
    return jsonify(result), 200


@app.route('/rag/feedback', methods=['POST'])
def rag_feedback():
    """
    人工纠偏反馈接口 —— AI 审核结果与人工判断不一致时调用。

    Request:  {"content": "被误判的文本", "correct_type": "violation"}
    correct_type: "violation" — AI 漏判了（AI 通过但实际违规）
                  "safe"      — AI 误杀了（AI 拦截但实际安全）

    Response: {"added": true, "id": "v_xxx", "md5": "xxx"}
              {"added": false, "reason": "duplicate"}
    """
    # 频率限制（人工纠偏低频操作，严格限制）
    ip = _client_ip()
    if not _check_rate_limit(f"feedback:{ip}", _RATE_LIMIT_FEEDBACK):
        return jsonify({"error": "rate limit exceeded", "retry_after": _RATE_LIMIT_WINDOW}), 429

    data = request.get_json()

    if not data or 'content' not in data:
        return jsonify({"error": "content is required"}), 400

    content = data.get('content', '').strip()
    correct_type = data.get('correct_type', 'violation').strip()

    if not content:
        return jsonify({"error": "content cannot be empty"}), 400

    if correct_type not in ("violation", "safe"):
        return jsonify({"error": "correct_type must be 'violation' or 'safe'"}), 400

    md5_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    entry_id = f"{correct_type[:1]}_{md5_hash[:12]}"
    category = "human_feedback"

    added = review_agent.rag.add_example(entry_id, content, correct_type, category)

    return jsonify({
        "added": added,
        "id": entry_id if added else None,
        "md5": md5_hash,
        "reason": None if added else "duplicate",
    }), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200


@app.route('/rag/list', methods=['GET'])
def rag_list():
    """返回 RAG 库中所有条目。"""
    entries = review_agent.rag.list_all()
    return jsonify({"total": len(entries), "entries": entries}), 200


ADDR = os.getenv("AI_REVIEW_HOST", "127.0.0.1")
PORT = int(os.getenv("AI_REVIEW_PORT", "8007"))

if __name__ == "__main__":
    print(f'AI Review Service listening at http://{ADDR}:{PORT}')
    serve(app, host=ADDR, port=PORT)
