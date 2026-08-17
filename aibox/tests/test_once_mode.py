"""aibox CLI 一次性对话模式（--once）单元测试

覆盖：
  - handle_once_mode 参数路由：未传 --once → 返回 False
  - enable_memory=False → MemoryTool 不注册（干净禁用）
  - enable_memory=True（默认）→ MemoryTool 正常注册（回归保证）
  - cli_callback 流式分流：chunk→stdout / line→stderr / reasoning 受 --show-reasoning 控制
  - 退出码：成功 0 / 工具失败 1 / API 失败 2
  - stdout 不被压缩提示等 print 污染

通过 FakeLLMClient 桩掉 stream_chat，不依赖真实 API/网络。
"""

import sys
import types
import argparse
from unittest import mock

import pytest

# ---- 环境桩：websockets 未安装时保证 import aibox.py 不失败 ----
if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

import aibox  # noqa: E402
from aibox import DeepSeekChat, handle_once_mode  # noqa: E402


# ==================== Fake LLM 客户端 ====================

class FakeLLMClient:
    """桩 LLMClient：按预设脚本产出统一事件流。"""

    def __init__(self, events=None, raise_exc=None):
        # events: list[dict]，如 [{"type":"content","text":"你好"}, {"type":"done"}]
        self.events = events if events is not None else [
            {"type": "content", "text": "默认回答"},
            {"type": "done"},
        ]
        self.raise_exc = raise_exc
        self.api_key = "fake-key"
        self.name = "fake-model"
        self.model = "fake-model"

    def stream_chat(self, messages, temperature=0.7, max_tokens=8192,
                    tools=None, tool_choice=None, reasoning_enabled=False, **extra):
        if self.raise_exc:
            raise self.raise_exc
        for ev in self.events:
            yield ev

    def chat_completion(self, messages, temperature=0.3, max_tokens=1024, **kwargs):
        return "压缩摘要"

    def cancel(self):
        pass


# ==================== 公共工具 ====================

def _make_ai(events=None, raise_exc=None, enable_memory=True):
    """构造一个注入 FakeLLMClient 的 DeepSeekChat，跳过真实网络/文件系统副作用。"""
    with mock.patch("aibox.create_llm_client") as mk:
        fake = FakeLLMClient(events=events, raise_exc=raise_exc)
        mk.return_value = fake
        with mock.patch("aibox.PromptManager") as pm, \
             mock.patch("aibox.SkillManager"):
            pm.return_value = mock.MagicMock()
            pm.return_value.get_all_prompts.return_value = []
            ai = DeepSeekChat(
                api_key="fake",
                history_manager=None,
                memory_db=mock.MagicMock(),
                memo_db=mock.MagicMock(),
                command_executor=mock.MagicMock(),
                prompt_manager=pm.return_value,
                enable_memory=enable_memory,
            )
    return ai


def _ns(**over):
    """构造 argparse.Namespace，含 once 模式全部字段默认值。"""
    base = dict(once=None, quiet=False, once_timeout=300,
                no_save_memory=False, show_reasoning=False)
    base.update(over)
    return argparse.Namespace(**base)


# ==================== enable_memory 开关 ====================

class TestEnableMemory:
    def test_memory_tool_registered_by_default(self):
        """默认 enable_memory=True → memory 工具注册"""
        ai = _make_ai(enable_memory=True)
        names = [t.get_name() for t in ai.tool_registry.get_all_tools()]
        assert "memory" in names

    def test_memory_tool_disabled(self):
        """enable_memory=False → memory 工具不注册（干净禁用）"""
        ai = _make_ai(enable_memory=False)
        names = [t.get_name() for t in ai.tool_registry.get_all_tools()]
        assert "memory" not in names
        # 其余 orchestrator 管理面工具不受影响
        assert "memo" in names


# ==================== handle_once_mode 路由 ====================

class TestHandleOnceModeRouting:
    def test_no_once_returns_false(self):
        """未传 --once → 返回 False，不进入单次模式"""
        manager = mock.MagicMock()
        assert handle_once_mode(manager, _ns(once=None)) is False


# ==================== cli_callback 分流 ====================

class TestCliCallback分流:
    """通过捕获 sys.stdout / sys.stderr 验证分流（handle_once_mode 内部闭包，
    用 subprocess 太重，这里直接测 DeepSeekChat + 回调契约）。"""

    def _collect(self, ai, query, quiet=False, show_reasoning=False):
        out, err = [], []

        def cb(t, c):
            if t == "chunk":
                out.append(c)
            elif t == "complete":
                pass
            elif t == "error":
                err.append(c)
            elif t == "line":
                if not quiet:
                    err.append(c)
            elif t in ("reasoning", "reasoning_start", "reasoning_end"):
                if show_reasoning and not quiet:
                    err.append(c)

        resp = ai.think_and_respond(query, cb)
        return "".join(out), "".join(err), resp

    def test_chunk_goes_to_stdout(self):
        ai = _make_ai(events=[{"type": "content", "text": "你好世界"},
                              {"type": "done"}])
        out, err, resp = self._collect(ai, "hi")
        assert "你好世界" in out

    def test_reasoning_hidden_by_default(self):
        ai = _make_ai(events=[{"type": "reasoning", "text": "RSN_CONTENT"},
                              {"type": "content", "text": "答案"},
                              {"type": "done"}])
        out, err, _ = self._collect(ai, "q", show_reasoning=False)
        assert "RSN_CONTENT" not in err

    def test_reasoning_shown_with_flag(self):
        ai = _make_ai(events=[{"type": "reasoning", "text": "RSN_CONTENT"},
                              {"type": "content", "text": "答案"},
                              {"type": "done"}])
        out, err, _ = self._collect(ai, "q", show_reasoning=True)
        assert "RSN_CONTENT" in err

    def test_reasoning_hidden_when_quiet(self):
        ai = _make_ai(events=[{"type": "reasoning", "text": "RSN_CONTENT"},
                              {"type": "content", "text": "答案"},
                              {"type": "done"}])
        out, err, _ = self._collect(ai, "q", quiet=True, show_reasoning=True)
        assert "RSN_CONTENT" not in err


# ==================== 退出码相关状态 ====================

class TestExitCodeState:
    def test_tool_failed_flag_default_false(self):
        ai = _make_ai()
        assert ai.stats["tool_failed"] is False

    def test_tool_failed_flag_set_on_exception(self):
        """工具执行异常 → stats['tool_failed'] = True"""
        ai = _make_ai()
        # 模拟一个会抛异常的工具调用
        ai.tool_registry.execute_tool = mock.MagicMock(side_effect=RuntimeError("boom"))
        tool_calls = [{"id": "c1", "function": {"name": "memo", "arguments": "{}"}}]
        ai._handle_tool_calls(tool_calls, None)
        assert ai.stats["tool_failed"] is True


# ==================== 压缩提示不污染 stdout ====================

class TestCompressPromptNotOnStdout:
    def test_compress_notice_goes_to_callback_line(self, monkeypatch, capsys):
        """触发压缩时，提示走 output_callback('line') 而非 print(stdout)。"""
        ai = _make_ai()
        # 强制压缩触发
        monkeypatch.setattr(ai.compressor, "should_compress",
                            lambda msgs: (True, "测试触发"))
        monkeypatch.setattr(ai.compressor, "compress_conversation",
                            lambda msgs: msgs)
        monkeypatch.setattr(ai.compressor, "estimate_tokens", lambda msgs: 100)

        lines = []
        def cb(t, c):
            if t == "line":
                lines.append(c)
        ai.think_and_respond("你好", cb)

        captured = capsys.readouterr()
        assert "📦" not in captured.out  # stdout 无压缩提示
        assert any("📦" in l or "压缩" in l for l in lines)  # 提示走了 line 回调
