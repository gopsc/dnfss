"""subsystems.db 单元测试

覆盖：
  - MemoryDatabase: 增删查 / FTS5 全文检索 / 批量删除
  - MemoDatabase: 增删查 / 待执行 / 重复任务 / 即时任务 / 统计
每个测试用独立临时 sqlite 文件，互不污染。
"""
from datetime import datetime, timedelta

import pytest

from subsystems import MemoryDatabase, MemoDatabase
from common import constants


@pytest.fixture
def mem_db(tmp_path):
    """独立临时文件的 MemoryDatabase。"""
    db_path = str(tmp_path / "test_memory.db")
    return MemoryDatabase(db_path=db_path)


@pytest.fixture
def memo_db(tmp_path):
    """独立临时文件的 MemoDatabase。"""
    db_path = str(tmp_path / "test_memo.db")
    return MemoDatabase(db_path=db_path)


# ==================== MemoryDatabase ====================

def test_memory_add_and_get(mem_db):
    mid = mem_db.add_memory(
        topic="测试主题", summary="测试摘要",
        content="详细内容", tags=["t1", "t2"]
    )
    assert isinstance(mid, int) and mid > 0

    m = mem_db.get_memory_by_id(mid)
    assert m is not None
    assert m["topic"] == "测试主题"
    assert m["summary"] == "测试摘要"
    assert m["tags"] == ["t1", "t2"]


def test_memory_search_by_keyword(mem_db):
    mem_db.add_memory(topic="Python learning", summary="learn Python basics", content="variables and functions")
    mem_db.add_memory(topic="Go learning", summary="learn Go basics", content="goroutine")

    results = mem_db.search_memories(["Python"])
    assert len(results) == 1
    assert "Python" in results[0]["topic"]


def test_memory_search_match_all(mem_db):
    mem_db.add_memory(topic="Python database", summary="sqlite usage", content="FTS5 search")
    mem_db.add_memory(topic="Python network", summary="requests usage", content="HTTP request")

    # match_all=True：两个关键词都要命中
    results = mem_db.search_memories(["Python", "sqlite"], match_all=True)
    assert len(results) == 1
    assert "database" in results[0]["topic"]

    # match_all=False：任一关键词命中
    results = mem_db.search_memories(["Python", "sqlite"], match_all=False)
    assert len(results) == 2


def test_memory_search_empty_keywords_returns_empty(mem_db):
    assert mem_db.search_memories([]) == []


def test_memory_delete(mem_db):
    mid = mem_db.add_memory(topic="待删除", summary="摘要")
    assert mem_db.delete_memory(mid) is True
    assert mem_db.get_memory_by_id(mid) is None
    # 再次删除应返回 False
    assert mem_db.delete_memory(mid) is False


def test_memory_batch_delete(mem_db):
    ids = [mem_db.add_memory(topic=f"t{i}", summary=f"s{i}") for i in range(3)]
    deleted = mem_db.delete_memories_by_ids(ids)
    assert deleted == 3
    for mid in ids:
        assert mem_db.get_memory_by_id(mid) is None


def test_memory_batch_delete_empty_list(mem_db):
    assert mem_db.delete_memories_by_ids([]) == 0


def test_memory_get_recent(mem_db):
    for i in range(5):
        mem_db.add_memory(topic=f"recent_{i}", summary=f"s{i}")
    recent = mem_db.get_recent_memories(limit=3)
    assert len(recent) == 3


def test_memory_nonexistent_id_returns_none(mem_db):
    assert mem_db.get_memory_by_id(99999) is None


# ==================== MemoDatabase ====================

def test_memo_add_and_get(memo_db):
    reminder = (datetime.now() + timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    mid = memo_db.add_memo(title="测试任务", content="内容", reminder_time=reminder)
    assert isinstance(mid, int) and mid > 0

    m = memo_db.get_memo(mid)
    assert m is not None
    assert m["title"] == "测试任务"
    assert m["is_completed"] == 0


def test_memo_pending_filter_by_time(memo_db):
    """未到期的任务不应出现在 pending 列表中。"""
    future = (datetime.now() + timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    past = (datetime.now() - timedelta(days=1)).strftime(constants.DATETIME_FORMAT)

    memo_db.add_memo(title="未来任务", reminder_time=future)
    memo_db.add_memo(title="过期任务", reminder_time=past)

    pending = memo_db.get_pending_memos()
    titles = [m["title"] for m in pending]
    assert "过期任务" in titles
    assert "未来任务" not in titles


def test_memo_complete(memo_db):
    reminder = (datetime.now() + timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    mid = memo_db.add_memo(title="一次性任务", reminder_time=reminder)
    assert memo_db.complete_memo(mid) is True

    m = memo_db.get_memo(mid)
    assert m["is_completed"] == 1


def test_memo_complete_nonexistent_returns_false(memo_db):
    assert memo_db.complete_memo(99999) is False


def test_memo_immediate(memo_db):
    """即时任务应被 get_immediate_memos 返回。"""
    mid = memo_db.add_memo(title="即时任务", is_immediate=True)
    imm = memo_db.get_immediate_memos()
    assert any(m["id"] == mid for m in imm)


def test_memo_mark_as_triggered_completes_one_shot(memo_db):
    """一次性任务触发后应标记完成。"""
    past = (datetime.now() - timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    mid = memo_db.add_memo(title="一次性", reminder_time=past)
    nxt = memo_db.mark_as_triggered(mid)
    assert nxt is None  # 一次性任务无下次提醒
    m = memo_db.get_memo(mid)
    assert m["is_completed"] == 1
    assert m["trigger_count"] == 1


def test_memo_mark_as_triggered_daily_repeat(memo_db):
    """daily 重复任务触发后应计算下次提醒时间。"""
    past = (datetime.now() - timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    mid = memo_db.add_memo(title="每日任务", reminder_time=past, repeat_type="daily")
    nxt = memo_db.mark_as_triggered(mid)
    assert nxt is not None
    # 下次应在未来
    next_dt = datetime.strptime(nxt, constants.DATETIME_FORMAT)
    assert next_dt > datetime.now() - timedelta(minutes=1)
    m = memo_db.get_memo(mid)
    assert m["is_completed"] == 0  # 重复任务不完成


def test_memo_stats(memo_db):
    past = (datetime.now() - timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    future = (datetime.now() + timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    memo_db.add_memo(title="过期", reminder_time=past)
    memo_db.add_memo(title="未来", reminder_time=future)
    memo_db.add_memo(title="即时", is_immediate=True)

    stats = memo_db.get_memo_stats()
    assert stats["total"] == 3
    assert stats["pending"] == 3
    assert stats["overdue"] == 1  # 只有过期且非即时的
    assert stats["immediate"] == 1


def test_memo_get_all_excludes_completed_by_default(memo_db):
    reminder = (datetime.now() + timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    mid = memo_db.add_memo(title="待完成", reminder_time=reminder)
    memo_db.complete_memo(mid)

    all_memos = memo_db.get_all_memos(include_completed=False)
    assert all(m["is_completed"] == 0 for m in all_memos)

    all_with_completed = memo_db.get_all_memos(include_completed=True)
    assert any(m["id"] == mid for m in all_with_completed)


def test_memo_get_pending_tasks_for_user(memo_db):
    past = (datetime.now() - timedelta(days=1)).strftime(constants.DATETIME_FORMAT)
    memo_db.add_memo(title="用户任务", reminder_time=past)

    tasks = memo_db.get_pending_tasks_for_user()
    assert len(tasks) >= 1
    assert all("id" in t and "title" in t and "is_overdue" in t for t in tasks)
    assert any(t["title"] == "用户任务" and t["is_overdue"] for t in tasks)


def test_memo_add_user_task_disabled(monkeypatch, memo_db):
    """当 MEMO_ALLOW_USER_TASKS=False 时应抛异常。"""
    monkeypatch.setattr(type(constants), "MEMO_ALLOW_USER_TASKS",
                        property(lambda self: False))
    with pytest.raises(Exception, match="禁用"):
        memo_db.add_user_task(title="x")


def test_memo_custom_repeat_minutes(memo_db):
    """custom 重复 + minutes 单位应正确计算下次。"""
    past = (datetime.now() - timedelta(minutes=1)).strftime(constants.DATETIME_FORMAT)
    mid = memo_db.add_memo(
        title="每5分钟", reminder_time=past,
        repeat_type="custom", repeat_interval=5, repeat_interval_unit="minutes"
    )
    nxt = memo_db.mark_as_triggered(mid)
    assert nxt is not None
    next_dt = datetime.strptime(nxt, constants.DATETIME_FORMAT)
    # 下次约在 5 分钟后
    delta = next_dt - datetime.now()
    assert timedelta(minutes=4) < delta < timedelta(minutes=6)
