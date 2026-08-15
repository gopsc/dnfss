# rght-hand.py —— rght-hand.mt 的 mtc DSL 源码（新格式）
# 右手（右前腿 s7/s8 为 rf，本脚本动 s3/s6 = 左后/左前？保持原脚本语义）：
# s6 收回 → s3/s6 展开维持 1s → 收回；蜂鸣 10→400→1000→10 Hz 阶跃。
# 编译：cd mtc && python -m mtc ../bot4cj/scripts/rght-hand.py -o ../bot4cj/scripts/rght-hand.mt

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

_STAND = [45, 0, 45, 0, 45, 0, 45, 0, 90, 90]

def stand(dur=10):
    for _i, _ang in enumerate(_STAND, 1):
        SetTo(f"s{_i}", _ang, dur)

# ---- 动作序列（200 + 200 + 1000 + 20，T=1420ms） ----
# spk.F 行级阶跃（pybot 微步对频率列不插值，直接取目标值），与 Beep 恒值段一致
stand()
Beep(10, 200)               # r1: F=10
Delay(200)
SetTo("s6", 0, 200)         # r2: s6 45→0
Beep(400, 200)              # r2: F=400
Delay(200)
SetTo("s3", 0, 1000)        # r3: s3 45→0，s6 0→150
SetTo("s6", 150, 1000)
Beep(1000, 1000)            # r3: F=1000
Delay(1000)
SetTo("s3", 45, 20)         # r4: 收回
SetTo("s6", 0, 20)
Beep(10, 20)                # r4: F=10
Delay(20)
