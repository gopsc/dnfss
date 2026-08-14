"""IMU 姿态归一化 —— 训练（dataset）与推理（predict / hailo_infer）共用

量纲差异大（pybot MPU6050 原始输出，见 pybot/hd/mpu6050.py）：
    roll/pitch  角度制，±180°
    ax/ay/az    加速度计原始 int16（±8g 量程，1g≈4096 LSB）
    gx/gy/gz    陀螺仪原始 int16（±500°/s 量程）

按物理量程做固定缩放归一化（不依赖数据统计文件，部署链零状态）：
    索引 0,1   /180   → ≈[-1, 1]
    索引 2..7  /32768 → ≈[-1, 1]

键序与 pybot robot.IMU_KEYS / recorder 一致：
    [roll, pitch, ax, ay, az, gx, gy, gz]

仅依赖 numpy，保证 hailo_infer.py（HAT 部署环境无 torch）可直接复用。
"""

import numpy as np

# 各索引归一化除数（imu_dim=8 全量；更短的向量取前缀）
IMU_SCALES = np.array([180.0, 180.0,
                       32768.0, 32768.0, 32768.0,
                       32768.0, 32768.0, 32768.0], dtype=np.float32)


def normalize_imu(imu, imu_dim: int = 8) -> np.ndarray:
    """原始姿态向量 → 归一化向量（pad/truncate 到 imu_dim）

    imu: list/np.ndarray，长度任意（None 等价全零）。
    返回 (imu_dim,) float32。
    """
    vec = np.zeros(imu_dim, dtype=np.float32)
    if imu is not None:
        src = np.asarray(imu, dtype=np.float32).ravel()
        n = min(len(src), imu_dim)
        vec[:n] = src[:n]
    return vec / IMU_SCALES[:imu_dim]
