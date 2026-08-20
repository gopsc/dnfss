"""aibox 第六轮审计（Batch L）修复回归测试（2026-08-20）

R1：/memos WS 命令对畸形 reminder_time 不再整体报错（J1 加固漏点）
R2：handle_interrupt 只取消本会话 AI，不再触碰 CLI 主 AI 的全局 executor
R3：unregister 补齐连接回收（关闭 websocket + 取消在途处理任务）
"""

import asyncio
import sys
import types
from unittest import mock

if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

import aibox  # noqa: E402


def _make_handler():
    handler = aibox.WebSocketHandler(ai_manager=mock.MagicMock())
    return handler


# ==================== R1：/memos 畸形时间容错 ====================

class TestR1MemosCommand:
    def test_malformed_reminder_time_shows_pending(self):
        """存量脏数据（畸形 reminder_time）：/memos 按"待执行"展示，不抛 ValueError"""
        handler = _make_handler()
        try:
            handler.ai_manager.memo_db.get_all_memos.return_value = [
                {"id": 1, "title": "脏数据任务", "reminder_time": "not-a-time",
                 "repeat_type": "none", "repeat_interval": 0, "is_immediate": False},
                {"id": 2, "title": "正常过期任务", "reminder_time": "2020-01-01 00:00:00",
                 "repeat_type": "none", "repeat_interval": 0, "is_immediate": False},
            ]
            sent = []

            async def fake_send(ws, msg):
                sent.append(msg)

            handler.send_message = fake_send
            ws = mock.AsyncMock()
            asyncio.run(handler.handle_command(ws, {"command": "/memos"}))

            results = [m for m in sent if m.get("type") == "command_result"]
            errors = [m for m in sent if m.get("type") == "error"]
            assert len(results) == 1
            assert not errors  # 不再落入顶层兜底"处理失败"
            content = results[0]["content"]
            assert "脏数据任务" in content and "正常过期任务" in content
        finally:
            handler.shutdown()

    def test_valid_reminder_time_still_marks_overdue(self):
        """合法过期时间仍标 overdue（语义不回归）"""
        handler = _make_handler()
        try:
            handler.ai_manager.memo_db.get_all_memos.return_value = [
                {"id": 1, "title": "过期任务", "reminder_time": "2020-01-01 00:00:00",
                 "repeat_type": "none", "repeat_interval": 0, "is_immediate": False},
                {"id": 2, "title": "未来任务", "reminder_time": "2099-01-01 00:00:00",
                 "repeat_type": "none", "repeat_interval": 0, "is_immediate": False},
            ]
            sent = []

            async def fake_send(ws, msg):
                sent.append(msg)

            handler.send_message = fake_send
            ws = mock.AsyncMock()
            asyncio.run(handler.handle_command(ws, {"command": "/memos"}))

            content = next(m["content"] for m in sent
                           if m.get("type") == "command_result")
            overdue = aibox.i18n.get('memo_overdue')
            pending = aibox.i18n.get('memo_pending')
            overdue_line = next(l for l in content.splitlines() if "过期任务" in l)
            pending_line = next(l for l in content.splitlines() if "未来任务" in l)
            assert overdue in overdue_line and pending not in overdue_line
            assert pending in pending_line and overdue not in pending_line
        finally:
            handler.shutdown()


# ==================== R2：interrupt 不再误杀 CLI 主 AI 子进程 ====================

class TestR2InterruptScope:
    def test_interrupt_does_not_touch_global_executor(self):
        """WS 中断不再取消 ai_manager.command_executor（CLI 主 AI 的执行器）"""
        handler = _make_handler()
        try:
            ws = mock.AsyncMock()
            asyncio.run(handler.handle_interrupt(ws, {}))
            handler.ai_manager.command_executor.cancel.assert_not_called()
        finally:
            handler.shutdown()

    def test_interrupt_still_cancels_session_ai(self):
        """本会话 AI 仍被正常取消（中断能力不回归）"""
        handler = _make_handler()
        try:
            ws = mock.AsyncMock()
            ai = mock.MagicMock()
            handler.client_sessions[ws] = {"ai": ai, "is_processing": True}
            asyncio.run(handler.handle_interrupt(ws, {}))
            ai.cancel.assert_called_once()
            handler.ai_manager.command_executor.cancel.assert_not_called()
        finally:
            handler.shutdown()


# ==================== R3：unregister 补齐连接回收 ====================

class TestR3UnregisterCleanup:
    def test_unregister_closes_websocket_and_cancels_tasks(self):
        """broadcast 超时路径走 unregister 后：websocket 被关闭、在途任务被取消、登记清空"""
        handler = _make_handler()
        try:
            ws = mock.AsyncMock()

            async def scenario():
                task = asyncio.create_task(asyncio.sleep(30))
                handler.connected_clients.add(ws)
                handler.client_sessions[ws] = {"ai": None, "is_processing": True}
                handler._processing_tasks[ws] = {task}
                await handler.unregister(ws)
                await asyncio.sleep(0)  # 让 cancel 传播
                assert task.done()  # 取消即视为完成（cancelled 或已收尾）

            asyncio.run(scenario())
            assert ws.close.called
            assert ws not in handler.connected_clients
            assert ws not in handler.client_sessions
            assert ws not in handler._processing_tasks
        finally:
            handler.shutdown()

    def test_unregister_idempotent_and_mock_safe(self):
        """重复注销幂等；close 抛异常的对象不炸（AttributeError 被兜底）"""
        class _BadCloseWS:
            remote_address = ("127.0.0.1", 1)

            async def close(self):
                raise AttributeError("no close")

        handler = _make_handler()
        try:
            ws = _BadCloseWS()
            handler.connected_clients.add(ws)
            handler.client_sessions[ws] = {"ai": None}
            asyncio.run(handler.unregister(ws))
            asyncio.run(handler.unregister(ws))  # 二次注销不抛
            assert ws not in handler.connected_clients
            assert ws not in handler.client_sessions
        finally:
            handler.shutdown()
