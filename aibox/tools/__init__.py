"""
tools - aibox 工具系统基础模块（包）

抽离自 aibox.py 的工具基类与注册器，供 aibox.py 及后续迁移的
工具子类导入。当前包含：
  - base.py           : Tool 抽象基类
  - registry.py       : ToolRegistry 工具注册器
  - basic_tools.py    : 基础工具集（A 批：文件/网页/时间/计算器，无外部子系统依赖）
  - command_tool.py   : CommandLineTool 命令行执行工具（B 批）
  - skill_tools.py    : 技能搜索/重载/安装/创建 4 个工具（B 批）
  - db_tools.py       : MemoryTool/MemoTool 数据库工具（B 批）
  - prompt_tools.py   : 身份/灵魂更新 + 结束对话 3 个工具（B 批）
  - proxy_tools.py    : SetProxy/GetProxy 代理工具（B 批）
  - misc_tools.py     : AskUserTool 询问工具（B 批）
形成单向依赖：common <- subsystems <- tools <- aibox。

采用"重导出"策略：外部统一用 `from tools import Tool, ToolRegistry`，
调用点无需关心具体子模块所在文件。
"""

from .base import Tool
from .registry import ToolRegistry
from .basic_tools import (
    FileToolBase,
    ReadFileTool,
    WriteFileTool,
    ListFilesTool,
    SimpleWebCrawlerTool,
    GetCurrentTimeTool,
    CalculatorTool,
)
# B 批工具
from .command_tool import CommandLineTool
from .skill_tools import (
    SearchSkillsTool,
    ReloadSkillsTool,
    InstallSkillTool,
    CreateSkillTool,
)
from .db_tools import MemoryTool, MemoTool
from .prompt_tools import (
    UpdateIdentityTool,
    UpdateSoulTool,
    SaveMemoryAndEndConversationTool,
)
from .proxy_tools import SetProxyTool, GetProxyTool
from .misc_tools import AskUserTool

__all__ = [
    "Tool",
    "ToolRegistry",
    # A 批基础工具
    "FileToolBase",
    "ReadFileTool",
    "WriteFileTool",
    "ListFilesTool",
    "SimpleWebCrawlerTool",
    "GetCurrentTimeTool",
    "CalculatorTool",
    # B 批工具
    "CommandLineTool",
    "SearchSkillsTool",
    "ReloadSkillsTool",
    "InstallSkillTool",
    "CreateSkillTool",
    "MemoryTool",
    "MemoTool",
    "UpdateIdentityTool",
    "UpdateSoulTool",
    "SaveMemoryAndEndConversationTool",
    "SetProxyTool",
    "GetProxyTool",
    "AskUserTool",
]
