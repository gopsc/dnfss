# jump.py —— jump.mt 的 mtc DSL 源码（新格式）
# 跳跃：四腿 1 号关节（s2/s4/s6/s8）快速下蹲 10°→20°→30°（各 20ms）后 200ms 弹回站立。
# 编译：cd mtc && python -m mtc ../bot4cj/scripts/jump.py -o ../bot4cj/scripts/jump.mt

Alias("A", "angle")
Alias("F", "frequency")

I2C("i2c-0", "/dev/i2c-0")
I2C("i2c-1", "/dev/i2c-1")

PCA9685("pca9685-0", "i2c-1", "0x40")

# 姿态传感器按需启用：
# MPU6050_BIND("mpu6050-0", "i2c-1", "0x69")
# MPU6050_BIND("mpu6050-1", "i2c-1", "0x68")
# MPU6050_BIND("mpu6050-2", "i2c-0", "0x68")
# MPU6050_BIND("mpu6050-3", "i2c-0", "0x69")

Speaker("spk", 2, 48000, 0.6, 5000, 0)

#        名     max  ch
_SERVOS = [("s1",   90, 0),   # rb0
           ("s2",  180, 1),   # rb1
           ("s3",  -90, 2),   # lb0
           ("s4", -180, 3),   # lb1
           ("s5",   90, 4),   # lf0
           ("s6",  180, 5),   # lf1
           ("s7",  -90, 6),   # rf0
           ("s8", -180, 7),   # rf1
           ("s9",  180, 8),   # cam0
           ("s10", 180, 9)]   # cam1
for _name, _max, _ch in _SERVOS:
    Servo(_name, _max, 0, 0, "pca9685-0", _ch)

# 录制源（pybot --record 需要 @Camera 块）
Camera("cam0", 640, 480, 30, 8)

_STAND = [45, 0, 45, 0, 45, 0, 45, 0, 90, 90]

def stand(dur=10):
    for _i, _ang in enumerate(_STAND, 1):
        SetTo(f"s{_i}", _ang, dur)

# ---- 动作序列（200 + 3×20 + 200，T=460ms） ----
stand()
Beep(10, 460)               # spk.F=10 贯穿全程
Delay(200)
for _ang in (10, 20, 30):
    for _dev in ("s2", "s4", "s6", "s8"):
        SetTo(_dev, _ang, 20)
    Delay(20)
stand(200)                  # 弹回站立
Delay(200)
