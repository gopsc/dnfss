"""pytest 公共配置：mtc 根入 sys.path；pybot parser 独立加载（绕包 __init__）

Windows 开发机无 fcntl：import pybot 会经 robot.py → hd/i2c.py 触发
fcntl 导入失败。e2e 仅需 MatrixParser，而 parser.py 只依赖 logging，
用 spec_from_file_location 独立加载即可（与 pybot tests/conftest.py
的符号链接 fallback 同属 Windows 兼容层）。
"""

import importlib.util
import sys
from pathlib import Path

MTC_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MTC_ROOT))

PYBOT_PARSER = MTC_ROOT.parent / "pybot" / "pybot" / "parser.py"


def load_pybot_parser():
    """独立加载 pybot/parser.py（不触发 pybot/__init__ 的硬件链）"""
    spec = importlib.util.spec_from_file_location(
        "pybot_parser_standalone", PYBOT_PARSER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
