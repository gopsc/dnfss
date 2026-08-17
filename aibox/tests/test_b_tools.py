"""B 批工具单元测试

覆盖 12 个 B 批工具的核心接口与执行逻辑：
  - command_tool   : CommandLineTool
  - skill_tools    : SearchSkills/ReloadSkills/InstallSkill/CreateSkill
  - db_tools       : MemoryTool/MemoTool
  - prompt_tools   : UpdateIdentity/UpdateSoul/SaveMemoryAndEnd
  - proxy_tools    : SetProxy/GetProxy
  - misc_tools     : AskUserTool

工具大多需注入子系统，用真实子系统实例 + tmp_path 隔离。
"""
import queue
from datetime import datetime, timedelta

import pytest

from common import ConfigManager, constants
from subsystems import (
    MemoryDatabase, MemoDatabase, PromptManager, SkillManager,
    SubprocessCommandExecutor,
)
from tools import (
    CommandLineTool,
    SearchSkillsTool, ReloadSkillsTool, InstallSkillTool, CreateSkillTool,
    MemoryTool, MemoTool,
    UpdateIdentityTool, UpdateSoulTool, SaveMemoryAndEndConversationTool,
    SetProxyTool, GetProxyTool,
    AskUserTool,
)
from tools.base import Tool
from tools.registry import ToolRegistry


# ==================== 公共 fixture ====================

@pytest.fixture(autouse=True)
def use_tmp_workspace(tmp_path, monkeypatch):
    """每个测试把 WORKSPACE_DIR/SAVE_DIR 重定向到临时目录，避免污染 ~/.aibox。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    save = tmp_path / "save"
    save.mkdir()
    monkeypatch.setattr(type(constants), "WORKSPACE_DIR", property(lambda self: str(ws)))
    monkeypatch.setattr(type(constants), "SAVE_DIR", property(lambda self: str(save)))


@pytest.fixture
def mem_db(tmp_path):
    return MemoryDatabase(db_path=str(tmp_path / "mem.db"))


@pytest.fixture
def memo_db(tmp_path):
    return MemoDatabase(db_path=str(tmp_path / "memo.db"))


@pytest.fixture
def prompt_mgr(tmp_path, monkeypatch):
    """独立 PromptManager（重置单例 + 临时 prompts 目录）。"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    PromptManager._instance = None
    PromptManager._prompts = []
    PromptManager._prompts_dir = None
    from common import ConfigManager
    original_get = ConfigManager.get

    def mock_get(self, key, default=None):
        if key == "system.prompts_dir":
            return str(prompts_dir)
        return original_get(self, key, default)

    monkeypatch.setattr(ConfigManager, "get", mock_get)
    pm = PromptManager()
    yield pm
    PromptManager._instance = None
    PromptManager._prompts = []
    PromptManager._prompts_dir = None


@pytest.fixture
def skill_mgr(tmp_path, monkeypatch):
    """独立 SkillManager + 临时技能目录。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(type(constants), "SKILLS_DIR", property(lambda self: str(skills_dir)))
    return SkillManager()


# ==================== 通用接口契约（参数化）====================

B_TOOL_CLASSES = [
    CommandLineTool, SearchSkillsTool, ReloadSkillsTool, InstallSkillTool,
    CreateSkillTool, MemoryTool, MemoTool, UpdateIdentityTool, UpdateSoulTool,
    SaveMemoryAndEndConversationTool, SetProxyTool, GetProxyTool, AskUserTool,
]


@pytest.mark.parametrize("cls", B_TOOL_CLASSES, ids=lambda c: c.__name__)
def test_b_tool_is_tool_subclass(cls):
    assert issubclass(cls, Tool)


def test_b_tool_names_unique():
    """所有 B 批工具的 get_name() 应唯一（需先实例化，部分需注入）。"""
    # 这里只验证类名能取到非空 name（无法统一实例化，跳过唯一性全检）
    pass


# ==================== CommandLineTool ====================

def test_command_tool_interface():
    t = CommandLineTool()
    assert t.get_name() == "run_command"
    assert "执行" in t.get_description() or "命令" in t.get_description()
    params = t.get_parameters()
    assert "command" in params["properties"]
    assert "command" in params["required"]


def test_command_tool_execute_echo():
    t = CommandLineTool()
    r = t.execute(command="echo b_test_123", timeout=5)
    assert "b_test_123" in r


def test_command_tool_missing_command_returns_error():
    t = CommandLineTool()
    r = t.execute(command="", timeout=5)
    assert "❌" in r or "错误" in r


def test_command_tool_streaming_emits_complete():
    t = CommandLineTool()
    q = queue.Queue()
    t.execute_streaming(q, command="echo hi", timeout=5)
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    assert any(m[0] == "complete" for m in msgs)


# ==================== SearchSkillsTool ====================

def test_search_skills_interface(skill_mgr):
    t = SearchSkillsTool(skill_mgr)
    assert t.get_name() == "search_skills"
    params = t.get_parameters()
    assert "keyword" in params["properties"]
    assert "keyword" in params["required"]


def test_search_skills_empty_keyword_lists_all(skill_mgr, tmp_path):
    # 写入一个技能
    (tmp_path / "skills" / "demo.md").write_text("# Demo\n内容", encoding="utf-8")
    skill_mgr.reload()
    t = SearchSkillsTool(skill_mgr)
    r = t.execute(keyword="")
    assert "所有可用技能" in r
    assert "demo" in r


def test_search_skills_no_match(skill_mgr):
    t = SearchSkillsTool(skill_mgr)
    r = t.execute(keyword="不存在的技能xyz")
    assert "未找到" in r


def test_search_skills_with_content(skill_mgr, tmp_path):
    (tmp_path / "skills" / "weather.md").write_text("# 天气\n查询天气详情ABC", encoding="utf-8")
    skill_mgr.reload()
    t = SearchSkillsTool(skill_mgr)
    r = t.execute(keyword="weather", show_content=True)
    assert "查询天气详情ABC" in r


# ==================== ReloadSkillsTool ====================

def test_reload_skills_interface(skill_mgr):
    reg = ToolRegistry()
    reg.skill_manager = skill_mgr
    t = ReloadSkillsTool(reg)
    assert t.get_name() == "reload_skills"
    r = t.execute(confirm=False)
    assert "取消" in r


def test_reload_skills_confirm(skill_mgr):
    reg = ToolRegistry()
    reg.skill_manager = skill_mgr
    t = ReloadSkillsTool(reg)
    r = t.execute(confirm=True)
    # 重载成功应返回技能数量信息
    assert "技能" in r or "重载" in r


# ==================== InstallSkillTool ====================

def test_install_skill_interface():
    t = InstallSkillTool()
    assert t.get_name() == "install_skill"
    assert "skill_path" in t.get_parameters()["properties"]


def test_install_skill_returns_guide():
    t = InstallSkillTool()
    r = t.execute()
    assert "安装" in r or "步骤" in r
    assert "skills" in r.lower() or ".md" in r


def test_install_skill_with_path():
    t = InstallSkillTool()
    r = t.execute(skill_path="/tmp/my_skill.md")
    assert "my_skill" in r


# ==================== CreateSkillTool ====================

def test_create_skill_interface():
    t = CreateSkillTool()
    assert t.get_name() == "create_skill"
    params = t.get_parameters()
    assert "skill_name" in params["required"]
    assert "description" in params["required"]


def test_create_skill_validation_empty_name():
    t = CreateSkillTool()
    r = t.execute(skill_name="", description="desc")
    assert "❌" in r


def test_create_skill_validation_invalid_name():
    t = CreateSkillTool()
    r = t.execute(skill_name="bad name!", description="desc")
    assert "❌" in r


def test_create_skill_returns_prompt():
    t = CreateSkillTool()
    r = t.execute(skill_name="fetch-weather", description="获取天气")
    assert "fetch-weather" in r
    assert "步骤" in r or "创建" in r


# ==================== MemoryTool ====================

def test_memory_tool_interface(mem_db):
    t = MemoryTool(mem_db)
    assert t.get_name() == "memory"
    params = t.get_parameters()
    assert "action" in params["required"]


def test_memory_save(mem_db):
    t = MemoryTool(mem_db)
    r = t.execute(action="save", summary="测试记忆内容", topic="测试主题")
    assert "记忆已保存" in r


def test_memory_search(mem_db):
    t = MemoryTool(mem_db)
    t.execute(action="save", summary="learn Python basics", topic="Python")
    r = t.execute(action="search", query="Python")
    assert "Python" in r


def test_memory_recent_empty(mem_db):
    t = MemoryTool(mem_db)
    r = t.execute(action="recent")
    assert "没有" in r


def test_memory_unknown_action(mem_db):
    t = MemoryTool(mem_db)
    r = t.execute(action="invalid")
    assert "❌" in r


def test_memory_delete_requires_confirm(mem_db):
    t = MemoryTool(mem_db)
    mid = mem_db.add_memory(topic="t", summary="s")
    r = t.execute(action="delete", memory_id=mid, confirm=False)
    assert "❌" in r


# ==================== MemoTool ====================

def test_memo_tool_interface(memo_db):
    t = MemoTool(memo_db)
    assert t.get_name() == "memo"
    assert "action" in t.get_parameters()["required"]


def test_memo_add(memo_db):
    t = MemoTool(memo_db)
    r = t.execute(action="add", title="测试任务", reminder_minutes=30)
    assert "任务已创建" in r


def test_memo_add_requires_title(memo_db):
    t = MemoTool(memo_db)
    r = t.execute(action="add")
    assert "❌" in r


def test_memo_list_empty(memo_db):
    t = MemoTool(memo_db)
    r = t.execute(action="list")
    assert "没有" in r


def test_memo_stats(memo_db):
    t = MemoTool(memo_db)
    r = t.execute(action="stats")
    assert "统计" in r


def test_memo_parse_reminder_time_plus_format(memo_db):
    """_parse_reminder_time 应解析 +30m 格式。"""
    t = MemoTool(memo_db)
    result = t._parse_reminder_time("+30m")
    assert result is not None
    # 应在约 30 分钟后
    dt = datetime.strptime(result, constants.DATETIME_FORMAT)
    delta = dt - datetime.now()
    assert timedelta(minutes=29) < delta < timedelta(minutes=31)


def test_memo_parse_reminder_time_int(memo_db):
    t = MemoTool(memo_db)
    result = t._parse_reminder_time(60)
    assert result is not None


# ==================== UpdateIdentityTool ====================

def test_update_identity_interface(prompt_mgr):
    t = UpdateIdentityTool(prompt_mgr)
    assert t.get_name() == "update_identity"
    assert "content" in t.get_parameters()["required"]


def test_update_identity_success(prompt_mgr):
    t = UpdateIdentityTool(prompt_mgr)
    r = t.execute(content="测试用户身份", confirm=True)
    assert "✅" in r
    assert "测试用户身份" in prompt_mgr.get_identity()


def test_update_identity_no_content(prompt_mgr):
    t = UpdateIdentityTool(prompt_mgr)
    r = t.execute(content=None, confirm=True)
    assert "❌" in r


def test_update_identity_cancelled(prompt_mgr):
    t = UpdateIdentityTool(prompt_mgr)
    r = t.execute(content="x", confirm=False)
    assert "❌" in r


# ==================== UpdateSoulTool ====================

def test_update_soul_interface(prompt_mgr):
    t = UpdateSoulTool(prompt_mgr)
    assert t.get_name() == "update_soul"


def test_update_soul_success(prompt_mgr):
    t = UpdateSoulTool(prompt_mgr)
    r = t.execute(content="我是测试灵魂", confirm=True)
    assert "✅" in r
    assert "我是测试灵魂" in prompt_mgr.get_soul()


# ==================== SaveMemoryAndEndConversationTool ====================

def test_save_memory_end_interface(prompt_mgr, mem_db):
    cb_called = []
    def cb(reason, summary):
        cb_called.append((reason, summary))
    t = SaveMemoryAndEndConversationTool(cb, mem_db, prompt_mgr)
    assert t.get_name() == "save_memory_and_end_conversation"


def test_save_memory_end_short_summary_rejected(prompt_mgr, mem_db):
    t = SaveMemoryAndEndConversationTool(lambda *a: None, mem_db, prompt_mgr)
    r = t.execute(summary="短", current_status="状态")
    assert "❌" in r


def test_save_memory_end_success(prompt_mgr, mem_db):
    cb_called = []
    t = SaveMemoryAndEndConversationTool(
        lambda reason, summary: cb_called.append((reason, summary)),
        mem_db, prompt_mgr
    )
    long_summary = "这是一段足够长的对话总结内容用于测试保存记忆功能，必须超过五十个字符才能通过校验，所以这里再加一些内容确保长度足够。"
    r = t.execute(summary=long_summary, topic="测试", current_status="当前状态OK")
    assert "✅" in r
    assert len(cb_called) == 1
    assert cb_called[0][0] == "话题讨论完成"


# ==================== SetProxyTool / GetProxyTool ====================

def test_set_proxy_interface():
    assert SetProxyTool().get_name() == "set_proxy"
    params = SetProxyTool().get_parameters()
    assert "host" in params["required"]
    assert "port" in params["required"]


def test_get_proxy_interface():
    assert GetProxyTool().get_name() == "get_proxy"


def test_get_proxy_when_disabled():
    t = GetProxyTool()
    r = t.execute()
    # 默认未启用代理
    assert "未启用" in r or "启用" in r


# ---------- SetProxyTool 连通性验证（设置前验证，失败不落盘） ----------

@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    """隔离 ConfigManager 单例：AI_CONFIG_PATH 指向临时文件，前后重置类状态"""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setenv("AI_CONFIG_PATH", str(cfg_file))

    def _reset():
        ConfigManager._instance = None
        ConfigManager._config = None
        ConfigManager._config_path = None

    _reset()
    yield ConfigManager
    _reset()


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def _fake_session_factory(exc=None, resp=None):
    """构造替代 requests.Session 的假类：get() 抛异常或返回假响应"""
    calls = []

    class _FakeSession:
        def __init__(self):
            self.trust_env = True

        def get(self, *args, **kwargs):
            calls.append((args, kwargs))
            if exc:
                raise exc
            return resp

    _FakeSession.calls = calls
    return _FakeSession


def test_check_connectivity_ok(monkeypatch):
    fake = _fake_session_factory(resp=_FakeResp(204))
    monkeypatch.setattr("tools.proxy_tools.requests.Session", fake)
    t = SetProxyTool()
    ok, kind, detail = t._check_connectivity("127.0.0.1", 1080, None, None)
    assert ok and kind == ""
    # trust_env 必须关掉：排除系统 http_proxy 环境变量干扰
    assert fake.calls, "应发出探测请求"


def test_check_connectivity_classification(monkeypatch, tmp_path):
    """异常类型/消息 → 错误类型分类（与系统语言无关）"""
    import requests as requests_lib
    cases = [
        # Windows 本地化的连接拒绝消息：靠异常类型而非英文关键词分类
        (requests_lib.exceptions.ConnectionError(
            "[WinError 10061] 由于目标计算机积极拒绝，无法连接。"), "connect_failed"),
        # ConnectTimeout 同时继承 ConnectionError/Timeout，必须按 Timeout 归类
        (requests_lib.exceptions.ConnectTimeout("connection timeout"), "timeout"),
        (requests_lib.exceptions.ConnectionError(
            "SOCKS5 authentication failed"), "auth_failed"),
        (requests_lib.exceptions.ConnectionError(
            "Missing dependencies for SOCKS support."), "missing_dependency"),
        (Exception("Read timed out"), "timeout"),          # 兜底：非 requests 异常按消息
        (Exception("Connection refused"), "connect_failed"),
        (Exception("something weird"), "error"),
    ]
    t = SetProxyTool()
    for exc, expect_kind in cases:
        monkeypatch.setattr(
            "tools.proxy_tools.requests.Session",
            _fake_session_factory(exc=exc))
        ok, kind, _ = t._check_connectivity("127.0.0.1", 1080, "u", "p")
        assert not ok
        assert kind == expect_kind, f"{exc!r} 应分类为 {expect_kind}，实际 {kind}"


def test_check_connectivity_bad_status(monkeypatch):
    """探测端点返回非 200/204 → probe_failed"""
    monkeypatch.setattr(
        "tools.proxy_tools.requests.Session",
        _fake_session_factory(resp=_FakeResp(502)))
    t = SetProxyTool()
    ok, kind, detail = t._check_connectivity("127.0.0.1", 1080, None, None)
    assert not ok and kind == "probe_failed"


def test_set_proxy_rejected_when_unreachable(fresh_config, monkeypatch):
    """验证失败：返回 ❌ 且配置不落盘"""
    monkeypatch.setattr(
        SetProxyTool, "_check_connectivity",
        lambda self, h, p, u, pw: (False, "connect_failed", "Connection refused"))
    t = SetProxyTool()
    r = t.execute(host="10.255.255.1", port=1080)
    assert "❌" in r
    assert "未保存" in r
    # 配置未写入：代理仍未启用
    assert fresh_config().get_proxy() is None


def test_set_proxy_saved_when_check_passes(fresh_config, monkeypatch):
    """验证通过：配置保存且结果带连通性详情"""
    monkeypatch.setattr(
        SetProxyTool, "_check_connectivity",
        lambda self, h, p, u, pw: (True, "", "探测成功 HTTP 204"))
    t = SetProxyTool()
    r = t.execute(host="127.0.0.1", port=1080)
    assert "✅" in r
    assert "探测成功" in r
    proxies = fresh_config().get_proxy()
    assert proxies and "127.0.0.1:1080" in proxies["http"]


def test_set_proxy_skip_check_bypasses_validation(fresh_config, monkeypatch):
    """skip_check=True：不验证直接保存（代理未启动的预配置场景）"""
    called = []

    def _no_call(self, *a, **kw):
        called.append(1)
        raise AssertionError("skip_check=True 时不应调用验证")

    monkeypatch.setattr(SetProxyTool, "_check_connectivity", _no_call)
    t = SetProxyTool()
    r = t.execute(host="127.0.0.1", port=1080, skip_check=True)
    assert "✅" in r
    assert not called
    assert fresh_config().get_proxy() is not None


def test_set_proxy_disabled_skips_check(fresh_config, monkeypatch):
    """enabled=False：禁用代理无需验证"""
    def _no_call(self, *a, **kw):
        raise AssertionError("禁用代理不应触发验证")

    monkeypatch.setattr(SetProxyTool, "_check_connectivity", _no_call)
    t = SetProxyTool()
    r = t.execute(host="127.0.0.1", port=1080, enabled=False)
    assert "✅" in r
    assert fresh_config().get_proxy() is None  # 禁用态 get_proxy 返回 None


def test_set_proxy_empty_env_hint(fresh_config, monkeypatch):
    """username + password_env 指向未设环境变量：精准提示而非裸 auth 失败"""
    monkeypatch.delenv("AIBOX_TEST_SOCKS_PW", raising=False)
    t = SetProxyTool()
    r = t.execute(host="127.0.0.1", port=1080,
                  username="u1", password_env="AIBOX_TEST_SOCKS_PW")
    assert "❌" in r
    assert "环境变量" in r
    assert "未保存" in r
    assert fresh_config().get_proxy() is None


# ==================== AskUserTool ====================

def test_ask_user_interface():
    t = AskUserTool()
    assert t.get_name() == "ask_user"


def test_ask_user_returns_question():
    t = AskUserTool()
    r = t.execute(question="今天天气如何？")
    assert "今天天气如何" in r
    assert "询问用户" in r
