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
