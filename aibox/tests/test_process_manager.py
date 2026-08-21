"""后台进程管理器（subsystems.process_manager）与 bg_* 工具回归测试

覆盖三决策语义与安全边界：
  - bg 生命周期：启动立即返回 / 惰性收割 / kill 幂等 / TTL 过期
  - 输出缓冲：tail/since 游标续读 / 环形淘汰提示 / 单行截断 / 拉取字节上限
  - 上限：运行中进程数上限 / 总条目 LRU 淘汰
  - 收尾：shutdown 杀非 detach、保留 detach；atexit 单例共享
  - 工具层：参数规整（字符串句柄）/ 双闸禁用 / 注册表分发

跨平台：长驻进程用 sys.executable -c "time.sleep(...)"（echo/ping 不可移植）。
Windows 注意：shell=True 下 terminate 只杀 shell 包装进程（与执行器 L9
回退语义一致），被杀判定以 popen.poll() 为准。
"""

import queue
import sys
import time
import types

import pytest

if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

from subsystems import ProcessManager, get_process_manager, reset_process_manager  # noqa: E402
from subsystems.process_manager import MAX_LINE_BYTES  # noqa: E402
from tools import (  # noqa: E402
    ToolRegistry,
    BgRunTool, BgWriteTool, BgListTool, BgOutputTool, BgKillTool,
)


PY = sys.executable


@pytest.fixture()
def echo_srv(tmp_path):
    """行协议回环服务器：stdin 读一行 → stdout 回 ECHO:行，EOF 退出。
    交互式 I/O（bg_write/watermark/优雅终止）测试的标准对端。"""
    script = (
        "import sys\n"
        "while True:\n"
        "    line = sys.stdin.readline()\n"
        "    if not line:\n"
        "        break\n"
        "    sys.stdout.write('ECHO:' + line.strip() + '\\n')\n"
        "    sys.stdout.flush()\n"
    )
    f = tmp_path / "echo_srv.py"
    f.write_text(script, encoding="utf-8")
    return f'"{PY}" "{f}"'


def _py(code: str) -> str:
    """跨平台 python 命令（code 内只用单引号）"""
    return f'"{PY}" -c "{code}"'


ECHO_10 = _py("for i in range(10): print('line-%d' % i)")
SLEEP_10 = _py("import time; time.sleep(10)")
HELLO = _py("print('hello-bg')")


@pytest.fixture()
def mgr():
    """每测试独立管理器（不走单例），收尾兜底清进程"""
    m = ProcessManager()
    try:
        yield m
    finally:
        m.shutdown()


def _wait_status(m: ProcessManager, handle: int, status: str, timeout: float = 10.0):
    """轮询等待句柄达到指定状态"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for p in m.list_procs():
            if p["handle"] == handle and p["status"] == status:
                return p
        time.sleep(0.1)
    return None


# ==================== 启动与生命周期 ====================

class TestStart:
    def test_start_returns_handle_immediately(self, mgr):
        t0 = time.time()
        r = mgr.start(SLEEP_10, name="sleeper")
        assert r["ok"] is True
        assert time.time() - t0 < 2  # 立即返回，不等进程结束
        assert isinstance(r["handle"], int) and r["pid"] > 0
        assert r["name"] == "sleeper"

    def test_start_default_name_from_command(self, mgr):
        r = mgr.start(HELLO)
        assert r["ok"] is True
        # 默认名称 = 命令前 40 字符
        assert r["name"] == HELLO.strip()[:40]

    def test_start_rejects_empty_command(self, mgr):
        assert mgr.start("   ")["ok"] is False
        assert mgr.start(None)["ok"] is False

    def test_start_rejects_bad_name(self, mgr):
        # 名称含控制字符（M5 惯例：防终端/前端注入）
        assert mgr.start(HELLO, name="bad\x1b[31m")["ok"] is False
        assert mgr.start(HELLO, name="x" * 65)["ok"] is False

    def test_start_missing_binary_returns_error(self, mgr):
        r = mgr.start("definitely-not-a-command-xyz --version")
        # shell 本身能启动（rc=127/9009），故启动成功、退出码非零
        assert r["ok"] is True
        p = _wait_status(mgr, r["handle"], "exited")
        assert p is not None
        assert p["exit_code"] != 0


class TestLifecycle:
    def test_exited_status_and_exit_code(self, mgr):
        r = mgr.start(HELLO)
        p = _wait_status(mgr, r["handle"], "exited")
        assert p is not None
        assert p["exit_code"] == 0

    def test_kill_running_process(self, mgr):
        r = mgr.start(SLEEP_10)
        time.sleep(0.5)  # 等进程真正起来
        k = mgr.kill(r["handle"])
        assert k["ok"] is True and k["status"] == "killed"
        assert mgr._procs[r["handle"]].popen.poll() is not None

    def test_kill_is_idempotent_on_exited(self, mgr):
        r = mgr.start(HELLO)
        assert _wait_status(mgr, r["handle"], "exited") is not None
        k = mgr.kill(r["handle"])
        assert k["ok"] is True and k["already_finished"] is True

    def test_kill_missing_handle(self, mgr):
        assert mgr.kill(999)["ok"] is False

    def test_ttl_expiry(self):
        m = ProcessManager(max_lifetime=0.5)
        try:
            r = m.start(SLEEP_10)
            time.sleep(0.8)
            # list 触发惰性 sweep
            assert _wait_status(m, r["handle"], "expired", timeout=2) is not None
        finally:
            m.shutdown()

    def test_max_processes_limit(self):
        m = ProcessManager(max_processes=2)
        try:
            assert m.start(SLEEP_10)["ok"] is True
            assert m.start(SLEEP_10)["ok"] is True
            r3 = m.start(SLEEP_10)
            assert r3["ok"] is False and "上限" in r3["error"]
        finally:
            m.shutdown()

    def test_max_entries_evicts_oldest_exited(self):
        m = ProcessManager(max_entries=2)
        try:
            h1 = m.start(HELLO)["handle"]
            h2 = m.start(HELLO)["handle"]
            assert _wait_status(m, h1, "exited") is not None
            assert _wait_status(m, h2, "exited") is not None
            # 第三条触发最老已退出条目（#1）LRU 淘汰
            h3 = m.start(HELLO)["handle"]
            assert h3 > 0
            handles = {p["handle"] for p in m.list_procs()}
            assert h1 not in handles and h2 in handles and h3 in handles
        finally:
            m.shutdown()


# ==================== 输出缓冲与游标 ====================

class TestOutput:
    def test_tail_and_since_cursor(self, mgr):
        r = mgr.start(ECHO_10)
        assert _wait_status(mgr, r["handle"], "exited") is not None
        time.sleep(0.3)  # reader 排空末尾行

        out = mgr.get_output(r["handle"], tail=3)
        assert out["ok"] is True
        assert out["lines"] == 3
        assert "line-9" in out["output"] and "line-6" not in out["output"]

        full = mgr.get_output(r["handle"], since=0)
        assert full["lines"] == 10
        assert full["next_cursor"] == 10

        inc = mgr.get_output(r["handle"], since=full["next_cursor"])
        assert inc["lines"] == 0 and inc["next_cursor"] == 10

    def test_since_before_eviction_reports_gap(self):
        m = ProcessManager(buffer_per_process=2048)
        try:
            r = m.start(_py("for i in range(50): print('pad-%03d-%s' % (i, 'x'*80))"))
            assert _wait_status(m, r["handle"], "exited") is not None
            time.sleep(0.3)
            out = m.get_output(r["handle"], since=0)
            assert out["ok"] is True
            assert out["evicted"] > 0  # 早期行被环形淘汰
            assert out["lines"] < 50
        finally:
            m.shutdown()

    def test_single_line_truncated(self, mgr):
        r = mgr.start(_py("print('A' * (1024*64))"))
        assert _wait_status(mgr, r["handle"], "exited") is not None
        time.sleep(0.3)
        out = mgr.get_output(r["handle"], tail=5)
        assert "单行截断" in out["output"]
        # 缓冲内存预算不被超长单行击穿
        entry = mgr._procs[r["handle"]]
        assert entry.buffer_bytes <= MAX_LINE_BYTES + 1024

    def test_fetch_byte_limit_truncates(self, mgr):
        r = mgr.start(_py("for i in range(100): print('y' * 200)"))
        assert _wait_status(mgr, r["handle"], "exited") is not None
        time.sleep(0.3)
        out = mgr.get_output(r["handle"], since=0, max_bytes=4096)
        assert out["truncated"] is True

    def test_missing_handle(self, mgr):
        assert mgr.get_output(999)["ok"] is False

    def test_invalid_since(self, mgr):
        r = mgr.start(HELLO)
        assert mgr.get_output(r["handle"], since="abc")["ok"] is False


# ==================== 交互式 I/O（stdin 管道 + watermark 对齐） ====================

class TestInteractive:
    def test_write_roundtrip_watermark_alignment(self, mgr, echo_srv):
        """核心对齐语义：bg_output(since=watermark) = 本条输入的响应且不含回显"""
        h = mgr.start(echo_srv)["handle"]
        r = mgr.write(h, "hello", wait_lines=1, wait_timeout=10)
        assert r["ok"] is True
        assert "ECHO:hello" in r["wait"]["output"]
        assert r["wait"]["timed_out"] is False

        out = mgr.get_output(h, since=r["watermark"])
        assert "ECHO:hello" in out["output"]
        assert "[stdin]" not in out["output"]  # 回显行序号 == watermark，不包含

    def test_echo_line_visible_in_tail(self, mgr, echo_srv):
        """[stdin] 回显进环形缓冲，tail 视图可见完整交互时序"""
        h = mgr.start(echo_srv)["handle"]
        assert mgr.write(h, "abc", wait_lines=1)["ok"] is True
        out = mgr.get_output(h, tail=10)
        assert "[stdin] abc" in out["output"]
        assert "ECHO:abc" in out["output"]

    def test_write_wait_sync_roundtrip(self, mgr, echo_srv):
        """wait_lines 同步等待：一步问答（expect 式）"""
        h = mgr.start(echo_srv)["handle"]
        r = mgr.write(h, "1+1", wait_lines=1)
        assert r["wait"]["lines"] == 1
        assert r["wait"]["output"].strip() == "ECHO:1+1"
        assert r["wait"]["next_cursor"] == r["watermark"] + 1

    def test_write_wait_timeout_on_silent_process(self, mgr):
        """不读不响应的进程：写入成功（管道缓冲），等待如实超时"""
        h = mgr.start(SLEEP_10)["handle"]
        r = mgr.write(h, "ping", wait_lines=1, wait_timeout=0.5)
        assert r["ok"] is True
        assert r["wait"]["timed_out"] is True
        assert r["wait"]["lines"] == 0

    def test_write_to_exited_process(self, mgr):
        h = mgr.start(HELLO)["handle"]
        assert _wait_status(mgr, h, "exited") is not None
        assert mgr.write(h, "x")["ok"] is False

    def test_write_rejects_empty_and_oversize(self, mgr, echo_srv):
        h = mgr.start(echo_srv)["handle"]
        assert mgr.write(h, "")["ok"] is False
        assert mgr.write(h, None)["ok"] is False
        assert mgr.write(h, "A" * (64 * 1024 + 1))["ok"] is False

    def test_stdin_eof_graceful_kill(self, mgr, echo_srv):
        """三级优雅终止第一级：关 stdin → 回环服务器见 EOF 自行退出"""
        h = mgr.start(echo_srv)["handle"]
        time.sleep(0.5)
        k = mgr.kill(h)  # 不 force：先关 stdin
        assert k["ok"] is True
        assert mgr._procs[h].popen.poll() is not None
        assert _wait_status(mgr, h, "killed") is not None

    def test_write_after_kill_rejected(self, mgr, echo_srv):
        h = mgr.start(echo_srv)["handle"]
        assert mgr.kill(h)["ok"] is True
        assert mgr.write(h, "late")["ok"] is False

    def test_log_file_tee(self, mgr, monkeypatch, tmp_path):
        """log_file 全档镜像：输出与 [stdin] 回显均落盘，可被原生工具读取"""
        from common import constants as cm
        monkeypatch.setattr(type(cm), "WORKSPACE_DIR",
                            property(lambda self: str(tmp_path)))
        script = tmp_path / "srv.py"
        script.write_text(
            "import sys\n"
            "for line in sys.stdin:\n"
            "    sys.stdout.write('ECHO:' + line.strip() + '\\n')\n"
            "    sys.stdout.flush()\n",
            encoding="utf-8")
        cmd = f'"{PY}" "{script}"'
        r = mgr.start(cmd, log_file=True)
        assert r["ok"] is True and r["log_file"] is not None
        assert mgr.write(r["handle"], "tee-me", wait_lines=1)["ok"] is True
        mgr.kill(r["handle"])
        log = (tmp_path / f"bg_{r['handle']}.log").read_text(encoding="utf-8")
        assert "[stdin] tee-me" in log
        assert "ECHO:tee-me" in log

    def test_bg_write_tool_flow(self, mgr, echo_srv):
        """工具层冒烟：BgWriteTool 参数规整 + 对齐读取提示"""
        assert BgRunTool(manager=mgr).execute(command=echo_srv).startswith("✅")
        out = BgWriteTool(manager=mgr).execute(handle="1", text="hi",
                                                wait_lines="1", wait_timeout=10)
        assert "ECHO:hi" in out
        assert "watermark" in out
        # 描述里的行缓冲指引（给模型看的适用前提）
        assert "PYTHONUNBUFFERED" in BgWriteTool(manager=mgr).get_description()

    def test_registry_dispatch_bg_write(self, mgr, echo_srv):
        reg = ToolRegistry()
        reg.register_many([BgRunTool(manager=mgr), BgWriteTool(manager=mgr)],
                          is_builtin=True)
        assert reg.execute_tool("bg_run", command=echo_srv).startswith("✅")
        r = reg.execute_tool("bg_write", handle=1, text="via-reg",
                             wait_lines=1, wait_timeout=10)
        assert "ECHO:via-reg" in r


# ==================== 退出收尾 ====================

class TestShutdown:
    def test_shutdown_kills_non_detach_keeps_detach(self):
        m = ProcessManager()
        try:
            h1 = m.start(SLEEP_10)["handle"]              # 常规：退出时终止
            h2 = m.start(SLEEP_10, detach=True)["handle"]  # detach：保留
            time.sleep(0.5)
            e1, e2 = m._procs[h1], m._procs[h2]

            r = m.shutdown()
            assert r["killed"] == 1 and r["detached"] == 1
            assert e1.popen.poll() is not None          # 常规进程已终止
            assert e2.popen.poll() is None              # detach 进程仍存活
            assert m.list_procs() == []                 # 注册表已清空（detach 仅注销）

            # 幂等
            assert m.shutdown() == {"killed": 0, "detached": 0}
            # 收尾后拒绝新进程
            assert m.start(HELLO)["ok"] is False
            # 清理 detach 测试进程
            e2.popen.kill()
        finally:
            m.shutdown()

    def test_singleton_shared_across_callers(self):
        m1 = get_process_manager()
        m2 = get_process_manager()
        assert m1 is m2  # 跨会话/跨子代理共享同一注册表
        reset_process_manager(None)
        m3 = get_process_manager()
        assert m3 is not m1
        m3.shutdown()
        reset_process_manager(None)


# ==================== 工具层 ====================

class TestTools:
    def test_bg_run_and_output_flow(self, mgr):
        run = BgRunTool(manager=mgr)
        r = run.execute(command=HELLO, proc_name="hello")
        assert r.startswith("✅") and "#" in r
        handle = 1
        assert _wait_status(mgr, handle, "exited") is not None
        time.sleep(0.3)

        out = BgOutputTool(manager=mgr).execute(handle=handle, since=0)
        assert "hello-bg" in out
        assert "next_cursor" in out

    def test_bg_run_string_handle_coercion(self, mgr):
        """LLM 可能传字符串数字句柄（W 批输入规整惯例）"""
        run = BgRunTool(manager=mgr)
        assert run.execute(command=HELLO).startswith("✅")
        assert _wait_status(mgr, 1, "exited") is not None
        out = BgOutputTool(manager=mgr).execute(handle="1", tail=5)
        assert "hello-bg" in out

    def test_bg_run_missing_command(self, mgr):
        r = BgRunTool(manager=mgr).execute()
        assert "缺少" in r

    def test_bg_list_empty_and_filled(self, mgr):
        lst = BgListTool(manager=mgr)
        assert "没有后台进程" in lst.execute()
        mgr.start(SLEEP_10, name="sleeper")
        r = lst.execute()
        assert "sleeper" in r and "运行中 1" in r

    def test_bg_kill_flow(self, mgr):
        mgr.start(SLEEP_10, name="sleeper")
        time.sleep(0.5)
        r = BgKillTool(manager=mgr).execute(handle=1)
        assert r.startswith("✅") and "已终止" in r

    def test_disabled_gate_blocks_all(self, mgr, monkeypatch):
        from common import constants
        monkeypatch.setattr(type(constants), "BG_PROCESSES_ENABLED",
                            property(lambda self: False))
        assert "禁用" in BgRunTool(manager=mgr).execute(command=HELLO)
        assert "禁用" in BgListTool(manager=mgr).execute()
        assert "禁用" in BgOutputTool(manager=mgr).execute(handle=1)
        assert "禁用" in BgKillTool(manager=mgr).execute(handle=1)

    def test_registry_dispatch(self, mgr):
        """4 个工具经 ToolRegistry 正常分发（worker 注册链路冒烟）"""
        reg = ToolRegistry()
        reg.register_many([
            BgRunTool(manager=mgr), BgListTool(manager=mgr),
            BgOutputTool(manager=mgr), BgKillTool(manager=mgr),
        ], is_builtin=True)
        r = reg.execute_tool("bg_run", command=HELLO, proc_name="smoke")
        assert r.startswith("✅")
        assert _wait_status(mgr, 1, "exited") is not None
        time.sleep(0.3)
        r = reg.execute_tool("bg_output", handle=1, since=0)
        assert "hello-bg" in r
        r = reg.execute_tool("bg_list")
        assert "smoke" in r


def test_worker_only_tools_includes_bg():
    """编排者误调拦截表包含 bg 工具（worker 执行面边界）"""
    from aibox import WORKER_ONLY_TOOLS
    assert {"bg_run", "bg_write", "bg_list", "bg_output", "bg_kill"} <= WORKER_ONLY_TOOLS
