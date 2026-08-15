# record_stand.py —— record_stand.mt 的 mtc DSL 源码（新格式）
# 录制用：stand → s1/s3/s5/s7 依次到 60°/45°/30°/45° 姿态序列（各 200ms），
# 含 @Camera（合成帧）与激活的 @MPU6050_BIND（imu.npy 录制）。
# 编译：cd mtc && python -m mtc ../bot4cj/scripts/record_stand.py -o ../bot4cj/scripts/record_stand.mt

Alias("A", "angle")
Alias("F", "frequency")

I2C("i2c-0", "/dev/i2c-0")
I2C("i2c-1", "/dev/i2c-1")

PCA9685("pca9685-0", "i2c-1", "0x40")

MPU6050_BIND("mpu6050-0", "i2c-1", "0x68")

Speaker("spk", 2, 48000, 0.6, 5000, 0)   # min_f 原为占位符（首行解析为 0）

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

Camera("cam0", 640, 480, 30, 8)

_STAND = [45, 0, 45, 0, 45, 0, 45, 0, 90, 90]

def stand(dur=10):
    for _i, _ang in enumerate(_STAND, 1):
        SetTo(f"s{_i}", _ang, dur)

# ---- 动作序列（5×200，T=1000ms → 100 栅格行） ----
# 注：原文件注释「首行不分解，共 81 步」已过时——99c1bc1 起首行也分解为
# 20 个恒等微步，原版/编译版在现行 pybot 语义下均为 100 步且逐元素相等。
stand()
Beep(10, 1000)              # spk.F=10 贯穿全程（r2-r5 占位符继承首行）
Delay(200)
for _ang in (60, 45, 30, 45):
    for _dev in ("s1", "s3", "s5", "s7"):
        SetTo(_dev, _ang, 200)
    Delay(200)
