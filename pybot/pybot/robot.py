"""机器人结构 —— 移植自 bot4cj/src/r.cj 的 RobotStruct

流程：解析 .mt 脚本 → 创建硬件变量字典 → active（按序 build）
      → run（逐行执行 @Action）→ deactive（倒序 destroy）。

与仓颉版的差异（审计修复，语义不变）：
    r.cj:911 FIXME —— 重名硬件变量先 destroy 旧值再覆盖，并输出警告；
    Q05: @Action 列名合法性（必须含一个 '.'）在执行前整表校验一次；
    Q07: deactive 幂等 + atexit 兜底 + with 上下文管理器，确定性释放 fd。
"""

import atexit
import logging

from . import plugin
from .hardvars import (AliasVar, CameraVar, I2CVar, MPU6050Var, ServoVar,
                       SpeakerVar)
from .parser import MatrixParser, MatrixSaver, get_mt, parse_mt

log = logging.getLogger("pybot.robot")

# 已知块名 → 硬件变量工厂（Action 特殊处理；未知块 → 插件）
_FACTORIES = {
    "I2C": I2CVar,
    "Alias": AliasVar,
    "Servo": ServoVar,
    "Speaker": SpeakerVar,
    "MPU6050_BIND": MPU6050Var,
    "Camera": CameraVar,
}


class RobotStruct:
    """机器人结构：从矩阵脚本动态构建

    用法：
        with RobotStruct(sys.stdin) as robot:   # __enter__ → active()
            robot.run()                         # __exit__ → deactive()
    """

    def __init__(self, source, dry_run=False):
        self.C = []         # 解析出的 MatrixObject 容器（保持脚本顺序）
        self.M = {}         # {name: HardwareVar}（dict 有序，支撑倒序 destroy）
        self.dry_run = dry_run
        self._deactivated = False
        self._read_matrix(source)
        self._create_hardvars()
        atexit.register(self.deactive)      # Q07 兜底：异常退出也释放 fd

    # ---------------- 生命周期 ----------------

    def active(self):
        """激活所有硬件变量（按创建顺序 build）"""
        for item in self.M.values():
            item.build()

    def deactive(self):
        """倒序释放所有硬件变量；幂等（with / atexit 双路径安全）"""
        if self._deactivated:
            return
        self._deactivated = True
        for item in reversed(list(self.M.values())):
            try:
                item.destroy()
            except Exception:
                log.exception("destroy %s failed", item.name)

    def __enter__(self):
        self.active()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.deactive()
        return False

    # ---------------- 解析与构建 ----------------

    def _read_matrix(self, source):
        parser = MatrixParser(source)
        while True:
            obj = parser.parse()
            if obj is None:
                break
            log.debug("read matrix block @%s", obj.name)
            self.C.append(obj)

    def _put_hardvar(self, name, var):
        """插入硬件变量；重名先释放旧值（修复 r.cj:911 FIXME）"""
        old = self.M.get(name)
        if old is not None:
            log.warning("hard-Var '%s' redefined, destroying the old one", name)
            old.destroy()
        self.M[name] = var

    def _create_hardvars(self):
        """按块名分发工厂；Action 跳过；未知块 → Python 插件"""
        for matrix in self.C:
            mt_name = matrix.name
            if mt_name == "Action":
                continue
            factory = _FACTORIES.get(mt_name)
            if factory is not None:
                idx_name = matrix.index_of_col(0, "name")
                for i in range(1, matrix.size):
                    name = matrix[i, idx_name]
                    self._put_hardvar(name, factory.from_matrix(
                        matrix, i, self.M, dry_run=self.dry_run))
            elif matrix.size > 0:
                self._create_plugin_vars(matrix)

    def _create_plugin_vars(self, matrix):
        """未知块：逐行构建元数据并加载 modules/<块名>.py 插件"""
        idx_name = matrix.index_of_col(0, "name")
        for i in range(1, matrix.size):
            name = matrix[i, idx_name]
            metadata = {}
            for j in range(matrix.col):
                col_name = matrix[0, j]
                value = get_mt(matrix, i, j)
                ref = self.M.get(value)
                if ref is not None:
                    # 值是已存在的硬件变量名 → play({}) 展开（如 I2C → fd）
                    metadata.update(ref.play({}))
                else:
                    metadata[col_name] = value
            self._put_hardvar(name, plugin.PluginVar(name, matrix.name,
                                                     metadata))

    # ---------------- 执行 ----------------

    def find_mt(self, name):
        """在矩阵对象容器中寻找目标块"""
        for item in self.C:
            if item.name.strip() == name:
                return item
        return None

    def run(self):
        """逐行执行 @Action 矩阵"""
        action = self.find_mt("Action")
        if action is None:
            raise ValueError("no @Action matrix in script")
        columns = self._validate_action(action)     # Q05: 执行前整表校验
        for i in range(1, action.size):
            row_sets = {}       # {设备名: {参数名: float}}
            for j, (dev, param) in enumerate(columns):
                # 可能的参数别名转换（A → angle）
                alias = self.M.get(param)
                if isinstance(alias, AliasVar):
                    param = alias.play({})
                val = parse_mt(action, i, j)
                row_sets.setdefault(dev, {})[param] = val
            # 执行一行：逐设备调用 play
            for dev, params in row_sets.items():
                self.M[dev].play(params)

    def _validate_action(self, action):
        """校验 @Action 全部列名为 '设备.参数' 且设备已定义，返回 [(dev, param)]"""
        columns = []
        for j in range(action.col):
            col_name = action[0, j]
            parts = col_name.split(".")
            if len(parts) != 2:
                raise ValueError(
                    f"@Action column {j} '{col_name}' is not valid "
                    f"(expected 'device.param')")
            dev, param = parts
            if dev not in self.M:
                raise ValueError(
                    f"@Action column {j} '{col_name}': "
                    f"device '{dev}' is not defined")
            columns.append((dev, param))
        return columns

    # ---------------- 序列化 ----------------

    def save(self, out):
        """储存整个矩阵变量表（--test roundtrip 用）"""
        MatrixSaver(out).save(self.C)
