"""aibox 模型接入点管理工具（model_config.py）单元测试（2026-08-18）

覆盖：
  - 列表归一化（默认标记/免密/失败转移展示字段）
  - 添加：字段校验（档案名/地址/模型名/重复）、Key 两种方式、温度/超时钳制、
    失败转移引用校验、设为默认、落盘（含脱敏）
  - 删除：物理移除、fallback 悬空引用清理、默认档案标记清除
  - 连通性验证：成功 / 失败 / 档案不存在
"""

import sys
import types
from unittest import mock

import pytest

if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

import model_config  # noqa: E402
from common import ConfigManager  # noqa: E402


def _cfg(tmp_path, initial: dict = None) -> ConfigManager:
    """构造指向临时配置文件的 ConfigManager（避免碰真实 ~/.aibox）"""
    cm = ConfigManager()
    cm._config_path = str(tmp_path / "config.json")
    cm._config = initial if initial is not None else {}
    return cm


def _base_models():
    return {
        "models": {
            "deepseek": {
                "api_url": "https://api.deepseek.com/v1/chat/completions",
                "model": "deepseek-chat",
                "api_key_env": "DEEPSEEK_API_KEY",
            },
            "local": {
                "api_url": "http://localhost:11434/v1/chat/completions",
                "model": "qwen3",
                "api_key_env": "",
            },
        },
        "api": {"default_model": "deepseek"},
    }


# ==================== 列表 ====================

class TestListProfiles:
    def test_list_normalized(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        profiles = model_config.list_profiles(cm)
        assert len(profiles) == 2
        ds = next(p for p in profiles if p["name"] == "deepseek")
        assert ds["is_default"] is True
        assert ds["api_key_env"] == "DEEPSEEK_API_KEY"
        local = next(p for p in profiles if p["name"] == "local")
        assert local["api_key_env"] == ""
        assert local["is_default"] is False

    def test_list_empty_models_section(self, tmp_path):
        cm = _cfg(tmp_path, {})
        assert model_config.list_profiles(cm) == []

    def test_list_ignores_malformed_entries(self, tmp_path):
        cm = _cfg(tmp_path, {"models": {"bad": "not-a-dict"}})
        assert model_config.list_profiles(cm) == []


# ==================== 校验 ====================

class TestValidateNewProfile:
    def test_name_rules(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        assert model_config.validate_new_profile(cm, "", "https://x", "m")
        assert model_config.validate_new_profile(cm, "坏名字!", "https://x", "m")
        assert model_config.validate_new_profile(cm, "deepseek", "https://x", "m")  # 已存在
        assert model_config.validate_new_profile(cm, "ok_name", "https://x", "m") is None

    def test_url_scheme_and_model_required(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        assert model_config.validate_new_profile(cm, "kimi", "ftp://x", "m")
        assert model_config.validate_new_profile(cm, "kimi", "https://x", "")


# ==================== 添加 ====================

class TestAddProfile:
    def test_add_with_env_key_and_default(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        msgs = model_config.add_profile(
            cm, "kimi", "https://api.moonshot.cn/v1/chat/completions",
            "moonshot-v1-8k", api_key_env="KIMI_KEY",
            display_name="Kimi", set_default=True)
        assert any("已添加" in m for m in msgs)
        assert any("设为默认" in m for m in msgs)
        assert cm.get("api.default_model") == "kimi"
        assert cm.get("models.kimi.api_key_env") == "KIMI_KEY"
        assert cm.get("models.kimi.api_url").startswith("https://")
        # 落盘文件存在且可解析
        import json
        disk = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert disk["models"]["kimi"]["model"] == "moonshot-v1-8k"

    def test_add_without_key_is_exempt_endpoint(self, tmp_path):
        cm = _cfg(tmp_path, {})
        msgs = model_config.add_profile(
            cm, "local", "http://localhost:11434/v1/chat/completions",
            "qwen3", api_key_env="")
        assert any("免密" in m for m in msgs)
        assert cm.get("models.local.api_key_env") == ""

    def test_add_rejects_invalid_fallback(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        msgs = model_config.add_profile(
            cm, "kimi", "https://x/v1", "m1",
            fallback=["ghost", "deepseek"])
        assert any("失败转移" in m for m in msgs)
        assert cm.get("models.kimi") is None  # 未写入

    def test_add_temperature_timeout_clamped(self, tmp_path):
        cm = _cfg(tmp_path, {})
        model_config.add_profile(cm, "a", "https://x/v1", "m1",
                                 temperature=9.9, timeout=1)
        assert cm.get("models.a.temperature") == 2.0
        assert cm.get("models.a.timeout") == 5

    def test_add_invalid_temperature_rejected(self, tmp_path):
        cm = _cfg(tmp_path, {})
        msgs = model_config.add_profile(cm, "a", "https://x/v1", "m1",
                                        temperature="abc")
        assert any("温度" in m for m in msgs)
        assert cm.get("models.a") is None


# ==================== 删除 ====================

class TestDeleteProfile:
    def test_delete_cleans_fallback_refs(self, tmp_path):
        cm = _cfg(tmp_path, {
            "models": {
                "main": {"api_url": "https://a", "model": "m1",
                         "fallback": ["backup"]},
                "backup": {"api_url": "https://b", "model": "m2"},
            },
        })
        msgs = model_config.delete_profile(cm, "backup")
        assert any("已删除" in m for m in msgs)
        assert any("失败转移引用" in m for m in msgs)
        assert cm.get("models.main.fallback") == []

    def test_delete_default_clears_default_flag(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        model_config.delete_profile(cm, "deepseek")
        assert cm.get("api.default_model") is None

    def test_delete_nonexistent(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        msgs = model_config.delete_profile(cm, "ghost")
        assert any("不存在" in m for m in msgs)


# ==================== 连通性验证 ====================

class TestTestProfile:
    def test_connectivity_ok(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        fake = mock.MagicMock()
        fake.chat_completion.return_value = "pong"
        with mock.patch("llm_client.create_llm_client", return_value=fake):
            r = model_config.test_profile(cm, "deepseek")
        assert "连通正常" in r and "pong" in r

    def test_connectivity_failure(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        fake = mock.MagicMock()
        fake.chat_completion.side_effect = RuntimeError("连接被拒绝")
        with mock.patch("llm_client.create_llm_client", return_value=fake):
            r = model_config.test_profile(cm, "deepseek")
        assert "验证失败" in r and "连接被拒绝" in r

    def test_connectivity_missing_profile(self, tmp_path):
        cm = _cfg(tmp_path, _base_models())
        assert "不存在" in model_config.test_profile(cm, "ghost")


# ==================== 落盘失败感知 ====================

class TestSaveFailure:
    def test_add_reports_save_failure(self, tmp_path):
        cm = _cfg(tmp_path, {})
        # J5：写路径改为原子写，磁盘失败经原子写函数模拟
        with mock.patch("common.config.atomic_write_text",
                        side_effect=PermissionError("disk ro")):
            msgs = model_config.add_profile(
                cm, "a", "https://x/v1", "m1", api_key_env="K")
        assert any("保存失败" in m for m in msgs)
