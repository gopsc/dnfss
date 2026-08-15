"""subsystems.conversation_compressor 单元测试

覆盖：
  - estimate_tokens: 中英文/工具调用 token 估算
  - should_compress: 消息数阈值触发 / Token 阈值触发 / 均未触发 / 功能禁用
  - compress_conversation: 消息数触发压缩 / Token 触发压缩 / LLM 失败保留原文
  - _validate_message_structure: 移除孤立 tool 消息
通过注入 FakeLLMClient 模拟 LLM 调用，不依赖真实 API。
"""

import pytest

from subsystems import ConversationCompressor
from common import constants, Logger


class FakeLLMClient:
    """模拟 llm_client 抽象层的 chat_completion"""

    def __init__(self, reply="这是压缩摘要", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def chat_completion(self, messages, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append({"max_tokens": max_tokens, "n_msgs": len(messages)})
        if self.error:
            raise self.error
        return self.reply


def make_messages(n_pairs=15, long_content=False):
    """构造 n_pairs 轮 user/assistant 对话（含 1 条前导 system）"""
    msgs = [{"role": "system", "content": "SYS_PROMPT"}]
    for i in range(n_pairs):
        content = f"用户消息内容{i}"
        if long_content:
            content += "很长的文档内容" * 200
        msgs.append({"role": "user", "content": content})
        msgs.append({"role": "assistant", "content": f"助手回复{i}"})
    return msgs


@pytest.fixture
def compressor():
    return ConversationCompressor(llm_client=FakeLLMClient(), logger=Logger("TestCompressor", 0))


def patch_const(monkeypatch, name, value):
    """把 Constants 类属性 property 替换为固定值（参照 test_db.py 既有做法）"""
    monkeypatch.setattr(type(constants), name, property(lambda self: value))


# ==================== estimate_tokens ====================

def test_estimate_tokens_chinese(compressor):
    assert compressor.estimate_tokens([{"role": "user", "content": "你好"}]) == 13  # 2*1.5 + 10


def test_estimate_tokens_english(compressor):
    # 2 词 * 1.3 + (11 字符 - 2 词) * 0.8 + 10 = 19.8 → int 截断为 19
    # （既有算法：other_chars 按词数抵扣，字母被计入 other，保持原行为）
    assert compressor.estimate_tokens([{"role": "user", "content": "hello world"}]) == 19


def test_estimate_tokens_tool_calls(compressor):
    msgs = [{
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": "run_command", "arguments": "{\"a\":1}"}}],
    }]
    # name 11 字符*1.5=16.5 + arguments 8 字符*0.8=6.4 + 50 = 72.9 → 72
    assert compressor.estimate_tokens(msgs) == 72


def test_estimate_tokens_empty(compressor):
    assert compressor.estimate_tokens([]) == 0


# ==================== should_compress ====================

def test_should_compress_below_thresholds(compressor, monkeypatch):
    patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 1000)
    patch_const(monkeypatch, "COMPRESS_TOKEN_THRESHOLD", 10 ** 9)
    should, reason = compressor.should_compress(make_messages(5))
    assert should is False


def test_should_compress_by_message_count(compressor, monkeypatch):
    patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 5)
    should, reason = compressor.should_compress(make_messages(5))  # 10 条对话消息
    assert should is True
    assert "消息数" in reason


def test_should_compress_by_token_count(compressor, monkeypatch):
    """消息数未超阈值但 Token 数超阈值时也应触发（v3.1 修复项）。"""
    patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 1000)
    patch_const(monkeypatch, "COMPRESS_TOKEN_THRESHOLD", 1)
    should, reason = compressor.should_compress(make_messages(5))
    assert should is True
    assert "Token" in reason


def test_should_compress_disabled(compressor, monkeypatch):
    patch_const(monkeypatch, "COMPRESS_ENABLED", False)
    should, _ = compressor.should_compress(make_messages(100))
    assert should is False


# ==================== compress_conversation ====================

def test_compress_by_message_count(compressor, monkeypatch):
    patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 10)
    messages = make_messages(15)  # 30 条对话消息 + 1 system
    result = compressor.compress_conversation(messages)

    non_system = [m for m in result if m["role"] != "system"]
    assert len(non_system) < 30  # 消息数下降
    # 原始 system 提示词保留
    assert any(m["content"] == "SYS_PROMPT" for m in result)
    # 压缩摘要已注入
    assert any(m["role"] == "system" and "这是压缩摘要" in m["content"] for m in result)
    # 最近消息保留
    assert any(m.get("content") == "助手回复14" for m in non_system)
    # LLM 被调用了一次
    assert len(compressor.llm_client.calls) == 1


def test_compress_by_token_count(compressor, monkeypatch):
    """Token 触发（消息数远低于阈值）时也应实际执行压缩（v3.1 修复项）。"""
    patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 1000)
    patch_const(monkeypatch, "COMPRESS_TOKEN_THRESHOLD", 1)
    messages = make_messages(15, long_content=True)  # 30 条长消息
    result = compressor.compress_conversation(messages)

    non_system = [m for m in result if m["role"] != "system"]
    assert len(non_system) < 30
    assert any(m["role"] == "system" and "这是压缩摘要" in m["content"] for m in result)
    assert len(compressor.llm_client.calls) == 1
    # Token 数下降
    assert compressor.estimate_tokens(non_system) < compressor.estimate_tokens(
        [m for m in messages if m["role"] != "system"])


def test_compress_llm_failure_keeps_original(monkeypatch):
    comp = ConversationCompressor(
        llm_client=FakeLLMClient(error=RuntimeError("api down")),
        logger=Logger("TestCompressor", 0),
    )
    patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 10)
    messages = make_messages(15)
    result = comp.compress_conversation(messages)
    assert result == messages  # 失败时原样返回


def test_compress_without_llm_client_keeps_original(monkeypatch):
    comp = ConversationCompressor(llm_client=None)
    patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 10)
    messages = make_messages(15)
    assert comp.compress_conversation(messages) == messages


def test_compress_empty_messages(compressor):
    assert compressor.compress_conversation([]) == []


def test_compress_too_few_messages_skips(compressor, monkeypatch):
    patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 10)
    messages = make_messages(3)  # 6 条对话消息，压缩块必然 < 5
    assert compressor.compress_conversation(messages) == messages
    assert compressor.llm_client.calls == []


# ==================== _validate_message_structure ====================

def test_validate_removes_orphan_tool_messages(compressor):
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "t1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "ok"},
        {"role": "tool", "tool_call_id": "orphan", "content": "孤儿消息"},
    ]
    result = compressor._validate_message_structure(msgs)
    assert len(result) == 3
    assert all(m.get("tool_call_id") != "orphan" for m in result)


def test_validate_keeps_paired_tool_messages(compressor):
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "t1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "ok"},
    ]
    assert len(compressor._validate_message_structure(msgs)) == 3
