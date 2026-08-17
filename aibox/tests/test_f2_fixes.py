#!/usr/bin/env python3
"""F2 修复回归测试：N6 registry 超时清理 + N4 reader 线程收尾。

N6：流式转发超时后必须清理 tool.streaming_output / tool.output_queue，
     防止僵尸 daemon 线程经实例属性把输出混入后续会话队列。
N4：_read_with_limit 超时/宽限 break 后必须 close stdout + join reader，
     防止 Windows shell=True 孙进程持管道场景下 reader 线程与 fd 泄漏。
"""

import queue
import threading
import time

import pytest

from subsystems.executors import SubprocessCommandExecutor
from tools.base import Tool
from tools.registry import ToolRegistry


# ---------- N6：registry 超时路径清理 ----------

class _StuckStreamTool(Tool):
    """execute_streaming 永不结束（模拟卡死的工具，如等待外部资源）"""

    def get_name(self):
        return "stuck_tool"

    def get_description(self):
        return "test only"

    def get_parameters(self):
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        time.sleep(30)
        return "should not reach"

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        self.output_queue = output_queue
        self.streaming_output = True
        try:
            time.sleep(30)  # 卡死，永不产出 complete
        finally:
            self.streaming_output = False


def test_registry_timeout_cleans_tool_state():
    """转发总超时后：返回超时消息，且工具实例属性被清理"""
    reg = ToolRegistry()
    reg.stream_tool_timeout = 1  # 测试调小（默认 3600）
    tool = _StuckStreamTool()
    reg.register(tool)
    reg.set_output_queue(queue.Queue())

    t0 = time.time()
    result = reg.execute_tool("stuck_tool")
    elapsed = time.time() - t0

    assert "超时" in result, f"应返回超时消息，实际: {result}"
    assert elapsed < 10, f"应 ~1s 返回，实际 {elapsed:.1f}s"
    # N6 核心：超时路径不得跳过清理
    assert tool.output_queue is None, "超时后 tool.output_queue 必须置 None（防僵尸输出串流）"
    assert tool.streaming_output is False, "超时后 tool.streaming_output 必须复位"


def test_registry_normal_path_cleans_tool_state():
    """正常流式完成后：清理同样执行，结果透传"""
    reg = ToolRegistry()

    class _OkTool(_StuckStreamTool):
        def get_name(self):
            return "ok_tool"

        def execute_streaming(self, output_queue: queue.Queue, **kwargs):
            self.output_queue = output_queue
            self.streaming_output = True
            try:
                output_queue.put(("line", "hello"))
                output_queue.put(("complete", "done-result"))
            finally:
                pass

    tool = _OkTool()
    reg.register(tool)
    reg.set_output_queue(queue.Queue())

    result = reg.execute_tool("ok_tool")
    assert result == "done-result"
    assert tool.output_queue is None
    assert tool.streaming_output is False


# ---------- N4：reader 线程收尾 ----------

class _FakeStdout:
    """模拟管道 stdout，可配置阻塞时长"""

    def __init__(self, block_seconds: float = 8):
        self.closed = False
        self._block = block_seconds

    def readline(self):
        time.sleep(self._block)  # > join 宽限 2s 即模拟"无 EOF 阻塞"
        return ""

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, block_seconds: float = 8):
        self.stdout = _FakeStdout(block_seconds)

    def poll(self):
        return None  # 进程"活着"

    def terminate(self):
        pass

    def kill(self):
        pass


def test_read_with_limit_timeout_returns_promptly():
    """超时 break 后 reader 仍阻塞：只做有限 join，不 close、不挂死。

    CPython 流锁决定了此时 close(stdout) 会阻塞到 readline 返回，
    故收尾策略为放弃等待（daemon 兜底），主流程必须及时返回。
    """
    ex = SubprocessCommandExecutor()
    proc = _FakeProcess(block_seconds=8)

    t0 = time.time()
    output, timeout_occurred, truncated = ex._read_with_limit(
        proc, timeout=1, start_time=time.time())
    elapsed = time.time() - t0

    assert timeout_occurred is True
    assert truncated is False
    # 超时 1s + join 宽限 2s，绝不能等到 block 8s
    assert elapsed < 5, f"应收尾后及时返回，实际 {elapsed:.1f}s"


def test_read_with_limit_grace_path_returns_promptly():
    """shell 死亡宽限 break 后 reader 仍阻塞：同样及时返回"""
    ex = SubprocessCommandExecutor()
    proc = _FakeProcess(block_seconds=8)
    proc.poll = lambda: 0  # shell 已死，但 reader 无 EOF（孙进程持管道）

    t0 = time.time()
    output, timeout_occurred, truncated = ex._read_with_limit(
        proc, timeout=0, start_time=time.time())  # timeout=0 不触发超时
    elapsed = time.time() - t0

    assert timeout_occurred is False
    # 宽限 1s + join 2s 上界，不能等到 block 8s
    assert elapsed < 5, f"宽限+收尾应 ~3s 内返回，实际 {elapsed:.1f}s"


def test_read_with_limit_closes_stdout_after_normal_eof():
    """reader 正常收到 EOF 退出后：显式 close stdout 释放管道 fd"""
    ex = SubprocessCommandExecutor()
    proc = _FakeProcess(block_seconds=0)  # readline 立即返回 ""（EOF）

    output, timeout_occurred, truncated = ex._read_with_limit(
        proc, timeout=5, start_time=time.time())

    assert timeout_occurred is False
    assert output == ""
    assert proc.stdout.closed is True, "reader 正常退出后必须 close stdout 释放 fd"
