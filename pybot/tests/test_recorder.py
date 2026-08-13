"""EpisodeRecorder 测试：actpred 格式数据集录制

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

# 仅 @Camera + @Action(cam0.frame) —— 不需要 I2C
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

# @Camera + @Servo + 多行 @Action（含占位符）—— 需要 mock I2C
SERVO_CAMERA_MT = """
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
  s2   180     -       -           -        1;
@

@Camera
 name  width  height  framerate  capacity;
 cam0    320      240        15         2;
@

@Action
s1.A  s2.A;
  45     0;
   -    10;
   -    20;
   -    30;
   -     0;
   -    10;
   -    20;
   -    30;
   -     0;
   -    10;
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
    assert len(jpgs) == 1              # CAMERA_MT 有 1 行 @Action


def test_action_vector_padding(tmp_path, mock_i2c):
    """2 列 .mt → 18 维向量，末尾 16 维为零"""
    robot = RobotStruct(SERVO_CAMERA_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    actions = np.load(tmp_path / "episode_0000" / "actions.npy")
    assert actions.shape == (10, 18)    # 10 行 @Action × 18 维
    assert np.all(actions[:, 2:] == 0)  # 列 2-17 全为零


def test_placeholder_resolution(tmp_path, mock_i2c):
    """`-` 占位符正确递归解析"""
    robot = RobotStruct(SERVO_CAMERA_MT, dry_run=True)
    robot.active()
    rec = EpisodeRecorder(str(tmp_path), action_dim=18)
    robot.run(recorder=rec)
    robot.deactive()

    actions = np.load(tmp_path / "episode_0000" / "actions.npy")
    # s1.A: row0=45, row1..9 全是 '-' → 递归取上一行 → 全 45
    assert np.all(actions[:, 0] == 45)
    # s2.A: 0,10,20,30,0,10,20,30,0,10
    expected_s2 = [0, 10, 20, 30, 0, 10, 20, 30, 0, 10]
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
        # 10 行，start=max(5,2)=5，samples: t=5..9 → 5 个样本
        assert len(ds) == 5
        sample = ds[0]
        # ActionDataset 默认 resize 到 640×480，与相机原始尺寸无关
        assert sample["images"].shape == (3, 3, 480, 640)  # (F, C, H, W)
        assert sample["action_history"].shape == (5, 18)
        assert sample["target"].shape == (18,)
    finally:
        sys.path.remove(actpred_dir)
