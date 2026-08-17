"""aibox 第三轮审计 Batch G 修复回归测试（2026-08-18，审计报告 §9.4）

覆盖：
  - P1：非瞬时 HTTP 错误响应体截断（密钥/超大错误页不进异常栈）
  - P2：Retry-After 解析上限 cap 3600s
  - P3：fallback 档案缺 model → 跳过并告警（与缺 Key 行为对齐）
  - P5：LLMClient 死状态 _cancelled 已删除
  - P6：fallback 列表重复项去重
  - N2：drain 线程批次结束即回收（不再泄漏常驻线程）
  - N5：子代理共享父代理取消事件
  - N7：FTS5 关键词内双引号翻倍转义
  - N8：registry 流式转发受 TOOL_MAX_RESULT_LENGTH 约束
  - N10：mark_as_triggered 单事务（缺失 id 路径回归）
  - N12：persona 路径边界校验（拒绝越界路径）
  - N16：同秒归档不再覆盖（文件名加序号）

N3（/new 移线程池）/N13（broadcast）/N14（任务登记集合）为 async WebSocket
路径，依赖事件循环与真实连接对象，由现有 WebSocket 测试面回归，不在此单测。
"""

import os
import queue
import sys
import threading
import types
from unittest import mock

import pytest

# ---- 环境桩：websockets 未安装时保证 import aibox.py 不失败 ----
if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

import aibox  # noqa: E402
from aibox import DeepSeekChat, run_full_subagent  # noqa: E402

from llm_client import (  # noqa: E402
    DeepSeekClient,
    FailoverClient,
    _retry_after_of,
    create_llm_client,
)
from subsystems.db import MemoryDatabase, MemoDatabase  # noqa: E402
from subsystems.conversation_history import ConversationHistory  # noqa: E402
from tools.base import Tool  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402
from common import constants  # noqa: E402


# ==================== P1/P2：错误响应体截断 + Retry-After cap ====================

class TestP1P2LlmClient:
    def test_p1_non_transient_error_body_truncated(self):
        """400 响应体 1000 字符 → 异常消息仅含前 200 字符"""
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        resp = mock.MagicMock()
        resp.status_code = 400
        resp.text = "A" * 1000
        resp.headers = {}
        with mock.patch("llm_client.requests.post", return_value=resp):
            with pytest.raises(RuntimeError) as ei:
                list(c.stream_chat([{"role": "user", "content": "hi"}]))
        msg = str(ei.value)
        assert "HTTP 400" in msg
        assert len(msg) < 300  # 200 字符正文 + 状态行前缀
        assert msg.count("A") <= 200

    def test_p2_retry_after_capped(self):
        """异常/恶意 Retry-After=999999 → cap 到 3600s"""
        resp = mock.MagicMock()
        resp.headers = {"Retry-After": "999999"}
        assert _retry_after_of(resp) == 3600.0

    def test_p2_retry_after_normal_value_kept(self):
        resp = mock.MagicMock()
        resp.headers = {"Retry-After": "30"}
        assert _retry_after_of(resp) == 30.0

    def test_p2_retry_after_invalid_returns_none(self):
        resp = mock.MagicMock()
        resp.headers = {"Retry-After": "not-a-number"}
        assert _retry_after_of(resp) is None


# ==================== P3/P5/P6：fallback 链构造 ====================

def _g_cfg():
    """主档案 main 的 fallback 链：缺 model 的 broken + 重复 dupe + 正常 ok2"""
    return {
        "models": {
            "main": {
                "api_url": "https://a.example/v1/chat/completions",
                "model": "m1", "api_key_env": "MAIN_KEY",
                "fallback": ["nomodel", "dupe", "dupe", "ok2"],
            },
            "nomodel": {  # P3：缺 model 字段
                "api_url": "https://b.example/v1/chat/completions",
                "api_key_env": "B_KEY",
            },
            "dupe": {
                "api_url": "https://c.example/v1/chat/completions",
                "model": "c1", "api_key_env": "C_KEY",
            },
            "ok2": {
                "api_url": "https://d.example/v1/chat/completions",
                "model": "d1", "api_key_env": "D_KEY",
            },
        }
    }


class TestP3P5P6FallbackChain:
    def test_p3_p6_fallback_missing_model_skipped_and_deduped(self):
        """缺 model 的 fallback 跳过；重复项去重；链 = [main, dupe, ok2]"""
        env = {k: f"sk-{k.lower()}" for k in
               ("MAIN_KEY", "B_KEY", "C_KEY", "D_KEY")}
        with mock.patch.dict(os.environ, env):
            c = create_llm_client(_g_cfg(), model="main")
        assert isinstance(c, FailoverClient)
        names = [cl.name for cl in c._clients]
        assert names == ["main", "dupe", "ok2"]

    def test_p5_dead_cancel_flag_removed(self):
        """LLMClient 不再持有只写不读的 _cancelled 死状态"""
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        assert not hasattr(c, "_cancelled")
        c.cancel()  # 无在途响应时为安全 no-op


# ==================== N2：drain 线程生命周期 ====================

class _EchoTool(Tool):
    def get_name(self):
        return "g_echo"

    def get_description(self):
        return "测试用回显工具"

    def get_parameters(self):
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "ok"


class TestN2DrainThread:
    def test_drain_thread_reclaimed_after_batch(self):
        """_handle_tool_calls 返回后 drain 线程必须已退出（finally join）"""
        from test_once_mode import _make_ai
        ai = _make_ai()
        ai.tool_registry.register(_EchoTool(ai.logger))
        tool_calls = [{"id": "c1", "function": {"name": "g_echo", "arguments": "{}"}}]
        ai._handle_tool_calls(tool_calls, output_callback=lambda t, c: None)
        drains = [t for t in threading.enumerate() if t.name == "tool-output-drain"]
        assert drains == [] or all(not t.is_alive() for t in drains)

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_drain_thread_reclaimed_on_tool_exception(self):
        """工具执行抛异常时 drain 线程同样被回收（finally 路径）"""
        from test_once_mode import _make_ai

        class _BoomTool(_EchoTool):
            def get_name(self):
                return "g_boom"

            def execute(self, **kwargs):
                raise RuntimeError("boom")

        ai = _make_ai()
        ai.tool_registry.register(_BoomTool(ai.logger))
        tool_calls = [{"id": "c1", "function": {"name": "g_boom", "arguments": "{}"}}]
        ai._handle_tool_calls(tool_calls, output_callback=lambda t, c: None)
        drains = [t for t in threading.enumerate() if t.name == "tool-output-drain"]
        assert drains == [] or all(not t.is_alive() for t in drains)


# ==================== N5：子代理取消传播 ====================

def _fake_sub_chat():
    fake = mock.MagicMock()
    fake.think_and_respond = mock.MagicMock(return_value="任务完成报告")
    fake.stats = {}
    fake.messages = []
    fake._cancel_event = threading.Event()
    return fake


class TestN5SubagentCancel:
    def test_subagent_shares_parent_cancel_event(self):
        """cancel_event 传入 → 子代理取消事件被替换为父事件（检查点联动）"""
        fake = _fake_sub_chat()
        parent_event = threading.Event()
        with mock.patch("aibox.DeepSeekChat", return_value=fake), \
             mock.patch("aibox.create_llm_client"), \
             mock.patch("aibox.SubprocessCommandExecutor"):
            run_full_subagent("r", "t", None, cancel_event=parent_event)
        assert fake._cancel_event is parent_event

    def test_subagent_keeps_own_event_when_not_provided(self):
        """cancel_event=None → 兼容旧行为，子代理保留自身事件"""
        fake = _fake_sub_chat()
        own = fake._cancel_event
        with mock.patch("aibox.DeepSeekChat", return_value=fake), \
             mock.patch("aibox.create_llm_client"), \
             mock.patch("aibox.SubprocessCommandExecutor"):
            run_full_subagent("r", "t", None)
        assert fake._cancel_event is own

    def test_subagent_tool_registered_with_parent_event(self):
        """DeepSeekChat 注册 SubAgentTool 时注入自身 _cancel_event（N5 链路）"""
        from test_once_mode import _make_ai
        ai = _make_ai()
        tool = next((t for t in ai.tool_registry.get_all_tools()
                     if t.get_name() == "create_subagent"), None)
        assert tool is not None
        assert tool.cancel_event is ai._cancel_event


# ==================== N7：FTS5 引号转义 ====================

class TestN7FtsQuoteEscape:
    def test_keyword_with_quote_does_not_break_search(self, tmp_path):
        """关键词含双引号 → MATCH 语法合法（翻倍转义），不抛异常"""
        db = MemoryDatabase(db_path=str(tmp_path / "mem.db"))
        db.add_memory(topic="引号话题", summary="摘要",
                      content='含"引号"的记忆内容',
                      start_time="2026-08-18 00:00:00",
                      end_time="2026-08-18 00:00:01", importance=2)
        res = db.search_memories(['带"引号"的关键词'])
        assert isinstance(res, list)  # 不抛 OperationalError 即通过

    def test_normal_keyword_still_matches(self, tmp_path):
        """常规关键词检索行为不回归"""
        db = MemoryDatabase(db_path=str(tmp_path / "mem.db"))
        db.add_memory(topic="普通话题", summary="天气摘要",
                      content="今天天气晴朗",
                      start_time="2026-08-18 00:00:00",
                      end_time="2026-08-18 00:00:01", importance=2)
        res = db.search_memories(["天气"])
        assert len(res) >= 1


# ==================== N8：流式转发长度上限 ====================

class _BigStreamTool(Tool):
    """流式工具：发出 3 行 × 40000 字符（总 120000 > 100000 上限）"""

    def get_name(self):
        return "g_bigstream"

    def get_description(self):
        return "测试用超长流式工具"

    def get_parameters(self):
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "done"

    def execute_streaming(self, output_queue, **kwargs):
        for _ in range(3):
            output_queue.put(("line", "x" * 40000))
        output_queue.put(("complete", "done"))


class TestN8StreamingLimit:
    def test_streaming_forward_truncated_to_budget(self):
        registry = ToolRegistry()
        registry.register(_BigStreamTool())
        captured = queue.Queue()
        registry.set_output_queue(captured)

        result = registry.execute_tool("g_bigstream")

        lines = []
        while not captured.empty():
            msg_type, content = captured.get_nowait()
            if msg_type == "line":
                lines.append(content)
        budget = constants.TOOL_MAX_RESULT_LENGTH
        total = sum(len(ln) for ln in lines)
        # 截断标记行本身占少量额外字符（含提示文案）
        assert total <= budget + 200
        assert any("已停止转发" in ln or "已截断" in ln for ln in lines)
        assert result == "done"


# ==================== N10：mark_as_triggered 单事务 ====================

class TestN10MarkTriggered:
    def test_missing_id_returns_none_via_transaction(self, tmp_path):
        """事务内读取不存在行 → rollback + None（不再走独立 get_memo）"""
        db = MemoDatabase(db_path=str(tmp_path / "memo.db"))
        assert db.mark_as_triggered(99999) is None

    def test_single_shot_memo_completes(self, tmp_path):
        """一次性任务触发 → completed + 返回 None（无下次提醒）"""
        db = MemoDatabase(db_path=str(tmp_path / "memo.db"))
        mid = db.add_memo(title="一次性", content="c",
                          reminder_time="2026-08-18 00:00:00")
        assert mid is not None
        assert db.mark_as_triggered(mid) is None
        memo = db.get_memo(mid)
        assert memo["is_completed"] == 1
        assert memo["trigger_count"] == 1


# ==================== N12：persona 路径边界 ====================

class TestN12PersonaBoundary:
    def _tool(self):
        return aibox.ExpressEmotionTool()

    def test_traversal_persona_rejected(self, tmp_path):
        """persona 含 ../ 越界 → 拒绝（返回空串，不读文件）"""
        tool = self._tool()
        skills = tmp_path / "skills"
        skills.mkdir()
        with mock.patch.object(type(aibox.constants), "SKILLS_DIR",
                               new_callable=mock.PropertyMock(return_value=str(skills))):
            assert tool._load_persona("../../etc/passwd", None) == ""
            assert tool._load_persona("../outside", None) == ""

    def test_absolute_persona_rejected(self, tmp_path):
        """persona 为绝对路径 → 解析结果不在技能目录内，拒绝"""
        tool = self._tool()
        skills = tmp_path / "skills"
        skills.mkdir()
        with mock.patch.object(type(aibox.constants), "SKILLS_DIR",
                               new_callable=mock.PropertyMock(return_value=str(skills))):
            assert tool._load_persona(str(tmp_path / "evil"), None) == ""

    def test_normal_persona_loads(self, tmp_path):
        """合法 persona（技能目录内）正常加载"""
        tool = self._tool()
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "ok.md").write_text("人格内容", encoding="utf-8")
        with mock.patch.object(type(aibox.constants), "SKILLS_DIR",
                               new_callable=mock.PropertyMock(return_value=str(skills))):
            assert "人格内容" in tool._load_persona("ok", None)


# ==================== N16：同秒归档防覆盖 ====================

class TestN16ArchiveCollision:
    def test_same_second_archive_no_overwrite(self, tmp_path):
        """同一秒内两次归档 → 文件名加序号，互不覆盖"""
        hm = ConversationHistory(save_dir=str(tmp_path))
        hm.start_new_conversation(topic="t1")
        hm.add_message("user", "hi", speaker="u")
        p1 = hm.archive_current_conversation()

        hm.start_new_conversation(topic="t2")
        hm.add_message("user", "hi2", speaker="u")
        p2 = hm.archive_current_conversation()

        assert p1 != p2
        assert os.path.exists(p1) and os.path.exists(p2)
