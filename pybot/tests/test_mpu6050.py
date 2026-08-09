"""MPU6050 测试：14 字节连读解包（含负值符号，回归 S06）与采样滤波"""

import struct

from pybot.hd import i2c
from pybot.hd.mpu6050 import (ACCEL_CONFIG, ACCEL_XOUT_H, ADDR_0, ADDR_1,
                              GYRO_CONFIG, MPU6050, PWR_MGMT_1)


def test_addr_constants():
    """修复 S05：ADDR_1 必须是 0x69（仓颉版误写 0x68）"""
    assert ADDR_0 == 0x68
    assert ADDR_1 == 0x69


def test_setup_sequence(mock_i2c):
    fd = i2c.open_dev("/dev/i2c-1")
    m = MPU6050(fd, ADDR_0)
    m.setup()
    writes = [(e[2], e[3]) for e in mock_i2c.journal if e[0] == "write_reg"]
    assert writes == [(PWR_MGMT_1, 0x00),       # 解除休眠
                      (GYRO_CONFIG, 0x08),      # ±500°/s
                      (ACCEL_CONFIG, 0x10)]     # ±8g
    assert ("set_addr", fd, ADDR_0) in mock_i2c.journal


def test_read_sensor_signed(mock_i2c):
    """负值（高字节 ≥ 0x80）必须正确解为负数——回归 S06 符号扩展错误"""
    fd = i2c.open_dev("/dev/i2c-1")
    m = MPU6050(fd)
    # ax=-2 ay=3 az=4096 temp=999(跳过) gx=100 gy=-200 gz=300
    mock_i2c.block_values[(fd, ACCEL_XOUT_H)] = struct.pack(
        ">7h", -2, 3, 4096, 999, 100, -200, 300)
    assert m.read_sensor() == (-2, 3, 4096, 100, -200, 300)


def test_sample_and_latest(mock_i2c):
    fd = i2c.open_dev("/dev/i2c-1")
    m = MPU6050(fd)
    # 静止平放：az=4096(+1g@±8g)，其余 0 → roll/pitch 趋近 0
    mock_i2c.block_values[(fd, ACCEL_XOUT_H)] = struct.pack(
        ">7h", 0, 0, 4096, 0, 0, 0, 0)
    for _ in range(50):     # 多次采样让卡尔曼收敛
        m._sample_once()
    latest = m.latest()
    assert abs(latest["roll"]) < 1.0
    assert abs(latest["pitch"]) < 1.0
    assert latest["az"] > 3000.0    # 滤波值向测量值收敛
