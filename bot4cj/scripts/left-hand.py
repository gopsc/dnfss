# left-hand.py —— left-hand.mt 的 mtc DSL 源码（新格式）
# 左手：s1/s8 展开维持 1s 后收回；蜂鸣 10→400→1000→10 Hz 阶跃。
# 编译：cd mtc && python -m mtc ../bot4cj/scripts/left-hand.py -o ../bot4cj/scripts/left-hand.mt

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

# ---- 动作序列（200 + 200 + 1000 + 20，T=1420ms） ----
stand()
Beep(10, 200)               # r1: F=10
Delay(200)
Beep(400, 200)              # r2: 仅变频率（原行无舵机变化）
Delay(200)
SetTo("s1", 0, 1000)        # r3: s1 45→0，s8 0→150
SetTo("s8", 150, 1000)
Beep(1000, 1000)            # r3: F=1000
Delay(1000)
SetTo("s1", 45, 20)         # r4: 收回
SetTo("s8", 0, 20)
Beep(10, 20)                # r4: F=10
Delay(20)
