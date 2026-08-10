"""pybot —— 机器人 .mt 动作脚本解析器（bot4cj 的 Python 迁移版）

架构与 bot4cj（仓颉版）逐语义对齐：
    .mt 脚本 → parser.MatrixParser → MatrixObject 列表
             → robot.RobotStruct（按 @块名分发硬件变量工厂）
             → active() / run() / deactive()

硬件驱动插件（modules/<Name>.py）替代仓颉版的 Lua 扩展层，
协议为 build(ctx) / call(ctx) / destroy(ctx) 三段生命周期。

迁移方案见 docs/reports/bot4cj_python_migration_dev_doc.md。
"""

__version__ = "0.1.0"

from .parser import (          # noqa: F401
    MatrixObject,
    MatrixParser,
    MatrixSaver,
    ParseError,
    DoesNotSync,
    MatrixIndexError,
    get_mt,
    parse_mt,
    to_int,
)
from .robot import RobotStruct  # noqa: F401
from .plugin import PluginNotFound, PluginProtocolError, PluginVar  # noqa: F401
