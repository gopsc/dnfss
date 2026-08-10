"""插件加载器测试：三级路径查找(S07) / 缺失报错 / 实例隔离"""

import pytest

from pybot import plugin


def test_find_builtin_module():
    """包内置 modules/ 能找到 PCA9685"""
    path = plugin.find_module("PCA9685")
    assert path.name == "PCA9685.py"
    assert path.parent.name == "modules"


def test_plugin_not_found():
    with pytest.raises(plugin.PluginNotFound) as ei:
        plugin.find_module("NoSuchDevice")
    assert "NoSuchDevice" in str(ei.value)
    assert "modules" in str(ei.value)       # 报错含搜索路径


def test_env_path_priority(tmp_path, monkeypatch):
    """$PYBOT_MODULES_PATH 优先于包内置目录"""
    (tmp_path / "PCA9685.py").write_text(
        "def build(ctx): return {'from': 'env'}\n"
        "def call(ctx): return {}\n"
        "def destroy(ctx): return {}\n", encoding="utf-8")
    monkeypatch.setenv("PYBOT_MODULES_PATH", str(tmp_path))
    assert plugin.find_module("PCA9685") == tmp_path / "PCA9685.py"
    mod = plugin.load_module("PCA9685")
    assert mod.build({}) == {"from": "env"}


def test_missing_protocol_function(tmp_path, monkeypatch):
    """插件缺 build/call/destroy 任一函数即拒绝加载"""
    (tmp_path / "Broken.py").write_text(
        "def build(ctx): return {}\n", encoding="utf-8")
    monkeypatch.setenv("PYBOT_MODULES_PATH", str(tmp_path))
    with pytest.raises(plugin.PluginProtocolError):
        plugin.load_module("Broken")


def test_independent_instances():
    """每次加载新模块实例：模块级全局状态互不共享（对齐 Lua 每设备状态机）"""
    a = plugin.load_module("SPI")
    b = plugin.load_module("SPI")
    assert a is not b
    a._fd = 42
    assert b._fd == -1
