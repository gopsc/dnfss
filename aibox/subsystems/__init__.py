"""
subsystems - aibox 子系统模块（包）

抽离自 aibox.py 的基础设施子系统，按职责分文件。
当前包含（Wave 1）：
  - executors.py       : CommandExecutor + SubprocessCommandExecutor
  - prompt_manager.py  : PromptManager（单例）
  - db.py              : MemoryDatabase + MemoDatabase
  - skill_manager.py   : SkillManager
Wave 2：
  - conversation_history.py : ConversationHistory（对话历史管理）
  - conversation_compressor.py : ConversationCompressor（对话压缩，注入 llm_client）

依赖方向：common <- subsystems <- aibox
不反向依赖 aibox 任何符号。

采用"重导出"策略：外部统一用 `from subsystems import MemoryDatabase`，
调用点无需关心具体子模块所在文件。
"""

from .executors import CommandExecutor, SubprocessCommandExecutor
from .prompt_manager import PromptManager
from .db import MemoryDatabase, MemoDatabase
from .skill_manager import SkillManager
from .conversation_history import ConversationHistory
from .conversation_compressor import ConversationCompressor
from .process_manager import (
    ProcessManager,
    get_process_manager,
    reset_process_manager,
)

__all__ = [
    "CommandExecutor",
    "SubprocessCommandExecutor",
    "PromptManager",
    "MemoryDatabase",
    "MemoDatabase",
    "SkillManager",
    "ConversationHistory",
    "ConversationCompressor",
    "ProcessManager",
    "get_process_manager",
    "reset_process_manager",
]
