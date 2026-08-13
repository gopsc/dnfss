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

    def run(self, recorder=None):
        """逐行执行 @Action 矩阵，带时延的行分解为 10ms 微步

        微步规则：
        - 首行（无前序状态）：不分解，按原逻辑执行
        - 后续行 + delay > 0：分解为 N=delay//10 个微步，舵机角度线性插值
        - delay 必须是 10 的倍数
        - 每步调 speaker.play(freq, 10) 提供声音 + 10ms 时序
        - dry_run 下补 sleep(0.01) 维持节拍

        recorder: 可选 EpisodeRecorder，每步录制 (frame, action)
        """
        action = self.find_mt("Action")
        if action is None:
            raise ValueError("no @Action matrix in script")
        columns = self._validate_action(action)     # Q05: 执行前整表校验
        delay_idx = self._find_delay_idx(columns)

        camera_var = None
        if recorder is not None:
            camera_var = self._find_camera()
            if camera_var is None and not self.dry_run:
                raise ValueError(
                    "--record 需要 @Camera 块（或使用 --dry-run 生成合成帧）")

        prev_vals = None

        for i in range(1, action.size):
            vals = self._resolve_row(action, i, len(columns))
            delay = int(vals[delay_idx]) if delay_idx is not None else 0

            if delay > 0 and prev_vals is not None:
                if delay % 10 != 0:
                    raise ValueError(
                        f"@Action row {i}: delay {delay} 不是 10 的倍数")
                n = delay // 10
                for k in range(n):
                    step_vals = self._build_step_vals(
                        prev_vals, vals, columns, k, n)
                    if recorder is not None:
                        recorder.record(
                            self._grab_frame(camera_var),
                            self._build_action_vec(
                                step_vals, recorder.action_dim))
                    self._execute_step(step_vals, columns)
                    if self.dry_run:
                        import time
                        time.sleep(0.01)
            else:
                if recorder is not None:
                    recorder.record(
                        self._grab_frame(camera_var),
                        self._build_action_vec(vals, recorder.action_dim))
                self._execute_step(vals, columns)

            prev_vals = vals

        if recorder is not None:
            path = recorder.flush()
            log.info("episode saved: %s", path)

    def _resolve_row(self, action, row, n_cols):
        """解析一行的全部值（占位符继承）"""
        return [parse_mt(action, row, j) for j in range(n_cols)]

    def _resolve_param(self, param):
        """解析别名 → 真实参数名"""
        alias = self.M.get(param)
        if isinstance(alias, AliasVar):
            return alias.play({})
        return param

    def _find_delay_idx(self, columns):
        """找到 spk.delay 列索引；无 Speaker 返回 None"""
        for j, (dev, param) in enumerate(columns):
            if isinstance(self.M.get(dev), SpeakerVar) and \
               self._resolve_param(param) == "delay":
                return j
        return None

    def _build_step_vals(self, prev_vals, target_vals, columns, k, n):
        """为微步 k 构建插值后的值列表"""
        vals = []
        for j, (dev, param) in enumerate(columns):
            rp = self._resolve_param(param)
            if isinstance(self.M.get(dev), ServoVar):
                vals.append(prev_vals[j] +
                            (target_vals[j] - prev_vals[j]) * (k + 1) / n)
            elif isinstance(self.M.get(dev), SpeakerVar) and rp == "delay":
                vals.append(10.0)
            else:
                vals.append(target_vals[j])
        return vals

    def _execute_step(self, vals, columns):
        """从值列表构建 row_sets 并逐设备 play()"""
        row_sets = {}
        for j, (dev, param) in enumerate(columns):
            rp = self._resolve_param(param)
            row_sets.setdefault(dev, {})[rp] = vals[j]
        for dev, params in row_sets.items():
            self.M[dev].play(params)

    def _find_camera(self):
        """在已创建的硬件变量中查找第一个 CameraVar"""
        for var in self.M.values():
            if isinstance(var, CameraVar):
                return var
        return None

    def _build_action_vec(self, vals, action_dim):
        """从值列表构建动作向量，pad/truncate 到 action_dim"""
        import numpy as np
        vec = np.zeros(action_dim, dtype=np.float32)
        n = min(len(vals), action_dim)
        vec[:n] = vals[:n]
        return vec

    def _grab_frame(self, camera_var):
        """从摄像头取最新帧；dry_run 或无帧时返回随机噪声帧"""
        import numpy as np
        if camera_var is not None:
            result = camera_var.play({})
            if result is not None:
                return result[1]       # (timestamp, frame)
        # dry_run 或队列空：随机噪声帧（用 camera 配置尺寸）
        h, w = 480, 640
        if camera_var is not None:
            cam = camera_var.camera
            h, w = cam.height, cam.width
        return np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)

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
