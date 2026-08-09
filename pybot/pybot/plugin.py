"""Python 插件加载器 —— 替代 bot4cj/src/r.cj 的 Lua 状态机桥接（MtVars_Lua）

插件协议（modules/<Name>.py，替代 Lua build/call/destroy，见方案 §3.4）：
    def build(ctx: dict) -> dict     # 初始化硬件
    def call(ctx: dict) -> dict      # ctx = 元数据 + 实时参数
    def destroy(ctx: dict) -> dict   # 复位/关闭硬件

与仓颉版的差异（审计修复 S07）：
    Lua 路径硬编码 ./modules/ → 三级查找：
    $PYBOT_MODULES_PATH（冒号分隔多路径）→ 包内置 modules/ → cwd/modules/。

每次加载都创建独立模块实例（spec_from_file_location 唯一模块名），
对齐仓颉版"每个设备一个独立 Lua 状态机"的隔离语义
（插件内模块级全局变量即设备私有状态）。
"""

import importlib.util
import itertools
import logging
import os
from pathlib import Path

from .hardvars import HardwareVar

log = logging.getLogger("pybot.plugin")

_instance_counter = itertools.count()


class PluginNotFound(Exception):
    """插件未找到（携带插件名与全部搜索路径）"""

    def __init__(self, name, searched):
        paths = ", ".join(str(p) for p in searched)
        super().__init__(f"plugin '{name}' not found (searched: {paths})")
        self.name = name
        self.searched = searched


def search_dirs():
    """插件搜索目录（按优先级）：$PYBOT_MODULES_PATH → 包内置 → cwd"""
    dirs = []
    env = os.environ.get("PYBOT_MODULES_PATH", "")
    for p in env.split(":"):
        if p:
            dirs.append(Path(p))
    dirs.append(Path(__file__).resolve().parent.parent / "modules")  # 包内置
    dirs.append(Path.cwd() / "modules")
    return dirs


def find_module(name):
    """按三级路径查找 <name>.py，找不到抛 PluginNotFound"""
    searched = search_dirs()
    for d in searched:
        path = d / f"{name}.py"
        if path.is_file():
            return path
    raise PluginNotFound(name, searched)


def load_module(name):
    """加载插件为独立模块实例（每次调用产生新实例，状态互不共享）"""
    path = find_module(name)
    mod_name = f"pybot_plugin_{name}_{next(_instance_counter)}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for fn in ("build", "call", "destroy"):
        if not callable(getattr(module, fn, None)):
            raise PluginNotFound(
                name, [path]) from ValueError(
                f"plugin '{name}' missing function {fn}(ctx)")
    log.debug("loaded plugin %s from %s", name, path)
    return module


class PluginVar(HardwareVar):
    """插件硬件变量（对应仓颉版 MtVars_Lua）

    metadata: @块行元数据 + 引用硬件的 play({}) 展开结果（如 I2C → fd）
    """

    def __init__(self, name, block_name, metadata):
        super().__init__(name)
        self.metadata = metadata
        self.module = load_module(block_name)

    def build(self):
        ctx = dict(self.metadata)
        log.info("plugin build parameters: %s", ctx)
        return self.module.build(ctx)

    def play(self, params):
        ctx = dict(self.metadata)   # 元数据拷贝，合并实时参数
        ctx.update(params)
        return self.module.call(ctx)

    def destroy(self):
        ctx = dict(self.metadata)
        log.info("plugin destroy parameters: %s", ctx)
        return self.module.destroy(ctx)
