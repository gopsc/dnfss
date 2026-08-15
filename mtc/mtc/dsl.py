"""mtc DSL —— 命令式动作脚本（Python）→ 事件收集

脚本即 .py，通过 exec(source, make_namespace(collector)) 执行，
命名空间内注入两类函数：

硬件声明（函数名 = .mt 块名，位置参数 = 表头列，严格一一对应）：
    I2C("i2c-1", "/dev/i2c-1")
    PCA9685("pca9685-0", "i2c-1", "0x40")
    Servo("s1", 90, 0, 0, "pca9685-0", 0)   # name,max,min,offset,controller,channel
    Speaker("spk", 2, 48000, 0.6, 5000, 20)
    Camera("cam0", 640, 480, 30, 8)
    MPU6050_BIND("imu0", "i2c-1", "0x68")
    Alias("A", "angle")
    register_block("ADS1115", ["name", "I2C", "addr"])  # 开放扩展

时间轴指令（编译期求值，不直接出现在 .mt 行中）：
    Action(dev, delta, dur)   # 相对运动：当前游标处注册，dur ms 内走 delta
    SetTo(dev, angle, dur)    # 绝对运动（糖：delta = angle - 已注册目标值）
    Delay(ms)                 # 游标推进固定量
    Wait()                    # 游标推进到所有在途运动的最晚完成时刻（汇合）
    Beep(freq, dur)           # 扬声器频率段（默认设备 spk）
    Pose(dev, angle)          # 声明编译期初始姿态（默认 0，不产生运动段）

dur/Delay 必须是 10 的倍数（.mt 栅格与 pybot 微步节拍均为 10ms）。
"""

from .timeline import STEP_MS, Channel, total_ms
from .emit import emit_mt

# 已知块 → 表头列（位置参数顺序；首列恒为 name）
_BLOCK_SCHEMAS = {
    "Alias": ["name", "alias"],
    "I2C": ["name", "path"],
    "PCA9685": ["name", "I2C", "address"],
    "MPU6050_BIND": ["name", "I2C", "addr"],
    "Speaker": ["name", "channels", "sample_rate", "volumn", "max_f", "min_f"],
    "Servo": ["name", "max", "min", "offset", "controller", "channel"],
    "Camera": ["name", "width", "height", "framerate", "capacity"],
}


class CompileError(Exception):
    """脚本编译错误（携带可读信息，CLI 原样输出）"""


class Collector:
    """一次编译的全部状态：硬件块 + 通道时间轴 + 游标 + 告警"""

    def __init__(self):
        self.blocks = []        # [(block_name, columns, row_values)]
        self.channels = {}      # {通道名(dev.param): Channel}（注册序）
        self.cursor = 0.0       # 当前时间游标 ms（单调推进）
        self.warnings = []

    # ---------------- 硬件 ----------------

    def add_block(self, block_name, columns, values):
        self.blocks.append((block_name, columns, [str(v) for v in values]))

    def device_names(self):
        """已声明硬件变量名集合（每块首列恒为 name）"""
        return {row[0] for _, _, row in self.blocks}

    def first_speaker(self):
        """第一个 Speaker 块声明的设备名（delay 节拍列所属），无则 None"""
        for block_name, _, row in self.blocks:
            if block_name == "Speaker":
                return row[0]
        return None

    # ---------------- 时间轴 ----------------

    def channel(self, name):
        ch = self.channels.get(name)
        if ch is None:
            ch = self.channels[name] = Channel(name)
        return ch

    def check_device(self, dev):
        if dev not in self.device_names():
            raise CompileError(
                f"设备 '{dev}' 未声明（先用 Servo/Speaker/插件块声明）")

    def warn_overlaps(self):
        for ch in self.channels.values():
            self.warnings.extend(ch.overlap_warns)

    # ---------------- 编译 ----------------

    def compile(self):
        """归一化 IR 并序列化为 .mt 文本，返回 (mt_text, warnings)"""
        self.warn_overlaps()
        active = [ch for ch in self.channels.values() if ch.segments]
        t_total = total_ms(self.channels, self.cursor)
        if not active and t_total <= 0:
            raise CompileError("脚本没有任何动作（@Action 将为空）")
        if self.first_speaker() is None:
            self.warnings.append(
                "无 Speaker 块：.mt 无 delay 节拍列，pybot 执行时行间隔"
                "不等待（时间轴语义丢失，仅 --loop/解析场景可用）")
        text = emit_mt(self.blocks, active, t_total,
                       speaker=self.first_speaker())
        return text, self.warnings


def _check_dur(ms, what):
    if float(ms) <= 0 or float(ms) % STEP_MS != 0:
        raise CompileError(f"{what} 必须是正的 {STEP_MS} 的倍数，got {ms!r}")


def make_namespace(collector):
    """构建脚本 exec 命名空间（硬件函数 + 时间轴指令）"""
    ns = {}
    schemas = dict(_BLOCK_SCHEMAS)

    def _make_block_fn(block_name, columns):
        def fn(*args):
            if len(args) != len(columns):
                raise CompileError(
                    f"{block_name}() 需要 {len(columns)} 个参数 "
                    f"({', '.join(columns)})，got {len(args)}")
            collector.add_block(block_name, columns, args)
        fn.__name__ = block_name
        return fn

    for block_name, cols in schemas.items():
        ns[block_name] = _make_block_fn(block_name, cols)

    def register_block(block_name, columns):
        """注册自定义块（插件扩展）：register_block("ADS1115", [...])"""
        if not columns or columns[0] != "name":
            raise CompileError("register_block: 首列必须是 name")
        ns[block_name] = _make_block_fn(block_name, list(columns))

    def _register(dev, param, value, dur, absolute):
        collector.check_device(dev)
        _check_dur(dur, f"{dev}.{param} 的 dur")
        if param == "delay":
            raise CompileError("spk.delay 列由编译器管理（恒 10），"
                               "节拍请用 Delay()/Wait()")
        ch = collector.channel(f"{dev}.{param}")
        v0 = ch.target          # 重叠时基准 = 前段目标值（编译期确定）
        v1 = float(value) if absolute else v0 + float(value)
        ch.add(collector.cursor, float(dur), v0, v1)

    def Action(dev, delta, dur, param="angle"):
        """相对运动：dur ms 内走 delta（可正可负）"""
        _register(dev, param, delta, dur, absolute=False)

    def SetTo(dev, angle, dur, param="angle"):
        """绝对运动：dur ms 内到达 angle"""
        _register(dev, param, angle, dur, absolute=True)

    def Delay(ms):
        """游标推进固定量"""
        _check_dur(ms, "Delay")
        collector.cursor += float(ms)

    def Wait():
        """汇合：游标推进到所有在途运动的最晚完成时刻"""
        busy = [ch.busy_until for ch in collector.channels.values()]
        collector.cursor = max([collector.cursor] + busy)

    def Beep(freq, dur, dev="spk"):
        """扬声器频率段：dev.frequency 在 dur ms 内恒为 freq（阶跃段）

        通道静止值设为 0：段前/段间空隙/段后回落 0 = 静音
        （对齐 pybot speaker 静音语义：freq<=0 不出声仅保节拍）。
        """
        collector.check_device(dev)
        _check_dur(dur, f"Beep({freq}) 的 dur")
        ch = collector.channel(f"{dev}.frequency")
        ch.rest = 0.0
        ch.add(collector.cursor, float(dur), float(freq), float(freq))

    def Pose(dev, angle, param="angle"):
        """声明编译期初始姿态（不产生运动段；须在 Action 之前）"""
        collector.check_device(dev)
        ch = collector.channel(f"{dev}.{param}")
        if ch.segments:
            raise CompileError(
                f"Pose({dev}) 必须在任何 Action 之前声明")
        ch.set_initial(angle)

    ns.update(Action=Action, SetTo=SetTo, Delay=Delay, Wait=Wait,
              Beep=Beep, Pose=Pose, register_block=register_block)
    return ns


def compile_script(source, filename="<script>"):
    """编译 DSL 脚本 → (mt_text, warnings)

    脚本语法错误包装为 CompileError（携带文件/行号）。
    """
    collector = Collector()
    ns = make_namespace(collector)
    try:
        code = compile(source, filename, "exec")
        exec(code, ns)
    except CompileError:
        raise
    except Exception as e:
        raise CompileError(f"{filename}: {type(e).__name__}: {e}") from e
    return collector.compile()
