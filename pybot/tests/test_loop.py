"""实时预测闭环测试：run_loop() + Mock predictor + Mock camera

全部在 dry_run / mock I2C 下运行，不碰真实硬件与真实 actpred。
predictor 用鸭子类型桩（具备 frame_len/history_len/action_dim/predict），
验证闭环的冷启动、采样→推理→执行→回写、索引映射与节拍。
"""

import numpy as np
import pytest

from pybot.robot import RobotStruct

# @Camera + @Servo + @Speaker + @Action（含别名 A=angle / F=frequency）
# @Action 表头 4 列：s1.A s2.A spk.F spk.delay → 动作向量 action_dim=4
LOOP_MT = """
@Alias
name      alias;
   A      angle;
   F  frequency;
@

@I2C
 name        path;
i2c-1  /dev/i2c-1;
@

@PCA9685
     name    I2C   address;
pca9685-0  i2c-1   0x40;
@

@Servo
 name   max  min  offset  controller  channel;
  s1    90     -       -   pca9685-0        0;
  s2   180     -       -           -        1;
@

@Speaker
name  channels  sample_rate  volumn  max_f  min_f;
 spk         2        48000     0.6   5000      -;
@

@Camera
 name  width  height  framerate  capacity;
 cam0    320      240        15         4;
@

@Action
s1.A  s2.A  spk.F  spk.delay;
  45     0     10       20;
@
"""

# 无 @Camera 的脚本（验证闭环缺相机报错）
NO_CAMERA_MT = """
@Alias
name      alias;
   A      angle;
@

@I2C
 name        path;
i2c-1  /dev/i2c-1;
@

@PCA9685
     name    I2C   address;
pca9685-0  i2c-1   0x40;
@

@Servo
 name   max  min  offset  controller  channel;
  s1    90     -       -   pca9685-0        0;
@

@Action
s1.A;
  45;
@
"""


class MockPredictor:
    """鸭子类型推理器桩：记录每次调用，返回固定/递增动作向量"""

    def __init__(self, frame_len=3, history_len=2, action_dim=4):
        self.frame_len = frame_len
        self.history_len = history_len
        self.action_dim = action_dim
        self.calls = []          # [(frames_list, history_array)]
        self._step = 0

    def predict(self, images, action_history):
        self.calls.append((list(images), np.array(action_history)))
        # 返回可辨识的动作：s1=step+1, s2=step+2, spk.F=step+3, delay=0
        act = np.arange(self._step + 1, self._step + 1 + self.action_dim,
                        dtype=np.float32)
        self._step += 1
        return act


def _make_robot(mt_text, mock_i2c):
    import io
    return RobotStruct(io.StringIO(mt_text), dry_run=True)


# ---------------- 冷启动 ----------------

def test_cold_start_fills_windows(mock_i2c):
    """冷启动：F 帧重复同帧 + H 步历史全零，首步推理前即填满"""
    robot = _make_robot(LOOP_MT, mock_i2c)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.active()
    robot.run_loop(pred, hz=1000.0, max_steps=1)
    robot.deactive()

    # 首次调用 predict：frames 应为 F=3 帧，history 应为 H=2 步全零
    frames, history = pred.calls[0]
    assert len(frames) == 3
    assert history.shape == (2, 4)
    assert np.allclose(history, 0.0)


def test_history_window_slides(mock_i2c):
    """回写：每步把 predictor 返回的动作 append 进 history，窗口滑动"""
    robot = _make_robot(LOOP_MT, mock_i2c)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.active()
    robot.run_loop(pred, hz=1000.0, max_steps=3)
    robot.deactive()

    # 第 3 次调用时 history 应含前两步返回的动作（[1..4] 与 [2..5]）
    _, history = pred.calls[2]
    assert np.allclose(history[0], [1, 2, 3, 4])
    assert np.allclose(history[1], [2, 3, 4, 5])


# ---------------- 动作向量 → 设备映射 ----------------

def test_execute_action_vec_maps_by_index(mock_i2c):
    """18 维向量按 @Action 表头索引映射；舵机角度写入 PCA9685"""
    robot = _make_robot(LOOP_MT, mock_i2c)
    fd = robot.M["i2c-1"].play({})["fd"]
    robot.active()
    mock_i2c.journal.clear()

    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=1)
    robot.deactive()

    # 首步返回 [1,2,3,4]：s1=1°, s2=2°, spk.F=3
    writes = [(e[2], e[3]) for e in mock_i2c.journal
              if e[0] == "write_reg" and e[1] == fd]
    # s1=1° → 脉宽 0.5+2/180 → offval = int((0.5+2/180)*4096/20)
    off1 = int((0.5 + 2 * 1 / 180) * 4096 / 20)
    off2 = int((0.5 + 2 * 2 / 180) * 4096 / 20)
    reg = lambda ch: 0x06 + 4 * ch  # noqa: E731
    assert (reg(0) + 2, off1 & 0xFF) in writes
    assert (reg(1) + 2, off2 & 0xFF) in writes


def test_speaker_delay_non_blocking(mock_i2c):
    """闭环剔除 spk.delay 阻塞：传给 speaker 的 delay 恒为 0（发声不阻塞）"""
    robot = _make_robot(LOOP_MT, mock_i2c)
    robot.active()
    spk = robot.M["spk"]
    played = []
    orig_play = spk.play
    spk.play = lambda p: played.append(dict(p)) or orig_play(p)

    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=1)
    robot.deactive()

    # 每次 play 含 frequency，且 delay 被强制为 0（非阻塞）
    assert played, "spk 应被 play（含 frequency）"
    for p in played:
        assert "frequency" in p
        assert p.get("delay") == 0


# ---------------- 校验与边界 ----------------

def test_loop_requires_camera(mock_i2c):
    """非 dry_run 且无 @Camera → 报错；dry_run 下合成帧可用"""
    robot = RobotStruct(__import__("io").StringIO(NO_CAMERA_MT), dry_run=False)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=1)
    with pytest.raises(ValueError, match="@Camera"):
        robot.run_loop(pred, max_steps=1)
    robot.deactive()


def test_loop_requires_action_block(mock_i2c):
    """缺 @Action 块 → 报错"""
    mt = """
@Camera
 name  width  height  framerate  capacity;
 cam0    320      240        15         4;
@
"""
    robot = _make_robot(mt, mock_i2c)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    with pytest.raises(ValueError, match="@Action"):
        robot.run_loop(pred, max_steps=1)
    robot.deactive()


def test_loop_action_dim_too_small(mock_i2c):
    """@Action 列数超过 predictor.action_dim → 报错"""
    robot = _make_robot(LOOP_MT, mock_i2c)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=2)  # < 4 列
    robot.active()
    with pytest.raises(ValueError, match="action_dim"):
        robot.run_loop(pred, max_steps=1)
    robot.deactive()


# ---------------- 节拍 ----------------

def test_loop_pacing(mock_i2c):
    """节拍：hz 控制周期下界（推理+执行 < 周期时 sleep 补足）"""
    import time
    robot = _make_robot(LOOP_MT, mock_i2c)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.active()
    t0 = time.monotonic()
    robot.run_loop(pred, hz=50.0, max_steps=5)   # 周期 20ms × 5 = ≥100ms
    elapsed = time.monotonic() - t0
    robot.deactive()
    assert elapsed >= 0.09   # 允许少量计时误差


# ---------------- 帧源：守护线程队列 ----------------

class _SeqGrabber:
    """递增序号帧源（dry_run 下闭环注入用）"""
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return self.n


def test_loop_consumes_daemon_queue(mock_i2c):
    """闭环从守护线程队列取最近 F 帧，而非 capture() 同步曝光

    dry_run 下守护线程不启动，手动 _grab_once 填队列模拟运行期队列。
    """
    robot = _make_robot(LOOP_MT, mock_i2c)
    robot.active()
    cam = robot.M["cam0"].camera
    cam._grabber = _SeqGrabber()
    for _ in range(5):            # 填 5 帧：队列容量 4，保留最新 [2,3,4,5]
        cam._grab_once()
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=1)
    robot.deactive()

    frames, _ = pred.calls[0]
    # 帧来自序号帧源（int），闭环应取队列最近 F=3 帧 [3,4,5]（旧→新递增）
    assert all(isinstance(f, int) for f in frames)
    assert frames == [3, 4, 5], "应取队列中最近 F=3 帧（旧→新）"


def test_loop_queue_underfill_repeats_oldest(mock_i2c):
    """队列未满 F 帧时：重复最旧帧兜底"""
    robot = _make_robot(LOOP_MT, mock_i2c)
    robot.active()
    cam = robot.M["cam0"].camera
    cam._grabber = _SeqGrabber()
    # 不启动守护线程，手动抓 1 帧入队（队列只有 1 帧 < F=3）
    cam._grab_once()
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=1)
    robot.deactive()

    frames, _ = pred.calls[0]
    assert frames == [1, 1, 1], "队列只有 1 帧时应重复最旧帧填满 F"


def test_loop_empty_queue_falls_back_synthetic(mock_i2c):
    """队列完全空（dry_run 无守护线程）时回退合成帧"""
    robot = _make_robot(LOOP_MT, mock_i2c)
    robot.active()           # dry_run：守护线程不启动，队列恒空
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=1)
    robot.deactive()

    frames, _ = pred.calls[0]
    # dry_run 合成帧是 numpy 数组（HWC uint8），非 int
    assert all(isinstance(f, np.ndarray) for f in frames)
    assert frames[0].shape == (240, 320, 3)   # LOOP_MT @Camera 320x240


# ---------------- IMU 姿态回流（R2 方案一：独立 IMU token） ----------------

# 在 LOOP_MT 基础上加 @MPU6050_BIND（i2c-1 已定义，相机块之前插入）
LOOP_IMU_MT = LOOP_MT.replace(
    "@Camera",
    """@MPU6050_BIND
     name    I2C   addr;
mpu6050-0  i2c-1   0x69;
@

@Camera""")


class ImuPredictor(MockPredictor):
    """声明 imu_dim 的推理器桩：闭环应以 imu= 关键字调用"""

    def __init__(self, *args, imu_dim=8, **kwargs):
        super().__init__(*args, **kwargs)
        self.imu_dim = imu_dim
        self.imus = []         # 每步收到的 imu 向量

    def predict(self, images, action_history, imu=None):
        self.imus.append(None if imu is None else np.asarray(imu).copy())
        return super().predict(images, action_history)


def _freeze_imu(robot, name="mpu6050-0", **vals):
    """停掉采样线程并注入固定姿态（避免 10ms 采样覆盖测试值）"""
    sensor = robot.M[name].sensor
    sensor.stop_daemon()
    sensor.wait_daemon()
    sensor._latest.update(**vals)


def test_loop_imu_passed_to_predictor(mock_i2c):
    """predictor 声明 imu_dim + 脚本含 @MPU6050_BIND → 每步 imu= 向量"""
    robot = _make_robot(LOOP_IMU_MT, mock_i2c)
    robot.active()
    _freeze_imu(robot, roll=1.5, pitch=-2.0, gz=30.0)
    pred = ImuPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=3)
    robot.deactive()

    assert len(pred.imus) == 3
    for imu in pred.imus:
        assert imu is not None and imu.shape == (8,)
        # IMU_KEYS 顺序：roll pitch ax ay az gx gy gz
        assert imu[0] == 1.5 and imu[1] == -2.0 and imu[7] == 30.0


def test_loop_imu_zero_without_bind(mock_i2c):
    """predictor 声明 imu_dim 但脚本无 @MPU6050_BIND → imu 零向量输入"""
    robot = _make_robot(LOOP_MT, mock_i2c)
    robot.active()
    pred = ImuPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=2)
    robot.deactive()

    assert len(pred.imus) == 2
    for imu in pred.imus:
        assert imu is not None and imu.shape == (8,)
        assert np.allclose(imu, 0.0)


def test_loop_imu_not_passed_to_legacy_predictor(mock_i2c):
    """未声明 imu_dim 的旧推理器：保持两参调用（向后兼容）"""
    robot = _make_robot(LOOP_IMU_MT, mock_i2c)
    robot.active()
    _freeze_imu(robot, roll=9.0)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=2)   # 不炸即兼容
    robot.deactive()
    assert len(pred.calls) == 2


def test_loop_imu_recorded(mock_i2c, tmp_path):
    """闭环录制：含 IMU 时 imu.npy 与 actions.npy 帧对齐（T 一致）"""
    from pybot.recorder import EpisodeRecorder
    robot = _make_robot(LOOP_IMU_MT, mock_i2c)
    robot.active()
    _freeze_imu(robot, roll=3.0, ay=0.5)
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=4, recorder=rec)
    robot.deactive()

    ep = tmp_path / "episode_0000"
    actions = np.load(ep / "actions.npy")
    imus = np.load(ep / "imu.npy")
    assert actions.shape == (4, 18)
    assert imus.shape == (4, 8)          # 与 actions 帧数对齐
    assert np.allclose(imus[:, 0], 3.0)  # roll
    assert np.allclose(imus[:, 3], 0.5)  # ay


# ---------------- 多 MPU6050（最多 4 个，8×N 槽位拼接） ----------------

# 双传感器脚本（槽位顺序对齐 std.mt 前两行：i2c-1 上 0x69 / 0x68）
LOOP_IMU2_MT = LOOP_MT.replace(
    "@Camera",
    """@MPU6050_BIND
     name    I2C   addr;
mpu6050-0  i2c-1   0x69;
mpu6050-1  i2c-1   0x68;
@

@Camera""")


def test_loop_imu_multi_sensor_concat(mock_i2c):
    """多 MPU6050：按块行顺序拼接 8×N 向量喂 predictor（槽位不串）"""
    robot = _make_robot(LOOP_IMU2_MT, mock_i2c)
    robot.active()
    _freeze_imu(robot, "mpu6050-0", roll=1.0, gz=7.0)
    _freeze_imu(robot, "mpu6050-1", roll=2.0, pitch=-3.0)
    pred = ImuPredictor(frame_len=3, history_len=2, action_dim=4, imu_dim=16)
    robot.run_loop(pred, hz=1000.0, max_steps=2)
    robot.deactive()

    assert len(pred.imus) == 2
    for imu in pred.imus:
        assert imu is not None and imu.shape == (16,)
        assert imu[0] == 1.0 and imu[7] == 7.0     # sensor0: roll, gz
        assert imu[8] == 2.0 and imu[9] == -3.0    # sensor1: roll, pitch


def test_loop_imu_multi_sensor_pad_truncate(mock_i2c):
    """传感器数与 predictor.imu_dim 不匹配：pad 零 / 截断，槽位不漂移"""
    robot = _make_robot(LOOP_IMU2_MT, mock_i2c)
    robot.active()
    _freeze_imu(robot, "mpu6050-0", roll=1.0)
    _freeze_imu(robot, "mpu6050-1", roll=2.0)

    pred24 = ImuPredictor(frame_len=3, history_len=2, action_dim=4,
                          imu_dim=24)
    robot.run_loop(pred24, hz=1000.0, max_steps=1)   # 16 → 24 补零
    imu = pred24.imus[0]
    assert imu.shape == (24,)
    assert imu[0] == 1.0 and imu[8] == 2.0
    assert np.allclose(imu[16:], 0.0)

    pred8 = ImuPredictor(frame_len=3, history_len=2, action_dim=4,
                         imu_dim=8)
    robot.run_loop(pred8, hz=1000.0, max_steps=1)    # 16 → 8 截断（仅 sensor0）
    imu = pred8.imus[0]
    assert imu.shape == (8,) and imu[0] == 1.0
    robot.deactive()


def test_loop_imu_multi_recorded(mock_i2c, tmp_path):
    """多传感器闭环录制：imu.npy 宽度 8×N 且槽位按块行顺序"""
    from pybot.recorder import EpisodeRecorder
    robot = _make_robot(LOOP_IMU2_MT, mock_i2c)
    robot.active()
    _freeze_imu(robot, "mpu6050-0", roll=1.0)
    _freeze_imu(robot, "mpu6050-1", pitch=4.0)
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    pred = MockPredictor(frame_len=3, history_len=2, action_dim=4)
    robot.run_loop(pred, hz=1000.0, max_steps=3, recorder=rec)
    robot.deactive()

    imus = np.load(tmp_path / "episode_0000" / "imu.npy")
    assert imus.shape == (3, 16)
    assert np.allclose(imus[:, 0], 1.0)   # sensor0 roll
    assert np.allclose(imus[:, 9], 4.0)   # sensor1 pitch


def test_robot_importable_without_numpy():
    """零依赖承诺（v3-P1-1）：屏蔽 numpy 后 pybot.robot 仍可导入

    纯 .mt 脚本执行路径不得要求 numpy——numpy 仅在 --record/--loop
    函数内 lazy import。子进程隔离避免污染当前 pytest 会话。
    """
    import os
    import subprocess
    import sys
    child = (
        "import sys, importlib.util\n"
        "if importlib.util.find_spec('fcntl') is None:\n"      # Windows 开发机
        "    from types import ModuleType\n"
        "    _m = ModuleType('fcntl'); _m.ioctl = lambda *a, **k: 0\n"
        "    sys.modules['fcntl'] = _m\n"
        "import builtins\n"
        "_real = builtins.__import__\n"
        "def _fake(name, *a, **k):\n"
        "    if name == 'numpy' or name.startswith('numpy.'):\n"
        "        raise ImportError('numpy blocked by test')\n"
        "    return _real(name, *a, **k)\n"
        "builtins.__import__ = _fake\n"
        "import pybot.robot\n"
        "print('ok')\n"
    )
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", child],
                       capture_output=True, text=True, cwd=repo)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr


def test_predictor_exception_still_flushes_recorder(mock_i2c, tmp_path):
    """P2-c：predictor 中途抛异常时 run_loop 的 finally 仍落盘，
    已录制 steps 不丢失且异常照常向上传播（由 with robot 清理硬件）"""
    from pybot.recorder import EpisodeRecorder

    class RaisingPredictor(MockPredictor):
        def predict(self, images, action_history):
            if self._step >= 1:
                raise RuntimeError("backend boom")
            return super().predict(images, action_history)

    robot = _make_robot(LOOP_MT, mock_i2c)
    robot.active()
    pred = RaisingPredictor(frame_len=3, history_len=2, action_dim=4)
    rec = EpisodeRecorder(str(tmp_path), action_dim=4)
    with pytest.raises(RuntimeError, match="backend boom"):
        robot.run_loop(pred, hz=1000.0, max_steps=5, recorder=rec)
    robot.deactive()

    # 第 1 步已录制（第 2 步 predict 抛异常），finally flush 已落盘
    acts = np.load(tmp_path / "episode_0000" / "actions.npy")
    assert acts.shape[0] == 1
