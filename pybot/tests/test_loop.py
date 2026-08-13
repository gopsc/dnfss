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

@Servo
 name   max  min  offset  controller  channel;
  s1    90     -       -           -        0;
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
