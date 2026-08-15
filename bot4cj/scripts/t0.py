# t0.py —— t0.mt 的 mtc DSL 源码（新格式）
# 对角步态（trot）单周期：左后/右前/左前/右后依次抬摆，spk.F 10→0→200→0 Hz。
# 编译：cd mtc && python -m mtc ../bot4cj/scripts/t0.py -o ../bot4cj/scripts/t0.mt

Alias("A", "angle")
Alias("F", "frequency")

# I2C("i2c-0", "/dev/i2c-0")   # 原 t0.mt 中此行为注释
I2C("i2c-1", "/dev/i2c-1")

PCA9685("pca9685-0", "i2c-1", "0x40")

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

_STAND = [45, 0, 45, 0, 45, 0, 45, 0, 90, 90]

def stand(dur=10):
    for _i, _ang in enumerate(_STAND, 1):
        SetTo(f"s{_i}", _ang, dur)

# ---- 动作序列（200+20 + 500+20 + 500+20 + 500+20 + 500+20，T=2300ms） ----
# 频率通道：Beep 段外 rest=0 = 静音（对齐原脚本 F=0 / 占位符继承 0 的行）
stand()
Beep(10, 220)                       # r1-r2: F=10（r2 占位符继承首行）
Delay(200)
SetTo("s1", 90, 20)                 # r2: left-behind
Delay(20)
SetTo("s1", 45, 500)                # r3: stand（F=0 → 段间空隙回落静音）
Delay(500)
SetTo("s3", 90, 20)                 # r4: right-behind
SetTo("s7", 45, 20)
Delay(20)
SetTo("s3", 45, 500)                # r5: stand + F=200
SetTo("s10", 45, 500)
Beep(200, 500)
Delay(500)
SetTo("s5", 90, 20)                 # r6: right-front（F=0）
Delay(20)
SetTo("s5", 45, 500)                # r7: stand
Delay(500)
SetTo("s7", 90, 20)                 # r8: left-front
Delay(20)
SetTo("s7", 45, 500)                # r9: stand
Delay(500)
SetTo("s10", 90, 20)                # r10: 云台回 90
Delay(20)
