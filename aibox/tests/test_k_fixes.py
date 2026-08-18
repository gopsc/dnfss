"""aibox 第五轮审计（Batch K）修复回归测试（2026-08-19）

波次 1：
  - J3：FTS 外部内容表删除换序后索引无残留（delete_memory / delete_memories_by_ids）
  - J4：model_config 连通性验证异常消息 Bearer 令牌脱敏
  - J6：PromptManager/ConfigManager 单例并发初始化不双重加载
  - J8：add_profile 对非法温度/超时的容错（交互层不再抢先 float()/int()）

波次 2（J1 任务系统投毒）：
  - add_memo 入库校验：畸形时间/重复间隔/重复类型拒绝，三入口一处覆盖
  - 存量脏数据防御：调度器预检、mark_as_triggered、展示路径不再打崩
  - WS task add 友好错误 + register 握手异常可回收
"""

import asyncio
import json
import os
import sqlite3
import sys
import threading
import types
from unittest import mock

import pytest

if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

import aibox  # noqa: E402
import model_config  # noqa: E402
from common import ConfigManager  # noqa: E402
from subsystems.db import MemoDatabase, MemoryDatabase  # noqa: E402
from subsystems.prompt_manager import PromptManager  # noqa: E402

from test_model_config import _cfg  # noqa: E402


# ==================== J3：FTS 删除顺序 ====================

def _fts_hits(db_path, keyword):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?",
            (f'"{keyword}"',)).fetchall()
    finally:
        conn.close()


def _add_mem(db, content):
    return db.add_memory(topic="话题", summary="摘要", content=content,
                         start_time="2026-08-19 00:00:00",
                         end_time="2026-08-19 00:00:01", importance=2)


class TestJ3FtsDeleteOrder:
    def test_delete_memory_leaves_no_fts_orphan(self, tmp_path):
        """单条删除后 FTS 索引不得残留（修复前 MATCH 仍命中已删 rowid）"""
        db = MemoryDatabase(db_path=str(tmp_path / "mem.db"))
        mid = _add_mem(db, "机密内容甲")
        assert _fts_hits(tmp_path / "mem.db", "机密内容甲")
        assert db.delete_memory(mid) is True
        assert _fts_hits(tmp_path / "mem.db", "机密内容甲") == []

    def test_batch_delete_leaves_no_fts_orphan(self, tmp_path):
        """批量删除同样不留索引残留"""
        db = MemoryDatabase(db_path=str(tmp_path / "mem.db"))
        ids = [_add_mem(db, f"机密内容乙{i}") for i in range(3)]
        assert db.delete_memories_by_ids(ids) == 3
        for i in range(3):
            assert _fts_hits(tmp_path / "mem.db", f"机密内容乙{i}") == []

    def test_search_after_delete_no_hit(self, tmp_path):
        """删除后搜索（FTS 路径）不再命中已删内容"""
        db = MemoryDatabase(db_path=str(tmp_path / "mem.db"))
        mid = _add_mem(db, "独特关键词丙")
        assert db.search_memories(["独特关键词丙"])
        db.delete_memory(mid)
        assert db.search_memories(["独特关键词丙"]) == []


# ==================== J4：验证异常脱敏 ====================

class TestJ4TestProfileSanitize:
    def _models_cfg(self, tmp_path):
        return _cfg(tmp_path, {"models": {"bad": {
            "api_url": "https://api.example.com/v1/chat/completions",
            "model": "m", "api_key_env": "K"}}})

    def test_bearer_token_masked_in_failure_message(self, tmp_path):
        """网关错误页回显 Authorization 头时，令牌不得出现在终端回显"""
        cm = self._models_cfg(tmp_path)
        err = RuntimeError("HTTP 502: Authorization: Bearer sk-secret12345")
        with mock.patch("llm_client.create_llm_client", side_effect=err):
            result = model_config.test_profile(cm, "bad")
        assert "sk-secret12345" not in result
        assert "Bearer ***" in result

    def test_plain_error_message_preserved(self, tmp_path):
        """无敏感内容的错误消息原样保留（脱敏不误伤）"""
        cm = self._models_cfg(tmp_path)
        with mock.patch("llm_client.create_llm_client",
                        side_effect=RuntimeError("连接被拒绝")):
            result = model_config.test_profile(cm, "bad")
        assert "连接被拒绝" in result


# ==================== J6：单例并发初始化 ====================

class TestJ6SingletonInitRace:
    def test_prompt_manager_concurrent_init_no_duplication(self, tmp_path):
        """多线程首例并发：单实例且提示词只加载一份（修复前整份翻倍）"""
        PromptManager._instance = None
        PromptManager._prompts = []
        PromptManager._prompts_dir = None
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        try:
            barrier = threading.Barrier(8)
            instances = [None] * 8

            def worker(k):
                barrier.wait()
                instances[k] = PromptManager()

            with mock.patch.object(ConfigManager, "get",
                                   return_value=str(prompts_dir)):
                threads = [threading.Thread(target=worker, args=(k,))
                           for k in range(8)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            assert len({id(i) for i in instances}) == 1
            files = [p["file"] for p in PromptManager._prompts]
            assert files, "提示词应已加载"
            assert len(files) == len(set(files)), f"提示词被重复加载: {files}"
        finally:
            PromptManager._instance = None
            PromptManager._prompts = []
            PromptManager._prompts_dir = None

    def test_config_manager_concurrent_init_single_load(self, tmp_path):
        """多线程首例并发：单实例且 _load_config 只执行一次"""
        cfg_file = tmp_path / "config.json"
        calls = {"n": 0}
        original_load = ConfigManager._load_config

        def counting_load(self):
            calls["n"] += 1
            original_load(self)

        ConfigManager._instance = None
        ConfigManager._config = None
        ConfigManager._config_path = None
        try:
            barrier = threading.Barrier(8)
            instances = [None] * 8

            def worker(k):
                barrier.wait()
                instances[k] = ConfigManager()

            with mock.patch.dict(os.environ, {"AI_CONFIG_PATH": str(cfg_file)}), \
                 mock.patch.object(ConfigManager, "_load_config", counting_load):
                threads = [threading.Thread(target=worker, args=(k,))
                           for k in range(8)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            assert len({id(i) for i in instances}) == 1
            assert calls["n"] == 1
        finally:
            ConfigManager._instance = None
            ConfigManager._config = None
            ConfigManager._config_path = None


# ==================== J8：交互输入容错 ====================

class TestJ8NumericTolerance:
    def test_invalid_temperature_returns_friendly_error(self, tmp_path):
        """非数字温度 → 友好错误而非 ValueError（交互层不再抢先 float()）"""
        cm = _cfg(tmp_path, {"models": {}})
        msgs = model_config.add_profile(
            cm, "p1", "https://api.example.com/v1/chat/completions", "m1",
            api_key_env="K", temperature="not-a-number")
        assert any(m.startswith("❌") and "温度" in m for m in msgs)
        assert "p1" not in model_config.get_models(cm)

    def test_invalid_timeout_returns_friendly_error(self, tmp_path):
        cm = _cfg(tmp_path, {"models": {}})
        msgs = model_config.add_profile(
            cm, "p1", "https://api.example.com/v1/chat/completions", "m1",
            api_key_env="K", timeout="abc")
        assert any(m.startswith("❌") and "超时" in m for m in msgs)
        assert "p1" not in model_config.get_models(cm)

    def test_string_numbers_accepted_and_clamped(self, tmp_path):
        """数字字符串正常接受并钳制（交互层原始字符串直传语义）"""
        cm = _cfg(tmp_path, {"models": {}})
        msgs = model_config.add_profile(
            cm, "p1", "https://api.example.com/v1/chat/completions", "m1",
            api_key_env="K", temperature="0.7", timeout="120")
        assert all(not m.startswith("❌") for m in msgs)
        profile = model_config.get_models(cm)["p1"]
        assert profile["temperature"] == 0.7
        assert profile["timeout"] == 120


# ==================== J1 波次 2：任务系统投毒 ====================

def _insert_memo_raw(db_path, reminder_time="2026-08-18 00:00:00",
                     repeat_type="none", repeat_interval=0,
                     repeat_interval_unit="days", repeat_end_time=None):
    """直接 SQL 插入任务行——模拟校验落地前的存量脏数据（绕过 add_memo）"""
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        cur.execute('''INSERT INTO memos
            (title, content, created_at, reminder_time, tags, priority, created_by,
             is_completed, source, repeat_type, repeat_interval, repeat_interval_unit,
             repeat_end_time, is_immediate)
            VALUES ('t', 'c', '2026-08-18 00:00:00', ?, '', 1, 'test',
             0, 'test', ?, ?, ?, ?, 0)''',
            (reminder_time, repeat_type, repeat_interval, repeat_interval_unit,
             repeat_end_time))
        return cur.lastrowid


class TestJ1AddMemoValidation:
    def test_malformed_reminder_time_rejected(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        with pytest.raises(ValueError, match="提醒时间格式非法"):
            db.add_memo(title="t", reminder_time="不是时间")
        with sqlite3.connect(str(tmp_path / "m.db")) as conn:
            assert conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0] == 0

    def test_malformed_repeat_end_time_rejected(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        with pytest.raises(ValueError, match="重复结束时间格式非法"):
            db.add_memo(title="t", reminder_time="2026-08-18 00:00:00",
                        repeat_type="daily", repeat_end_time="不是时间")

    def test_invalid_repeat_type_rejected(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        with pytest.raises(ValueError, match="无效的重复类型"):
            db.add_memo(title="t", reminder_time="2026-08-18 00:00:00",
                        repeat_type="every-second")

    def test_non_numeric_repeat_interval_rejected(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        with pytest.raises(ValueError, match="重复间隔必须是整数"):
            db.add_memo(title="t", reminder_time="2026-08-18 00:00:00",
                        repeat_type="custom", repeat_interval="垃圾")

    def test_custom_repeat_requires_positive_interval(self, tmp_path):
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        with pytest.raises(ValueError, match="repeat_interval"):
            db.add_memo(title="t", reminder_time="2026-08-18 00:00:00",
                        repeat_type="custom", repeat_interval=0)

    def test_numeric_string_interval_coerced(self, tmp_path):
        """数字字符串被强转接受（WS 传 JSON 数字/字符串均安全）"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        mid = db.add_memo(title="t", reminder_time="2026-08-18 00:00:00",
                          repeat_type="custom", repeat_interval="30",
                          repeat_interval_unit="minutes")
        assert db.get_memo(mid)["repeat_interval"] == 30

    def test_add_user_task_propagates_validation(self, tmp_path):
        """WS 路径（add_user_task 薄包装 add_memo）同样被校验拦截"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        with pytest.raises(ValueError, match="提醒时间格式非法"):
            db.add_user_task(title="t", reminder_time="不是时间")


class TestJ1DirtyDataDefense:
    def test_mark_as_triggered_completes_malformed_one_shot(self, tmp_path):
        """存量畸形 reminder_time 一次性任务：触发不再抛异常，正常完成"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        mid = _insert_memo_raw(tmp_path / "m.db", reminder_time="不是时间")
        assert db.mark_as_triggered(mid) is None
        assert db.get_memo(mid)["is_completed"] == 1

    def test_mark_as_triggered_handles_garbage_repeat_interval(self, tmp_path):
        """存量 custom 任务 repeat_interval 为文本：按非重复处理，不打崩事务"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        mid = _insert_memo_raw(tmp_path / "m.db", reminder_time="2026-08-18 00:00:00",
                               repeat_type="custom", repeat_interval="垃圾")
        assert db.mark_as_triggered(mid) is None
        assert db.get_memo(mid)["is_completed"] == 1

    def test_malformed_repeat_end_time_treated_as_no_end(self, tmp_path):
        """存量畸形 repeat_end_time：解析失败视为无结束时间，不抛异常"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        mid = _insert_memo_raw(tmp_path / "m.db", reminder_time="2026-08-19 00:00:00",
                               repeat_type="daily", repeat_end_time="不是时间")
        memo = db.get_memo(mid)
        from datetime import datetime
        base = datetime.strptime("2026-08-19 00:00:00", "%Y-%m-%d %H:%M:%S")
        assert db._should_end_repeat(memo, base) is False
        assert db._calculate_next_reminder(memo, base) is not None

    def test_pending_tasks_for_user_malformed_time_overdue(self, tmp_path):
        """展示路径：畸形 reminder_time 行按到期展示，不再打崩握手"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        mid = _insert_memo_raw(tmp_path / "m.db", reminder_time="不是时间")
        tasks = db.get_pending_tasks_for_user()
        row = next(t for t in tasks if t["id"] == mid)
        assert row["is_overdue"] is True


class TestJ1WsAndScheduler:
    def test_ws_task_add_rejects_malformed_repeat_end(self, tmp_path):
        """WS task add 传畸形 repeat_end_time：友好错误且不入库"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        ai_manager = mock.MagicMock()
        ai_manager.memo_db = db
        handler = aibox.WebSocketHandler(ai_manager)
        try:
            ws = mock.AsyncMock()
            asyncio.run(handler.handle_task_message(ws, {
                "type": "task", "action": "add", "title": "t", "delay": 5,
                "repeat_type": "daily", "repeat_end_time": "不是时间"}))
            sent = json.loads(ws.send.call_args[0][0])
            assert sent["type"] == "error"
            assert "任务添加失败" in sent["content"]
            with sqlite3.connect(str(tmp_path / "m.db")) as conn:
                assert conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0] == 0
        finally:
            handler.shutdown()

    def test_scheduler_precheck_malformed_time_executes_not_loops(self, tmp_path):
        """调度器预检：畸形 reminder_time 按到期执行并标记触发（不再热循环）"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        mid = _insert_memo_raw(tmp_path / "m.db", reminder_time="不是时间")
        loop = asyncio.new_event_loop()
        executed = {"n": 0}
        try:
            scheduler = aibox.TaskScheduler(loop=loop, logger=None)
            scheduler.broadcast_enabled = False

            async def fake_execute(memo, memo_db):
                executed["n"] += 1
                return "ok"

            scheduler.execute_task_as_conversation = fake_execute
            loop.run_until_complete(scheduler.check_and_execute_tasks(db))
        finally:
            loop.close()
        assert executed["n"] == 1
        assert db.get_memo(mid)["is_completed"] == 1


# ==================== J5 波次 4：原子写 + 损坏备份 ====================

class TestJ5AtomicWrites:
    def test_save_config_atomic_no_tmp_leftover(self, tmp_path):
        """_save_config 原子写：无 .aibox_tmp_ 残留且 JSON 合法"""
        cm = ConfigManager()
        cm._config_path = str(tmp_path / "config.json")
        cm._config = {"api": {"deepseek_model": "m"}, "models": {}}
        assert cm._save_config() is True
        import json as _json
        data = _json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert data["api"]["deepseek_model"] == "m"
        assert list(tmp_path.glob(".aibox_tmp_*")) == []

    def test_corrupted_config_backed_up_before_fallback(self, tmp_path):
        """损坏配置加载：备份 .corrupt.* 文件后回退默认，原内容不丢"""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text('{"api": {"broken": ', encoding="utf-8")  # 半截 JSON
        ConfigManager._instance = None
        ConfigManager._config = None
        ConfigManager._config_path = None
        try:
            with mock.patch.dict(os.environ, {"AI_CONFIG_PATH": str(cfg_file)}):
                cm = ConfigManager()
            assert cm._config is not None
            backups = list(tmp_path.glob("config.json.corrupt.*"))
            assert len(backups) == 1
            assert backups[0].read_text(encoding="utf-8") == '{"api": {"broken": '
        finally:
            ConfigManager._instance = None
            ConfigManager._config = None
            ConfigManager._config_path = None

    def test_prompt_write_atomic(self, tmp_path):
        """prompt 更新原子写：内容生效且无临时文件残留"""
        from subsystems.prompt_manager import PromptManager
        PromptManager._instance = None
        PromptManager._prompts = []
        PromptManager._prompts_dir = None
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        try:
            with mock.patch.object(ConfigManager, "get",
                                   return_value=str(prompts_dir)):
                pm = PromptManager()
                assert pm.update_identity("新身份内容") is True
                identity = prompts_dir / "00_IDENTITY.md"
                assert "新身份内容" in identity.read_text(encoding="utf-8")
            assert list(tmp_path.glob(".aibox_tmp_*")) == []
        finally:
            PromptManager._instance = None
            PromptManager._prompts = []
            PromptManager._prompts_dir = None


# ==================== J7 波次 4：broadcast 超时回收 ====================

class TestJ7BroadcastTimeout:
    def test_stuck_client_timed_out_and_unregistered(self):
        """半开 TCP 僵死客户端：broadcast 在超时内返回并回收该连接"""
        from aibox import WebSocketHandler
        handler = WebSocketHandler(ai_manager=mock.MagicMock())
        handler.broadcast_send_timeout = 0.1
        try:
            ws = mock.AsyncMock()

            async def stuck_send(*args, **kwargs):
                await asyncio.sleep(30)

            ws.send.side_effect = stuck_send
            handler.connected_clients.add(ws)
            asyncio.run(handler.broadcast({"type": "ping"}))
            assert ws not in handler.connected_clients
        finally:
            handler.shutdown()

    def test_healthy_client_stays_connected(self):
        """正常客户端：发送成功且不被回收"""
        from aibox import WebSocketHandler
        handler = WebSocketHandler(ai_manager=mock.MagicMock())
        handler.broadcast_send_timeout = 1.0
        try:
            ws = mock.AsyncMock()
            handler.connected_clients.add(ws)
            asyncio.run(handler.broadcast({"type": "ping"}))
            assert ws in handler.connected_clients
            assert ws.send.called
        finally:
            handler.shutdown()


# ==================== J9 波次 5：MemoDatabase 连接层 ====================

class TestJ9MemoDbConnect:
    def test_wal_enabled_after_init(self, tmp_path):
        """WAL 显式设置——不再依赖与 MemoryDatabase 的创建顺序"""
        MemoDatabase(db_path=str(tmp_path / "m.db"))
        with sqlite3.connect(str(tmp_path / "m.db")) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_connection_factory_sets_busy_timeout(self, tmp_path):
        """统一连接工厂：busy_timeout 覆盖全部写路径"""
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        conn = db._connect()
        try:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        finally:
            conn.close()


# ==================== J10 波次 5：memo 批量删除 ====================

class TestJ10BatchDelete:
    def test_batch_delete_via_memo_tool(self, tmp_path):
        """schema 声明的 memo_ids 批量删除真正生效（此前静默忽略）"""
        from tools.db_tools import MemoTool
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        ids = [db.add_memo(title=f"任务{i}",
                           reminder_time="2026-08-18 00:00:00") for i in range(3)]
        tool = MemoTool(db)
        r = tool.execute(action="delete", confirm=True, memo_ids=ids)
        assert "3/3" in r
        with sqlite3.connect(str(tmp_path / "m.db")) as conn:
            assert conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0] == 0

    def test_batch_delete_requires_list(self, tmp_path):
        from tools.db_tools import MemoTool
        db = MemoDatabase(db_path=str(tmp_path / "m.db"))
        tool = MemoTool(db)
        r = tool.execute(action="delete", confirm=True, memo_ids="1")
        assert "列表" in r


# ==================== J15 波次 5：未知 task action ====================

class TestJ15UnknownTaskAction:
    def test_unknown_action_returns_error(self):
        """未知 task action 明确报错（此前静默吞掉）"""
        from aibox import WebSocketHandler
        handler = WebSocketHandler(ai_manager=mock.MagicMock())
        try:
            ws = mock.AsyncMock()
            asyncio.run(handler.handle_task_message(ws, {"action": "explode"}))
            sent = json.loads(ws.send.call_args[0][0])
            assert sent["type"] == "error"
            assert "未知的任务操作" in sent["content"]
        finally:
            handler.shutdown()


# ==================== J16 波次 5：skill_manager ====================

class TestJ16SkillManager:
    def test_markdown_skipped_when_executable_same_name(self, tmp_path, monkeypatch):
        """同名键冲突：可执行技能确定性优先（此前由 listdir 顺序决定）"""
        from common import constants
        from subsystems.skill_manager import SkillManager
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        exe = skills_dir / "foo"
        exe.write_text("#!/bin/sh\necho '可执行技能简介'", encoding="utf-8")
        exe.chmod(0o755)
        (skills_dir / "foo.md").write_text("# Markdown 简介\n内容", encoding="utf-8")
        monkeypatch.setattr(type(constants), "SKILLS_DIR",
                            property(lambda self: str(skills_dir)))
        sm = SkillManager()
        assert "foo" in sm.skills
        assert sm.skills["foo"]["is_executable"] is True

    def test_help_output_truncated(self, tmp_path, monkeypatch):
        """--help 输出截断（内容进系统提示词，防撑爆上下文）"""
        from common import constants
        from subsystems.skill_manager import SkillManager
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        exe = skills_dir / "big"
        exe.write_text("#!/bin/sh\necho '" + "x" * 5000 + "'", encoding="utf-8")
        exe.chmod(0o755)
        monkeypatch.setattr(type(constants), "SKILLS_DIR",
                            property(lambda self: str(skills_dir)))
        sm = SkillManager()
        assert len(sm.skills["big"]["content"]) <= SkillManager.MAX_HELP_LENGTH + 20
