"""run_full_subagent 最大轮次（max_turns）行为回归测试（2026-08-19）

覆盖 aibox.py run_full_subagent 的轮次限制链路（think_with_limit 补丁）：
  - 超限拦截：任务轮 LLM 调用被限制在 max_turns 次，超限递归返回哨兵串
  - 哨兵串文本契约（"已达到最大对话轮次，任务结束。"）
  - 收尾报告：超限/正常结束均由 original_think 绕过轮次限制生成，作为返回值
  - 入参钳制：max_turns 为 0/None 回退默认 10，负数钳制下限 1
  - 超限不置 conversation_active=False（收尾保留完整上下文，aibox.py 763-765 注释）
  - 收尾失败兜底链：最后一条 assistant 消息 → "子代理未产生有效对话内容。"
  - SubAgentTool.execute 的 max_turns 参数透传（默认 10）
"""

import sys
import threading
import types
from unittest import mock

import pytest

# ---- 环境桩：websockets 未安装时保证 import aibox.py 不失败 ----
if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

from aibox import SubAgentTool, run_full_subagent  # noqa: E402

# 与 aibox.py think_with_limit 超限返回值一致的哨兵串（文本契约）
SENTINEL = "已达到最大对话轮次，任务结束。"
WRAP_UP_MARKER = "【收尾】"


def _make_fake_chat(task_rounds=999, final_answer=None, wrap_report="收尾报告"):
    """模拟 DeepSeekChat：think_and_respond 的"原始实现"。

    每次任务轮调用递归一次（模拟 aibox.py:1801 工具调用后递归继续，
    经由补丁后的 think_and_respond 走轮次计数）；累计 task_rounds 轮后
    若给出 final_answer 则正常结束（模拟 AI 返回纯文本）。task_rounds
    设为大数且不给 final_answer 时永不自行收敛——只有轮次限制能终止它。
    检测到收尾 prompt（含【收尾】标记）时返回 wrap_report。
    """
    fake = mock.MagicMock()
    state = {"n": 0, "task_inputs": [], "recurse_returns": []}

    def original_think(input_text, output_callback=None, is_continuation=False):
        if WRAP_UP_MARKER in input_text:
            return wrap_report
        state["task_inputs"].append(input_text)
        state["n"] += 1
        if final_answer is not None and state["n"] >= task_rounds:
            return final_answer
        ret = fake.think_and_respond("请基于以上工具执行结果继续回答。",
                                     output_callback, is_continuation=True)
        state["recurse_returns"].append(ret)
        return ret

    fake.think_and_respond = original_think
    fake.stats = {}
    fake.messages = []
    fake._cancel_event = threading.Event()
    fake.conversation_active = True
    fake._state = state
    return fake


def _run(fake, **kwargs):
    with mock.patch("aibox.DeepSeekChat", return_value=fake), \
         mock.patch("aibox.create_llm_client"), \
         mock.patch("aibox.SubprocessCommandExecutor"):
        return run_full_subagent("角色", "任务", None, **kwargs)


class TestMaxTurnsCutoff:
    def test_task_turns_capped_at_max_turns(self):
        """永不收敛的任务：任务轮 LLM 调用恰为 max_turns 次，第 max_turns+1 次被拦截"""
        fake = _make_fake_chat(task_rounds=999)
        result = _run(fake, max_turns=3)
        assert len(fake._state["task_inputs"]) == 3
        assert result == "收尾报告"

    def test_overrun_recursion_returns_sentinel(self):
        """超限那轮递归返回哨兵串（不调 LLM），并沿递归链上抛"""
        fake = _make_fake_chat(task_rounds=999)
        _run(fake, max_turns=3)
        assert fake._state["recurse_returns"]
        assert all(r == SENTINEL for r in fake._state["recurse_returns"])

    def test_wrap_up_runs_after_overrun(self):
        """超限后收尾报告仍被请求（original_think 绕过轮次限制），且为最终返回值"""
        fake = _make_fake_chat(task_rounds=999, wrap_report="最终报告")
        result = _run(fake, max_turns=2)
        assert result == "最终报告"

    def test_max_turns_zero_clamped_to_default(self):
        """max_turns=0 → 回退默认 10（对齐 max_tokens 的 0/空回退惯例）"""
        fake = _make_fake_chat(task_rounds=999)
        _run(fake, max_turns=0)
        assert len(fake._state["task_inputs"]) == 10

    def test_negative_max_turns_clamped_to_one(self):
        """max_turns 为负 → 钳制下限 1（至少执行一个任务轮，不再首轮即拦截）"""
        fake = _make_fake_chat(task_rounds=999)
        _run(fake, max_turns=-3)
        assert len(fake._state["task_inputs"]) == 1

    def test_max_turns_none_falls_back_to_default(self):
        """max_turns=None → 默认 10"""
        fake = _make_fake_chat(task_rounds=999)
        _run(fake, max_turns=None)
        assert len(fake._state["task_inputs"]) == 10

    def test_overrun_does_not_deactivate_conversation(self):
        """超限不置 conversation_active=False（否则收尾前上下文被重置清空）"""
        fake = _make_fake_chat(task_rounds=999)
        _run(fake, max_turns=2)
        assert fake.conversation_active is True


class TestNormalCompletion:
    def test_result_is_wrap_up_report_not_final_answer(self):
        """正常收敛时返回值同样是收尾报告（最终回答不作为工具结果返回）"""
        fake = _make_fake_chat(task_rounds=2, final_answer="最终答案")
        result = _run(fake, max_turns=10)
        assert len(fake._state["task_inputs"]) == 2
        # 第一轮递归的返回值即第 2 轮的最终回答（无哨兵串、无超限拦截）
        assert fake._state["recurse_returns"] == ["最终答案"]
        assert result == "收尾报告"

    def test_sentinel_never_appears_when_within_limit(self):
        """未达上限：任何递归都不返回哨兵串"""
        fake = _make_fake_chat(task_rounds=3, final_answer="最终答案")
        _run(fake, max_turns=10)
        assert SENTINEL not in fake._state["recurse_returns"]


class TestWrapUpFallback:
    def test_empty_wrap_up_falls_back_to_last_assistant_message(self):
        """收尾返回空 → 兜底取最后一条有内容的 assistant 消息"""
        fake = _make_fake_chat(task_rounds=1, final_answer="x", wrap_report="")
        fake.messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "中间回答"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "最终回答"},
        ]
        assert _run(fake, max_turns=10) == "【子代理最终回答】\n最终回答"

    def test_no_wrap_up_and_no_messages(self):
        """收尾为空且无任何 assistant 消息 → 固定兜底文案"""
        fake = _make_fake_chat(task_rounds=1, final_answer="x", wrap_report="")
        assert _run(fake, max_turns=10) == "子代理未产生有效对话内容。"

    def test_wrap_up_exception_falls_back(self):
        """收尾调用抛异常 → 不中断，走 assistant 消息兜底"""
        fake = _make_fake_chat(task_rounds=1, final_answer="x")
        original = fake.think_and_respond

        def boom_on_wrap_up(input_text, output_callback=None, is_continuation=False):
            if WRAP_UP_MARKER in input_text:
                raise RuntimeError("wrap-up boom")
            return original(input_text, output_callback, is_continuation)

        fake.think_and_respond = boom_on_wrap_up
        fake.messages = [{"role": "assistant", "content": "最终回答"}]
        assert _run(fake, max_turns=10) == "【子代理最终回答】\n最终回答"


class TestSubAgentToolMaxTurns:
    def test_max_turns_passed_through(self):
        """SubAgentTool.execute 的 max_turns 透传给 run_full_subagent"""
        tool = SubAgentTool(logger=None)
        with mock.patch("aibox.run_full_subagent", return_value="ok") as m:
            tool.execute(role="r", task="t", max_turns=4)
        assert m.call_args.kwargs["max_turns"] == 4

    def test_max_turns_default_is_10(self):
        tool = SubAgentTool(logger=None)
        with mock.patch("aibox.run_full_subagent", return_value="ok") as m:
            tool.execute(role="r", task="t")
        assert m.call_args.kwargs["max_turns"] == 10
