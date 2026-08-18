"""aibox 第四轮审计 Batch I 修复回归测试（2026-08-18，审计报告 §十二）

覆盖：
  - N9：prompt 写入内容长度上限（持久化注入面约束）
  - Q7b：load_conversation 越界路径拒绝（realpath 前缀校验）
  - F5：run_light_subagent 取消传播（流式可中断 + 父已取消早退）
  - Q5：提醒时间格式异常的任务按到期返回（不再静默丢弃）
  - Q8：对话文件原子写（无 .tmp 残留、JSON 完整）
  - F6：WebSocketHandler 命令专用线程池存在且可释放
  - limit=0 语义修正（None 才不加 LIMIT）

Q4（busy_timeout 10s）为时序性行为（默认 5s 等待已能通过短竞争），
不做慢速单测；F9（--once 硬超时）为进程级行为，由 CLI 集成面覆盖。
"""

import os
import queue
import sqlite3
import sys
import threading
import time
import types
from pathlib import Path
from unittest import mock

import pytest

# ---- 环境桩：websockets 未安装时保证 import aibox.py 不失败 ----
if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

import aibox  # noqa: E402
from aibox import run_light_subagent  # noqa: E402

from subsystems.db import MemoryDatabase, MemoDatabase  # noqa: E402
from subsystems.prompt_manager import PromptManager  # noqa: E402
from subsystems.conversation_history import ConversationHistory  # noqa: E402
from tools.prompt_tools import (  # noqa: E402
    UpdateIdentityTool,
    UpdateSoulTool,
    SaveMemoryAndEndConversationTool,
)


# ==================== N9：prompt 写入长度上限 ====================

class TestN9PromptContentCap:
    def test_update_identity_oversized_rejected(self, tmp_path, monkeypatch):
        """manager 层兜底：超长身份内容拒绝写入"""
        pm = PromptManager()
        with mock.patch.object(pm, "_prompts_dir", str(tmp_path)):
            assert pm.update_identity("x" * (PromptManager.MAX_CONTENT_LENGTH + 1)) is False
            assert not (tmp_path / pm.IDENTITY_FILE).exists()

    def test_update_identity_tool_friendly_error(self, tmp_path):
        """工具层：超长内容返回明确提示而非写入"""
        pm = PromptManager()
        with mock.patch.object(pm, "_prompts_dir", str(tmp_path)):
            tool = UpdateIdentityTool(pm, None)
            r = tool.execute(content="x" * (PromptManager.MAX_CONTENT_LENGTH + 1))
        assert "内容过长" in r

    def test_update_soul_tool_friendly_error(self, tmp_path):
        pm = PromptManager()
        with mock.patch.object(pm, "_prompts_dir", str(tmp_path)):
            tool = UpdateSoulTool(pm, None)
            r = tool.execute(content="y" * (PromptManager.MAX_CONTENT_LENGTH + 1))
        assert "内容过长" in r

    def test_save_memory_status_oversized_rejected(self, tmp_path):
        """save_memory_and_end_conversation：状态内容超长拒绝"""
        pm = PromptManager()
        with mock.patch.object(pm, "_prompts_dir", str(tmp_path)):
            tool = SaveMemoryAndEndConversationTool(
                exit_callback=lambda *a: None,
                memory_db=None, prompt_manager=pm, logger=None)
            r = tool.execute(summary="这是一段足够长的对话总结" * 20,
                             current_status="x" * PromptManager.MAX_CONTENT_LENGTH)
        assert "状态内容过长" in r

    def test_normal_content_still_written(self, tmp_path):
        pm = PromptManager()
        with mock.patch.object(pm, "_prompts_dir", str(tmp_path)):
            assert pm.update_identity("正常身份内容") is True
            assert "正常身份内容" in (tmp_path / pm.IDENTITY_FILE).read_text(encoding="utf-8")


# ==================== Q7b：load_conversation 路径校验 ====================

class TestQ7bLoadConversationBoundary:
    def test_traversal_rejected(self, tmp_path):
        h = ConversationHistory(save_dir=str(tmp_path / "save"), load_history=False)
        secret = tmp_path / "secret.json"
        secret.write_text('{"leak": true}', encoding="utf-8")
        assert h.load_conversation("../secret.json") is None

    def test_normal_file_still_loads(self, tmp_path):
        h = ConversationHistory(save_dir=str(tmp_path / "save"), load_history=False)
        h.current_conversation = {
            "id": "1", "start_time": "2026-08-18 00:00:00", "end_time": None,
            "topic": "t", "messages": [{"role": "user", "content": "hi"}],
            "tool_calls": [], "stats": {},
        }
        h.save_current_conversation()
        h.archive_current_conversation()
        archives = list(h.archive_dir.glob("conversation_*.json"))
        assert archives
        loaded = h.load_conversation(archives[0].name)
        assert loaded is not None and loaded["id"] == "1"


# ==================== F5：run_light_subagent 取消传播 ====================

class _BlockingLightClient:
    """流式产出首段后卡住，直到 cancel() 才中断的伪 LLM 客户端"""

    model = "fake-model"
    default_temperature = None

    def __init__(self):
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def stream_chat(self, messages, **kwargs):
        yield {"type": "content", "text": "开头"}
        while not self.cancelled.wait(timeout=0.1):
            pass
        raise RuntimeError("连接已中断（cancel）")


class TestF5LightSubagentCancel:
    def test_cancelled_before_start_returns_early(self):
        """父事件已置位 → 直接返回取消提示，不创建客户端"""
        parent = threading.Event()
        parent.set()
        with mock.patch("aibox.create_llm_client") as mk:
            r = run_light_subagent("r", "t", None, cancel_event=parent)
        assert "取消" in r
        mk.assert_not_called()

    def test_cancel_during_stream_aborts(self):
        """流式中父级取消 → 观察者线程 cancel 客户端 → 立即中断返回"""
        parent = threading.Event()
        fake = _BlockingLightClient()
        with mock.patch("aibox.create_llm_client", return_value=fake):
            result_holder = {}

            def _run():
                result_holder["r"] = run_light_subagent(
                    "r", "t", None, cancel_event=parent)

            th = threading.Thread(target=_run, daemon=True)
            th.start()
            time.sleep(0.5)  # 等待首段产出、观察者就绪
            parent.set()
            th.join(timeout=5)
        assert not th.is_alive()  # 未挂死
        assert "失败" in result_holder.get("r", "")

    def test_express_emotion_registered_with_parent_event(self):
        """DeepSeekChat 注册 ExpressEmotionTool 时注入自身取消事件"""
        from test_once_mode import _make_ai
        ai = _make_ai()
        tool = next((t for t in ai.tool_registry.get_all_tools()
                     if t.get_name() == "express_emotion"), None)
        assert tool is not None
        assert tool.cancel_event is ai._cancel_event


# ==================== Q5：格式异常任务不丢弃 ====================

class TestQ5MalformedReminderTime:
    def test_malformed_time_task_returned_as_due(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        # J1（审计 2026-08-19）：add_memo 已拒绝畸形时间（入库校验），
        # 模拟校验落地前的存量脏数据需绕过 add_memo 直接 SQL 插入
        import sqlite3
        with sqlite3.connect(str(tmp_path / "m.db")) as conn:
            cur = conn.cursor()
            cur.execute('''INSERT INTO memos
                (title, content, created_at, reminder_time, is_completed, source,
                 repeat_type, repeat_interval, repeat_interval_unit, is_immediate)
                VALUES (?, ?, ?, ?, 0, 'test', 'none', 0, 'days', 0)''',
                ("格式异常", "c", "2026-08-18 00:00:00", "不是时间"))
            mid = cur.lastrowid
        pending = db.get_pending_memos(before_time="2026-08-18 00:00:00")
        assert any(m["id"] == mid for m in pending)

    def test_normal_pending_still_filtered(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        db.add_memo(title="未来任务", content="c", reminder_time="2099-01-01 00:00:00")
        pending = db.get_pending_memos(before_time="2026-08-18 00:00:00")
        assert pending == []


# ==================== Q8：对话文件原子写 ====================

class TestQ8AtomicWrites:
    def test_no_tmp_leftover_and_valid_json(self, tmp_path):
        h = ConversationHistory(save_dir=str(tmp_path / "save"), load_history=False)
        h.current_conversation = {
            "id": "1", "start_time": "2026-08-18 00:00:00", "end_time": None,
            "topic": "t", "messages": [], "tool_calls": [], "stats": {},
        }
        h.save_current_conversation()
        assert h.current_file.exists()  # 文件名可能被用户配置覆盖，用实例属性
        import json
        json.loads(h.current_file.read_text(encoding="utf-8"))  # 不抛即合法 JSON
        h.archive_current_conversation()  # 归档后 current 文件按设计移除
        assert list(Path(tmp_path).rglob("*.tmp")) == []


# ==================== F6：WS 命令专用线程池 ====================

class TestF6CmdExecutor:
    def test_handler_has_dedicated_executor(self):
        from aibox import WebSocketHandler
        h = WebSocketHandler(ai_manager=None)
        assert h._cmd_executor is not None
        h.shutdown()
        assert h._cmd_executor is None


# ==================== limit=0 语义 ====================

class TestLimitZeroSemantics:
    def test_pending_memos_limit_zero_returns_none(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        db.add_memo(title="t", content="c", reminder_time="2026-01-01 00:00:00")
        assert db.get_pending_memos(limit=0, before_time="2026-08-18 00:00:00") == []

    def test_immediate_memos_limit_zero_returns_none(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        db.add_memo(title="t", content="c", is_immediate=True)
        assert db.get_immediate_memos(limit=0) == []
