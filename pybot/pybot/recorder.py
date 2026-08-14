"""Episode 录制器 —— 运行 .mt 脚本时同步产出 actpred 格式的自监督数据集

产出格式（与 actpred/dataset.py 的 DataRecorder 兼容）：
    output_dir/
      episode_XXXX/
        images/
          0000.jpg, 0001.jpg, ...
        actions.npy    # shape (T, action_dim) float32
        imu.npy        # 可选，shape (T, imu_dim) float32（MPU6050 姿态回流，R2）

numpy / Pillow 为可选依赖（lazy import），仅 --record 时需要，
不影响 pybot 的零依赖运行时承诺。
"""

import logging
from pathlib import Path

log = logging.getLogger("pybot.recorder")


class EpisodeRecorder:
    """录制 actpred 格式的 episode（images/*.jpg + actions.npy [+ imu.npy]）

    用法：
        rec = EpisodeRecorder("./actpred/data", action_dim=18)
        for frame, action in stream:
            rec.record(frame, action, imu_vec=imu_or_None)
        rec.flush()

    imu_vec：可选 MPU6050 姿态向量（单传感器 roll,pitch,ax,ay,az,gx,gy,gz；
    多传感器按脚本 @MPU6050_BIND 行顺序拼接为 8×N，最多 4 个）。
    RobotStruct 会在录制前把 imu_dim 同步为 8×N（N=传感器数）。
    只要同一 record 调用内同时传入帧/动作/姿态，三者即按帧时刻天然对齐。
    全程 None（无 IMU 硬件）则不产出 imu.npy，保持旧格式兼容。
    """

    def __init__(self, output_dir, action_dim=18, imu_dim=8):
        self.action_dim = action_dim
        self.imu_dim = imu_dim
        self._frame_idx = 0
        self._actions = []
        self._imus = []
        self._has_imu = False

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        ep_idx = 0
        for d in output.glob("episode_*"):
            try:
                ep_idx = max(ep_idx, int(d.name.split("_")[1]) + 1)
            except (ValueError, IndexError):
                pass

        while True:
            self.episode_dir = output / f"episode_{ep_idx:04d}"
            self.images_dir = self.episode_dir / "images"
            try:
                self.images_dir.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                ep_idx += 1

        log.info("recording to %s (action_dim=%d imu_dim=%d)",
                 self.episode_dir, action_dim, imu_dim)

    def record(self, frame, action_vec, imu_vec=None):
        """记录一帧 + 动作向量 [+ 姿态向量]

        frame: np.ndarray (H,W,3) uint8 或 None（跳过图像保存）
        action_vec: list/np.array，长度任意，内部 pad/truncate 到 action_dim
        imu_vec: 可选 list/np.array/dict，长度任意，pad/truncate 到 imu_dim；
                 dict 时按 (roll,pitch,ax,ay,az,gx,gy,gz) 顺序取值（单传感器；
                 多传感器请传 RobotStruct._read_imus 拼好的 8×N 向量）。
                 为 None 时该帧记零向量（混合数据集中标记"无姿态"）。
        """
        import numpy as np

        vec = np.zeros(self.action_dim, dtype=np.float32)
        src = np.asarray(action_vec, dtype=np.float32).ravel()
        n = min(len(src), self.action_dim)
        vec[:n] = src[:n]
        self._actions.append(vec)

        if imu_vec is None:
            ivec = np.zeros(self.imu_dim, dtype=np.float32)
        else:
            self._has_imu = True
            if isinstance(imu_vec, dict):
                imu_vec = [imu_vec.get(k, 0.0) for k in
                           ("roll", "pitch", "ax", "ay", "az", "gx", "gy", "gz")]
            ivec = np.zeros(self.imu_dim, dtype=np.float32)
            isrc = np.asarray(imu_vec, dtype=np.float32).ravel()
            m = min(len(isrc), self.imu_dim)
            ivec[:m] = isrc[:m]
        self._imus.append(ivec)

        if frame is not None:
            from PIL import Image
            img = Image.fromarray(frame)
            img.save(self.images_dir / f"{self._frame_idx:04d}.jpg",
                     quality=90)

        self._frame_idx += 1

    def flush(self):
        """保存 actions.npy（及有姿态时的 imu.npy），返回 episode 路径"""
        import numpy as np
        if self._actions:
            actions = np.array(self._actions, dtype=np.float32)
            np.save(self.episode_dir / "actions.npy", actions)
            # 仅当确实采到非零姿态才落盘 imu.npy，避免全程无 IMU 时产生误导性文件
            if self._has_imu:
                imus = np.array(self._imus, dtype=np.float32)
                np.save(self.episode_dir / "imu.npy", imus)
                log.info("recorded imu.npy shape=%s", imus.shape)
        log.info("recorded %d frames to %s",
                 self._frame_idx, self.episode_dir)
        return str(self.episode_dir)
