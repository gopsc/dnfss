"""SPI 总线插件 —— 与 bot4cj/modules/SPI.lua 逐语义对齐

@SPI 块元数据：name / path / spi_mode / bits / speed / delay
build 打开并配置 spidev；call 返回 {"fd": fd}（供其他块引用展开）；
destroy 关闭。模块级全局 _fd 即设备私有状态
（pybot 每设备加载独立模块实例，与 Lua 每设备独立状态机等价）。
"""

import logging

from pybot.hd import spi

log = logging.getLogger("pybot.modules.SPI")

_fd = -1    # 全局文件句柄（对应 SPI.lua 的 fd）


def build(ctx):
    global _fd
    for key in ("path", "spi_mode", "bits", "speed", "delay"):
        if key not in ctx:
            return {"result": "ERR", "message": f"Invalid Arguments: {key}"}
    log.info("SPI build parameters: %s", ctx)
    _fd = spi.open_dev(ctx["path"])
    if _fd <= 0:
        return {"result": "ERR", "message": "Failed to open SPI file"}
    spi.setup(_fd, int(str(ctx["spi_mode"]), 0),
              int(ctx["bits"]), int(ctx["speed"]))
    return {"result": "OK", "fd": _fd}


def call(ctx):
    return {"fd": _fd}


def destroy(ctx):
    global _fd
    log.info("SPI destroy, fd=%d", _fd)
    if _fd > 0:
        spi.close_dev(_fd)
    _fd = -1
    return {"result": "OK"}
