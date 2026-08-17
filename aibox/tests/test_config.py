#!/usr/bin/env python3
"""F1（N1 高危）修复回归测试：代理密码不落盘。

覆盖三点：
  1. set_proxy(password=..., save=True) 后配置文件无明文密码（_save_config 脱敏）
  2. password_env 解析：get_proxy() 从环境变量取密码拼 socks5:// URL
  3. 旧版明文配置迁移：加载后盘上明文被清除，内存中本会话仍可用
"""

import json

import pytest

from common.config import ConfigManager


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


def _read_cfg_file() -> dict:
    path = ConfigManager()._config_path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_set_proxy_sanitizes_password_on_save(fresh_config, tmp_path):
    """save=True 落盘后，文件中不得出现明文密码（纵深防御脱敏）"""
    cfg = fresh_config()
    cfg.set_proxy("127.0.0.1", 1080, username="u1", password="secret123",
                  password_env="MY_SOCKS_PW", save=True)

    on_disk = _read_cfg_file()
    socks = on_disk["proxy"]["socks5"]
    assert socks["password"] in (None, ""), "落盘文件不得含明文密码"
    assert socks["password_env"] == "MY_SOCKS_PW", "password_env 应持久化"

    raw = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert "secret123" not in raw, "整个配置文件不得出现明文密码字符串"

    # 内存中本会话仍可用
    proxies = cfg.get_proxy()
    assert proxies is not None
    assert "u1:secret123@" in proxies["https"]


def test_password_env_resolution(fresh_config, monkeypatch):
    """password 为空时按 password_env 从环境变量解析"""
    monkeypatch.setenv("TEST_SOCKS_PW", "env-secret-42")
    cfg = fresh_config()
    cfg.set_proxy("10.0.0.8", 7890, username="u2", password=None,
                  password_env="TEST_SOCKS_PW", save=True)

    proxies = cfg.get_proxy()
    assert proxies is not None
    assert "u2:env-secret-42@10.0.0.8:7890" in proxies["https"]

    # get_proxy_info 不泄漏密码值，只给布尔 + 变量名
    info = cfg.get_proxy_info()
    assert info["password"] is True
    assert info["password_env"] == "TEST_SOCKS_PW"


def test_legacy_plaintext_migration(fresh_config, tmp_path):
    """旧版明文配置：加载后盘上清除，内存保留供本会话使用"""
    cfg_file = tmp_path / "config.json"
    legacy = {
        "proxy": {
            "socks5": {
                "enabled": True, "host": "1.2.3.4", "port": 1080,
                "username": "old_u", "password": "legacy_plain_pw"
            }
        }
    }
    cfg_file.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    cfg = fresh_config()  # 触发 _load_config → 迁移

    # 盘上已脱敏
    on_disk = _read_cfg_file()
    assert on_disk["proxy"]["socks5"]["password"] in (None, "")
    raw = cfg_file.read_text(encoding="utf-8")
    assert "legacy_plain_pw" not in raw

    # 内存中本会话仍可解析出旧密码（代理不断）
    proxies = cfg.get_proxy()
    assert proxies is not None
    assert "old_u:legacy_plain_pw@1.2.3.4:1080" in proxies["https"]
