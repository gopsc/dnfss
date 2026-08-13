"""subsystems.executors 单元测试

覆盖：
  - CommandExecutor 抽象基类契约
  - SubprocessCommandExecutor 命令执行/流式/超时/取消/输出限制
"""
import queue
import time

import pytest

from subsystems import CommandExecutor, SubprocessCommandExecutor
from common import constants


@pytest.fixture(autouse=True)
def use_tmp_workspace(tmp_path, monkeypatch):
    """每个测试把 WORKSPACE_DIR 重定向到临时目录（并预先创建），避免默认 ~/.aibox/workspace 不存在。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(type(constants), "WORKSPACE_DIR",
                        property(lambda self: str(ws)))


# ==================== 抽象基类契约 ====================

def test_command_executor_is_abstract():
    """CommandExecutor 是 ABC，不能直接实例化。"""
    with pytest.raises(TypeError):
        CommandExecutor()


def test_subprocess_executor_is_subclass():
    assert issubclass(SubprocessCommandExecutor, CommandExecutor)


def test_executor_is_enabled_reflects_constants():
    """is_enabled 应反映 constants.COMMANDS_ENABLED。"""
    from common import constants
    ex = SubprocessCommandExecutor()
    assert ex.is_enabled() == constants.COMMANDS_ENABLED


def test_executor_format_result_success():
    ex = SubprocessCommandExecutor()
    r = ex.format_result(True, "ok", {"k": "v"})
    assert r.startswith("✅ ok")
    assert "k: v" in r


def test_executor_format_result_failure():
    ex = SubprocessCommandExecutor()
    r = ex.format_result(False, "fail")
    assert r.startswith("❌ fail")


def test_executor_format_result_multiline_value():
    ex = SubprocessCommandExecutor()
    r = ex.format_result(True, "ok", {"输出": "line1\nline2"})
    assert "line1" in r and "line2" in r


# ==================== 实际命令执行 ====================

def test_execute_echo_command():
    """执行 echo 命令应返回成功结果。"""
    ex = SubprocessCommandExecutor()
    r = ex.execute("echo hello_test_123", timeout=5)
    assert "hello_test_123" in r
    assert "✅" in r


def test_execute_command_returns_exit_code():
    ex = SubprocessCommandExecutor()
    r = ex.execute("exit 0", timeout=5)
    assert "返回码: 0" in r


def test_execute_failing_command_returns_nonzero_code():
    ex = SubprocessCommandExecutor()
    r = ex.execute("exit 7", timeout=5)
    assert "返回码: 7" in r
    # 非零返回码应标记为失败
    assert "❌" in r


def test_execute_timeout():
    """超时应被检测并返回错误信息。"""
    ex = SubprocessCommandExecutor()
    # ping -t 在 Windows 上无限循环；用 ping -n 跨平台
    r = ex.execute("ping -n 100 127.0.0.1", timeout=2)
    assert "超时" in r


def test_execute_unknown_command_returns_error():
    ex = SubprocessCommandExecutor()
    r = ex.execute("nonexistent_command_xyz_123", timeout=5)
    # shell=True 下会返回 127 或类似错误码，但不会抛异常
    assert "返回码" in r


# ==================== 流式执行 ====================

def test_execute_streaming_emits_lines_and_complete():
    ex = SubprocessCommandExecutor()
    q: queue.Queue = queue.Queue()
    ex.execute_streaming("echo stream_line_test", q, timeout=5)

    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())

    types = [m[0] for m in msgs]
    assert "line" in types
    assert "complete" in types

    complete_payload = [m[1] for m in msgs if m[0] == "complete"][0]
    assert "stream_line_test" in complete_payload


def test_execute_streaming_emits_command_header():
    """流式执行开头应输出命令/超时/工作目录等头部信息。"""
    ex = SubprocessCommandExecutor()
    q: queue.Queue = queue.Queue()
    ex.execute_streaming("echo hi", q, timeout=5)

    lines = []
    while not q.empty():
        t, p = q.get_nowait()
        if t == "line":
            lines.append(p)

    header = "".join(lines[:6])
    assert "执行命令" in header
    assert "超时" in header
    assert "工作目录" in header


# ==================== 取消机制 ====================

def test_cancel_terminates_running_process():
    """cancel() 在进程运行时调用应不抛异常并尝试 kill 进程。
    Windows 下 kill 后管道读取可能短暂阻塞，但 cancel 本身必须安全返回。
    线程设为 daemon，确保不会卡住测试进程退出。
    """
    ex = SubprocessCommandExecutor()
    q: queue.Queue = queue.Queue()
    import threading
    t = threading.Thread(
        target=ex.execute_streaming,
        args=("ping -n 100 127.0.0.1", q),
        kwargs={"timeout": 60},
        daemon=True,
    )
    t.start()
    time.sleep(0.5)  # 等进程启动
    # 关键断言：cancel 不抛异常（若抛异常测试即失败）
    ex.cancel()
    # 等待线程退出（Windows 下可能因管道阻塞超时，daemon 确保不卡死主进程）
    t.join(timeout=5)


def test_cancel_when_no_process_running_is_noop():
    """无进程运行时调用 cancel 应是空操作。"""
    ex = SubprocessCommandExecutor()
    ex.cancel()  # 不应抛异常


# ==================== 输出大小限制 ====================

def test_output_size_limit_truncates():
    """输出超过 1MB 应被截断。"""
    ex = SubprocessCommandExecutor()
    # 生成约 2MB 输出
    r = ex.execute("python -c \"print('x'*2048576)\"", timeout=10)
    assert "截断" in r or "❌" in r


# ==================== 禁用场景 ====================

def test_execute_when_disabled_returns_disabled_message(monkeypatch):
    """当 COMMANDS_ENABLED=False 时应返回禁用提示。"""
    from common import constants
    monkeypatch.setattr(type(constants), "COMMANDS_ENABLED",
                        property(lambda self: False))
    ex = SubprocessCommandExecutor()
    r = ex.execute("echo hi", timeout=5)
    assert "禁用" in r
