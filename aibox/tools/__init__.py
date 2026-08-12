"""
tools - aibox 工具系统基础模块（包）

抽离自 aibox.py 的工具基类与注册器，供 aibox.py 及后续迁移的
工具子类导入。当前包含：
  - base.py     : Tool 抽象基类
  - registry.py : ToolRegistry 工具注册器
形成单向依赖：common <- tools <- aibox。

采用"重导出"策略：外部统一用 `from tools import Tool, ToolRegistry`，
调用点无需关心具体子模块所在文件。
"""

from .base import Tool
from .registry import ToolRegistry

__all__ = ["Tool", "ToolRegistry"]
