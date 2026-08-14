"""动作向量固定物理范围归一化 —— 训练（dataset/pretrain_mt）与推理
（predict / onnx_infer / hailo_infer / gen_calibration / export_deploy）共用

18 列动作向量的物理量纲（pybot 侧契约）：
    0-9    舵机角度 [0, 180]°（pybot/hd/servo.py MAX_ANGLE=180.0 校准后全行程）
    10     spk.F 扬声器频率 [0, 5000] Hz（@Speaker max_f=5000）
    11     spk.delay 延时 [0, 2000] ms（脚本实用上限，std.mt 用 10-500）
    12-17  保留位（恒 0，hi-lo=1.0 使归一化后仍为 0；损失权重 0 不参与优化）

min-max 映射到 [0,1]：x_norm = clip((x - lo) / (hi - lo), 0, 1)。
与 z-score 的区别：固定物理范围不依赖 action_stats.npy 数据统计文件，
部署链零状态；clamp 防御真实数据越界（越界值截断到 [0,1]，不扩散）。

仅依赖 numpy，保证 hailo_infer.py（HAT 部署环境无 torch）可直接复用。
"""

import numpy as np

# 各列物理下界（当前全 0；保留数组以便未来引入非零下界列）
ACTION_LO = np.zeros(18, dtype=np.float32)
# 各列物理上界（18 列标准布局；action_dim 不同用 action_bounds() 适配）
ACTION_HI = np.array(
    [180.0] * 10 +          # 0-9 舵机角
    [5000.0, 2000.0] +      # 10 spk.F，11 spk.delay
    [1.0] * 6,              # 12-17 保留位（恒 0，span=1.0 保持归一化后为 0）
    dtype=np.float32)

# 归一化方式：fixed_range 线性 min-max；log_range 在 spk 两列上用
# log10(1+x) 对数尺度，修正低端聚集导致的精度错配
# （10Hz 在线性尺度占 0.002、对数尺度占 0.27）
RANGE_MODES = ("fixed_range", "log_range")
LOG_COLS = (10, 11)         # spk.F / spk.delay


def is_range_mode(mode: str) -> bool:
    """normalize_mode 是否为固定范围类（fixed_range / log_range）"""
    return mode in RANGE_MODES


def action_bounds(action_dim: int = 18):
    """(lo, hi) 物理界：action_dim ≤ 18 取前缀；> 18 的超出列按 [0, 1] 补足"""
    if action_dim <= len(ACTION_LO):
        return ACTION_LO[:action_dim], ACTION_HI[:action_dim]
    pad = action_dim - len(ACTION_LO)
    return (np.concatenate([ACTION_LO, np.zeros(pad, dtype=np.float32)]),
            np.concatenate([ACTION_HI, np.ones(pad, dtype=np.float32)]))


def normalize_action(action, action_dim: int = None,
                     mode: str = "fixed_range") -> np.ndarray:
    """原始动作向量 → [0,1] 归一化向量（clamp 防御越界值）

    action: (..., action_dim) 任意前导维；action_dim 缺省取末维长度。
    mode="fixed_range"：全列线性 min-max；
    mode="log_range"：LOG_COLS 列用 log10(1+x)（负值先截 0），其余列线性。
    """
    a = np.asarray(action, dtype=np.float32)
    lo, hi = action_bounds(action_dim or a.shape[-1])
    out = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    if mode == "log_range":
        for c in LOG_COLS:
            if c < a.shape[-1]:
                hc = float(hi[c])
                col = np.clip(a[..., c], 0.0, None)
                out[..., c] = np.log10(col + 1.0) / np.log10(hc + 1.0)
    return out


def denormalize_action(norm_action, action_dim: int = None,
                       mode: str = "fixed_range") -> np.ndarray:
    """[0,1] 归一化向量 → 原始动作向量（不 clamp，保留模型外推能力）

    mode="log_range" 时 LOG_COLS 列指数还原：x = 10^(n·log10(hi+1)) - 1。
    """
    n = np.asarray(norm_action, dtype=np.float32)
    lo, hi = action_bounds(action_dim or n.shape[-1])
    out = n * (hi - lo) + lo
    if mode == "log_range":
        for c in LOG_COLS:
            if c < n.shape[-1]:
                hc = float(hi[c])
                out[..., c] = (np.power(10.0, n[..., c] * np.log10(hc + 1.0))
                               - 1.0)
    return out


def default_column_weights(action_dim: int = 18) -> np.ndarray:
    """逐列损失权重默认表：舵机 1.0（安全关键）、spk.F 0.2（保留梯度）、
    spk.delay 0.0（步级数据恒 10ms——微动作分解的隐式节拍，时长信息
    已编码在步数中）、保留位 0.0（恒 0 纯噪声，参与只会稀释有效梯度）。
    action_dim>18 的扩展列默认 1.0：超出当前 18 列语义布局的通道视为
    新执行器，静默给 0 会让其完全不参与训练（P2-a1）。"""
    w = np.zeros(action_dim, dtype=np.float32)
    w[:min(10, action_dim)] = 1.0
    if action_dim > 10:
        w[10] = 0.2
    if action_dim > 18:
        w[18:] = 1.0
    return w
