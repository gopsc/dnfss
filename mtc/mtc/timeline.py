"""时间轴 IR —— 运动段、重叠截断、10ms 栅格化采样

非阻塞时间轴模型（与 mtc 设计定稿一致）：
    Action 在当前游标处注册运动段并立即返回，Delay/Wait 推进游标；
    .mt 一行即全通道快照，段在不同通道上的重叠天然表达并发。

栅格语义：格点 t=10,20,…,T，每行代表该 10ms 结束时刻的姿态。
与 pybot 执行的等价性：delay=10 的行在 robot.run() 中分解为 1 个
微步且插值系数 1.0（k=0,n=1），即直接到位 —— 栅格行语义严格成立。
"""

from dataclasses import dataclass, field

STEP_MS = 10          # .mt 栅格步长，与 pybot run() 微步节拍一致


@dataclass
class Segment:
    """一个运动段：[t0, t0+dur) 内从 v0 线性运动到 v1（绝对值）"""

    t0: float
    dur: float
    v0: float
    v1: float

    @property
    def end(self):
        return self.t0 + self.dur

    def value_at(self, t):
        """t 时刻的值；段前取 v0、段后取 v1（保持语义）"""
        if t <= self.t0:
            return self.v0
        if t >= self.end:
            return self.v1
        return self.v0 + (self.v1 - self.v0) * (t - self.t0) / self.dur


@dataclass
class Channel:
    """单通道时间轴（如 s1.angle / spk.frequency）

    段按注册序追加（游标单调 ⇒ t0 单调），因此任何时刻最多一个
    未完结段（末尾段）。重叠规则：新段注册时若末尾段未完结，则
    截断末尾段至 t0，新段基准取前段目标值 v1（编译期确定，不依赖
    运行时实际姿态），由调用方在注册前取 target 作为新段 v0。

    rest：静止值。None（舵机等姿态通道）段外保持前段目标值；
    数值（如 frequency 通道 = 0）段前/段间空隙/段后统一回落该值
    —— 对齐 pybot speaker 静音语义（freq<=0 不出声仅保节拍）。
    """

    name: str
    initial: float = 0.0                # Pose() 声明的编译期初始姿态
    rest: float = None                  # 静止值（None=保持前段目标值）
    segments: list = field(default_factory=list)
    overlap_warns: list = field(default_factory=list)   # 截断告警文本

    @property
    def target(self):
        """已注册目标姿态（最后段 v1；无段取 initial）"""
        return self.segments[-1].v1 if self.segments else self.initial

    @property
    def busy_until(self):
        """最晚在途完成时刻"""
        return self.segments[-1].end if self.segments else 0.0

    def set_initial(self, angle):
        if self.segments:
            raise ValueError(
                f"Pose({self.name}) 必须在任何 Action 之前声明")
        self.initial = float(angle)

    def add(self, t0, dur, v0, v1):
        """注册运动段；与未完结末尾段重叠时截断之并记录告警"""
        if self.segments and self.segments[-1].end > t0:
            prev = self.segments[-1]
            self.overlap_warns.append(
                f"{self.name}: 段 [{prev.t0:g},{prev.end:g}) 与新段 "
                f"[{t0:g},{t0 + dur:g}) 重叠，前段截断至 {t0:g}ms "
                f"(新段基准取前段目标值 {prev.v1:g})")
            prev.dur = t0 - prev.t0     # t0/t0 均为 10 倍数，截断后仍对齐栅格
            if prev.dur <= 0:
                self.segments.pop()
        self.segments.append(Segment(t0, dur, v0, v1))

    def _rest_or(self, v):
        """段外取值：设定了 rest 则回落静止值，否则保持前段目标值"""
        return self.rest if self.rest is not None else v

    def value_at(self, t):
        """格点采样：段内插值；段前/段间空隙/段后按 rest 规则取值"""
        if not self.segments:
            return self._rest_or(self.initial)
        prev_v1 = self.initial
        for seg in self.segments:
            if t <= seg.t0:
                return self._rest_or(prev_v1)      # 段前（含段间空隙）
            if t <= seg.end:
                return seg.value_at(t)             # 段内（端点连续）
            prev_v1 = seg.v1
        return self._rest_or(prev_v1)              # 全部段后


def total_ms(channels, cursor):
    """编译总时长：所有通道最晚完成时刻与游标的最大值"""
    return max([ch.busy_until for ch in channels.values()] + [cursor])
