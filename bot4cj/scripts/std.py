# std.py —— std.mt 的 mtc DSL 源码（新格式）
# Dashun 蜘蛛机器人站立姿态：10 舵机到位 + spk 10Hz 节拍 200ms。
#
# 编译（产物覆盖同目录 std.mt）：
#   cd mtc && python -m mtc ../bot4cj/scripts/std.py -o ../bot4cj/scripts/std.mt
#
# 与原手写 std.mt 的执行等价性：
#   原 @Action 单行 delay=200，pybot 分解为 20 个恒等微步（首行不插值）；
#   本脚本编译为 20 行 delay=10 的常量栅格（servo 段 10ms 到位后保持，
#   spk.frequency 段覆盖全程），两者微步序列完全一致
#   （tests 验证：两版本 recorder 录制的 actions.npy 逐元素相等）。

Alias("A", "angle")
Alias("F", "frequency")

I2C("i2c-0", "/dev/i2c-0")
I2C("i2c-1", "/dev/i2c-1")

PCA9685("pca9685-0", "i2c-1", "0x40")

# 姿态传感器按需启用（对应原 std.mt 中的注释行）：
# MPU6050_BIND("mpu6050-0", "i2c-1", "0x69")
# MPU6050_BIND("mpu6050-1", "i2c-1", "0x68")
# MPU6050_BIND("mpu6050-2", "i2c-0", "0x68")
# MPU6050_BIND("mpu6050-3", "i2c-0", "0x69")

Speaker("spk", 2, 48000, 0.6, 5000, 0)   # min_f 原为占位符 '-'（首行解析为 0）

Servo("s1",    90, 0, 0, "pca9685-0", 0)   # rb0
Servo("s2",   180, 0, 0, "pca9685-0", 1)   # rb1
Servo("s3",   -90, 0, 0, "pca9685-0", 2)   # lb0
Servo("s4",  -180, 0, 0, "pca9685-0", 3)   # lb1
Servo("s5",    90, 0, 0, "pca9685-0", 4)   # lf0
Servo("s6",   180, 0, 0, "pca9685-0", 5)   # lf1
Servo("s7",   -90, 0, 0, "pca9685-0", 6)   # rf0
Servo("s8",  -180, 0, 0, "pca9685-0", 7)   # rf1
Servo("s9",   180, 0, 0, "pca9685-0", 8)   # cam0
Servo("s10",  180, 0, 0, "pca9685-0", 9)   # cam1

# 录制源（pybot --record 需要 @Camera 块）
Camera("cam0", 640, 480, 30, 8)

# stand：10ms（一个栅格步）内到位并保持；首行即目标值，无爬升
SetTo("s1", 45, 10)
SetTo("s2", 0, 10)
SetTo("s3", 45, 10)
SetTo("s4", 0, 10)
SetTo("s5", 45, 10)
SetTo("s6", 0, 10)
SetTo("s7", 45, 10)
SetTo("s8", 0, 10)
SetTo("s9", 90, 10)
SetTo("s10", 90, 10)

# spk 10Hz 持续 200ms —— 对齐原行 spk.F=10 + delay=200；T=200（20 栅格行）
Beep(10, 200)
