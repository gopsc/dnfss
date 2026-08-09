"""PCA9685 16 路 PWM 舵机控制器插件 —— 与 bot4cj/modules/PCA9685.lua 逐语义对齐

pybot 插件协议：build(ctx) / call(ctx) / destroy(ctx)
ctx 元数据来自 @PCA9685 块：
    name / fd（I2C 引用展开）/ address（原始字符串如 "0x40"）
call 实时参数：channel（int）/ angle（int，0~180）
"""

import logging
import time

from pybot.hd import i2c

log = logging.getLogger("pybot.modules.PCA9685")

# MODE1 寄存器（0x00）控制位
MODE1 = 0x00
MODE1_RESTART = 0x80
MODE1_SLEEP = 0x10
MODE1_AUTOINC = 0x20
PRE_SCALE = 0xFE
LED0_ON_L = 0x06        # 通道 0 PWM 寄存器组起点，每通道 4 个寄存器

# 50Hz 预分频：25MHz / (4096 * 50Hz) - 1，四舍五入 = 121（与 Lua 版一致）
PRESCALE_50HZ = int(25000000.0 / (4096.0 * 50.0) - 1.0 + 0.5)


def _fd_addr(ctx):
    return int(ctx["fd"]), int(str(ctx["address"]), 0)


def build(ctx):
    """初始化：重启 → SLEEP → 设 prescale(50Hz) → 唤醒（寄存器序列与 Lua 版一致）"""
    fd, addr = _fd_addr(ctx)
    log.info("PCA9685 build: fd=%d addr=0x%02X prescale=%d",
             fd, addr, PRESCALE_50HZ)
    with i2c.transaction(fd):
        i2c.set_addr(fd, addr)
        i2c.write_reg(fd, MODE1, MODE1_RESTART)     # 复位
        time.sleep(0.005)
        i2c.write_reg(fd, MODE1, MODE1_SLEEP)       # 休眠（改 prescale 前提）
        i2c.write_reg(fd, PRE_SCALE, PRESCALE_50HZ)  # 50Hz
        i2c.write_reg(fd, MODE1, MODE1_AUTOINC)     # 唤醒 + 自动递增
        time.sleep(0.005)
    return {"result": "OK"}


def call(ctx):
    """写通道 PWM：0.5ms(0°) ~ 2.5ms(180°) 脉宽 @ 50Hz(20ms 周期)"""
    fd, addr = _fd_addr(ctx)
    channel = int(ctx["channel"])
    angle = float(ctx["angle"])
    width = 0.5 + 2.0 * angle / 180.0               # 脉宽 ms
    offval = int(width * 4096.0 / 20.0)             # 20ms 周期内的计数（floor）
    reg = LED0_ON_L + 4 * channel
    with i2c.transaction(fd):
        i2c.set_addr(fd, addr)
        i2c.write_reg(fd, reg + 0, 0x00)            # ON_L
        i2c.write_reg(fd, reg + 1, 0x00)            # ON_H
        i2c.write_reg(fd, reg + 2, offval & 0xFF)   # OFF_L
        i2c.write_reg(fd, reg + 3, (offval >> 8) & 0xFF)  # OFF_H
    return {"result": "OK"}


def destroy(ctx):
    """复位 PCA9685"""
    fd, addr = _fd_addr(ctx)
    log.info("PCA9685 destroy: fd=%d addr=0x%02X", fd, addr)
    with i2c.transaction(fd):
        i2c.set_addr(fd, addr)
        i2c.write_reg(fd, MODE1, MODE1_RESTART)
    return {"result": "OK"}
