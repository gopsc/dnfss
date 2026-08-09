"""舵机类 —— 移植自 bot4cj/src/hd/Servo.cj

含最大值、最小值、漂移值三个调整项，以及一个设置角度回调函数。
角度算法与仓颉版逐行对齐：
    a = angle + offset
    a = clamp(a, |min|, |max|)
    if max < 0: a = 180 - a     # 负 max 表示舵机反装，角度翻转
"""

MAX_ANGLE = 180.0


class Servo:

    def __init__(self, max_angle, min_angle, offset, set_angle_callback):
        self.max = max_angle
        self.min = min_angle
        self.offset = offset
        self._set_angle = set_angle_callback

    def set_angle(self, angle):
        a = angle + self.offset             # 加上漂移值
        if a > abs(self.max):
            a = abs(self.max)
        if a < abs(self.min):
            a = abs(self.min)
        if self.max < 0.0:
            a = MAX_ANGLE - a               # 反装翻转
        self._set_angle(a)
