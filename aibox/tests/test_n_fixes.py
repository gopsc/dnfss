"""tests.test_n_fixes - Batch N 修复单元测试

波次 3 平台兼容（2026-08-21，审计第八轮 T 系列）：
  - T3  basic_tools 平台感知引用 + Windows PowerShell/curl.exe 指引
  - T11 Windows 进程树收割（taskkill /T /F，executors + process_manager）
  - T25 MemoryDatabase 统一连接工厂（busy_timeout）
  - T26 SkillManager 原子 reload（中途不清空 self.skills）
  - T27 atomic_write_text 对 Windows PermissionError 短退避重试

Windows 分支在 Linux 上以 mock.patch("os.name", "nt") 模拟验证命令生成
逻辑（不实际执行生成的命令）。
"""

import os
import sqlite3
from unittest import mock

import pytest

from common.atomic import atomic_write_text
from subsystems import MemoryDatabase, SkillManager
from subsystems.executors import SubprocessCommandExecutor
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
