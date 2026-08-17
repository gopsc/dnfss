"""多模型档案（models 配置节）单元测试

覆盖：
  - _resolve_model_profile 三级优先级：档案名命中 > default_model 档案 > 回退 None
  - 裸模型名语义：用默认档案端点、覆盖档案内模型名
  - create_llm_client 档案构造：url/model/timeout 来自档案
  - resolve_api_key：档案级 env 名、空串免密（allow_missing）、缺 key 报错
  - 档案级 reasoning_enabled 附加到 client 并被 DeepSeekChat._reasoning_enabled 读取
  - 向后兼容：无 models 节时走旧 api 扁平节

不依赖真实 API/网络：仅测构造与参数解析，不发请求。
"""

import os
import sys
import types
from unittest import mock

import pytest
import requests

# ---- 环境桩：保证 import aibox.py 不失败 ----
if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

from llm_client import (  # noqa: E402
    create_llm_client,
    resolve_api_key,
    _resolve_model_profile,
)


class FakeConfig:
    """带 get(key, default) 的最小配置桩。"""

    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        cur = self._data
        for k in key.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur


MODELS = {
    "deepseek": {
        "api_url": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-v4-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "timeout": 120,
    },
    "kimi": {
        "api_url": "https://api.moonshot.cn/v1/chat/completions",
        "model": "kimi-k2",
        "api_key_env": "MOONSHOT_API_KEY",
        "reasoning_enabled": True,
    },
    "local": {
        "api_url": "http://localhost:11434/v1/chat/completions",
        "model": "qwen3:32b",
        "api_key_env": "",  # 免密端点
        "timeout": 300,
    },
}


def _cfg(models=MODELS, default_model="deepseek", **api_over):
    api = {
        "provider": "openai_compatible",
        "deepseek_api_url": "https://api.deepseek.com/chat/completions",
        "deepseek_model": "deepseek-v4-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "reasoning_enabled": False,
        "timeout": 30,
        "default_model": default_model,
    }
    api.update(api_over)
    data = {"api": api}
    if models is not None:
        data["models"] = models
    return FakeConfig(data)


# ==================== _resolve_model_profile 优先级 ====================

class TestResolveProfile:
    def test_explicit_profile_name(self):
        """model=档案名 → 完整采用该档案，模型名取档案内 model"""
        profile, model, pname = _resolve_model_profile(_cfg(), "kimi")
        assert profile is MODELS["kimi"]
        assert model == "kimi-k2"
        assert pname == "kimi"

    def test_bare_model_name_uses_default_profile(self):
        """model=裸名 → 默认档案端点 + 裸名作为模型名"""
        profile, model, pname = _resolve_model_profile(_cfg(), "deepseek-v3-legacy")
        assert profile is MODELS["deepseek"]
        assert model == "deepseek-v3-legacy"
        assert pname == "deepseek"

    def test_no_model_uses_default_profile(self):
        """未指定 model → 默认档案与档案内模型名"""
        profile, model, pname = _resolve_model_profile(_cfg(), None)
        assert profile is MODELS["deepseek"]
        assert model == "deepseek-v4-flash"
        assert pname == "deepseek"

    def test_fallback_when_no_models_section(self):
        """无 models 节 → (None, model) 回退旧 api 扁平节"""
        profile, model, pname = _resolve_model_profile(_cfg(models=None), "anything")
        assert profile is None
        assert model == "anything"
        assert pname is None

    def test_fallback_when_default_model_missing(self):
        """default_model 指向不存在的档案且 model 未命中 → 回退"""
        profile, model, pname = _resolve_model_profile(_cfg(default_model="nope"), "whatever")
        assert profile is None
        assert model == "whatever"
        assert pname is None


# ==================== create_llm_client 档案构造 ====================

class TestCreateClient:
    def test_profile_selects_url_model_timeout(self):
        """档案命中 → client 的 url/model/timeout 全部来自档案"""
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            c = create_llm_client(_cfg(), model="kimi")
        assert c.base_url == "https://api.moonshot.cn/v1/chat/completions"
        assert c.model == "kimi-k2"
        assert c.api_key == "sk-moon"

    def test_bare_name_keeps_default_endpoint(self):
        """裸模型名 → 默认档案端点 + 裸模型名"""
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds"}):
            c = create_llm_client(_cfg(), model="deepseek-v4-lite")
        assert c.base_url == "https://api.deepseek.com/chat/completions"
        assert c.model == "deepseek-v4-lite"

    def test_backward_compat_flat_api(self):
        """无 models 节 → 旧 api 扁平节行为完全不变"""
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds"}):
            c = create_llm_client(_cfg(models=None))
        assert c.base_url == "https://api.deepseek.com/chat/completions"
        assert c.model == "deepseek-v4-flash"

    def test_profile_reasoning_flag_attached(self):
        """档案 reasoning_enabled → client 属性附加"""
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            c = create_llm_client(_cfg(), model="kimi")
        assert c.reasoning_enabled is True

    def test_profile_without_env_falls_back_to_api_section(self):
        """档案未配置 api_key_env → 回退 api.api_key_env（用户自定义变量名兼容，回归保证）"""
        models = dict(MODELS)
        models["noenv"] = {
            "api_url": "https://ark.example/v1/chat/completions",
            "model": "doubao-pro",
        }
        with mock.patch.dict(os.environ, {"ARK_API_KEY": "sk-ark"}):
            c = create_llm_client(_cfg(models=models, api_key_env="ARK_API_KEY"),
                                  model="noenv")
        assert c.api_key == "sk-ark"
        assert c.base_url == "https://ark.example/v1/chat/completions"

    def test_no_profile_reasoning_is_none(self):
        """无档案 → reasoning_enabled=None（构造默认，消费方回退全局配置）"""
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds"}):
            c = create_llm_client(_cfg(models=None))
        assert c.reasoning_enabled is None


# ==================== resolve_api_key 档案级解析 ====================

class TestResolveApiKey:
    def test_profile_env_name(self):
        """档案级 api_key_env 指定环境变量"""
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            assert resolve_api_key(None, _cfg(), env_name="MOONSHOT_API_KEY") == "sk-moon"

    def test_empty_env_name_allows_missing(self):
        """api_key_env 空串 + allow_missing → 返回空串（免密端点）"""
        assert resolve_api_key(None, _cfg(), env_name="", allow_missing=True) == ""

    def test_missing_key_raises(self):
        """无 key 且不允许免密 → RuntimeError"""
        env = {k: "" for k in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY")}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("builtins.open", side_effect=OSError):
            with pytest.raises(RuntimeError):
                resolve_api_key(None, _cfg(), env_name="MOONSHOT_API_KEY")

    def test_explicit_key_wins(self):
        """显式传入 api_key 优先"""
        assert resolve_api_key("sk-explicit", _cfg()) == "sk-explicit"


def _key_file_readable(path, *args, **kwargs):
    """mock open：deepseek.key 存在且可读（返回假 key），证明泄漏路径是否被触发。"""
    if "deepseek.key" in str(path):
        import io
        m = io.StringIO("sk-deepseek-leaked")
        m.name = str(path)
        return m
    raise OSError(path)


class TestH1KeyIsolation:
    """H1：跨厂商 API Key 兜底泄漏——档案端点绝不回退读 ~/.cert/deepseek.key。

    场景：deepseek.key 文件存在且可读（mock），若修复缺失，
    档案端点会静默拿到该 DeepSeek Key 发往其他厂商。
    """

    def test_profile_env_missing_never_reads_key_file(self):
        """档案显式 env 名 + 环境变量未设置 + key 文件存在 → 报错而非读文件"""
        env = {k: "" for k in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY")}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("builtins.open", side_effect=_key_file_readable):
            with pytest.raises(RuntimeError, match="MOONSHOT_API_KEY"):
                resolve_api_key(None, _cfg(), env_name="MOONSHOT_API_KEY",
                                allow_key_file=False)

    def test_profile_via_create_client_never_reads_key_file(self):
        """端到端：create_llm_client 档案命中 + env 缺失 + key 文件存在 → 报错"""
        env = {k: "" for k in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY")}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("builtins.open", side_effect=_key_file_readable):
            with pytest.raises(RuntimeError, match="MOONSHOT_API_KEY"):
                create_llm_client(_cfg(), model="kimi")

    def test_profile_no_env_fallback_missing_raises(self):
        """档案未配 api_key_env + 回退变量也未设置 + key 文件存在 → 报错"""
        models = dict(MODELS)
        models["noenv"] = {
            "api_url": "https://ark.example/v1/chat/completions",
            "model": "doubao-pro",
        }
        env = {k: "" for k in ("ARK_API_KEY", "DEEPSEEK_API_KEY")}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("builtins.open", side_effect=_key_file_readable):
            with pytest.raises(RuntimeError, match="ARK_API_KEY"):
                create_llm_client(_cfg(models=models, api_key_env="ARK_API_KEY"),
                                  model="noenv")

    def test_flat_api_still_reads_key_file(self):
        """回归保证：无档案旧扁平节 + 环境变量未设置 → 仍回退读 key 文件"""
        env = {k: "" for k in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY")}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("builtins.open", side_effect=_key_file_readable):
            assert resolve_api_key(None, _cfg(models=None)) == "sk-deepseek-leaked"


# ==================== H2：档案 schema 校验 ====================

class TestH2SchemaValidation:
    """H2：档案缺 api_url/model 或类型错误 → 解析期立即报清晰 ValueError。

    此前：缺 api_url 延迟到 requests InvalidURL；缺 model 会把档案名当
    模型名发出（或 "model": null → API 400），错误远离配置源头。
    """

    def test_explicit_profile_missing_model(self):
        """显式命中档案 + 档案缺 model → 报错（不再把档案名当模型名）"""
        models = {"nomodel": {"api_url": "https://api.example/v1/chat/completions"}}
        with pytest.raises(ValueError, match="nomodel.*model"):
            _resolve_model_profile(_cfg(models=models, default_model=None), "nomodel")

    def test_explicit_profile_missing_api_url(self):
        """显式命中档案 + 档案缺 api_url → 报错而非延迟到 InvalidURL"""
        models = {"nourl": {"model": "some-model"}}
        with pytest.raises(ValueError, match="nourl.*api_url"):
            _resolve_model_profile(_cfg(models=models, default_model=None), "nourl")

    def test_default_profile_missing_model_no_param(self):
        """默认档案缺 model + 未传 model → 报错（此前发出 "model": null）"""
        models = {"deepseek": {"api_url": "https://api.deepseek.com/chat/completions"}}
        with pytest.raises(ValueError, match="deepseek.*model"):
            _resolve_model_profile(_cfg(models=models), None)

    def test_default_profile_missing_model_bare_name_ok(self):
        """默认档案缺 model + 传裸模型名 → 裸名补位，合法"""
        models = {"deepseek": {"api_url": "https://api.deepseek.com/chat/completions"}}
        profile, model, _ = _resolve_model_profile(_cfg(models=models), "deepseek-v4-lite")
        assert profile is models["deepseek"]
        assert model == "deepseek-v4-lite"

    def test_non_dict_profile_rejected(self):
        """档案值非字典（如写成字符串）→ 报错指出实际类型"""
        models = {"bad": "https://api.example/v1/chat/completions"}
        with pytest.raises(ValueError, match="bad.*str"):
            _resolve_model_profile(_cfg(models=models, default_model=None), "bad")

    def test_create_client_raises_on_invalid_profile(self):
        """端到端：create_llm_client 对无效档案抛 ValueError（配置源头暴露）"""
        models = {"kimi": {"api_url": "https://api.moonshot.cn/v1/chat/completions"}}
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            with pytest.raises(ValueError, match="kimi.*model"):
                create_llm_client(_cfg(models=models, default_model=None), model="kimi")


# ==================== H3：--model 服务模式传导 ====================

class TestH3ModelPlumbing:
    """H3：--model 此前仅在 --once 模式生效，WebSocket 服务模式被静默忽略。

    验证进程级覆盖传导链：main() → EnhancedAIManager(model) →
    add_ai / WebSocketHandler._create_session_ai / TaskScheduler._create_task_ai
    三类 AI 实例的 DeepSeekChat(model=...) 构造参数。
    """

    def _stub_logger(self):
        return types.SimpleNamespace(level=0, info=lambda *a, **k: None,
                                     warning=lambda *a, **k: None,
                                     error=lambda *a, **k: None)

    def test_manager_stores_and_passes_model_to_add_ai(self):
        """EnhancedAIManager(model) → add_ai() → DeepSeekChat(model=...)"""
        from aibox import EnhancedAIManager
        mgr = EnhancedAIManager.__new__(EnhancedAIManager)  # 跳过重初始化，只测传导
        mgr.model = "kimi"
        mgr.logger = self._stub_logger()
        mgr.history_manager = None
        mgr.memory_db = None
        mgr.memo_db = None
        mgr.command_executor = None
        mgr.prompt_manager = None
        with mock.patch("aibox.DeepSeekChat") as mc:
            mgr.add_ai()
        assert mc.call_args.kwargs.get("model") == "kimi"

    def test_manager_default_model_is_none(self):
        """未指定 --model → model=None，DeepSeekChat 走配置默认"""
        from aibox import EnhancedAIManager
        mgr = EnhancedAIManager.__new__(EnhancedAIManager)
        mgr.model = None
        mgr.logger = self._stub_logger()
        mgr.history_manager = None
        mgr.memory_db = None
        mgr.memo_db = None
        mgr.command_executor = None
        mgr.prompt_manager = None
        with mock.patch("aibox.DeepSeekChat") as mc:
            mgr.add_ai()
        assert mc.call_args.kwargs.get("model") is None

    def test_session_ai_receives_model(self):
        """WebSocketHandler._create_session_ai → DeepSeekChat(model=...)"""
        from aibox import WebSocketHandler
        handler = WebSocketHandler.__new__(WebSocketHandler)
        handler.ai_manager = types.SimpleNamespace(
            model="kimi", memory_db=None, memo_db=None, prompt_manager=None)
        handler.logger = self._stub_logger()
        handler._shared_skill_manager = object()  # 非 None 即复用，不触发扫描
        with mock.patch("aibox.DeepSeekChat") as mc, \
             mock.patch("aibox.SubprocessCommandExecutor"):
            handler._create_session_ai()
        assert mc.call_args.kwargs.get("model") == "kimi"

    def test_task_ai_receives_model(self):
        """TaskScheduler._create_task_ai → DeepSeekChat(model=...)"""
        from aibox import TaskScheduler
        sched = TaskScheduler.__new__(TaskScheduler)
        sched.ai_manager = types.SimpleNamespace(
            model="kimi", memory_db=None, memo_db=None, prompt_manager=None)
        sched.logger = self._stub_logger()
        sched._shared_skill_manager = object()
        with mock.patch("aibox.DeepSeekChat") as mc, \
             mock.patch("aibox.SubprocessCommandExecutor"):
            sched._create_task_ai()
        assert mc.call_args.kwargs.get("model") == "kimi"

    def test_task_ai_without_manager_passes_none(self):
        """调度器未挂 manager（ai_manager=None）→ model=None 不报错"""
        from aibox import TaskScheduler
        sched = TaskScheduler.__new__(TaskScheduler)
        sched.ai_manager = None
        sched.logger = self._stub_logger()
        sched._shared_skill_manager = object()
        with mock.patch("aibox.DeepSeekChat") as mc, \
             mock.patch("aibox.SubprocessCommandExecutor"):
            sched._create_task_ai()
        assert mc.call_args.kwargs.get("model") is None

    def test_parse_args_model(self):
        """CLI 解析：--model kimi → args.model"""
        from aibox import parse_args
        with mock.patch("sys.argv", ["aibox.py", "--model", "kimi"]):
            args = parse_args()
        assert args.model == "kimi"


# ==================== M2：create_subagent 工具注入档案名列表 ====================

class TestM2SubAgentModelHint:
    """M2：LLM 此前不知道合法档案名，幻觉名走裸名语义（默认端点+错误模型）→ API 400。

    验证 SubAgentTool 的 description 与 model 参数 description 动态携带
    models 节档案名列表（默认档案标注），models 为空时保持原样（零破坏）。
    """

    def _tool_with(self, models, default_model="deepseek"):
        from aibox import SubAgentTool
        tool = SubAgentTool(logger=None)
        tool.config = FakeConfig({
            "models": models or {},
            "api": {"default_model": default_model},
        })
        return tool

    def test_description_lists_profiles(self):
        """工具描述包含全部档案名"""
        tool = self._tool_with(MODELS)
        desc = tool.get_description()
        assert "kimi" in desc and "local" in desc and "deepseek" in desc
        assert "可用模型档案" in desc

    def test_default_profile_marked(self):
        """默认档案带（默认）标注"""
        tool = self._tool_with(MODELS, default_model="kimi")
        desc = tool.get_description()
        assert "kimi（默认）" in desc

    def test_param_schema_lists_profiles(self):
        """JSON Schema 的 model 参数 description 同样携带档案名"""
        tool = self._tool_with(MODELS)
        model_desc = tool.get_parameters()["properties"]["model"]["description"]
        assert "kimi" in model_desc and "deepseek" in model_desc

    def test_empty_models_keeps_original(self):
        """models 为空 → 描述保持原文案，不含提示语（零破坏）"""
        tool = self._tool_with({})
        desc = tool.get_description()
        assert "可用模型档案" not in desc
        model_desc = tool.get_parameters()["properties"]["model"]["description"]
        assert model_desc == "子代理使用的模型名称，默认与主代理一致"

    def test_non_dict_models_tolerated(self):
        """models 节类型异常（如字符串）→ 容错返回空提示"""
        tool = self._tool_with("not-a-dict")
        assert "可用模型档案" not in tool.get_description()


# ==================== M1：set_client 统一替换 LLM 客户端 ====================

class TestM1SetClient:
    """M1：run_full_subagent 换模型后 compressor 仍持旧 client（压缩链用默认模型）。

    验证 DeepSeekChat.set_client 统一替换：client/api_key/name/compressor.llm_client
    四处同步刷新；run_full_subagent 经 set_client 而非直接赋值。
    """

    def test_set_client_refreshes_all_references(self):
        """set_client → client/api_key/name/compressor.llm_client 同步为新 client"""
        from test_once_mode import _make_ai, FakeLLMClient
        ai = _make_ai()
        assert ai.compressor.llm_client is ai.client  # 前置：初始一致
        new_client = FakeLLMClient()
        new_client.api_key = "sk-new"
        new_client.name = "NewVendor"
        ai.set_client(new_client)
        assert ai.client is new_client
        assert ai.api_key == "sk-new"
        assert ai.name == "NewVendor"
        assert ai.compressor.llm_client is new_client

    def test_run_full_subagent_uses_set_client(self):
        """run_full_subagent(model=...) → 经 set_client 替换（compressor 不再持旧 client）"""
        from aibox import run_full_subagent
        fake_sub_chat = mock.MagicMock()
        fake_sub_chat.think_and_respond = mock.MagicMock(
            return_value="任务完成报告")
        fake_sub_chat.stats = {}
        fake_sub_chat.messages = []
        fake_new_client = mock.MagicMock()
        with mock.patch("aibox.DeepSeekChat", return_value=fake_sub_chat), \
             mock.patch("aibox.create_llm_client", return_value=fake_new_client), \
             mock.patch("aibox.SubprocessCommandExecutor"):
            run_full_subagent("测试角色", "测试任务", None,
                              model="kimi", logger=None)
        fake_sub_chat.set_client.assert_called_once_with(fake_new_client)


# ==================== M3：reasoning_enabled 构造参数契约 ====================

class TestM3ReasoningConstructor:
    """M3：档案级 reasoning_enabled 从"事后挂实例属性"改为构造参数。

    契约：LLMClient.reasoning_enabled 三态——True/False（档案级显式，
    含档案缺省时回退全局值的具体化）/ None（无档案，消费方回退全局）。
    """

    def test_profile_true_via_constructor(self):
        """档案显式 reasoning_enabled=True → 构造参数传入 True"""
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            c = create_llm_client(_cfg(), model="kimi")
        assert c.reasoning_enabled is True

    def test_profile_missing_falls_back_to_global_concrete(self):
        """档案未配置该字段 → 具体化为全局 api.reasoning_enabled 的值"""
        models = dict(MODELS)
        models["noreason"] = {
            "api_url": "https://ark.example/v1/chat/completions",
            "model": "doubao-pro",
            "api_key_env": "ARK_API_KEY",
        }
        with mock.patch.dict(os.environ, {"ARK_API_KEY": "sk-ark"}):
            c = create_llm_client(_cfg(models=models), model="noreason")
        assert c.reasoning_enabled is False  # 全局 api.reasoning_enabled=False

    def test_base_client_default_is_none(self):
        """基类构造默认 → reasoning_enabled=None（未配置语义）"""
        from llm_client import DeepSeekClient
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds"}):
            c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        assert c.reasoning_enabled is None

    def test_chat_none_falls_back_to_global(self):
        """DeepSeekChat 消费端：client.reasoning_enabled=None → 全局配置"""
        from test_once_mode import _make_ai
        ai = _make_ai()
        ai.client.reasoning_enabled = None  # 显式置 None（模拟无档案契约）
        assert ai._reasoning_enabled() == constants_reasoning_default()


def constants_reasoning_default():
    from common import constants
    return constants.REASONING_ENABLED


# ==================== L 批：展示名/前缀逃逸/档案温度/思考字段别名 ====================

class TestLBatch:
    """L1 展示名 / L2 model:-profile: 前缀逃逸 / L3 档案级 temperature / L5 思考流字段别名"""

    # ---------- L1：client.name 按档案展示 ----------

    def test_l1_name_is_profile_name(self):
        """档案命中 → client.name = 档案名（不再恒为 DeepSeek）"""
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            c = create_llm_client(_cfg(), model="kimi")
        assert c.name == "kimi"

    def test_l1_display_name_override(self):
        """档案 display_name 字段 > 档案名"""
        models = dict(MODELS)
        models["kimi"] = dict(models["kimi"], display_name="Kimi K2")
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            c = create_llm_client(_cfg(models=models), model="kimi")
        assert c.name == "Kimi K2"

    def test_l1_flat_api_keeps_deepseek(self):
        """旧扁平节（无档案）→ name 保持 DeepSeek（零破坏）"""
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds"}):
            c = create_llm_client(_cfg(models=None))
        assert c.name == "DeepSeek"

    def test_l1_bare_name_uses_default_profile_name(self):
        """裸模型名走默认档案 → name = 默认档案名"""
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds"}):
            c = create_llm_client(_cfg(), model="deepseek-v4-lite")
        assert c.name == "deepseek"

    # ---------- L2：前缀逃逸 ----------

    def test_l2_model_prefix_forces_bare(self):
        """档案恰好叫 deepseek-chat 时 model:deepseek-chat 仍走裸名语义"""
        models = {
            "deepseek-chat": dict(MODELS["deepseek"]),
            "kimi": dict(MODELS["kimi"]),
        }
        profile, model, pname = _resolve_model_profile(
            _cfg(models=models, default_model="kimi"), "model:deepseek-chat")
        assert profile is models["kimi"]        # 默认档案端点
        assert model == "deepseek-chat"          # 裸名作模型名
        assert pname == "kimi"

    def test_l2_profile_prefix_forces_profile(self):
        """profile:kimi 强制档案语义（即便存在同名裸模型的歧义）"""
        profile, model, pname = _resolve_model_profile(_cfg(), "profile:kimi")
        assert profile is MODELS["kimi"]
        assert model == "kimi-k2"
        assert pname == "kimi"

    def test_l2_profile_prefix_missing_raises(self):
        """profile:不存在的档案 → 明确报错（不再静默落回裸名语义）"""
        with pytest.raises(ValueError, match="不存在"):
            _resolve_model_profile(_cfg(), "profile:nonexistent")

    def test_l2_empty_prefix_raises(self):
        """前缀后为空 → 报错"""
        with pytest.raises(ValueError, match="前缀后为空"):
            _resolve_model_profile(_cfg(), "model:")
        with pytest.raises(ValueError, match="前缀后为空"):
            _resolve_model_profile(_cfg(), "profile:")

    def test_l2_prefix_via_create_client(self):
        """端到端：--model model:NAME / profile:NAME 经 create_llm_client 生效"""
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon",
                                          "DEEPSEEK_API_KEY": "sk-ds"}):
            c1 = create_llm_client(_cfg(), model="model:deepseek-v4-lite")
            assert c1.model == "deepseek-v4-lite" and c1.name == "deepseek"
            c2 = create_llm_client(_cfg(), model="profile:kimi")
            assert c2.model == "kimi-k2" and c2.name == "kimi"

    # ---------- L3：档案级 temperature ----------

    def test_l3_profile_temperature_default(self):
        """档案 temperature → client.default_temperature；chat_completion 未传参时生效"""
        models = dict(MODELS)
        models["kimi"] = dict(models["kimi"], temperature=0.15)
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            c = create_llm_client(_cfg(models=models), model="kimi")
        assert c.default_temperature == 0.15

    def test_l3_no_temperature_keeps_none(self):
        """档案未配置 temperature → default_temperature=None（旧硬编码默认）"""
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-moon"}):
            c = create_llm_client(_cfg(), model="kimi")
        assert c.default_temperature is None

    def test_l3_chat_completion_resolves_default(self):
        """chat_completion(temperature=None) → 档案温度；显式传参仍优先"""
        from llm_client import DeepSeekClient
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x",
                           default_temperature=0.15)
        captured = {}
        with mock.patch("llm_client.requests.post",
                        return_value=mock.MagicMock(
                            status_code=200,
                            json=lambda: {"choices": [{"message": {"content": "ok"}}]})) as mp:
            c.chat_completion([{"role": "user", "content": "hi"}])
            captured["default"] = mp.call_args.kwargs["json"]["temperature"]
            c.chat_completion([{"role": "user", "content": "hi"}], temperature=0.9)
            captured["explicit"] = mp.call_args.kwargs["json"]["temperature"]
        assert captured["default"] == 0.15
        assert captured["explicit"] == 0.9

    def test_l3_chat_completion_hardcoded_default(self):
        """无档案温度 + 未传参 → 0.3（与旧版行为一致）"""
        from llm_client import DeepSeekClient
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        with mock.patch("llm_client.requests.post",
                        return_value=mock.MagicMock(
                            status_code=200,
                            json=lambda: {"choices": [{"message": {"content": "ok"}}]})) as mp:
            c.chat_completion([{"role": "user", "content": "hi"}])
        assert mp.call_args.kwargs["json"]["temperature"] == 0.3

    # ---------- L5：思考流字段别名 ----------

    def test_l5_reasoning_field_aliases(self):
        """reasoning/thinking/thinking_content 字段均归一化为 reasoning 事件"""
        from llm_client import DeepSeekClient
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        cases = [
            {"reasoning_content": "a"},
            {"reasoning": "b"},
            {"thinking": "c"},
            {"thinking_content": "d"},
        ]
        for delta in cases:
            events = list(c._parse_delta(delta, reasoning_enabled=True))
            assert events == [{"type": "reasoning",
                               "text": list(delta.values())[0]}], delta

    def test_l5_first_present_field_wins(self):
        """多字段并存 → 首个命中别名生效，不重复产出"""
        from llm_client import DeepSeekClient
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        events = list(c._parse_delta(
            {"reasoning_content": "x", "reasoning": "y"}, reasoning_enabled=True))
        assert events == [{"type": "reasoning", "text": "x"}]

    def test_l5_reasoning_disabled_ignores_all(self):
        """reasoning_enabled=False → 别名字段一律不产出（正文不受影响）"""
        from llm_client import DeepSeekClient
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        events = list(c._parse_delta(
            {"reasoning_content": "x", "content": "hello"}, reasoning_enabled=False))
        assert events == [{"type": "content", "text": "hello"}]


# ==================== L4a：chat_completion 跨档案失败转移 ====================

def _resp(status=200, text="", content="ok", headers=None):
    m = mock.MagicMock()
    m.status_code = status
    m.text = text
    m.json.return_value = {"choices": [{"message": {"content": content}}]}
    m.headers = headers or {}
    return m


class _RecordingLogger:
    def __init__(self):
        self.records = []

    def info(self, m): self.records.append(("info", m))
    def warning(self, m): self.records.append(("warning", m))
    def error(self, m): self.records.append(("error", m))


class TestL4aChatCompletionFailover:
    """L4a：档案带 fallback 时 chat_completion 在瞬时故障（429/5xx/连接异常）按链转移。

    - 转移：TransientLLMError（429/5xx）与 requests 连接类异常
    - 不转移：4xx 认证/参数失败（底层返回 None 直接透传——配置问题响亮暴露）
    - 失败档案进入冷却期（60s 内跳过）；链耗尽返回 None
    - fallback 档案构造失败（缺 Key 等环境问题）→ 跳过 + warning，不阻断主档案
    - stream_chat 转移属 L4b，本批仅验证委托主 client
    """

    FB_MODELS = None  # 延迟构造（依赖 MODELS）

    def _models(self):
        return {
            "deepseek": dict(MODELS["deepseek"], fallback=["kimi"]),
            "kimi": dict(MODELS["kimi"]),
        }

    def _fc(self, models=None, env=None, logger=None):
        models = models or self._models()
        env = env or {"DEEPSEEK_API_KEY": "sk-ds", "MOONSHOT_API_KEY": "sk-moon"}
        with mock.patch.dict(os.environ, env):
            return create_llm_client(_cfg(models=models), logger=logger)

    def test_no_fallback_returns_plain_client(self):
        """无 fallback 字段 → 返回普通 DeepSeekClient（零破坏）"""
        from llm_client import DeepSeekClient, FailoverClient
        c = self._fc(models={"deepseek": dict(MODELS["deepseek"]), "kimi": dict(MODELS["kimi"])})
        assert isinstance(c, DeepSeekClient) and not isinstance(c, FailoverClient)

    def test_fallback_returns_wrapper_with_chain(self):
        """带 fallback → FailoverClient，链 = [主, fallback]，属性委托主档案"""
        from llm_client import FailoverClient
        fc = self._fc()
        assert isinstance(fc, FailoverClient)
        assert [c.name for c in fc._clients] == ["deepseek", "kimi"]
        assert fc.model == "deepseek-v4-flash"   # __getattr__ 委托主 client
        assert fc.api_key == "sk-ds"

    def test_fallback_type_validated(self):
        """fallback 非字符串列表 → ValueError（H2 风格源头校验）"""
        models = self._models()
        models["deepseek"] = dict(models["deepseek"], fallback="kimi")
        with pytest.raises(ValueError, match="fallback.*无效"):
            self._fc(models=models)

    def test_fallback_unknown_profile_raises(self):
        """fallback 指向不存在档案 → 报错并列出可用档案"""
        models = self._models()
        models["deepseek"] = dict(models["deepseek"], fallback=["nope"])
        with pytest.raises(ValueError, match="nope.*可用档案"):
            self._fc(models=models)

    def test_fallback_self_reference_raises(self):
        """fallback 包含自身 → 报错"""
        models = self._models()
        models["deepseek"] = dict(models["deepseek"], fallback=["deepseek"])
        with pytest.raises(ValueError, match="不能包含自身"):
            self._fc(models=models)

    def test_failover_on_429(self):
        """主档案 429 → 转移 kimi 成功；请求按链顺序发出"""
        fc = self._fc()
        with mock.patch("llm_client.requests.post",
                        side_effect=[_resp(429, "rate limited"), _resp(200, content="from-kimi")]) as mp:
            result = fc.chat_completion([{"role": "user", "content": "hi"}])
        assert result == "from-kimi"
        urls = [c.args[0] for c in mp.call_args_list]
        assert urls[0].startswith("https://api.deepseek.com")
        assert urls[1].startswith("https://api.moonshot.cn")

    def test_failover_on_5xx(self):
        """主档案 503 → 转移成功"""
        fc = self._fc()
        with mock.patch("llm_client.requests.post",
                        side_effect=[_resp(503, "unavailable"), _resp(200, content="ok5")]):
            assert fc.chat_completion([{"role": "user", "content": "hi"}]) == "ok5"

    def test_failover_on_connection_error(self):
        """主档案连接失败 → 转移成功"""
        fc = self._fc()
        with mock.patch("llm_client.requests.post",
                        side_effect=[requests.exceptions.ConnectionError("boom"),
                                     _resp(200, content="ok-conn")]):
            assert fc.chat_completion([{"role": "user", "content": "hi"}]) == "ok-conn"

    def test_no_failover_on_auth_failure(self):
        """主档案 401（不可转移失败）→ 直接 None，不调 fallback"""
        fc = self._fc()
        with mock.patch("llm_client.requests.post",
                        return_value=_resp(401, "unauthorized")) as mp:
            assert fc.chat_completion([{"role": "user", "content": "hi"}]) is None
        assert len(mp.call_args_list) == 1  # 只打了主档案

    def test_chain_exhausted_returns_none(self):
        """全链瞬时失败 → None（与 chat_completion 失败契约一致）"""
        fc = self._fc()
        with mock.patch("llm_client.requests.post",
                        side_effect=[_resp(429, "x"), _resp(500, "y")]) as mp:
            assert fc.chat_completion([{"role": "user", "content": "hi"}]) is None
        assert len(mp.call_args_list) == 2

    def test_cooldown_skips_recently_failed(self):
        """主档案失败后进入冷却：下次调用直接走 fallback；冷却过期后恢复主档案"""
        import time as _time
        fc = self._fc()
        with mock.patch("llm_client.requests.post",
                        side_effect=[_resp(429, "x"), _resp(200, content="r1")]):
            assert fc.chat_completion([{"role": "user", "content": "q1"}]) == "r1"
        # 冷却中：第一次请求即打 kimi（deepseek 被跳过）
        with mock.patch("llm_client.requests.post",
                        return_value=_resp(200, content="r2")) as mp:
            assert fc.chat_completion([{"role": "user", "content": "q2"}]) == "r2"
            assert mp.call_args_list[0].args[0].startswith("https://api.moonshot.cn")
        # 冷却过期（手动回拨失败时间戳）：恢复走主档案
        fc._fail_at["deepseek"] = (_time.monotonic() - fc.cooldown_seconds - 1,
                                   fc.cooldown_seconds)
        with mock.patch("llm_client.requests.post",
                        return_value=_resp(200, content="r3")) as mp:
            assert fc.chat_completion([{"role": "user", "content": "q3"}]) == "r3"
            assert mp.call_args_list[0].args[0].startswith("https://api.deepseek.com")

    def test_missing_key_fallback_skipped(self):
        """fallback 档案缺 API Key（环境问题）→ 跳过 + warning，主档案仍可用"""
        logger = _RecordingLogger()
        fc = self._fc(env={"DEEPSEEK_API_KEY": "sk-ds"}, logger=logger)  # 未设 MOONSHOT_API_KEY
        from llm_client import FailoverClient
        assert isinstance(fc, FailoverClient)
        assert len(fc._clients) == 1  # kimi 被跳过
        assert any("kimi" in m for lvl, m in logger.records if lvl == "warning")
        with mock.patch("llm_client.requests.post",
                        return_value=_resp(200, content="solo")):
            assert fc.chat_completion([{"role": "user", "content": "hi"}]) == "solo"

    def test_transient_429_5xx_raise(self):
        """裸 DeepSeekClient：429/502 → TransientLLMError 上抛（不被自身 catch 吞掉）"""
        from llm_client import DeepSeekClient, TransientLLMError
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        for status in (429, 502):
            with mock.patch("llm_client.requests.post", return_value=_resp(status, "err")):
                with pytest.raises(TransientLLMError):
                    c.chat_completion([{"role": "user", "content": "hi"}])

    def test_auth_4xx_returns_none_no_raise(self):
        """裸 DeepSeekClient：401/400 → 返回 None 不抛（回归，响亮暴露由消费方处理）"""
        from llm_client import DeepSeekClient, TransientLLMError
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        for status in (401, 400):
            with mock.patch("llm_client.requests.post", return_value=_resp(status, "err")):
                assert c.chat_completion([{"role": "user", "content": "hi"}]) is None

    def test_stream_chat_delegates_to_primary(self):
        """L4a：stream_chat 直接委托主 client（转移属 L4b）"""
        from llm_client import FailoverClient
        primary = mock.MagicMock()
        primary.name = "deepseek"
        primary.stream_chat = mock.MagicMock(
            return_value=iter([{"type": "content", "text": "x"}, {"type": "done"}]))
        fc = FailoverClient([primary])
        events = list(fc.stream_chat([{"role": "user", "content": "hi"}],
                                     temperature=0.5, max_tokens=99))
        assert events == [{"type": "content", "text": "x"}, {"type": "done"}]
        kwargs = primary.stream_chat.call_args.kwargs
        assert kwargs["temperature"] == 0.5 and kwargs["max_tokens"] == 99


# ==================== L4b：stream_chat 首事件前转移 + 中断联动 ====================

def _fake_stream(events=None, exc=None, before=None):
    """构造伪 stream_chat 可调用：先 before()，逐个 yield events，最后抛 exc。"""
    def call(*args, **kwargs):
        if before is not None:
            before()
        for e in (events or []):
            yield e
        if exc is not None:
            raise exc
    return call


class TestL4bStreamFailover:
    """L4b：流式仅在首事件前转移；已产出事件/用户中断绝不转移。

    - 转移窗口：连接建立失败、HTTP 429/5xx（TransientLLMError）——此时未产出任何事件
    - 不转移：产出首个事件后的任何失败（重放会前端重复/工具增量错乱）、cancel 引发的异常
    - cancel() 广播整条链并置标志；chat_completion 同样受标志保护
    """

    EVENTS = [{"type": "reasoning", "text": "think"},
              {"type": "content", "text": "hello"},
              {"type": "done"}]

    def _client(self, name, stream=None, completion=None):
        c = mock.MagicMock()
        c.name = name
        if stream is not None:
            c.stream_chat = mock.MagicMock(side_effect=stream)
        if completion is not None:
            c.chat_completion = mock.MagicMock(side_effect=completion)
        return c

    def _fc(self, primary, fallback):
        from llm_client import FailoverClient
        return FailoverClient([primary, fallback])

    def test_failover_before_first_event(self):
        """主档案未产出任何事件即瞬时失败 → 转移 fallback，事件完整来自 fallback"""
        from llm_client import TransientLLMError
        primary = self._client("deepseek",
                               stream=_fake_stream(exc=TransientLLMError("HTTP 429")))
        fallback = self._client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        assert list(fc.stream_chat([{"role": "user", "content": "q"}])) == self.EVENTS
        assert fallback.stream_chat.called

    def test_failover_on_connection_error_before_events(self):
        """主档案连接失败（未产出事件）→ 转移成功"""
        primary = self._client("deepseek",
                               stream=_fake_stream(exc=requests.exceptions.ConnectionError("boom")))
        fallback = self._client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        assert list(fc.stream_chat([{"role": "user", "content": "q"}])) == self.EVENTS

    def test_no_failover_after_first_event(self):
        """已产出 1 个事件后失败 → 直接上抛，不调 fallback（防前端重复输出）"""
        primary = self._client("deepseek",
                               stream=_fake_stream(events=self.EVENTS[:1],
                                                   exc=requests.exceptions.ConnectionError("mid")))
        fallback = self._client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        with pytest.raises(requests.exceptions.ConnectionError):
            list(fc.stream_chat([{"role": "user", "content": "q"}]))
        fallback.stream_chat.assert_not_called()

    def test_cancel_never_fails_over_stream(self):
        """用户中断（cancel 引发连接异常）→ 上抛原异常，绝不转移"""
        holder = {}

        def do_cancel():
            holder["fc"].cancel()

        primary = self._client("deepseek",
                               stream=_fake_stream(before=do_cancel,
                                                   exc=requests.exceptions.ConnectionError("cancelled")))
        fallback = self._client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        holder["fc"] = fc
        with pytest.raises(requests.exceptions.ConnectionError):
            list(fc.stream_chat([{"role": "user", "content": "q"}]))
        fallback.stream_chat.assert_not_called()
        assert fc._cancelled is True
        primary.cancel.assert_called_once()  # 广播到主 client

    def test_cancel_never_fails_over_chat_completion(self):
        """chat_completion 期间的 cancel 引发异常 → 返回 None，不转移"""
        holder = {}

        def raise_after_cancel(*a, **k):
            holder["fc"].cancel()
            raise requests.exceptions.ConnectionError("cancelled")

        primary = self._client("deepseek", completion=raise_after_cancel)
        fallback = self._client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        holder["fc"] = fc
        assert fc.chat_completion([{"role": "user", "content": "q"}]) is None
        fallback.chat_completion.assert_not_called()

    def test_cancel_broadcasts_to_whole_chain(self):
        """fc.cancel() → 链上全部 client.cancel() 均被调用 + 自身置标志"""
        primary = self._client("deepseek")
        fallback = self._client("kimi")
        fc = self._fc(primary, fallback)
        fc.cancel()
        assert fc._cancelled is True
        primary.cancel.assert_called_once()
        fallback.cancel.assert_called_once()

    def test_chain_exhausted_raises_last(self):
        """全链首事件前瞬时失败 → 抛最后一个异常（既有错误处理接住）"""
        from llm_client import TransientLLMError
        primary = self._client("deepseek", stream=_fake_stream(exc=requests.exceptions.Timeout("t1")))
        fallback = self._client("kimi", stream=_fake_stream(exc=TransientLLMError("HTTP 500")))
        fc = self._fc(primary, fallback)
        with pytest.raises(TransientLLMError):
            list(fc.stream_chat([{"role": "user", "content": "q"}]))

    def test_all_cooling_raises_runtime_error(self):
        """全链处于冷却期 → RuntimeError（不发任何请求）"""
        import time as _time
        primary = self._client("deepseek", stream=_fake_stream(events=self.EVENTS))
        fallback = self._client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        fc._fail_at = {"deepseek": (_time.monotonic(), fc.cooldown_seconds),
                       "kimi": (_time.monotonic(), fc.cooldown_seconds)}
        with pytest.raises(RuntimeError, match="冷却期"):
            list(fc.stream_chat([{"role": "user", "content": "q"}]))
        primary.stream_chat.assert_not_called()

    def test_cooldown_skips_failed_primary_stream(self):
        """主档案冷却中 → 流式直接走 fallback"""
        import time as _time
        primary = self._client("deepseek", stream=_fake_stream(events=self.EVENTS))
        fallback = self._client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        fc._fail_at["deepseek"] = (_time.monotonic(), fc.cooldown_seconds)
        assert list(fc.stream_chat([{"role": "user", "content": "q"}])) == self.EVENTS
        primary.stream_chat.assert_not_called()

    def test_success_does_not_touch_fallback(self):
        """主档案成功（含 done 自然结束）→ fallback 从未被调"""
        primary = self._client("deepseek", stream=_fake_stream(events=self.EVENTS))
        fallback = self._client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        assert list(fc.stream_chat([{"role": "user", "content": "q"}])) == self.EVENTS
        fallback.stream_chat.assert_not_called()

    def test_http_transient_status_raises_transient(self):
        """裸 DeepSeekClient.stream_chat：429/503 → TransientLLMError（可转移信号）"""
        from llm_client import DeepSeekClient, TransientLLMError
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        for status in (429, 503):
            resp = mock.MagicMock(status_code=status, text="err")
            with mock.patch("llm_client.requests.post", return_value=resp):
                with pytest.raises(TransientLLMError):
                    list(c.stream_chat([{"role": "user", "content": "hi"}]))

    def test_http_auth_status_raises_runtime(self):
        """裸 DeepSeekClient.stream_chat：401 → RuntimeError（非瞬时，不触发转移类型）"""
        from llm_client import DeepSeekClient
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        resp = mock.MagicMock(status_code=401, text="unauthorized")
        with mock.patch("llm_client.requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="401"):
                list(c.stream_chat([{"role": "user", "content": "hi"}]))


# ==================== L4c：可观测性（stats/hook）+ 预算 + Retry-After ====================

class TestL4cObservabilityAndBudget:
    """L4c：转移可观测性与预算控制。

    - get_stats：requests/failovers/cooldown_skips/exhausted 聚合计数
    - on_failover(from, to, reason) 回调，to=None 表示链耗尽；回调异常不影响主流程
    - failover_timeout_budget：整链总预算（秒），耗尽停止尝试剩余档案（缺省不限）
    - Retry-After（数字秒）经 TransientLLMError.retry_after 延长档案冷却期
    """

    EVENTS = [{"type": "content", "text": "hi"}, {"type": "done"}]

    def _mk_client(self, name, stream=None, completion=None):
        c = mock.MagicMock()
        c.name = name
        if stream is not None:
            c.stream_chat = mock.MagicMock(side_effect=stream)
        if completion is not None:
            c.chat_completion = mock.MagicMock(side_effect=completion)
        return c

    def _fc(self, primary, fallback=None, **kwargs):
        from llm_client import FailoverClient
        clients = [primary] + ([fallback] if fallback is not None else [])
        return FailoverClient(clients, **kwargs)

    # ---------- stats ----------

    def test_stats_failover_success(self):
        """主 429 → fallback 成功：requests=1, failovers=1, exhausted=0"""
        from llm_client import TransientLLMError
        primary = self._mk_client("deepseek",
                                  completion=mock.MagicMock(side_effect=TransientLLMError("429")))
        fallback = self._mk_client("kimi",
                                   completion=mock.MagicMock(return_value="ok"))
        fc = self._fc(primary, fallback)
        assert fc.chat_completion([{"role": "user", "content": "q"}]) == "ok"
        s = fc.get_stats()
        assert s == {"requests": 1, "failovers": 1, "cooldown_skips": 0, "exhausted": 0}

    def test_stats_chain_exhausted(self):
        """全链失败：failovers=2（两次转移决策），exhausted=1"""
        from llm_client import TransientLLMError
        primary = self._mk_client("deepseek",
                                  completion=mock.MagicMock(side_effect=TransientLLMError("429")))
        fallback = self._mk_client("kimi",
                                   completion=mock.MagicMock(side_effect=TransientLLMError("500")))
        fc = self._fc(primary, fallback)
        assert fc.chat_completion([{"role": "user", "content": "q"}]) is None
        s = fc.get_stats()
        assert s["failovers"] == 2 and s["exhausted"] == 1

    def test_stats_cooldown_skips_and_stream(self):
        """冷却跳过计数 + 流式成功路径 requests 计数"""
        import time as _time
        primary = self._mk_client("deepseek",
                                  stream=_fake_stream(events=self.EVENTS))
        fallback = self._mk_client("kimi", stream=_fake_stream(events=self.EVENTS))
        fc = self._fc(primary, fallback)
        fc._fail_at["deepseek"] = (_time.monotonic(), fc.cooldown_seconds)
        assert list(fc.stream_chat([{"role": "user", "content": "q"}])) == self.EVENTS
        s = fc.get_stats()
        assert s == {"requests": 1, "failovers": 0, "cooldown_skips": 1, "exhausted": 0}

    def test_get_stats_returns_copy(self):
        """get_stats 返回快照副本，外部修改不影响内部计数"""
        fc = self._fc(self._mk_client("deepseek"))
        snap = fc.get_stats()
        snap["requests"] = 999
        assert fc.get_stats()["requests"] == 0

    # ---------- on_failover hook ----------

    def test_on_failover_hook_called(self):
        """转移时回调 (from, to, reason)；链耗尽时 to=None"""
        from llm_client import TransientLLMError
        calls = []
        primary = self._mk_client("deepseek",
                                  completion=mock.MagicMock(side_effect=TransientLLMError("HTTP 429")))
        fallback = self._mk_client("kimi",
                                   completion=mock.MagicMock(side_effect=TransientLLMError("HTTP 500")))
        fc = self._fc(primary, fallback, on_failover=lambda f, t, r: calls.append((f, t, r)))
        assert fc.chat_completion([{"role": "user", "content": "q"}]) is None
        assert len(calls) == 2
        assert calls[0][0] == "deepseek" and calls[0][1] == "kimi"
        assert calls[1][0] == "kimi" and calls[1][1] is None  # 链尾
        assert "429" in calls[0][2] and "500" in calls[1][2]

    def test_on_failover_hook_exception_safe(self):
        """回调抛异常不影响主流程（仍完成转移）"""
        from llm_client import TransientLLMError

        def bad_hook(f, t, r):
            raise RuntimeError("hook boom")

        primary = self._mk_client("deepseek",
                                  completion=mock.MagicMock(side_effect=TransientLLMError("429")))
        fallback = self._mk_client("kimi",
                                   completion=mock.MagicMock(return_value="ok"))
        fc = self._fc(primary, fallback, on_failover=bad_hook)
        assert fc.chat_completion([{"role": "user", "content": "q"}]) == "ok"

    # ---------- timeout_budget ----------

    def test_timeout_budget_stops_chain(self):
        """预算耗尽 → 不再尝试 fallback（chat_completion 返回 None）"""
        from llm_client import TransientLLMError
        primary = self._mk_client("deepseek",
                                  completion=mock.MagicMock(side_effect=TransientLLMError("429")))
        fallback = self._mk_client("kimi",
                                   completion=mock.MagicMock(return_value="ok"))
        fc = self._fc(primary, fallback, timeout_budget=50.0)
        # monotonic 序列：_call_started=0 → 主档案预算检查=0 → _mark_fail=0 → fallback 预算检查=100
        with mock.patch("llm_client.time.monotonic",
                        side_effect=[0.0, 0.0, 0.0, 100.0]):
            assert fc.chat_completion([{"role": "user", "content": "q"}]) is None
        fallback.chat_completion.assert_not_called()
        assert fc.get_stats()["exhausted"] == 1

    def test_timeout_budget_invalid_treated_as_none(self):
        """timeout_budget<=0 → warning + 视为未配置（全链仍可尝试）"""
        from llm_client import TransientLLMError
        primary = self._mk_client("deepseek",
                                  completion=mock.MagicMock(side_effect=TransientLLMError("429")))
        fallback = self._mk_client("kimi",
                                   completion=mock.MagicMock(return_value="ok"))
        fc = self._fc(primary, fallback, timeout_budget=-5)
        assert fc.timeout_budget is None
        assert fc.chat_completion([{"role": "user", "content": "q"}]) == "ok"

    def test_timeout_budget_config_field(self):
        """档案 failover_timeout_budget 字段 → wrapper.timeout_budget 生效"""
        models = {
            "deepseek": dict(MODELS["deepseek"],
                             fallback=["kimi"], failover_timeout_budget=30),
            "kimi": dict(MODELS["kimi"]),
        }
        with mock.patch.dict(os.environ,
                             {"DEEPSEEK_API_KEY": "sk-ds", "MOONSHOT_API_KEY": "sk-moon"}):
            c = create_llm_client(_cfg(models=models))
        assert c.timeout_budget == 30

    def test_no_budget_by_default(self):
        """缺省无预算（None）——现有全链行为不变（回归）"""
        from llm_client import TransientLLMError
        primary = self._mk_client("deepseek",
                                  completion=mock.MagicMock(side_effect=TransientLLMError("429")))
        fallback = self._mk_client("kimi",
                                   completion=mock.MagicMock(return_value="ok"))
        fc = self._fc(primary, fallback)
        assert fc.timeout_budget is None
        assert fc.chat_completion([{"role": "user", "content": "q"}]) == "ok"

    # ---------- Retry-After ----------

    def test_retry_after_header_parsed(self):
        """429 响应 Retry-After: 120 → TransientLLMError.retry_after=120"""
        from llm_client import DeepSeekClient, TransientLLMError
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        resp = _resp(429, "limited", headers={"Retry-After": "120"})
        with mock.patch("llm_client.requests.post", return_value=resp):
            with pytest.raises(TransientLLMError) as ei:
                c.chat_completion([{"role": "user", "content": "hi"}])
        assert ei.value.retry_after == 120

    def test_retry_after_invalid_ignored(self):
        """Retry-After 非 数字（HTTP-date/垃圾值）→ None（默认冷却）"""
        from llm_client import DeepSeekClient, TransientLLMError
        c = DeepSeekClient(api_key="k", model="m", base_url="https://x")
        resp = _resp(429, "limited", headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
        with mock.patch("llm_client.requests.post", return_value=resp):
            with pytest.raises(TransientLLMError) as ei:
                c.chat_completion([{"role": "user", "content": "hi"}])
        assert ei.value.retry_after is None

    def test_retry_after_extends_cooldown(self):
        """retry_after=300 → 主档案冷却 300s：下次请求仍走 fallback；过期后恢复"""
        import time as _time
        from llm_client import TransientLLMError
        primary = self._mk_client("deepseek",
                                  completion=mock.MagicMock(
                                      side_effect=TransientLLMError("429", retry_after=300)))
        primary_ok = mock.MagicMock(return_value="from-ds")
        fallback = self._mk_client("kimi", completion=mock.MagicMock(return_value="from-kimi"))
        fc = self._fc(primary, fallback)
        # 第一次：主失败（retry_after=300）→ fallback
        assert fc.chat_completion([{"role": "user", "content": "q1"}]) == "from-kimi"
        # 冷却中（默认 60s 已过但 300s 未到）：仍走 fallback
        t, _ = fc._fail_at["deepseek"]
        fc._fail_at["deepseek"] = (t - 100, 300)
        primary.chat_completion = primary_ok
        assert fc.chat_completion([{"role": "user", "content": "q2"}]) == "from-kimi"
        primary_ok.assert_not_called()
        # 300s 冷却过期：恢复主档案
        fc._fail_at["deepseek"] = (t - 301, 300)
        assert fc.chat_completion([{"role": "user", "content": "q3"}]) == "from-ds"
        primary_ok.assert_called_once()


# ==================== DeepSeekChat 档案级 reasoning 读取 ====================

class TestChatReasoningFlag:
    def test_reasoning_enabled_reads_client_attr(self):
        """client 带 reasoning_enabled 属性 → DeepSeekChat._reasoning_enabled 优先读它"""
        from aibox import DeepSeekChat
        from test_once_mode import _make_ai  # 复用 FakeLLMClient 构造
        ai = _make_ai()
        ai.client.reasoning_enabled = True   # 档案级开
        assert ai._reasoning_enabled() is True
        ai.client.reasoning_enabled = False  # 档案级关（覆盖全局）
        assert ai._reasoning_enabled() is False

    def test_reasoning_falls_back_to_global(self):
        """client 无属性 → 回退 constants.REASONING_ENABLED"""
        from test_once_mode import _make_ai
        ai = _make_ai()
        assert ai._reasoning_enabled() == False  # noqa: E712 全局默认 False
