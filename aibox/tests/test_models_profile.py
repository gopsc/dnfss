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
        profile, model = _resolve_model_profile(_cfg(), "kimi")
        assert profile is MODELS["kimi"]
        assert model == "kimi-k2"

    def test_bare_model_name_uses_default_profile(self):
        """model=裸名 → 默认档案端点 + 裸名作为模型名"""
        profile, model = _resolve_model_profile(_cfg(), "deepseek-v3-legacy")
        assert profile is MODELS["deepseek"]
        assert model == "deepseek-v3-legacy"

    def test_no_model_uses_default_profile(self):
        """未指定 model → 默认档案与档案内模型名"""
        profile, model = _resolve_model_profile(_cfg(), None)
        assert profile is MODELS["deepseek"]
        assert model == "deepseek-v4-flash"

    def test_fallback_when_no_models_section(self):
        """无 models 节 → (None, model) 回退旧 api 扁平节"""
        profile, model = _resolve_model_profile(_cfg(models=None), "anything")
        assert profile is None
        assert model == "anything"

    def test_fallback_when_default_model_missing(self):
        """default_model 指向不存在的档案且 model 未命中 → 回退"""
        profile, model = _resolve_model_profile(_cfg(default_model="nope"), "whatever")
        assert profile is None
        assert model == "whatever"


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

    def test_no_profile_no_reasoning_attr(self):
        """无档案 → client 无 reasoning_enabled 属性（走全局配置）"""
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds"}):
            c = create_llm_client(_cfg(models=None))
        assert not hasattr(c, "reasoning_enabled")


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
