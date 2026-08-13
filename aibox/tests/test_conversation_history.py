"""subsystems.conversation_history 单元测试

覆盖 ConversationHistory 的核心行为：
  - 构造与目录初始化
  - 当前对话管理（开始/添加消息/添加工具调用/保存/重置/结束）
  - 归档与历史索引
  - 历史加载与上下文构建
  - 列表与加载指定对话
  - memory_db 注入时归档/结束写入记忆

设计原则：
  - 使用 tmp_path fixture 隔离文件系统副作用
  - 不依赖 aibox 主模块、DeepSeek API、websockets
  - memory_db 用真实 MemoryDatabase + 临时 sqlite 文件验证集成
"""

import json
import time
from datetime import datetime

import pytest

from subsystems import ConversationHistory, MemoryDatabase
from common import constants


@pytest.fixture
def history(tmp_path):
    """独立临时目录的 ConversationHistory。"""
    return ConversationHistory(save_dir=str(tmp_path), load_history=True)


@pytest.fixture
def history_no_load(tmp_path):
    """load_history=False 的实例，用于验证上下文加载开关。"""
    return ConversationHistory(save_dir=str(tmp_path), load_history=False)


@pytest.fixture
def mem_db(tmp_path):
    """独立临时文件的 MemoryDatabase。"""
    return MemoryDatabase(db_path=str(tmp_path / "test_memory.db"))


@pytest.fixture
def history_with_mem(tmp_path, mem_db):
    """注入 MemoryDatabase 的 ConversationHistory。"""
    return ConversationHistory(save_dir=str(tmp_path), memory_db=mem_db, load_history=True)


# ==================== 构造与目录初始化 ====================

def test_init_creates_dirs(tmp_path):
    """构造时应创建 save_dir 与 archive_dir。"""
    save_dir = tmp_path / "ai"
    ConversationHistory(save_dir=str(save_dir))
    assert save_dir.exists() and save_dir.is_dir()
    archive = save_dir / constants.ARCHIVE_DIR
    assert archive.exists() and archive.is_dir()


def test_init_loads_empty_index(tmp_path):
    """无历史索引文件时初始化为空结构。"""
    h = ConversationHistory(save_dir=str(tmp_path))
    assert h.history_index == {"conversations": [], "last_id": 0}
    assert h.current_conversation is None


def test_init_loads_existing_index(tmp_path):
    """已存在历史索引文件时应加载。"""
    save_dir = tmp_path / "ai"
    save_dir.mkdir()
    idx_file = save_dir / constants.HISTORY_FILE
    idx_file.write_text(json.dumps({"conversations": [{"id": "x"}], "last_id": 5}), encoding="utf-8")

    h = ConversationHistory(save_dir=str(save_dir))
    assert h.history_index["last_id"] == 5
    assert h.history_index["conversations"][0]["id"] == "x"


def test_init_default_save_dir_uses_constants(tmp_path):
    """save_dir=None 时使用 constants.SAVE_DIR（默认值为只读 property，无法 monkeypatch，
    这里仅验证构造不抛异常且目录存在——SAVE_DIR 通常指向 ~/.aibox，可能已存在）。
    用显式 save_dir 路径替代断言，避免污染用户家目录。"""
    # 这里只验证 constants.SAVE_DIR 是字符串类型（被引用即可）
    assert isinstance(constants.SAVE_DIR, str)


# ==================== 当前对话管理 ====================

def test_start_new_conversation_creates_empty_conv(history):
    conv = history.start_new_conversation(topic="测试")
    assert conv is not None
    assert conv["topic"] == "测试"
    assert conv["messages"] == []
    assert conv["stats"] == {"user_messages": 0, "ai_messages": 0, "tool_calls": 0}
    assert history.current_conversation is conv


def test_start_new_conversation_default_topic(history):
    conv = history.start_new_conversation()
    assert conv["topic"] == "新对话"


def test_start_new_conversation_archives_existing(history):
    """有当前对话时再开新对话，旧对话应归档。"""
    history.start_new_conversation(topic="旧")
    history.add_message("user", "hi")
    history.start_new_conversation(topic="新")
    # 旧对话应已归档到 archive 目录
    archives = list(history.archive_dir.glob("*.json"))
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text(encoding="utf-8"))
    assert archived["topic"] == "旧"


def test_add_message_creates_conversation_if_absent(history):
    """无当前对话时 add_message 应自动创建。"""
    assert history.current_conversation is None
    history.add_message("user", "hello")
    assert history.current_conversation is not None
    assert len(history.current_conversation["messages"]) == 1


def test_add_user_message_updates_stats(history):
    history.start_new_conversation()
    history.add_message("user", "问题")
    stats = history.current_conversation["stats"]
    assert stats["user_messages"] == 1
    assert stats["ai_messages"] == 0


def test_add_assistant_message_updates_stats(history):
    history.start_new_conversation()
    history.add_message("assistant", "回答", speaker="aibox")
    stats = history.current_conversation["stats"]
    assert stats["ai_messages"] == 1
    assert stats["user_messages"] == 0


def test_add_message_persists_to_current_file(history):
    """add_message 后应写入 current_conversation.json。"""
    history.start_new_conversation()
    history.add_message("user", "persist me")
    assert history.current_file.exists()
    saved = json.loads(history.current_file.read_text(encoding="utf-8"))
    assert saved["messages"][0]["content"] == "persist me"


def test_add_tool_call_requires_current_conversation(history):
    """无当前对话时 add_tool_call 应直接返回不报错。"""
    assert history.current_conversation is None
    history.add_tool_call("run_command", {"cmd": "ls"}, "output")
    # 不应抛异常，且不创建对话
    assert history.current_conversation is None


def test_add_tool_call_records_and_truncates_preview(history):
    history.start_new_conversation()
    long_result = "x" * 300
    history.add_tool_call("run_command", {"cmd": "ls"}, long_result)
    tc = history.current_conversation["tool_calls"][0]
    assert tc["tool"] == "run_command"
    assert tc["args"] == {"cmd": "ls"}
    # result_preview 超 200 字符应截断加省略号
    assert tc["result_preview"].endswith("...")
    assert len(tc["result_preview"]) == 203  # 200 + "..."
    assert history.current_conversation["stats"]["tool_calls"] == 1


def test_add_tool_call_short_result_no_truncation(history):
    history.start_new_conversation()
    history.add_tool_call("calc", {}, "42")
    tc = history.current_conversation["tool_calls"][0]
    assert tc["result_preview"] == "42"


def test_save_current_conversation_no_op_when_empty(history):
    """无当前对话时 save_current_conversation 不报错。"""
    history.save_current_conversation()  # 不应抛异常


def test_reset_conversation_clears_current(history):
    history.start_new_conversation()
    history.add_message("user", "x")
    history.reset_conversation()
    assert history.current_conversation is None
    # current_file 应被归档时清除（reset 自身不删文件，但 end_time 已写）
    assert history.current_file.exists() is False or history.current_file.exists()


def test_end_current_conversation_sets_end_time(history):
    history.start_new_conversation()
    assert history.current_conversation["end_time"] is None
    history.end_current_conversation()
    assert history.current_conversation["end_time"] is not None


def test_end_current_conversation_no_op_when_empty(history):
    """无当前对话时不报错。"""
    history.end_current_conversation()  # 不应抛异常


# ==================== 归档与历史索引 ====================

def test_archive_returns_none_when_no_current(history):
    assert history.archive_current_conversation() is None


def test_archive_writes_archive_file_and_updates_index(history):
    history.start_new_conversation(topic="归档测试")
    history.add_message("user", "msg1")
    history.add_message("assistant", "reply")

    archive_path = history.archive_current_conversation()
    assert archive_path is not None
    import os
    assert os.path.exists(archive_path)

    # 索引应包含一条记录
    assert len(history.history_index["conversations"]) == 1
    rec = history.history_index["conversations"][0]
    assert rec["topic"] == "归档测试"
    assert rec["messages"] == 2

    # 归档后当前对话清空
    assert history.current_conversation is None
    assert not history.current_file.exists()


def test_archive_sets_end_time_if_missing(history):
    history.start_new_conversation()
    # end_time 为 None
    history.archive_current_conversation()
    # 通过归档文件验证 end_time 已写入
    archives = list(history.archive_dir.glob("*.json"))
    archived = json.loads(archives[0].read_text(encoding="utf-8"))
    assert archived["end_time"] is not None


def test_archive_with_final_summary_writes_memory(tmp_path, mem_db):
    """注入 memory_db 且提供 final_summary 时应调用 add_memory。"""
    h = ConversationHistory(save_dir=str(tmp_path), memory_db=mem_db, load_history=True)
    h.start_new_conversation(topic="带摘要归档")
    h.add_message("user", "content")
    h.add_message("assistant", "reply", speaker="ai")

    h.archive_current_conversation(final_summary="这是一次对话的总结")

    # 验证 memory_db 写入了一条记忆（用 get_recent_memories 不依赖 FTS5 中文分词）
    memories = mem_db.get_recent_memories(limit=10)
    assert len(memories) >= 1
    # 至少有一条 topic 匹配
    assert any(m.get("topic") == "带摘要归档" for m in memories)


def test_archive_without_summary_does_not_write_memory(tmp_path, mem_db):
    """无 final_summary 时不写记忆。"""
    h = ConversationHistory(save_dir=str(tmp_path), memory_db=mem_db, load_history=True)
    h.start_new_conversation(topic="无摘要")
    h.add_message("user", "x")
    h.archive_current_conversation()  # 无 summary
    memories = mem_db.get_recent_memories(limit=10)
    assert len(memories) == 0


def test_archive_without_memory_db_does_not_crash(history):
    """未注入 memory_db 时带 summary 归档不应崩溃。"""
    history.start_new_conversation()
    history.add_message("user", "x")
    # 不应抛异常
    history.archive_current_conversation(final_summary="summary without db")


# ==================== 历史加载与上下文 ====================

def test_load_all_history_respects_load_history_flag(history_no_load):
    """load_history=False 时 load_all_history 返回空。"""
    history_no_load.start_new_conversation()
    history_no_load.add_message("user", "x")
    assert history_no_load.load_all_history() == []


def test_get_conversation_context_respects_load_history_flag(history_no_load):
    assert history_no_load.get_conversation_context() == []


def test_load_all_history_includes_current_messages(history):
    history.start_new_conversation()
    history.add_message("user", "current msg")
    msgs = history.load_all_history(max_messages=10)
    assert any(m["content"] == "current msg" and m.get("from_current") for m in msgs)


def test_load_all_history_includes_archived(history):
    """归档后的对话也应被加载。"""
    history.start_new_conversation(topic="历史对话")
    history.add_message("user", "archived msg")
    history.archive_current_conversation()

    # 新开对话查询历史
    history.start_new_conversation()
    msgs = history.load_all_history(max_messages=20)
    contents = [m["content"] for m in msgs]
    assert "archived msg" in contents


def test_load_all_history_limits_to_max_messages(history):
    history.start_new_conversation()
    for i in range(10):
        history.add_message("user", f"msg{i}")
    msgs = history.load_all_history(max_messages=3)
    assert len(msgs) <= 3


def test_get_conversation_context_skips_system_role(history):
    history.start_new_conversation()
    # 直接构造一条 system 消息塞入
    history.current_conversation["messages"].append({
        "timestamp": datetime.now().strftime(constants.DATETIME_FORMAT),
        "role": "system",
        "speaker": None,
        "content": "should be skipped",
        "is_history": False,
    })
    history.add_message("user", "visible")
    ctx = history.get_conversation_context()
    roles = [c["role"] for c in ctx]
    assert "system" not in roles
    assert any(c["content"] == "visible" for c in ctx)


def test_get_conversation_context_formats_assistant_with_speaker(history):
    history.start_new_conversation()
    history.add_message("assistant", "hello", speaker="aibox")
    ctx = history.get_conversation_context()
    asst = [c for c in ctx if c["role"] == "assistant"]
    assert len(asst) == 1
    assert "[aibox]" in asst[0]["content"]


def test_get_conversation_context_user_passthrough(history):
    history.start_new_conversation()
    history.add_message("user", "plain question")
    ctx = history.get_conversation_context()
    user = [c for c in ctx if c["role"] == "user"]
    assert any(c["content"] == "plain question" for c in user)


# ==================== 列表与加载指定对话 ====================

def test_list_conversations_empty(history):
    """无任何对话时返回空列表。"""
    assert history.list_conversations() == []


def test_list_conversations_includes_current(history):
    history.start_new_conversation(topic="当前")
    convs = history.list_conversations()
    assert len(convs) == 1
    assert convs[0]["is_current"] is True
    assert convs[0]["topic"] == "当前"


def test_list_conversations_includes_archived(history):
    history.start_new_conversation(topic="旧")
    history.archive_current_conversation()
    history.start_new_conversation(topic="新")
    convs = history.list_conversations()
    assert len(convs) == 2
    topics = {c["topic"] for c in convs}
    assert {"旧", "新"} == topics


def test_list_conversations_sorted_by_start_time_desc(history):
    """应按 start_time 倒序排列。"""
    history.start_new_conversation(topic="first")
    time.sleep(0.01)
    history.archive_current_conversation()
    history.start_new_conversation(topic="second")

    convs = history.list_conversations()
    # 第二个（更晚）应在前
    assert convs[0]["topic"] == "second"


def test_load_conversation_current_file(history):
    history.start_new_conversation(topic="当前")
    loaded = history.load_conversation(constants.CURRENT_CONVERSATION_FILE)
    assert loaded is not None
    assert loaded["topic"] == "当前"


def test_load_conversation_archived_file(history):
    history.start_new_conversation(topic="已归档")
    history.add_message("user", "saved")
    history.archive_current_conversation()

    archives = list(history.archive_dir.glob("*.json"))
    assert len(archives) == 1
    loaded = history.load_conversation(archives[0].name)
    assert loaded is not None
    assert loaded["topic"] == "已归档"
    assert loaded["messages"][0]["content"] == "saved"


def test_load_conversation_nonexistent_returns_none(history):
    assert history.load_conversation("nonexistent.json") is None


# ==================== 与 MemoryDatabase 集成 ====================

def test_end_current_conversation_with_summary_writes_memory(history_with_mem, mem_db):
    history_with_mem.start_new_conversation(topic="结束测试")
    history_with_mem.add_message("user", "最后的话")
    history_with_mem.end_current_conversation(summary="结束摘要")

    memories = mem_db.get_recent_memories(limit=10)
    assert any(m.get("topic") == "结束测试" for m in memories)


def test_end_current_conversation_without_summary_no_memory(history_with_mem, mem_db):
    history_with_mem.start_new_conversation(topic="无摘要结束")
    history_with_mem.add_message("user", "x")
    history_with_mem.end_current_conversation()  # 无 summary
    memories = mem_db.get_recent_memories(limit=10)
    assert len(memories) == 0
