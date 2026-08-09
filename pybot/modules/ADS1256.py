"""ADS1256 24 位 SPI ADC 插件 —— 对应 bot4cj/modules/ADS1256.lua（空桩）

Lua 版即为空桩（无任何实现），保持同等状态：三个入口仅记日志。
实机接入时参考 modules/SPI.py 取 fd 后经 pybot.hd.spi.transfer 实现。
"""

import logging

log = logging.getLogger("pybot.modules.ADS1256")


def build(ctx):
    log.warning("ADS1256 is a stub (not implemented), build ctx: %s", ctx)
    return {"result": "OK", "status": "stub"}


def call(ctx):
    log.warning("ADS1256 is a stub (not implemented)")
    return {"result": "OK", "status": "stub"}


def destroy(ctx):
    log.warning("ADS1256 is a stub (not implemented)")
    return {"result": "OK", "status": "stub"}
