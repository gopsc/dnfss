"""pytest 公共配置：把 aibox 目录加入 sys.path，桩掉可选第三方依赖。

aibox 主模块 aibox.py 依赖 websockets，但 tools/basic_tools.py 仅依赖
common + 标准库，所以单测 basic_tools 不需要真实 websockets——桩掉即可
让 `from tools import ...` 间接 import aibox.py 时不出错（虽然本测试
不直接 import aibox，但保持环境干净）。
"""

import sys
import types
from pathlib import Path

# 把 aibox 目录加入 sys.path（无需安装为包即可 import tools/common）
AIBOX_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AIBOX_DIR))
# tests 目录也加入：测试文件互相导入（如 test_models_profile 复用 test_once_mode 的 _make_ai）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 桩掉 websockets（aibox.py 顶部 import websockets，测试环境可能未安装）
if "websockets" not in sys.modules:
    sys.modules["websockets"] = types.ModuleType("websockets")

import pytest  # noqa: E402
