"""硬件变量 —— 移植自 bot4cj/src/r.cj 的 MatrixVars_Hardware 及六个工厂

仓颉版三闭包 rise/things/free 改为基类三方法 build()/play(params)/destroy()，
JsonValue 通信协议改为原生 dict/str（无跨语言边界后无需序列化）。

每个子类提供 from_matrix(matrix, i, M, **kwargs) 工厂：
    matrix: MatrixObject（行 0 是表头）
    i:      数据行号（从 1 开始）
    M:      已创建的硬件变量字典（供引用其他硬件，如 Servo→controller）
"""

import logging

from .hd import i2c as hd_i2c
from .hd.camera import Camera
from .hd.mpu6050 import MPU6050
from .hd.servo import Servo
from .hd.speaker import Speaker
from .parser import get_mt, parse_mt, to_int

log = logging.getLogger("pybot.hardvars")


class HardwareVar:
    """硬件变量基类（对应仓颉版 MatrixVars_Hardware）

    build()       —— 激活（对应闭包 rise）
    play(params)  —— 调用（对应闭包 things），入参/返回 dict（Alias 返回 str）
    destroy()     —— 释放（对应闭包 free），要求幂等
    """

    def __init__(self, name):
        self.name = name

    def build(self):
        pass

    def play(self, params):
        return {}

    def destroy(self):
        pass


# ---------------- Alias 别名变量 ----------------

class AliasVar(HardwareVar):
    """参数别名：@Action 列名 `s1.A` 中的 A → angle；play() 返回真实参数名 str"""

    def __init__(self, name, alias):
        super().__init__(name)
        self.alias = alias

    def play(self, params):
        return self.alias

    @classmethod
    def from_matrix(cls, matrix, i, M, **kwargs):
        name = matrix[i, matrix.index_of_col(0, "name")]
        alias = get_mt(matrix, i, matrix.index_of_col(0, "alias"))
        log.info("Create a Alias hard-Var: %s -> %s", name, alias)
        return cls(name, alias)


# ---------------- I2C 总线变量 ----------------

class I2CVar(HardwareVar):
    """I2C 总线：工厂阶段即打开设备（与仓颉版一致）；play() 返回 {"fd": fd}"""

    def __init__(self, name, path):
        super().__init__(name)
        self.bus = hd_i2c.I2CBus(path)

    def play(self, params):
        return {"fd": self.bus.fd}

    def destroy(self):
        log.info("I2C HVar: %s is closing now", self.name)
        self.bus.close()        # close 幂等

    @classmethod
    def from_matrix(cls, matrix, i, M, **kwargs):
        name = matrix[i, matrix.index_of_col(0, "name")]
        path = get_mt(matrix, i, matrix.index_of_col(0, "path"))
        log.info("Create a I2C hard-Var: %s, path: %s", name, path)
        return cls(name, path)


# ---------------- MPU6050_BIND 传感器变量 ----------------

class MPU6050Var(HardwareVar):
    """MPU6050 绑定：build 启动采样线程，destroy 停止；play() 返回最近姿态"""

    def __init__(self, name, sensor):
        super().__init__(name)
        self.sensor = sensor

    def build(self):
        log.info("MPU6050_BIND HVar: %s is starting now", self.name)
        self.sensor.setup()
        self.sensor.start_daemon()

    def play(self, params):
        return self.sensor.latest()

    def destroy(self):
        log.info("MPU6050_BIND HVar: %s is closing now", self.name)
        self.sensor.stop_daemon()
        self.sensor.wait_daemon()

    @classmethod
    def from_matrix(cls, matrix, i, M, **kwargs):
        name = matrix[i, matrix.index_of_col(0, "name")]
        i2c_name = get_mt(matrix, i, matrix.index_of_col(0, "I2C"))
        addr = get_mt(matrix, i, matrix.index_of_col(0, "addr"))
        log.info("Create a MPU6050_BIND HVar: %s, use i2c interf: %s, "
                 "i2c addr: %s", name, i2c_name, addr)
        i2c_var = M[i2c_name]
        if not isinstance(i2c_var, I2CVar):
            raise TypeError(
                f"@MPU6050_BIND '{name}' references '{i2c_name}', "
                f"but it is a {type(i2c_var).__name__}, not an I2CVar")
        fd = i2c_var.play({})["fd"]
        return cls(name, MPU6050(fd, to_int(addr)))


# ---------------- Servo 舵机变量 ----------------

class ServoVar(HardwareVar):
    """舵机：play({"angle": x}) 经校准算法后回调 controller（如 PCA9685 插件）"""

    def __init__(self, name, servo):
        super().__init__(name)
        self.servo = servo

    def play(self, params):
        self.servo.set_angle(float(params["angle"]))
        return {"status": "OK"}

    @classmethod
    def from_matrix(cls, matrix, i, M, **kwargs):
        name = matrix[i, matrix.index_of_col(0, "name")]
        max_a = parse_mt(matrix, i, matrix.index_of_col(0, "max"))
        min_a = parse_mt(matrix, i, matrix.index_of_col(0, "min"))
        offset = parse_mt(matrix, i, matrix.index_of_col(0, "offset"))
        ctrl = get_mt(matrix, i, matrix.index_of_col(0, "controller"))
        channel = parse_mt(matrix, i, matrix.index_of_col(0, "channel"), int)
        log.info("Create a Servo hard-Var: %s, max: %s, min: %s, offset: %s, "
                 "controller: %s, channel: %s",
                 name, max_a, min_a, offset, ctrl, channel)
        controller = M[ctrl]                # like pca9685
        # int(angle) 截断对齐仓颉版 JsonInt(Int64(angle))
        servo = Servo(max_a, min_a, offset,
                      lambda angle: controller.play(
                          {"channel": channel, "angle": int(angle)}))
        return cls(name, servo)


# ---------------- Speaker 扬声器变量 ----------------

class SpeakerVar(HardwareVar):
    """扬声器：play({"frequency": f, "delay": ms}) 发声并阻塞 delay 毫秒"""

    def __init__(self, name, speaker):
        super().__init__(name)
        self.speaker = speaker

    def play(self, params):
        freq = float(params["frequency"])
        delay = int(float(params["delay"]))     # 对齐仓颉版 Int32(asFloat)
        self.speaker.play(freq, delay)
        return {"status": "OK"}

    @classmethod
    def from_matrix(cls, matrix, i, M, dry_run=False, **kwargs):
        name = matrix[i, matrix.index_of_col(0, "name")]
        channels = parse_mt(matrix, i, matrix.index_of_col(0, "channels"), int)
        sample_rate = parse_mt(matrix, i,
                               matrix.index_of_col(0, "sample_rate"), int)
        volumn = parse_mt(matrix, i, matrix.index_of_col(0, "volumn"))
        max_f = parse_mt(matrix, i, matrix.index_of_col(0, "max_f"))
        min_f = parse_mt(matrix, i, matrix.index_of_col(0, "min_f"))
        log.info("Create a Speaker: %s, channels: %s, sample_rate: %s, "
                 "volume: %s, max_f: %s, min_f: %s",
                 name, channels, sample_rate, volumn, max_f, min_f)
        speaker = Speaker(channels=channels, sample_rate=sample_rate,
                          volumn=volumn, max_f=max_f, min_f=min_f,
                          dry_run=dry_run)
        return cls(name, speaker)


# ---------------- Camera 摄像头变量（pybot 新增，无仓颉版对应） ----------------

class CameraVar(HardwareVar):
    """摄像头：build 启动后台抓帧线程维护有界队列；play() 返回最新帧"""

    def __init__(self, name, camera):
        super().__init__(name)
        self.camera = camera

    def build(self):
        log.info("Camera HVar: %s is starting now", self.name)
        self.camera.setup()
        self.camera.start_daemon()

    def play(self, params):
        """返回 (时间戳, frame) 或 None（队列空 / dry_run）"""
        return self.camera.latest()

    def destroy(self):
        log.info("Camera HVar: %s is closing now", self.name)
        self.camera.stop_daemon()
        self.camera.wait_daemon()
        self.camera.close()

    @classmethod
    def from_matrix(cls, matrix, i, M, dry_run=False, **kwargs):
        name = matrix[i, matrix.index_of_col(0, "name")]
        width = parse_mt(matrix, i, matrix.index_of_col(0, "width"), int)
        height = parse_mt(matrix, i, matrix.index_of_col(0, "height"), int)
        framerate = parse_mt(matrix, i, matrix.index_of_col(0, "framerate"))
        capacity = parse_mt(matrix, i, matrix.index_of_col(0, "capacity"), int)
        log.info("Create a Camera hard-Var: %s, %dx%d @ %.1ffps, capacity: %s",
                 name, width, height, framerate, capacity)
        camera = Camera(width=width, height=height, framerate=framerate,
                        capacity=capacity, dry_run=dry_run)
        return cls(name, camera)
