"""多头 one-hot 分箱 —— 18 维连续动作向量与多组 one-hot 分类目标互转

分箱设计（actpred_onehot_multihead_dev_plan_20260814.md）：
    0-9   舵机角：均匀 1°/箱 → 180 箱（匹配 0.75° 微动作插值数据粒度）
    10    spk.F：对数分箱 log10(1+x) ∈ [0, log10(5001)] → 32 箱
    11    spk.delay：对数分箱 log10(1+x) ∈ [0, log10(2001)] → 32 箱
    12-17 保留位：无头（恒 0，不参与分类）

编码/解码与 action_norm 的关系：箱生活在原始物理空间（舵机箱=角度），
decode 直接产出原始量纲，无需反归一化；输入历史仍按 normalize_mode
归一化（与现状一致），仅监督目标换为 one-hot。

软标签：encode(x, sigma) 把真值 one-hot 高斯扩散到邻近箱，缓解
804→1864 分类面的数据稀疏（多数箱极少被访问）。

仅依赖 numpy，推理/部署文件可直接复用。
"""

import numpy as np

# 每组 one-hot 的箱数（按列索引；保留位无头）
BINS_PER_COL = [180] * 10 + [32, 32]
# 有头的列
HEAD_COLS = tuple(range(12))
# 各组起始位置（组 i 占 [START[i], START[i+1])）
START = np.concatenate([[0], np.cumsum(BINS_PER_COL)])
TOTAL_BINS = int(START[-1])      # 1864

LOG_COLS = (10, 11)              # 对数分箱列
LOG_HI = {10: 5000.0, 11: 2000.0}

# 权重 0 列的输出固定值（原始量纲）：spk.F 固定 0（只影响声音不影响
# 运动，闭环中持续发声无意义）；spk.delay 在步级数据中恒 10ms（微动作
# 分解的隐式节拍）。无监督头在闭环中会输出中段垃圾污染历史，
# 推理/反馈时直接固定为常量。其余权重 0 列（保留位）固定 0。
FIXED_OUTPUT = {10: 0.0, 11: 10.0}


def fixed_zero_columns(weights, action_dim: int = 18) -> np.ndarray:
    """按列权重表返回权重<=0 列的固定输出向量（其余列 0）

    与 default_column_weights 联动：列权重 0 → 推理输出固定为
    FIXED_OUTPUT 常量（缺省 0），避免无监督头在闭环反馈中输出垃圾。
    """
    w = np.asarray(weights, dtype=np.float32)
    fixed = np.zeros(action_dim, dtype=np.float32)
    for i in range(min(action_dim, len(w))):
        if w[i] <= 0.0:
            fixed[i] = FIXED_OUTPUT.get(i, 0.0)
    return fixed


def bin_edges(col: int) -> np.ndarray:
    """列 col 的箱边界数组（长度 bins+1），原始量纲递增"""
    n = BINS_PER_COL[col]
    if col in LOG_COLS:
        hi = LOG_HI[col]
        lo_log, hi_log = 0.0, np.log10(hi + 1.0)
        # 对数空间均匀边界 → 原始空间箱边界
        return np.power(10.0, np.linspace(lo_log, hi_log, n + 1)) - 1.0
    lo, hi = 0.0, 180.0
    return np.linspace(lo, hi, n + 1)


def bin_centers(col: int) -> np.ndarray:
    """列 col 的箱中心（decode 期望值的坐标），原始量纲"""
    e = bin_edges(col)
    return 0.5 * (e[:-1] + e[1:])


def encode(x: np.ndarray, action_dim: int = 18, sigma: float = 0.0,
           out: np.ndarray = None) -> np.ndarray:
    """连续动作向量 (..., D) → 多组 one-hot (..., TOTAL_BINS)

    sigma > 0 时启用软标签：真值箱高斯扩散 exp(-d²/2σ²)，d=与真值箱的
    距离（按箱索引）；归一化后总和为 1。保留位列（12+）无头，恒不编码。
    """
    a = np.asarray(x, dtype=np.float32)
    flat = a.reshape(-1, a.shape[-1])
    n_rows = flat.shape[0]
    if out is None:
        out = np.zeros(a.shape[:-1] + (TOTAL_BINS,), dtype=np.float32)
    out_flat = out.reshape(n_rows, TOTAL_BINS)
    rows = np.arange(n_rows)
    for col in HEAD_COLS:
        if col >= flat.shape[1]:
            break
        edges = bin_edges(col)
        idx = np.clip(np.searchsorted(edges, flat[:, col], side="right") - 1,
                      0, BINS_PER_COL[col] - 1)
        s, e = START[col], START[col + 1]
        if sigma <= 0.0:
            out_flat[rows, s + idx] = 1.0
        else:
            n = BINS_PER_COL[col]
            d = np.abs(np.arange(n)[None, :] - idx[:, None])
            g = np.exp(-(d * d) / (2.0 * sigma * sigma))
            g = g / g.sum(axis=1, keepdims=True)
            out_flat[:, s:s + n] = g
    return out


def decode(logits: np.ndarray, action_dim: int = 18,
           temperature: float = 1.0) -> np.ndarray:
    """多头 logits (..., TOTAL_BINS) → 连续动作向量 (..., D)，原始量纲

    逐头 softmax → 期望值 Σ pᵢ·箱中心（分辨率超越箱宽）；保留位列填 0。
    temperature 用于探索采样（=1 为正常推理）。
    """
    l = np.asarray(logits, dtype=np.float32)
    out = np.zeros(l.shape[:-1] + (action_dim,), dtype=np.float32)
    for col in HEAD_COLS:
        if col >= action_dim:
            break
        s, e = START[col], START[col + 1]
        z = l[..., s:e] / max(temperature, 1e-6)
        z = z - z.max(axis=-1, keepdims=True)   # 数值稳定
        p = np.exp(z)
        p = p / p.sum(axis=-1, keepdims=True)
        out[..., col] = p @ bin_centers(col)
    return out


def argmax_decode(logits: np.ndarray, action_dim: int = 18) -> np.ndarray:
    """argmax 解码：每头取最大概率箱中心（确定性，量化到箱宽）"""
    l = np.asarray(logits, dtype=np.float32)
    out = np.zeros(l.shape[:-1] + (action_dim,), dtype=np.float32)
    for col in HEAD_COLS:
        if col >= action_dim:
            break
        s, e = START[col], START[col + 1]
        idx = np.argmax(l[..., s:e], axis=-1)
        out[..., col] = bin_centers(col)[idx]
    return out
