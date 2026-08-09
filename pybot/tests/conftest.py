"""pytest 公共配置：把项目根加入 sys.path（无需安装即可 import pybot）"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from pybot.hd import i2c, spi  # noqa: E402


SCRIPTS_DIR = ROOT / "scripts"


@pytest.fixture()
def mock_i2c():
    """切换到 Mock I2C 总线，测试结束恢复真实后端"""
    mock = i2c.use_mock()
    yield mock
    i2c.use_real()


@pytest.fixture()
def mock_spi():
    """切换到 Mock SPI 总线，测试结束恢复真实后端"""
    mock = spi.use_mock()
    yield mock
    spi.use_real()
