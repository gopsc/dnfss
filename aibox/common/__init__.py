"""
common - aibox 底层基础设施公共模块（包）

抽离自 aibox.py 的零依赖基础层，供 aibox.py 及后续 tools.py 等模块导入。
当前包含：
  - i18n.py      : I18n 类 + i18n 实例（多语言）
  - config.py    : ConfigManager（配置管理）
  - constants.py : Constants 类 + constants 实例（系统常量）
  - logger.py    : Logger（统一日志）
形成单向依赖：common <- tools <- aibox。

采用"重导出"策略：外部统一用 `from common import i18n, I18n, ...`，
调用点无需关心具体子模块所在文件。
"""

from .i18n import i18n, I18n
from .config import ConfigManager
from .constants import Constants, constants
from .logger import Logger

__all__ = ["i18n", "I18n", "ConfigManager", "Constants", "constants", "Logger"]
