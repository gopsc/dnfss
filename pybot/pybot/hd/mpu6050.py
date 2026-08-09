"""MPU6050 六轴姿态传感器 —— 移植自 bot4cj/src/hd/MPU6050.cj

10ms 周期采样线程 + 6 路一维卡尔曼滤波，输出 roll/pitch 姿态角。

与仓颉版的差异（审计修复，语义不变）：
    S05: ADDR_1 = 0x69（AD0 拉高时的地址；仓颉版误写 0x68 与 ADDR_0 重复）；
    Q06: 停止采样用 threading.Event（仓颉版裸 bool 无内存屏障）；
    pitch 用 atan2(-ax, sqrt(ay²+az²)) 代替 atan(-ax/sqrt(...))，防除零且值域一致。
"""

import logging
import math
import struct
import threading
import time

from . import i2c
from .kalman import Kalman

log = logging.getLogger("pybot.mpu6050")

ADDR_0 = 0x68           # AD0 引脚接地
ADDR_1 = 0x69           # AD0 引脚拉高（修复 S05）

# 寄存器地址
PWR_MGMT_1 = 0x6B
GYRO_CONFIG = 0x1B
ACCEL_CONFIG = 0x1C
ACCEL_XOUT_H = 0x3B     # 14 字节连读起点：加速度(6) + 温度(2) + 陀螺仪(6)

RAD_TO_DEG = 57.2956    # 弧度转角度（与仓颉版一致）

SAMPLE_INTERVAL = 0.01  # 10ms 采样周期


class MPU6050:
    """MPU6050 传感器：绑定到某条 I2C 总线的 fd 上

    用法：
        m = MPU6050(fd, ADDR_0)
        m.setup()
        m.start_daemon()
        ... m.latest() ...
        m.stop_daemon(); m.wait_daemon()
    """

    def __init__(self, fd, addr=ADDR_0):
        self.fd = fd
        self.addr = addr
        # 6 路卡尔曼滤波（Q=20 R=200，与仓颉版参数一致）
        self._k_ax = Kalman(20.0, 200.0)
        self._k_ay = Kalman(20.0, 200.0)
        self._k_az = Kalman(20.0, 200.0)
        self._k_gx = Kalman(20.0, 200.0)
        self._k_gy = Kalman(20.0, 200.0)
        self._k_gz = Kalman(20.0, 200.0)
        self._stop = threading.Event()      # 修复 Q06
        self._thread = None
        self._lock = threading.Lock()
        self._latest = {"roll": 0.0, "pitch": 0.0,
                        "ax": 0.0, "ay": 0.0, "az": 0.0,
                        "gx": 0.0, "gy": 0.0, "gz": 0.0}

    # ---------------- 初始化 ----------------

    def setup(self):
        """唤醒 + 配置量程：陀螺仪 ±500°/s、加速度 ±8g（与仓颉版一致）"""
        with i2c.transaction(self.fd):
            i2c.set_addr(self.fd, self.addr)
            time.sleep(0.001)
            i2c.write_reg(self.fd, PWR_MGMT_1, 0x00)    # 解除休眠
            i2c.write_reg(self.fd, GYRO_CONFIG, 0x08)   # ±500°/s
            i2c.write_reg(self.fd, ACCEL_CONFIG, 0x10)  # ±8g
        time.sleep(0.1)     # 等待传感器稳定（锁外，不占总线）
        log.debug("mpu6050 setup done (fd=%d addr=0x%02X)", self.fd, self.addr)

    # ---------------- 读取 ----------------

    def read_sensor(self):
        """一次事务连读 14 字节，返回 (ax, ay, az, gx, gy, gz) 原始值"""
        with i2c.transaction(self.fd):
            i2c.set_addr(self.fd, self.addr)
            data = i2c.read_block(self.fd, ACCEL_XOUT_H, 14)
        vals = struct.unpack(">7h", data)   # 大端 7 个有符号 16 位
        # 索引 3 是温度，跳过
        return vals[0], vals[1], vals[2], vals[4], vals[5], vals[6]

    def _sample_once(self):
        ax, ay, az, gx, gy, gz = self.read_sensor()
        fax = self._k_ax.filter(float(ax))
        fay = self._k_ay.filter(float(ay))
        faz = self._k_az.filter(float(az))
        fgx = self._k_gx.filter(float(gx))
        fgy = self._k_gy.filter(float(gy))
        fgz = self._k_gz.filter(float(gz))
        # 姿态角（加速度计解算，与仓颉版公式一致；pitch 用 atan2 防除零）
        roll = math.atan2(fay, faz) * RAD_TO_DEG
        pitch = math.atan2(-fax, math.sqrt(fay * fay + faz * faz)) * RAD_TO_DEG
        with self._lock:
            self._latest.update(roll=roll, pitch=pitch,
                                ax=fax, ay=fay, az=faz,
                                gx=fgx, gy=fgy, gz=fgz)

    def latest(self):
        """最近一次滤波后的姿态数据（线程安全，返回副本）"""
        with self._lock:
            return dict(self._latest)

    # ---------------- 采样线程 ----------------

    def start_daemon(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="mpu6050-sampler", daemon=True)
        self._thread.start()
        log.debug("mpu6050 sampler started")

    def _loop(self):
        while not self._stop.wait(SAMPLE_INTERVAL):    # 10ms 周期
            try:
                self._sample_once()
            except i2c.I2CError as e:
                log.warning("mpu6050 sample failed: %s", e)

    def stop_daemon(self):
        self._stop.set()

    def wait_daemon(self):
        if self._thread is not None:
            self._thread.join()
            self._thread = None
            log.debug("mpu6050 sampler stopped")
