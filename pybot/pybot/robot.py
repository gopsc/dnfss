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
import time
from collections import deque

from . import plugin
from .hardvars import (AliasVar, CameraVar, I2CVar, MPU6050Var, ServoVar,
                       SpeakerVar)
from .parser import MatrixParser, MatrixSaver, get_mt, parse_mt

log = logging.getLogger("pybot.robot")

# MPU6050 姿态向量键序（recorder.record 的 dict 取值顺序与此一致，
# mpu6050.MPU6050._latest 的键集合相同）
IMU_KEYS = ("roll", "pitch", "ax", "ay", "az", "gx", "gy", "gz")

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
        imu_vars = []
        if recorder is not None:
            camera_var = self._find_camera()
            if camera_var is None and not self.dry_run:
                raise ValueError(
                    "--record 需要 @Camera 块（或使用 --dry-run 生成合成帧）")
            imu_vars = self._find_imus()
            if not imu_vars:
                log.info("--record: 无 @MPU6050_BIND 块，不产出 imu.npy")
            else:
                # 槽位宽度 8×N（按块顺序拼接），录制前同步给 recorder
                recorder.imu_dim = len(IMU_KEYS) * len(imu_vars)

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
                                step_vals, recorder.action_dim),
                            imu_vec=self._read_imus(imu_vars))
                    self._execute_step(step_vals, columns)
                    if self.dry_run:
                        import time
                        time.sleep(0.01)
            else:
                if recorder is not None:
                    recorder.record(
                        self._grab_frame(camera_var),
                        self._build_action_vec(vals, recorder.action_dim),
                        imu_vec=self._read_imus(imu_vars))
                self._execute_step(vals, columns)

            prev_vals = vals

        if recorder is not None:
            path = recorder.flush()
            log.info("episode saved: %s", path)

    # ---------------- 实时预测闭环 ----------------

    def run_loop(self, predictor, hz=10.0, max_steps=None, recorder=None):
        """实时预测闭环：无限循环 采样→推理→执行→回写历史

        predictor: actpred 推理器（鸭子类型），需具备
            frame_len / history_len / action_dim 属性与
            predict(images, action_history) -> (action_dim,) 方法。
            pybot 不 import 具体推理类，保持零依赖承诺。
            若额外声明 imu_dim 属性（R2 方案一），则以
            predict(images, action_history, imu=(imu_dim,)) 调用；
            脚本无 @MPU6050_BIND 块时 imu 为零向量。多个 MPU6050
            （最多 4 个）按脚本块行顺序拼接为 8×N 原始向量。
        hz: 控制频率（周期 = 1/hz 秒，须 ≥ 推理 + 执行耗时）
        max_steps: 步数上限，None 表示无限循环（Ctrl-C 退出）
        recorder: 可选 EpisodeRecorder，闭环中同步录制 (frame, action)

        与 run() 的差异：run() 是脚本驱动一次性执行（行内 delay 分解为
        10ms 微步插值）；run_loop() 由 predictor 逐步产出动作，节奏由
        hz 主循环掌控，每步直接下发不做行分解（delay 仅作回放兼容，
        闭环中跳过其发声阻塞）。

        帧源：闭环从 camera 守护线程的有界队列取最近 F 帧（低延迟，
        让 framerate/capacity 配置生效），而非 capture() 同步曝光
        （会阻塞 ~1/framerate 秒，把 hz 锁死在相机帧率之下）。dry_run
        或队列空时回退 _grab_frame 合成帧。
        """
        import numpy as np
        camera_var = self._find_camera()
        if camera_var is None and not self.dry_run:
            raise ValueError("闭环需要 @Camera 块（或使用 --dry-run 生成合成帧）")

        imu_vars = self._find_imus()
        # 鸭子类型约定：predictor 声明 imu_dim 属性即接受 imu= 关键字
        # （方案一：独立 IMU token）；未声明则保持旧两参调用，向后兼容
        use_imu = getattr(predictor, "imu_dim", None) is not None
        if not imu_vars:
            if use_imu:
                log.warning("predictor 声明 imu_dim 但脚本无 @MPU6050_BIND "
                            "块，imu 将以零向量输入（等价于无姿态先验）")
        else:
            if recorder is not None:
                recorder.imu_dim = len(IMU_KEYS) * len(imu_vars)
            log.info("closed loop imu: %s → predictor %s",
                     ",".join(v.name for v in imu_vars),
                     "imu= 接入" if use_imu else "不接入（仅录制）")

        action = self.find_mt("Action")
        if action is None:
            raise ValueError("no @Action matrix in script")
        columns = self._validate_action(action)

        F, H = predictor.frame_len, predictor.history_len
        action_dim = predictor.action_dim
        if len(columns) > action_dim:
            raise ValueError(
                f"@Action 列数 {len(columns)} 超过 predictor.action_dim "
                f"{action_dim}，无法按索引对齐")
        exec_cols = self._loop_exec_columns(columns)

        frames = deque(maxlen=F)
        history = deque(maxlen=H)
        self._loop_cold_start(frames, history, camera_var, action_dim)

        dt = 1.0 / hz
        step = 0
        overrun = 0
        log.info("closed loop start: hz=%.1f F=%d H=%d action_dim=%d",
                 hz, F, H, action_dim)
        try:
            while max_steps is None or step < max_steps:
                t0 = time.monotonic()
                frames.extend(self._loop_recent_frames(camera_var, F))  # 1 采样
                imu = self._read_imus(imu_vars)
                if use_imu:                                            # 2 推理
                    act = predictor.predict(list(frames), np.array(history),
                                            imu=self._pad_imu(
                                                imu, predictor.imu_dim))
                else:
                    act = predictor.predict(list(frames), np.array(history))
                act = np.asarray(act, dtype=np.float32).reshape(-1)
                self._execute_action_vec(act, exec_cols)               # 3 执行
                history.append(act.copy())                             # 4 回写
                if recorder is not None:
                    recorder.record(
                        frames[-1],
                        self._build_action_vec(act, recorder.action_dim),
                        imu_vec=imu)
                step += 1
                remain = dt - (time.monotonic() - t0)                  # 节拍
                if remain > 0:
                    time.sleep(remain)
                else:
                    overrun += 1
                    log.warning("loop overrun: step %d took %.1fms > %.1fms "
                                "（推理+执行超过周期，实际频率已低于 %.1fHz）",
                                step, (dt - remain) * 1000, dt * 1000, hz)
        finally:
            # max_steps 正常退出与 Ctrl-C（KeyboardInterrupt）都要落盘，
            # 否则闭环录制的 actions.npy/imu.npy 全部丢失
            if recorder is not None:
                path = recorder.flush()
                log.info("episode saved: %s", path)
        if overrun:
            log.warning("closed loop overrun %d/%d steps (%.0f%%)，"
                        "建议降低 --hz 或检查推理后端延迟",
                        overrun, step, 100.0 * overrun / max(step, 1))
        log.info("closed loop stop after %d steps", step)

    def _loop_recent_frames(self, camera_var, F):
        """从 camera 守护线程队列取最近 F 帧（旧→新），无阻塞

        队列未填满 F 帧时重复最旧帧兜底；队列完全空（dry_run / 守护线程
        刚启动）时回退 _grab_frame 合成帧。相比 capture() 同步曝光，
        从队列消费让 framerate/capacity 配置生效，闭环周期不被曝光阻塞。
        """
        if camera_var is None:
            return [self._grab_frame(camera_var)] * F
        snap = camera_var.camera.snapshot()      # [(ts, frame), ...] 旧→新
        if not snap:
            return [self._grab_frame(camera_var)] * F
        recent = [f for _, f in snap[-F:]]
        if len(recent) < F:                       # 队列未满：重复最旧帧
            recent = [recent[0]] * (F - len(recent)) + recent
        return recent

    def _loop_exec_columns(self, columns):
        """闭环执行列：剔除 Speaker.delay（发声阻塞会卡死实时节拍）

        返回 [(action_idx, dev, param)]，action_idx 是动作向量索引（按
        @Action 表头顺序，与 actpred 的 action_dim 对齐）。
        """
        out = []
        for j, (dev, param) in enumerate(columns):
            rp = self._resolve_param(param)
            if isinstance(self.M.get(dev), SpeakerVar) and rp == "delay":
                continue
            out.append((j, dev, rp))
        return out

    def _loop_cold_start(self, frames, history, camera_var, action_dim):
        """冷启动：F 帧滑窗从队列取最近帧（空则合成帧重复），H 步零动作

        帧源与主循环一致（_loop_recent_frames），避免冷启动与运行期帧源
        不一致。history 用零向量填满是保守初值，避免随机姿态跳变损坏舵机。
        """
        import numpy as np
        frames.extend(self._loop_recent_frames(camera_var, frames.maxlen))
        for _ in range(history.maxlen):
            history.append(np.zeros(action_dim, dtype=np.float32))

    def _execute_action_vec(self, act, exec_cols):
        """把动作向量按 exec_cols 索引映射到设备并直接下发（不分解微步）

        SpeakerVar.play 强制要求 delay 键；闭环已剔除 delay 列（避免发声
        阻塞卡死节拍），此处为其补 delay=0 —— 发声但不阻塞，节拍由 hz 掌控。
        """
        row_sets = {}
        for j, dev, rp in exec_cols:
            row_sets.setdefault(dev, {})[rp] = float(act[j])
        for dev, params in row_sets.items():
            if isinstance(self.M[dev], SpeakerVar) and "delay" not in params:
                params["delay"] = 0
            self.M[dev].play(params)

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

    def _find_imus(self):
        """按脚本块顺序收集全部 MPU6050Var（无则空列表，最多 4 个）"""
        return [v for v in self.M.values() if isinstance(v, MPU6050Var)]

    def _read_imus(self, imu_vars):
        """读全部 MPU6050 最新姿态，按块顺序拼接为 (8×N,) 原始向量

        imu_vars 为空返回 None（无传感器：recorder 不产出 imu.npy）；
        单个传感器读失败其 8 槽记零并 log。latest() 冷启动返回全零
        dict，语义安全无需特判。
        """
        if not imu_vars:
            return None
        import numpy as np
        nk = len(IMU_KEYS)
        out = np.zeros(nk * len(imu_vars), dtype=np.float32)
        for k, var in enumerate(imu_vars):
            try:
                latest = var.play({})
            except Exception:
                log.exception("imu read failed: %s", var.name)
                continue
            out[k * nk:(k + 1) * nk] = [latest.get(key, 0.0)
                                        for key in IMU_KEYS]
        return out

    @staticmethod
    def _pad_imu(vec, imu_dim):
        """拼接姿态向量 → pad/truncate 到 predictor 的 imu_dim（None → 全零）"""
        import numpy as np
        out = np.zeros(imu_dim, dtype=np.float32)
        if vec is not None:
            src = np.asarray(vec, dtype=np.float32).ravel()
            n = min(len(src), imu_dim)
            out[:n] = src[:n]
        return out

    def _build_action_vec(self, vals, action_dim):
        """从值列表构建动作向量，pad/truncate 到 action_dim"""
        import numpy as np
        vec = np.zeros(action_dim, dtype=np.float32)
        n = min(len(vals), action_dim)
        vec[:n] = vals[:n]
        return vec

    def _grab_frame(self, camera_var):
        """从摄像头同步抓一帧；dry_run 或无帧时返回随机噪声帧"""
        import numpy as np
        if camera_var is not None:
            frame = camera_var.camera.capture()
            if frame is not None:
                return frame
        # dry_run 或无 grabber：随机噪声帧（用 camera 配置尺寸）
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
