"""EpisodeRecorder 测试：actpred 格式数据集录制 + 微动作分解

全部在 dry_run / mock I2C 下运行，不碰真实硬件。
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pybot.recorder import EpisodeRecorder
from pybot.robot import RobotStruct

# ---- 测试用 .mt 脚本 ----

# 仅 @Camera + @Action(cam0.frame) —— 不需要 I2C，无 Speaker 无微动作
CAMERA_MT = """
@Camera
 name  width  height  framerate  capacity;
 cam0    320      240        15         2;
@

@Action
cam0.frame;
0;
@
"""

# @Camera + @Servo + @Speaker + 多行 @Action（含占位符 + delay）—— 需要 mock I2C
# 10 行 @Action，首行不分解，后续行 delay=20 → N=2 微步
# 总步数：1 + 9×2 = 19
SERVO_CAMERA_MT = """
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
 cam0    320      240        15         2;
@

@Action
s1.A  s2.A  spk.F  spk.delay;
  45     0     10       20;
   -    10      -       20;
   -    20      -       20;
   -    30      -       20;
   -     0      -       20;
   -    10      -       20;
   -    20      -       20;
   -    30      -       20;
   -     0      -       20;
   -    10      -       20;
@
"""

# 简单插值测试：2 行 @Action，delay=30 → N=3
# s1: row1=0（首行不分解），row2 从 0→30 分 3 步 → 10, 20, 30
MICRO_ACTION_MT = """
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
@

@Speaker
name  channels  sample_rate  volumn  max_f  min_f;
 spk         2        48000     0.6   5000      -;
@

@Action
s1.A  spk.F  spk.delay;
   0     10       30;
  30      -       30;
@
"""

# delay 非 10 倍数 → 应报错
BAD_DELAY_MT = """
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
@

@Speaker
name  channels  sample_rate  volumn  max_f  min_f;
 spk         2        48000     0.6   5000      -;
@

@Action
s1.A  spk.F  spk.delay;
   0     10       15;
  30      -       15;
@
"""

# @Servo + @Action 但无 @Camera —— 用于测试无摄像头报错
NO_CAMERA_MT = """
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


# ---- 测试 ----

def test_episode_format(tmp_path):
    """录制后目录结构正确（images/*.jpg + actions.npy）"""
    robot = RobotStruct(CAMERA_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    ep_dir = tmp_path / "episode_0000"
    assert ep_dir.is_dir()
    assert (ep_dir / "images").is_dir()
    assert (ep_dir / "actions.npy").exists()
    jpgs = list((ep_dir / "images").glob("*.jpg"))
    assert len(jpgs) == 1              # CAMERA_MT 有 1 行 @Action，无 Speaker 不分解


def test_action_vector_padding(tmp_path, mock_i2c):
    """4 列 .mt → 18 维向量，末尾 14 维为零；微步展开后 19 步"""
    robot = RobotStruct(SERVO_CAMERA_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    actions = np.load(tmp_path / "episode_0000" / "actions.npy")
    assert actions.shape == (19, 18)   # 1（首行）+ 9×2（微步）= 19
    assert np.all(actions[:, 4:] == 0)  # 列 4-17 全为零


def test_placeholder_resolution(tmp_path, mock_i2c):
    """`-` 占位符递归解析 + 微步插值"""
    robot = RobotStruct(SERVO_CAMERA_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    actions = np.load(tmp_path / "episode_0000" / "actions.npy")
    # s1.A: 全 45（首行 45，后续行 '-' 继承 45，插值 45→45 仍为 45）
    assert np.all(actions[:, 0] == 45)
    # s2.A: 首行 0，后续行插值
    expected_s2 = [0, 5, 10, 15, 20, 25, 30, 15, 0, 5, 10, 15, 20, 25, 30, 15, 0, 5, 10]
    assert list(actions[:, 1]) == expected_s2


def test_dry_run_synthetic_frame(tmp_path):
    """dry_run 下生成随机噪声帧，尺寸与 camera 配置一致"""
    robot = RobotStruct(CAMERA_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    img_path = tmp_path / "episode_0000" / "images" / "0000.jpg"
    assert img_path.exists()
    img = Image.open(img_path)
    assert img.size == (320, 240)      # (width, height)
    arr = np.array(img)
    assert arr.shape == (240, 320, 3)
    assert not np.all(arr == 0)          # 随机噪声，非全黑


def test_no_camera_error(tmp_path, mock_i2c):
    """非 dry_run 无 @Camera 时 --record 报错"""
    robot = RobotStruct(NO_CAMERA_MT, dry_run=False)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    with pytest.raises(ValueError, match="--record 需要 @Camera"):
        robot.run(recorder=rec)
    robot.deactive()


def test_episode_auto_increment(tmp_path):
    """多次录制自动递增 episode 编号"""
    for _ in range(2):
        robot = RobotStruct(CAMERA_MT, dry_run=True)
        robot.active()
        rec = EpisodeRecorder(str(tmp_path), action_dim=18)
        robot.run(recorder=rec)
        robot.deactive()

    assert (tmp_path / "episode_0000").is_dir()
    assert (tmp_path / "episode_0001").is_dir()


def test_micro_action_interpolation(tmp_path, mock_i2c):
    """微动作插值：s1 从 0→30 分 3 步 → 10, 20, 30"""
    robot = RobotStruct(MICRO_ACTION_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    actions = np.load(tmp_path / "episode_0000" / "actions.npy")
    # 首行 s1=0（不分解），row2 s1 从 0→30 分 3 步
    expected_s1 = [0, 10, 20, 30]
    assert list(actions[:, 0]) == expected_s1


def test_delay_not_multiple_of_10(tmp_path, mock_i2c):
    """delay 非 10 倍数时报错"""
    robot = RobotStruct(BAD_DELAY_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    with pytest.raises(ValueError, match="不是 10 的倍数"):
        robot.run(recorder=rec)
    robot.deactive()


# ---------------- IMU 姿态录制（R2） ----------------

# @Camera + @MPU6050_BIND + 2 行 @Action —— 脚本驱动录制姿态
IMU_MT = """
@I2C
 name        path;
i2c-1  /dev/i2c-1;
@

@MPU6050_BIND
     name    I2C   addr;
mpu6050-0  i2c-1   0x69;
@

@Camera
 name  width  height  framerate  capacity;
 cam0    320      240        15         2;
@

@Action
cam0.frame;
0;
0;
@
"""


def test_imu_npy_written_with_dict(tmp_path):
    """record 传姿态 dict → flush 产出 imu.npy（T×8），键序 roll..gz"""
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    imu = {"roll": 1.0, "pitch": 2.0, "ax": 3.0, "ay": 4.0,
           "az": 5.0, "gx": 6.0, "gy": 7.0, "gz": 8.0}
    rec.record(None, [0.0], imu_vec=imu)
    rec.record(None, [0.0], imu_vec=imu)
    rec.flush()

    imus = np.load(tmp_path / "episode_0000" / "imu.npy")
    assert imus.shape == (2, 8)
    assert list(imus[0]) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]


def test_imu_mixed_none_marks_zero(tmp_path):
    """混合数据集：部分帧无姿态 → 该帧记零向量，仍产出 imu.npy"""
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    rec.record(None, [0.0], imu_vec=None)
    rec.record(None, [0.0], imu_vec={"roll": 9.0})
    rec.flush()

    imus = np.load(tmp_path / "episode_0000" / "imu.npy")
    assert imus.shape == (2, 8)
    assert np.allclose(imus[0], 0.0)
    assert imus[1, 0] == 9.0


def test_no_imu_no_file(tmp_path):
    """全程无姿态（旧数据路径）→ 不产出 imu.npy，保持旧格式兼容"""
    robot = RobotStruct(CAMERA_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    ep_dir = tmp_path / "episode_0000"
    assert (ep_dir / "actions.npy").exists()
    assert not (ep_dir / "imu.npy").exists()


def test_run_with_imu_block(tmp_path, mock_i2c):
    """脚本含 @MPU6050_BIND 时 run() 录制产出 imu.npy 且与 actions 帧对齐"""
    robot = RobotStruct(IMU_MT, dry_run=True)
    robot.active()
    # 停采样线程注入固定姿态，避免 mock 采样值干扰断言
    sensor = robot.M["mpu6050-0"].sensor
    sensor.stop_daemon()
    sensor.wait_daemon()
    sensor._latest.update(roll=-4.0, gz=12.0)
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    ep_dir = tmp_path / "episode_0000"
    actions = np.load(ep_dir / "actions.npy")
    imus = np.load(ep_dir / "imu.npy")
    assert actions.shape == (2, 18)      # 2 行 @Action，无 Speaker 不分解
    assert imus.shape == (2, 8)
    assert np.allclose(imus[:, 0], -4.0)
    assert np.allclose(imus[:, 7], 12.0)


# 双传感器脚本（槽位顺序对齐 std.mt 前两行：i2c-1 上 0x69 / 0x68）
IMU2_MT = """
@I2C
 name        path;
i2c-1  /dev/i2c-1;
@

@MPU6050_BIND
     name    I2C   addr;
mpu6050-0  i2c-1   0x69;
mpu6050-1  i2c-1   0x68;
@

@Camera
 name  width  height  framerate  capacity;
 cam0    320      240        15         2;
@

@Action
cam0.frame;
0;
0;
@
"""


def test_run_multi_imu_wide_layout(tmp_path, mock_i2c):
    """多 MPU6050：run() 录制 imu.npy 宽度 8×N，槽位按块行顺序"""
    robot = RobotStruct(IMU2_MT, dry_run=True)
    robot.active()
    for name, vals in (("mpu6050-0", {"roll": -4.0}),
                       ("mpu6050-1", {"roll": 6.0, "gz": 12.0})):
        sensor = robot.M[name].sensor
        sensor.stop_daemon()
        sensor.wait_daemon()
        sensor._latest.update(**vals)
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    imus = np.load(tmp_path / "episode_0000" / "imu.npy")
    assert imus.shape == (2, 16)
    assert np.allclose(imus[:, 0], -4.0)    # sensor0 roll
    assert np.allclose(imus[:, 8], 6.0)     # sensor1 roll（槽位 8 起）
    assert np.allclose(imus[:, 15], 12.0)   # sensor1 gz


def test_actpred_compatibility(tmp_path, mock_i2c):
    """用 actpred 的 ActionDataset 加载录制的 episode"""
    pytest.importorskip("torch")
    pytest.importorskip("torchvision")

    robot = RobotStruct(SERVO_CAMERA_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    actpred_dir = str(Path(__file__).resolve().parent.parent.parent / "actpred")
    sys.path.insert(0, actpred_dir)
    try:
        from dataset import ActionDataset
        ds = ActionDataset(str(tmp_path), history_len=5,
                           action_dim=18, frame_len=3)
        # 19 帧，start=max(5,2)=5，samples: t=5..18 → 14 个样本
        assert len(ds) == 14
        sample = ds[0]
        # ActionDataset 默认 resize 到 640×480，与相机原始尺寸无关
        assert sample["images"].shape == (3, 3, 480, 640)  # (F, C, H, W)
        assert sample["action_history"].shape == (5, 18)
        assert sample["target"].shape == (18,)
    finally:
        sys.path.remove(actpred_dir)
