"""tests.test_n_fixes - Batch N 修复单元测试

波次 3 平台兼容（2026-08-21，审计第八轮 T 系列）：
  - T3  basic_tools 平台感知引用 + Windows PowerShell/curl.exe 指引
  - T11 Windows 进程树收割（taskkill /T /F，executors + process_manager）
  - T25 MemoryDatabase 统一连接工厂（busy_timeout）
  - T26 SkillManager 原子 reload（中途不清空 self.skills）
  - T27 atomic_write_text 对 Windows PermissionError 短退避重试

波次 4 LLM 健壮性（2026-08-21）：
  - T10 调度器 wait_for 硬超时兜底
  - T19 FailoverClient 异常收窄（永久配置错误不转移不冷却）
  - T20 流式响应未收到 [DONE] 抛 TransientLLMError（不再静默截断）
  - T21 env 覆盖黑名单（LD_*/DYLD_*/PATH/PYTHONPATH 等劫持面）
  - T23 压缩后 tool 配对双向修复（assistant.tool_calls 失配剥离）
  （T14 FTS5 经复现证伪：JOIN 别名场景下表名 MATCH 正常命中，
   全链路中文检索长词走 trigram、短词走 LIKE 兜底，无需修复）

波次 5 权限隔离（2026-08-21，产品决策：T1 仅告警不加鉴权、T2 维持现状）：
  - T22 定时任务精简 profile（task：无 update_identity/update_soul/set_proxy）
       + ProcessManager owner 隔离（写/杀权限，观察面共享）
  - T-L17 model_config 按域名推荐 env 名（防 DeepSeek 凭据发往他厂）
  - T-L18 list_profiles 输出过滤控制字符（防手写 config ANSI 注入）

Windows 分支在 Linux 上以 mock.patch("os.name", "nt") 模拟验证命令生成
逻辑（不实际执行生成的命令）。
"""

import asyncio
import os
import sqlite3
import threading
from unittest import mock

import pytest
import requests

from common.atomic import atomic_write_text
from subsystems import MemoryDatabase, SkillManager
from subsystems.executors import SubprocessCommandExecutor, filter_env_overrides
from tools.basic_tools import (
    ListFilesTool,
    ReadFileTool,
    SimpleWebCrawlerTool,
    WriteFileTool,
    _quote_arg,
)


# ==================== T3：平台感知引用 ====================

class TestQuoteArg:
    def test_posix_uses_shlex(self):
        quoted, err = _quote_arg("/tmp/my file.txt")
        assert err is None
        assert quoted == "'/tmp/my file.txt'"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX 引用语义")
    def test_posix_specials_escaped(self):
        quoted, err = _quote_arg("a`b$c$(d)\\e")
        assert err is None
        assert quoted == "'a`b$c$(d)\\e'"

    def test_win_wraps_double_quotes(self):
        with mock.patch("os.name", "nt"):
            quoted, err = _quote_arg(r"C:\Program Files\my app\doc.txt")
        assert err is None
        assert quoted == r'"C:\Program Files\my app\doc.txt"'

    def test_win_rejects_double_quote(self):
        with mock.patch("os.name", "nt"):
            quoted, err = _quote_arg('evil" & del /f /s /q C:\\')
        assert quoted == ""
        assert err is not None and "双引号" in err

    def test_win_rejects_cmd_var_expansion(self):
        with mock.patch("os.name", "nt"):
            quoted, err = _quote_arg("https://evil.com/?x=%PATH%")
        assert quoted == ""
        assert err is not None and "注入" in err

    def test_win_allows_url_percent_encoding(self):
        """URL 编码（%20/%E4 等）非 %变量名% 形态，不拒绝。"""
        with mock.patch("os.name", "nt"):
            quoted, err = _quote_arg("https://example.com/a%20b?x=1&y=2")
        assert err is None
        assert quoted == '"https://example.com/a%20b?x=1&y=2"'


# ==================== T3：Windows 指引命令生成 ====================

class TestReadFileWindows:
    def test_head_powershell(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello\n" * 30, encoding="utf-8")
        with mock.patch("os.name", "nt"):
            r = ReadFileTool().execute(filepath=str(f), head=True, max_lines=10)
        assert "powershell" in r and "Get-Content" in r
        assert "run_command" in r
        assert "sed -n" not in r and "head -n" not in r

    def test_tail_powershell(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello\n" * 30, encoding="utf-8")
        with mock.patch("os.name", "nt"):
            r = ReadFileTool().execute(filepath=str(f), tail=True, max_lines=5)
        assert "Get-Content" in r and "-Tail 5" in r

    def test_range_powershell(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello\n" * 30, encoding="utf-8")
        with mock.patch("os.name", "nt"):
            r = ReadFileTool().execute(filepath=str(f), start_line=5, end_line=15)
        assert "Select-Object -Skip 4 -First 11" in r

    def test_posix_unchanged(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello\n" * 30, encoding="utf-8")
        r = ReadFileTool().execute(filepath=str(f), start_line=1, end_line=5)
        assert "sed -n '1,5p'" in r


class TestWriteFileWindows:
    def test_base64_set_content(self, tmp_path):
        target = tmp_path / "sub" / "out.txt"
        content = "line1\nline2\n含中文 \"quoted\" & special"
        with mock.patch("os.name", "nt"):
            r = WriteFileTool().execute(filepath=str(target), content=content, mode="w")
        assert "Set-Content" in r
        assert "FromBase64String" in r
        # 明文内容（含元字符）不得出现在命令行中
        assert "quoted" not in r and "special" not in r

    def test_append_uses_add_content(self, tmp_path):
        with mock.patch("os.name", "nt"):
            r = WriteFileTool().execute(
                filepath=str(tmp_path / "a.txt"), content="more", mode="a")
        assert "Add-Content" in r

    def test_oversize_rejected(self, tmp_path):
        with mock.patch("os.name", "nt"):
            r = WriteFileTool().execute(
                filepath=str(tmp_path / "big.txt"), content="x" * 10000, mode="w")
        assert "过大" in r and "追加" in r


class TestListFilesWindows:
    def test_get_child_item(self, tmp_path):
        with mock.patch("os.name", "nt"):
            r = ListFilesTool().execute(directory=str(tmp_path), show_hidden=True)
        assert "Get-ChildItem" in r and "-Force" in r
        assert "ls -l" not in r

    def test_posix_unchanged(self, tmp_path):
        r = ListFilesTool().execute(directory=str(tmp_path))
        assert "ls -l " in r


class TestWebWindows:
    def test_get_curl_exe_amp_url_quoted(self):
        tool = SimpleWebCrawlerTool()
        url = "https://example.com/?a=1&b=2"
        with mock.patch("os.name", "nt"):
            r = tool.execute(action="get", url=url)
        assert "curl.exe" in r
        # & 必须在双引号内（cmd 双引号内为字面量）
        assert '"https://example.com/?a=1&b=2"' in r

    def test_cmd_var_url_rejected(self):
        tool = SimpleWebCrawlerTool()
        with mock.patch("os.name", "nt"):
            r = tool.execute(action="get", url="https://evil.com/?x=%PATH%")
        assert "拒绝" in r

    def test_text_action_powershell_pipe(self):
        tool = SimpleWebCrawlerTool()
        with mock.patch("os.name", "nt"):
            r = tool.execute(action="text", url="https://example.com/")
        assert "curl.exe" in r and "powershell" in r

    def test_posix_unchanged(self):
        tool = SimpleWebCrawlerTool()
        r = tool.execute(action="get", url="https://example.com/")
        assert "curl -sL" in r


# ==================== T11：Windows 进程树收割 ====================

class TestTerminateTreeWindows:
    def test_executors_taskkill_tree(self):
        ex = SubprocessCommandExecutor()
        proc = mock.Mock()
        proc.pid = 12345
        proc.poll.return_value = None
        with mock.patch("os.name", "nt"), \
             mock.patch("subprocess.run") as run:
            ex._terminate_tree(proc, force=True)
        run.assert_called_once()
        assert run.call_args[0][0] == ["taskkill", "/PID", "12345", "/T", "/F"]
        proc.kill.assert_called_once()  # 兜底单进程 kill

    def test_executors_taskkill_process_gone(self):
        """taskkill 目标已退出：兜底 kill 不执行（poll 非 None）。"""
        ex = SubprocessCommandExecutor()
        proc = mock.Mock()
        proc.pid = 12346
        proc.poll.return_value = 0
        with mock.patch("os.name", "nt"), \
             mock.patch("subprocess.run") as run:
            ex._terminate_tree(proc, force=False)
        run.assert_called_once()
        proc.kill.assert_not_called()

    def test_process_manager_taskkill_tree(self):
        from subsystems.process_manager import ProcessManager
        pm = ProcessManager()
        entry = mock.Mock()
        entry.popen.pid = 999
        entry.popen.poll.return_value = None
        with mock.patch("os.name", "nt"), \
             mock.patch("subprocess.run") as run:
            pm._terminate_tree(entry, force=True)
        assert run.call_args[0][0] == ["taskkill", "/PID", "999", "/T", "/F"]

    @pytest.mark.skipif(os.name != "posix", reason="POSIX 进程组语义")
    def test_posix_no_taskkill(self):
        """POSIX 分支不调用 taskkill（走 killpg）。"""
        import subprocess as _sp
        ex = SubprocessCommandExecutor()
        proc = _sp.Popen(["sleep", "30"], start_new_session=True)
        try:
            with mock.patch("subprocess.run") as run:
                ex._terminate_tree(proc, force=False)
            run.assert_not_called()
        finally:
            try:
                import signal as _sig
                os.killpg(os.getpgid(proc.pid), _sig.SIGKILL)
            except Exception:
                pass


# ==================== T25：MemoryDatabase busy_timeout ====================

class TestMemoryDbBusyTimeout:
    def test_connect_sets_busy_timeout(self, tmp_path):
        db = MemoryDatabase(db_path=str(tmp_path / "mem.db"))
        conn = db._connect()
        try:
            value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
        assert value == 10000

    def test_all_paths_via_factory(self, tmp_path):
        """写/读路径不再直连 sqlite3.connect（统一走 _connect 工厂）。"""
        db = MemoryDatabase(db_path=str(tmp_path / "mem.db"))
        mid = db.add_memory(topic="t", summary="s", content="c",
                            start_time="2026-01-01 00:00:00",
                            end_time="2026-01-01 00:01:00")
        assert mid is not None
        rows = db.get_recent_memories(limit=5)
        assert any(r["id"] == mid for r in rows)


# ==================== T26：SkillManager 原子 reload ====================

@pytest.fixture
def isolated_skill_dir(tmp_path, monkeypatch):
    from common import constants
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(type(constants), "SKILLS_DIR",
                        property(lambda self: str(skills_dir)))
    return skills_dir


class TestSkillReloadAtomic:
    def test_skills_visible_during_reload(self, isolated_skill_dir):
        """reload 过程中 self.skills 保持旧表（T26：不再先清空）。"""
        (isolated_skill_dir / "a.md").write_text("# A\n内容A", encoding="utf-8")
        sm = SkillManager()
        assert sm.count() == 1

        (isolated_skill_dir / "b.md").write_text("# B\n内容B", encoding="utf-8")
        original = sm._read_markdown_skill
        observed = []
        calls = []

        def spy(path):
            calls.append(path)
            if len(calls) == 2:
                observed.append(dict(sm.skills))
            return original(path)

        sm._read_markdown_skill = spy
        sm.reload()

        # 加载第 2 个技能时，旧表完整可见（未被清空/半截）
        assert len(observed) == 1
        assert set(observed[0].keys()) == {"a"}
        # reload 完成后一次性替换为新表
        assert set(sm.skills.keys()) == {"a", "b"}

    def test_reload_missing_dir_keeps_old(self, isolated_skill_dir, monkeypatch):
        """目录消失时清空（旧行为保留），正常 reload 不经过空表中间态。"""
        from common import constants
        (isolated_skill_dir / "a.md").write_text("# A\n内容A", encoding="utf-8")
        sm = SkillManager()
        assert sm.count() == 1
        monkeypatch.setattr(type(constants), "SKILLS_DIR",
                            property(lambda self: str(isolated_skill_dir / "nope")))
        sm.reload()
        assert sm.count() == 0


# ==================== T27：atomic_write_text 重试 ====================

class TestAtomicWriteRetry:
    def test_retries_then_succeeds(self, tmp_path):
        target = tmp_path / "cfg.json"
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append((src, dst))
            if len(calls) < 3:
                raise PermissionError(5, "Access is denied")
            return real_replace(src, dst)

        with mock.patch("os.replace", side_effect=flaky_replace):
            atomic_write_text(str(target), '{"k": 1}')
        assert len(calls) == 3
        assert target.read_text(encoding="utf-8") == '{"k": 1}'

    def test_raises_after_exhausted(self, tmp_path):
        target = tmp_path / "cfg.json"
        with mock.patch("os.replace",
                        side_effect=PermissionError(5, "Access is denied")):
            with pytest.raises(PermissionError):
                atomic_write_text(str(target), "x")
        assert not target.exists()
        # 临时文件已清理
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".aibox_tmp_")]
        assert leftovers == []

    def test_normal_write_no_retry(self, tmp_path):
        target = tmp_path / "ok.txt"
        with mock.patch("os.replace", wraps=os.replace) as spy:
            atomic_write_text(str(target), "hello")
        spy.assert_called_once()
        assert target.read_text(encoding="utf-8") == "hello"


# ==================== T19：FailoverClient 异常收窄 ====================

def _make_failover_client():
    """deepseek(→kimi fallback) 链，Key 走 mock 环境变量。"""
    from llm_client import create_llm_client
    from test_models_profile import MODELS, _cfg
    models = {
        "deepseek": dict(MODELS["deepseek"], fallback=["kimi"]),
        "kimi": dict(MODELS["kimi"]),
    }
    env = {"DEEPSEEK_API_KEY": "sk-ds", "MOONSHOT_API_KEY": "sk-moon"}
    with mock.patch.dict(os.environ, env):
        return create_llm_client(_cfg(models=models))


class TestT19TransientExceptionScope:
    def test_permanent_schema_error_not_failed_over(self):
        """MissingSchema（api_url 手写错等永久配置错误）→ 流式路径直接上抛，
        不转移（只打一次请求）、不计 failover、不冷却。"""
        fc = _make_failover_client()
        with mock.patch("llm_client.requests.post",
                        side_effect=requests.exceptions.MissingSchema("bad url")) as mp:
            with pytest.raises(requests.exceptions.MissingSchema):
                list(fc.stream_chat([{"role": "user", "content": "hi"}]))
        assert len(mp.call_args_list) == 1
        assert fc.get_stats()["failovers"] == 0

    def test_permanent_error_no_cooldown_pollution(self):
        """永久错误后档案不进冷却——下次调用仍打主档案。"""
        fc = _make_failover_client()
        ok_stream = _stream_response([
            'data: {"choices":[{"delta":{"content":"recovered"}}]}',
            'data: [DONE]'])
        with mock.patch("llm_client.requests.post",
                        side_effect=[requests.exceptions.MissingSchema("bad"),
                                     ok_stream]) as mp:
            with pytest.raises(requests.exceptions.MissingSchema):
                list(fc.stream_chat([{"role": "user", "content": "hi"}]))
            events = list(fc.stream_chat([{"role": "user", "content": "hi"}]))
        # 第二次仍先打主档案（deepseek），未进冷却跳过
        assert mp.call_args_list[1].args[0].startswith("https://api.deepseek.com")
        assert any(e["type"] == "content" and e["text"] == "recovered" for e in events)
        assert fc.get_stats()["cooldown_skips"] == 0

    def test_connection_error_still_fails_over(self):
        """瞬时连接错误仍正常转移（收窄不误伤）。"""
        fc = _make_failover_client()
        from test_models_profile import _resp
        with mock.patch("llm_client.requests.post",
                        side_effect=[requests.exceptions.ConnectionError("boom"),
                                     _resp(200, content="ok-conn")]):
            assert fc.chat_completion([{"role": "user", "content": "hi"}]) == "ok-conn"

    def test_chunked_encoding_error_fails_over(self):
        """ChunkedEncodingError（响应体中途损坏）属瞬时故障 → 转移。"""
        fc = _make_failover_client()
        from test_models_profile import _resp
        with mock.patch("llm_client.requests.post",
                        side_effect=[requests.exceptions.ChunkedEncodingError("broken"),
                                     _resp(200, content="ok-chunked")]):
            assert fc.chat_completion([{"role": "user", "content": "hi"}]) == "ok-chunked"


# ==================== T20：不完整流信号 ====================

def _stream_response(lines):
    m = mock.MagicMock()
    m.status_code = 200
    m.iter_lines.return_value = [l.encode("utf-8") for l in lines]
    return m


def _make_deepseek_client():
    from llm_client import DeepSeekClient
    return DeepSeekClient(
        api_key="sk-test", model="test-model",
        base_url="https://api.example/v1/chat/completions")


class TestT20IncompleteStream:
    def test_missing_done_raises_transient(self):
        """流中途断开（iter_lines 耗尽无 [DONE]）→ TransientLLMError，
        不再静默视为成功导致回答无声截断。"""
        from llm_client import TransientLLMError
        c = _make_deepseek_client()
        lines = [
            'data: {"choices":[{"delta":{"content":"he"}}]}',
            'data: {"choices":[{"delta":{"content":"llo"}}]}',
            # 连接断开：无 [DONE]，末行可能还是半截 JSON
            'data: {"choices":[{"delta":{"cont',
        ]
        with mock.patch("llm_client.requests.post",
                        return_value=_stream_response(lines)):
            with pytest.raises(TransientLLMError, match="不完整"):
                list(c.stream_chat([{"role": "user", "content": "hi"}]))

    def test_with_done_completes_normally(self):
        from llm_client import TransientLLMError
        c = _make_deepseek_client()
        lines = [
            'data: {"choices":[{"delta":{"content":"hi"}}]}',
            'data: [DONE]',
        ]
        with mock.patch("llm_client.requests.post",
                        return_value=_stream_response(lines)):
            events = list(c.stream_chat([{"role": "user", "content": "hi"}]))
        assert events[-1] == {"type": "done"}
        assert any(e["type"] == "content" and e["text"] == "hi" for e in events)

    def test_failover_first_event_incomplete_stream(self):
        """FailoverClient：首事件前不完整流（0 事件 + 无 [DONE]）→ 可转移。"""
        fc = _make_failover_client()
        # 主档案：断流无任何事件；fallback（kimi）：正常完整流
        broken = _stream_response(['data: {"choices":[{"delta'])
        ok_stream = _stream_response([
            'data: {"choices":[{"delta":{"content":"from-kimi"}}]}',
            'data: [DONE]'])
        with mock.patch("llm_client.requests.post",
                        side_effect=[broken, ok_stream]):
            events = list(fc.stream_chat([{"role": "user", "content": "hi"}]))
        assert any(e["type"] == "content" and e["text"] == "from-kimi" for e in events)
        assert events[-1]["type"] == "done"


# ==================== T21：env 覆盖黑名单 ====================

class TestT21EnvDenylist:
    def test_denies_hijack_keys(self):
        safe, rejected = filter_env_overrides({
            "LD_PRELOAD": "/evil", "PATH": "/bin", "PYTHONPATH": "/p",
            "MY_VAR": "ok"})
        assert safe == {"MY_VAR": "ok"}
        assert set(rejected) == {"LD_PRELOAD", "PATH", "PYTHONPATH"}

    def test_case_insensitive_and_prefixes(self):
        safe, rejected = filter_env_overrides({
            "ld_preload": "x", "DYLD_INSERT_LIBRARIES": "y", "path": "z",
            "PSMODULEPATH": "w", "OK1": "1"})
        assert safe == {"OK1": "1"}
        assert len(rejected) == 4

    def test_empty_and_none(self):
        assert filter_env_overrides(None) == ({}, [])
        assert filter_env_overrides({}) == ({}, [])

    def test_executor_blocks_denied_env(self):
        """执行路径集成：正常 env 注入生效，黑名单键被拒。"""
        ex = SubprocessCommandExecutor()
        r = ex.execute(
            "echo $T21VAR",
            env={"T21VAR": "visible", "LD_PRELOAD": "/nonexistent/libevil.so"})
        assert "visible" in r
        # LD_PRELOAD 未注入（若注入且文件不存在，loader 报错会进合并输出）
        assert "/nonexistent" not in r


# ==================== T23：压缩后 tool 配对双向修复 ====================

def _make_compressor():
    from subsystems.conversation_compressor import ConversationCompressor
    return ConversationCompressor(llm_client=None)


class TestT23ToolPairValidation:
    def test_orphan_tool_calls_stripped(self):
        """assistant.tool_calls 的 tool 结果被压缩掉 → 剥离 tool_calls。"""
        comp = _make_compressor()
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "看结果",
             "tool_calls": [{"id": "t1", "type": "function",
                             "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "user", "content": "next"},
        ]
        out = comp._validate_message_structure(msgs)
        assert len(out) == 3
        assert "tool_calls" not in out[1]
        assert out[1]["content"] == "看结果"  # 原有内容保留

    def test_empty_content_filled_when_stripped(self):
        comp = _make_compressor()
        msgs = [
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "t1", "type": "function",
                             "function": {"name": "read", "arguments": "{}"}}]},
        ]
        out = comp._validate_message_structure(msgs)
        assert "tool_calls" not in out[0]
        assert out[0]["content"] == ""  # 防产生非法空消息

    def test_partial_pair_keeps_matched_calls(self):
        """两个 tool_calls 只剩一个配对 → 保留配对的那个。"""
        comp = _make_compressor()
        msgs = [
            {"role": "assistant", "content": "run",
             "tool_calls": [
                 {"id": "t1", "type": "function",
                  "function": {"name": "a", "arguments": "{}"}},
                 {"id": "t2", "type": "function",
                  "function": {"name": "b", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t2", "content": "result-b"},
        ]
        out = comp._validate_message_structure(msgs)
        assert len(out) == 2
        assert [tc["id"] for tc in out[0]["tool_calls"]] == ["t2"]
        assert out[1]["tool_call_id"] == "t2"

    def test_orphan_tool_message_removed(self):
        """既有行为不回归：孤立 tool 消息（无 assistant）移除。"""
        comp = _make_compressor()
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "tool", "tool_call_id": "ghost", "content": "x"},
        ]
        out = comp._validate_message_structure(msgs)
        assert [m["role"] for m in out] == ["user"]

    def test_intact_pair_untouched(self):
        """完整配对原样保留（同一对象引用，零开销路径）。"""
        comp = _make_compressor()
        assistant = {"role": "assistant", "content": "ok",
                     "tool_calls": [{"id": "t1", "type": "function",
                                     "function": {"name": "a", "arguments": "{}"}}]}
        tool = {"role": "tool", "tool_call_id": "t1", "content": "r"}
        out = comp._validate_message_structure([assistant, tool])
        assert out == [assistant, tool]


# ==================== T10：调度器硬超时兜底 ====================

class _UnkillableAI:
    """软取消无法中断的卡死 AI（模拟 requests 读阻塞被滴流输出规避）。"""

    def __init__(self):
        self.cancel_called = 0
        self._release = threading.Event()

    def cancel(self):
        self.cancel_called += 1
        # 无效：think_and_respond 仍不返回

    def think_and_respond(self, input_text, output_callback=None,
                          is_continuation=False):
        self._release.wait(timeout=30)
        return "never-reached"


class TestT10SchedulerHardTimeout:
    def test_stuck_ai_hard_timeout(self):
        """软取消无效的卡死任务 → wait_for 硬超时放行，调度循环继续。"""
        from aibox import ConfigManager, TaskScheduler

        class _FakeMemoDB:
            def get_memo(self, memo_id):
                return None

        loop = asyncio.new_event_loop()
        fake_ai = _UnkillableAI()
        try:
            scheduler = TaskScheduler(loop=loop, logger=None)
            memo = {"id": 7, "title": "卡死任务", "content": "内容", "trigger_count": 0}
            orig_get = ConfigManager.get

            def fake_get(self, key, default=None):
                # task_timeout 设为 -29.5：软取消立即触发（call_later 负延迟），
                # wait_for 硬超时 = -29.5 + 30 = 0.5s 后放行
                if key == "scheduler.task_timeout":
                    return -29.5
                return orig_get(self, key, default)

            with mock.patch.object(ConfigManager, "get", fake_get), \
                 mock.patch.object(scheduler, "_create_task_ai", return_value=fake_ai):
                result = loop.run_until_complete(
                    scheduler.execute_task_as_conversation(memo, _FakeMemoDB()))
            assert "硬超时" in result
            assert fake_ai.cancel_called >= 1  # 软取消先于硬超时触发
        finally:
            fake_ai._release.set()  # 释放卡死线程
            loop.close()

    def test_normal_task_unaffected(self):
        """正常返回的任务不受硬超时包装影响（结果正常透传）。"""
        from aibox import ConfigManager, TaskScheduler

        class _FakeMemoDB:
            def get_memo(self, memo_id):
                return None

        class _FastAI:
            def cancel(self):
                pass

            def think_and_respond(self, input_text, output_callback=None,
                                  is_continuation=False):
                return "任务完成"

        loop = asyncio.new_event_loop()
        try:
            scheduler = TaskScheduler(loop=loop, logger=None)
            memo = {"id": 8, "title": "正常任务", "content": "内容", "trigger_count": 0}
            orig_get = ConfigManager.get

            def fake_get(self, key, default=None):
                if key == "scheduler.task_timeout":
                    return 600
                return orig_get(self, key, default)

            with mock.patch.object(ConfigManager, "get", fake_get), \
                 mock.patch.object(scheduler, "_create_task_ai", return_value=_FastAI()):
                result = loop.run_until_complete(
                    scheduler.execute_task_as_conversation(memo, _FakeMemoDB()))
            assert "任务完成" in result
        finally:
            loop.close()


# ==================== T22：owner 隔离（ProcessManager） ====================

class TestT22OwnerIsolation:
    def test_kill_denied_for_other_owner(self):
        from subsystems.process_manager import ProcessManager
        pm = ProcessManager()
        r = pm.start("sleep 30", owner="session-a")
        handle = r["handle"]
        try:
            denied = pm.kill(handle, owner="session-b")
            assert not denied["ok"]
            assert "属主" in denied["error"]
            # 进程仍在运行（未被误杀）
            assert pm._lookup(handle).status == "running"
        finally:
            pm.kill(handle)

    def test_kill_allowed_for_owner(self):
        from subsystems.process_manager import ProcessManager
        pm = ProcessManager()
        r = pm.start("sleep 30", owner="session-a")
        result = pm.kill(r["handle"], owner="session-a")
        assert result["ok"]

    def test_write_denied_for_other_owner(self):
        from subsystems.process_manager import ProcessManager
        pm = ProcessManager()
        r = pm.start("cat", owner="session-a")
        handle = r["handle"]
        try:
            denied = pm.write(handle, "hi", owner="session-b")
            assert not denied["ok"]
            assert "属主" in denied["error"]
            ok = pm.write(handle, "hi", owner="session-a")
            assert ok["ok"]
        finally:
            pm.kill(handle)

    def test_backward_compatible_without_owner(self):
        """任一侧无 owner 标识 → 不隔离（测试/管理面直调兼容）。"""
        from subsystems.process_manager import ProcessManager
        pm = ProcessManager()
        r1 = pm.start("sleep 5", owner="a")
        assert pm.kill(r1["handle"])["ok"]  # caller 无 owner
        r2 = pm.start("sleep 5")  # entry 无 owner
        assert pm.kill(r2["handle"], owner="b")["ok"]

    def test_list_shows_owner_field(self):
        from subsystems.process_manager import ProcessManager
        pm = ProcessManager()
        r = pm.start("sleep 5", owner="whoami")
        try:
            infos = pm.list_procs()
            assert any(e.get("owner") == "whoami" for e in infos)
        finally:
            pm.kill(r["handle"])

    def test_output_not_isolated(self):
        """观察面（get_output）不受 owner 限制。"""
        from subsystems.process_manager import ProcessManager
        pm = ProcessManager()
        r = pm.start("echo hello", owner="session-a")
        handle = r["handle"]
        import time as _t
        _t.sleep(0.3)
        result = pm.get_output(handle)  # 观察面无 owner 概念
        assert result["ok"]
        assert "hello" in result.get("output", "")


# ==================== T22：task profile（定时任务精简工具面） ====================

def _make_chat(profile, owner=None):
    from aibox import DeepSeekChat
    with mock.patch("aibox.create_llm_client") as mclc:
        mclc.return_value = mock.MagicMock(
            api_key="sk-test", name="DS", default_temperature=None)
        return DeepSeekChat(tool_profile=profile, owner_id=owner)


class TestT22TaskProfile:
    def test_task_profile_excludes_global_side_effect_tools(self):
        ai = _make_chat("task")
        names = set(ai.tool_registry._tools.keys())
        assert "update_identity" not in names
        assert "update_soul" not in names
        assert "set_proxy" not in names
        # 管理面其余工具保留
        assert "get_proxy" in names
        assert "create_subagent" in names

    def test_orchestrator_profile_still_has_them(self):
        ai = _make_chat("orchestrator")
        names = set(ai.tool_registry._tools.keys())
        assert "update_identity" in names
        assert "update_soul" in names
        assert "set_proxy" in names

    def test_worker_bg_tools_carry_owner(self):
        ai = _make_chat("worker", owner="sess-1")
        assert ai.tool_registry.get_tool("bg_run").owner == "sess-1"
        assert ai.tool_registry.get_tool("bg_kill").owner == "sess-1"
        assert ai.tool_registry.get_tool("bg_write").owner == "sess-1"
        # 观察面工具不带 owner
        assert getattr(ai.tool_registry.get_tool("bg_list"), "owner", "unset") == "unset"

    def test_create_task_ai_uses_task_profile(self):
        from aibox import TaskScheduler
        # 显式新建 loop：全量运行时主线程可能已无可用的当前事件循环
        loop = asyncio.new_event_loop()
        try:
            scheduler = TaskScheduler(loop=loop, logger=None)
            with mock.patch("aibox.create_llm_client") as mclc:
                mclc.return_value = mock.MagicMock(
                    api_key="sk-test", name="DS", default_temperature=None)
                ai = scheduler._create_task_ai()
            assert ai.tool_profile == "task"
            assert ai.owner_id and ai.owner_id.startswith("task-")
        finally:
            loop.close()


# ==================== T-L17/T-L18：model_config 展示与推荐 ====================

class TestTL17EnvSuggestion:
    def test_suggest_by_domain(self):
        from model_config import _suggest_env_name
        assert _suggest_env_name("https://api.moonshot.cn/v1") == "MOONSHOT_API_KEY"
        assert _suggest_env_name("https://api.deepseek.com/chat") == "DEEPSEEK_API_KEY"
        assert _suggest_env_name("https://dashscope.aliyuncs.com/x") == "DASHSCOPE_API_KEY"

    def test_unknown_domain_empty(self):
        from model_config import _suggest_env_name
        assert _suggest_env_name("http://localhost:11434/v1") == ""
        assert _suggest_env_name("") == ""
        assert _suggest_env_name(None) == ""


class TestTL18DisplaySanitize:
    def test_sanitize_strips_control_chars(self):
        from model_config import _sanitize_display
        out = _sanitize_display("a\x1b]0;pwned\x07b")
        assert "\x1b" not in out and "\x07" not in out
        assert out == "a]0;pwnedb"  # 控制符剥离，可打印部分保留
        assert _sanitize_display("正常文本") == "正常文本"
        assert _sanitize_display(None) == ""

    def test_print_profiles_filters_escape_sequences(self, capsys):
        from model_config import _print_profiles
        _print_profiles([{
            "name": "evil\x1b[31m", "is_default": False,
            "display_name": "d\x07x", "api_url": "https://x\x1b[2J",
            "model": "m", "api_key_env": "K\x1bZ",
            "temperature": None, "timeout": None, "fallback": ["fb\x07"],
        }])
        out = capsys.readouterr().out
        assert "\x1b" not in out
        assert "\x07" not in out
        assert "evil" in out  # 可打印部分保留
