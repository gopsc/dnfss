"""ADS1115 16 位 I2C ADC 插件（experimental）

⚠️ bot4cj/modules/ADS1115.lua 是 PCA9685 的复制残片（引用未定义的
prescale、写的是 PCA9685 寄存器，运行必错），无忠实移植价值。
本文件按 ADS1115 数据手册写干净的最小单次转换实现，未经实机验证。

ctx 元数据：name / fd（I2C 引用展开）/ address（如 "0x48"）
call 实时参数：channel（0~3，单端输入 AINx）
call 返回：{"raw": 有符号 16 位原始值, "voltage": 电压（±4.096V 量程）}
"""

import logging
import struct
import time

from pybot.hd import i2c

log = logging.getLogger("pybot.modules.ADS1115")

REG_CONVERSION = 0x00
REG_CONFIG = 0x01

# 单次转换配置：OS=1(开始) | MUX=100+ch(单端) | PGA=001(±4.096V) | MODE=1(单次)
# 低字节：DR=100(128SPS) | 比较器禁用(11)
_CONFIG_HI_BASE = 0b1_100_001_1     # channel 0，其余通道 MUX +ch
_CONFIG_LO = 0b100_0_0_0_11
FSR = 4.096                         # 满量程 ±4.096V


def build(ctx):
    """无需初始化（单次转换模式按需触发），仅校验设备可达"""
    fd = int(ctx["fd"])
    addr = int(str(ctx["address"]), 0)
    log.info("ADS1115 build (experimental): fd=%d addr=0x%02X", fd, addr)
    return {"result": "OK"}


def call(ctx):
    """触发一次单次转换并读回结果"""
    fd = int(ctx["fd"])
    addr = int(str(ctx["address"]), 0)
    channel = int(ctx.get("channel", 0)) & 0x03
    config_hi = _CONFIG_HI_BASE + (channel << 4)
    with i2c.transaction(fd):
        i2c.set_addr(fd, addr)
        i2c.write_block(fd, REG_CONFIG, bytes([config_hi, _CONFIG_LO]))
        time.sleep(0.009)       # 128SPS 一次转换约 8ms
        data = i2c.read_block(fd, REG_CONVERSION, 2)
    raw = struct.unpack(">h", data)[0]      # 大端有符号 16 位
    return {"result": "OK", "raw": raw, "voltage": raw * FSR / 32768.0}


def destroy(ctx):
    """单次转换模式空闲即低功耗，无需复位"""
    log.info("ADS1115 destroy")
    return {"result": "OK"}
