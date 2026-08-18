"""aibox 第四轮审计 Batch H 修复回归测试（2026-08-18，审计报告 §十一）

覆盖：
  - H1：子代理共享父取消事件后首轮 think 不再清除父事件（N5 回归）+ 父已取消时跳过启动
  - M3：调度器任务级超时兜底（AI think 卡死不再停摆调度循环）
  - M4：get_recent_memories(limit=None) 使用默认上限（不再全表加载）
  - M5：memo delete 物理删除（此前只标记完成）
  - M6：PromptManager.reload 原子替换（并发读者不再读到空/半截列表）
  - L1：read_file 的 sed 参数强转 int（防引号注入）
  - L2：set_proxy 落盘失败如实报告（不再静默报"已保存"）
  - L3：SSE 非 UTF-8 行不中断流（errors=replace）
  - L4：persona 路径校验解析符号链接（软链越界拒绝）
  - L5：1MB 截断不再产生半个字符（�）
  - L6：compressor 空内容压缩不再除零
  - L7：InstallSkillTool 指引命令路径引用（防拆词/注入）
  - L8：ask_user 缺参返回明确错误（不再 TypeError）
  - L10：web 工具仅允许 http/https（file:// 等拒绝）
  - L11：registry 日志打码改包含匹配（apiKey/access_token 等变体）
  - L12：FailoverClient 日志 Bearer 令牌脱敏

M1（/reload_prompts 移线程池）/M2（技能扫描启动前预热）为 async WebSocket /
启动路径，依赖事件循环与真实连接对象，由既有 WebSocket 测试面与启动逻辑回归。
"""

import asyncio
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
from aibox import DeepSeekChat, TaskScheduler, run_full_subagent  # noqa: E402

from llm_client import DeepSeekClient, FailoverClient, _sanitize_log_msg  # noqa: E402
from subsystems.db import MemoryDatabase, MemoDatabase  # noqa: E402
from subsystems.prompt_manager import PromptManager  # noqa: E402
from subsystems.conversation_compressor import ConversationCompressor  # noqa: E402
from common import constants  # noqa: E402

from test_conversation_compressor import FakeLLMClient  # noqa: E402
from test_once_mode import _make_ai  # noqa: E402


def _fake_sub_chat():
    fake = mock.MagicMock()
    fake.think_and_respond = mock.MagicMock(return_value="任务完成报告")
    fake.stats = {}
    fake.messages = []
    fake._cancel_event = threading.Event()
    return fake


# ==================== H1：子代理取消事件清除回归 ====================

class TestH1SubagentCancelClear:
    def test_shared_event_not_cleared_on_first_think(self):
        """共享父事件后首轮 think 不清除事件——父级已置位的取消信号不被吞掉"""
        ai = _make_ai()
        parent_event = threading.Event()
        parent_event.set()
        ai._cancel_event = parent_event
        ai._shared_cancel_event = True
        with pytest.raises(InterruptedError):
            ai._think_and_respond_impl("hi")
        assert parent_event.is_set()

    def test_own_event_still_cleared_on_first_think(self):
        """未共享（普通会话）时首轮仍清除自身事件——原语义不回归"""
        import contextlib
        ai = _make_ai()
        ai._cancel_event.set()
        with contextlib.suppress(Exception):
            ai._think_and_respond_impl("hi")
        assert not ai._cancel_event.is_set()

    def test_subagent_not_started_when_parent_already_cancelled(self):
        """父事件已置位 → 直接返回取消提示，不启动子代理"""
        fake = _fake_sub_chat()
        parent_event = threading.Event()
        parent_event.set()
        with mock.patch("aibox.DeepSeekChat", return_value=fake), \
             mock.patch("aibox.create_llm_client"), \
             mock.patch("aibox.SubprocessCommandExecutor"):
            result = run_full_subagent("r", "t", None, cancel_event=parent_event)
        assert "取消" in result
        fake.think_and_respond.assert_not_called()


# ==================== M3：调度器任务超时 ====================

class _BlockingAI:
    """think 卡死直到被 cancel 的伪 AI（模拟 LLM 挂起）"""

    def __init__(self):
        self.cancelled = threading.Event()
        self.cancel_called = 0

    def cancel(self):
        self.cancel_called += 1
        self.cancelled.set()

    def think_and_respond(self, input_text, output_callback=None,
                          is_continuation=False):
        self.cancelled.wait(timeout=15)
        raise InterruptedError("用户中断")


class _FakeMemoDB:
    def get_memo(self, memo_id):
        return None


class TestM3SchedulerTimeout:
    def test_stuck_task_times_out_and_scheduler_continues(self):
        from aibox import ConfigManager
        loop = asyncio.new_event_loop()
        try:
            scheduler = TaskScheduler(loop=loop, logger=None)
            fake_ai = _BlockingAI()
            memo = {"id": 7, "title": "卡死任务", "content": "内容", "trigger_count": 0}
            orig_get = ConfigManager.get

            def fake_get(self, key, default=None):
                # 只把任务超时压到 0.3s，其余配置读取走原实现（防止
                # DATETIME_FORMAT 等属性读到错误类型）
                if key == "scheduler.task_timeout":
                    return 0.3
                return orig_get(self, key, default)

            with mock.patch.object(ConfigManager, "get", fake_get), \
                 mock.patch.object(scheduler, "_create_task_ai", return_value=fake_ai):
                result = loop.run_until_complete(
                    scheduler.execute_task_as_conversation(memo, _FakeMemoDB()))
            assert "超时" in result
            assert fake_ai.cancel_called >= 1
        finally:
            loop.close()


# ==================== M4/M5：记忆上限与任务删除 ====================

class TestM4M5Db:
    def test_recent_memories_none_capped(self, tmp_path, monkeypatch):
        """limit=None 不再全表加载，使用默认上限（对齐 search_memories）"""
        from test_conversation_compressor import patch_const
        db = MemoryDatabase(db_path=str(tmp_path / "m.db"))
        for i in range(7):
            db.add_memory(topic=f"t{i}", summary=f"s{i}", content="c",
                          start_time="2026-08-18 00:00:00",
                          end_time="2026-08-18 00:00:01", importance=1)
        patch_const(monkeypatch, "MEMORY_MAX_SEARCH_RESULTS", 5)
        res = db.get_recent_memories(limit=None)
        assert len(res) == 5

    def test_delete_memo_physically_removes(self, tmp_path):
        """delete_memo 真实删除行（此前 complete_memo 只标记完成）"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        mid = db.add_memo(title="待删任务", content="内容")
        assert db.get_memo(mid) is not None
        assert db.delete_memo(mid) is True
        assert db.get_memo(mid) is None
        assert db.delete_memo(mid) is False  # 已不存在

    def test_memo_tool_delete_uses_physical_delete(self, tmp_path):
        """MemoTool delete 走 delete_memo：行消失而非仅标记完成"""
        from tools import MemoTool
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        mid = db.add_memo(title="重复任务", content="内容", repeat_type="daily")
        tool = MemoTool(db, None)
        result = tool.execute(action="delete", memo_id=mid, confirm=True)
        assert "已删除" in result
        assert db.get_memo(mid) is None
        stats = db.get_memo_stats()
        assert stats["total"] == 0


# ==================== M6：PromptManager 原子 reload ====================

class TestM6PromptReloadAtomic:
    def test_reload_builds_new_list_then_swaps(self):
        """reload 在本地新列表构建完成后才原子替换——读者始终看到完整列表"""
        pm = PromptManager()
        old_list = pm._prompts
        seen = []

        def fake_load(target):
            seen.append(target)
            target.append({"file": "X_test.md", "content": "y"})

        with mock.patch.object(pm, "_load_prompts", side_effect=fake_load):
            n = pm.reload()
        assert len(seen) == 1
        assert seen[0] is not old_list          # 构建用的是新列表
        assert pm._prompts is seen[0]           # 完成后才替换
        assert n == 1


# ==================== L1：read_file 参数强转 ====================

class TestL1ReadFileCoercion:
    def test_non_integer_lines_rejected(self, tmp_path):
        from tools import ReadFileTool
        f = tmp_path / "f.txt"
        f.write_text("line1\nline2\n", encoding="utf-8")
        tool = ReadFileTool()
        r = tool.execute(filepath=str(f), start_line="1'; rm -rf /; echo '",
                         end_line=5)
        assert "参数错误" in r

    def test_valid_integers_still_work(self, tmp_path):
        from tools import ReadFileTool
        f = tmp_path / "f.txt"
        f.write_text("line1\nline2\n", encoding="utf-8")
        tool = ReadFileTool()
        r = tool.execute(filepath=str(f), start_line=1, end_line=2)
        assert "sed -n" in r and "参数错误" not in r


# ==================== L2：set_proxy 落盘失败如实报告 ====================

class TestL2ProxySaveFailure:
    def test_set_proxy_returns_false_on_save_failure(self, tmp_path):
        from common import ConfigManager
        cm = ConfigManager()
        cm._config_path = str(tmp_path / "config.json")
        # J5（审计 2026-08-19）：写路径改为原子写（mkstemp+os.replace），
        # 磁盘失败改由 patch 原子写函数模拟（builtins.open 已不在路径上）
        with mock.patch("common.config.atomic_write_text",
                        side_effect=PermissionError("disk ro")):
            ok = cm.set_proxy("127.0.0.1", 1080, save=True)
        assert ok is False

    def test_proxy_tool_reports_save_failure(self, tmp_path):
        from tools import SetProxyTool
        tool = SetProxyTool(None)
        # J5：磁盘失败经原子写函数模拟
        with mock.patch("common.config.atomic_write_text",
                        side_effect=PermissionError("disk ro")):
            r = tool.execute(host="127.0.0.1", port=1080, save=True,
                             skip_check=True, enabled=True)
        assert "保存失败" in r


# ==================== L3：SSE 非 UTF-8 行不中断流 ====================

class TestL3SseDecode:
    def test_non_utf8_line_does_not_break_stream(self):
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = [
            b"data: \xff\xfe",
            b'data: {"choices":[{"delta":{"content":"ok"}}]}',
            b"data: [DONE]",
        ]
        with mock.patch("llm_client.requests.post", return_value=resp):
            events = list(c.stream_chat([{"role": "user", "content": "hi"}]))
        assert any(e.get("type") == "content" and e.get("text") == "ok"
                   for e in events)


# ==================== L4：persona 软链越界 ====================

class TestL4PersonaSymlink:
    def test_symlink_outside_skills_dir_rejected(self, tmp_path, monkeypatch):
        from test_conversation_compressor import patch_const
        from aibox import ExpressEmotionTool
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("秘密内容", encoding="utf-8")
        (skills_dir / "evil.md").symlink_to(outside)
        tool = ExpressEmotionTool()
        patch_const(monkeypatch, "SKILLS_DIR", str(skills_dir))
        content = tool._load_persona("evil", "")
        assert content == ""

    def test_normal_persona_still_loads(self, tmp_path, monkeypatch):
        from test_conversation_compressor import patch_const
        from aibox import ExpressEmotionTool
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "happy.md").write_text("快乐人格", encoding="utf-8")
        tool = ExpressEmotionTool()
        patch_const(monkeypatch, "SKILLS_DIR", str(skills_dir))
        content = tool._load_persona("happy", "")
        assert content == "快乐人格"


# ==================== L5：截断无半个字符 ====================

class TestL5NoPartialChar:
    def test_truncated_output_has_no_replacement_char(self):
        from subsystems.executors import SubprocessCommandExecutor
        ex = SubprocessCommandExecutor()
        r = ex.execute("python3 -c \"print('中' * 600000)\"", timeout=60)
        assert "截断" in r
        assert "\ufffd" not in r

    @pytest.mark.skipif(os.name != "posix", reason="Linux 进程组语义")
    def test_grandchild_holding_pipe_is_reaped(self):
        """`(sleep N &) ; echo done` 形态：shell 退出后孙进程持管道写端，
        宽限窗口到期强杀进程组——不悬挂 reader、孙进程被清理"""
        import subprocess as sp
        from subsystems.executors import SubprocessCommandExecutor
        marker = f"sleep {12340}"
        ex = SubprocessCommandExecutor()
        r = ex.execute(f"({marker} &) ; echo done", timeout=8)
        assert "done" in r
        leftover = sp.run(["pgrep", "-f", marker],
                          capture_output=True, text=True).stdout.strip()
        assert leftover == ""


# ==================== L6：compressor 除零 ====================

class TestL6CompressorDivZero:
    def test_empty_content_compress_no_div0(self, monkeypatch):
        comp = ConversationCompressor(llm_client=FakeLLMClient(),
                                      logger=None)
        from test_conversation_compressor import patch_const
        patch_const(monkeypatch, "COMPRESS_MESSAGE_THRESHOLD", 10)
        messages = [{"role": "system", "content": "SYS"}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": ""}
            for i in range(15)
        ]
        result = comp.compress_conversation(messages)
        assert isinstance(result, list)


# ==================== L7：InstallSkillTool 路径引用 ====================

class TestL7SkillPathQuoting:
    def test_guide_commands_quote_paths(self):
        from tools import InstallSkillTool
        tool = InstallSkillTool()
        r = tool.execute(skill_path="/tmp/a b; rm -rf /")
        assert "ls -l '/tmp/a b; rm -rf /'" in r
        assert "cp '/tmp/a b; rm -rf /' " in r


# ==================== L8：ask_user 缺参 ====================

class TestL8AskUser:
    def test_missing_question_returns_error(self):
        from tools import AskUserTool
        tool = AskUserTool()
        assert "需要提供问题" in tool.execute()

    def test_question_passed_through(self):
        from tools import AskUserTool
        tool = AskUserTool()
        assert "测试问题" in tool.execute(question="测试问题")


# ==================== L10：web 工具 scheme 白名单 ====================

class TestL10WebScheme:
    def test_file_url_rejected(self):
        from tools import SimpleWebCrawlerTool
        tool = SimpleWebCrawlerTool()
        r = tool.execute(action="get", url="file:///etc/shadow")
        assert "仅支持 http/https" in r

    def test_http_url_accepted(self):
        from tools import SimpleWebCrawlerTool
        tool = SimpleWebCrawlerTool()
        r = tool.execute(action="get", url="https://example.com")
        assert "curl" in r and "仅支持" not in r


# ==================== L11：registry 打码包含匹配 ====================

class TestL11SanitizeSubstring:
    def test_variant_keys_redacted(self):
        from tools.registry import _sanitize_kwargs
        out = _sanitize_kwargs({
            "apiKey": "x", "access_token": "y", "passwd": "z",
            "url": "https://ok",
        })
        assert out == {"apiKey": "***", "access_token": "***",
                       "passwd": "***", "url": "https://ok"}

    def test_normal_keys_untouched(self):
        from tools.registry import _sanitize_kwargs
        out = _sanitize_kwargs({"filepath": "/tmp/a", "question": "hi"})
        assert out == {"filepath": "/tmp/a", "question": "hi"}


# ==================== L12：failover 日志 Bearer 脱敏 ====================

class TestL12LogSanitize:
    def test_sanitize_msg_redacts_bearer(self):
        msg = "瞬时故障: HTTP 401: Bearer sk-abc123xyz"
        out = _sanitize_log_msg(msg)
        assert "sk-abc123xyz" not in out
        assert "Bearer ***" in out

    def test_failover_log_redacts_bearer(self):
        logger = mock.MagicMock()
        c = FailoverClient([], logger=logger)
        c._log("warning", "瞬时故障（Bearer sk-secret456）")
        logged = logger.warning.call_args[0][0]
        assert "sk-secret456" not in logged
        assert "Bearer ***" in logged
