"""actpred HEF NPU 输入敏感性复验脚本（重编译后的验收门槛）

用法: venv/bin/python _npu_test.py

4 角测试（真实/全零帧 × 真实/全零历史）+ 与 PyTorch 参考对比。
HEF 尺寸/布局从 vstream info 自动推导，checkpoint 用
checkpoints/best_inference.pt（v4 onehot，fixed_range）。
"""
import sys
import time
sys.path.insert(0, "/opt/blackbird/actpred")

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from hailo_platform import HEF

from config import parse_checkpoint_config
from model import ActionPredictor
from hailo_infer import HailoPredictor

HEF_PATH = "checkpoints/actpred_v4.hef"
CKPT = "checkpoints/best_inference.pt"
IMG_DIR = "data/episode_0000/images"

hef = HEF(HEF_PATH)
print("=== HEF vstream infos ===")
for i in hef.get_input_vstream_infos():
    q = i.quant_info
    print(f"IN  {i.name:16s} {tuple(i.shape)} {i.format.type} "
          f"zp={q.qp_zp} scale={q.qp_scale:.6g}")
for o in hef.get_output_vstream_infos():
    q = o.quant_info
    print(f"OUT {o.name:16s} {tuple(o.shape)} {o.format.type} "
          f"zp={q.qp_zp} scale={q.qp_scale:.6g}")

imgs_real = [np.array(Image.open(f"{IMG_DIR}/000{i}.jpg").convert("RGB"))
             for i in range(3)]
imgs_zero = [np.zeros_like(imgs_real[0]) for _ in range(3)]

hist_real = np.array([
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 10, 0, 0, 0, 0, 0, 0],
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 10, 0, 0, 0, 0, 0, 0],
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 10, 0, 0, 0, 0, 0, 0],
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 10, 0, 0, 0, 0, 0, 0],
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 10, 0, 0, 0, 0, 0, 0],
], dtype=np.float32)
hist_zero = np.zeros((5, 18), dtype=np.float32)

# PyTorch 参考（checkpoint 自带 config）
ck = torch.load(CKPT, map_location="cpu", weights_only=True)
cfg = parse_checkpoint_config(ck["config"])
model = ActionPredictor(cfg.model, cfg.data, pretrained=False)
model.load_state_dict(ck["model_state_dict"])
model.eval()
pred_learned = model.pred_token.data.unsqueeze(1).numpy()   # (1,1,1,d)
imu_dim = cfg.data.imu_dim

# 归一化模型：torch 参考在喂入前归一化；HailoPredictor 传 normalize_mode
# 由封装内部归一化（hist_real/hist_zero 保持原始值，避免双重归一化）。
# fixed_range 免 action_stats.npy（该模式训练不产出统计文件）。
import os
stats_path = None
norm_mode = cfg.data.normalize_mode if cfg.data.normalize_actions else "none"
if norm_mode == "zscore":
    stats_path = cfg.data.action_stats_path or os.path.join(
        os.path.dirname(os.path.abspath(CKPT)), "action_stats.npy")
    if not os.path.isabs(stats_path):
        stats_path = os.path.join(os.path.dirname(os.path.abspath(CKPT)),
                                  stats_path)
    if not os.path.exists(stats_path):
        raise FileNotFoundError(f"统计文件不存在: {stats_path}")
    print(f"HailoPredictor stats: {stats_path}")
elif norm_mode == "fixed_range":
    print("fixed_range 归一化：免 action_stats.npy，按固定物理范围 min-max")

from torchvision import transforms
tf = transforms.Compose([
    transforms.Resize((cfg.data.image_height, cfg.data.image_width)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])


def torch_pred(imgs, hist, use_learned_pred=True, imu=None):
    x = torch.stack([tf(Image.fromarray(im)) for im in imgs]).unsqueeze(0)
    h = torch.tensor(hist).unsqueeze(0)
    if norm_mode == "zscore" and stats_path is not None:
        stats = np.load(stats_path)
        h = (h - torch.tensor(stats[0])) / torch.tensor(stats[1])
    elif norm_mode == "fixed_range":
        from action_norm import normalize_action
        h = torch.from_numpy(normalize_action(h.numpy(), cfg.data.action_dim))
    ii = None
    if imu_dim > 0:
        # 与 HailoPredictor 同口径：原始姿态 → 内部归一化前需手动归一
        from imu_norm import normalize_imu
        ii = torch.from_numpy(normalize_imu(imu, imu_dim)).unsqueeze(0)
    if use_learned_pred:
        with torch.no_grad():
            out = model(x, h, ii).squeeze(0).numpy()
    else:
        saved = model.pred_token.data.clone()
        model.pred_token.data.zero_()
        with torch.no_grad():
            out = model(x, h, ii).squeeze(0).numpy()
        model.pred_token.data.copy_(saved)
    if cfg.data.onehot:
        # 多头 logits → 期望值解码 → 原始量纲（与 predict.py 同语义，
        # 含权重 0 列固定常量），对比前无需反归一化
        from bins import decode as bin_decode, fixed_zero_columns
        from action_norm import default_column_weights
        out = bin_decode(out, cfg.data.action_dim).astype(np.float32)
        w = (np.asarray(cfg.data.column_weights, dtype=np.float32)
             if cfg.data.column_weights is not None
             else default_column_weights(cfg.data.action_dim))
        fixed = fixed_zero_columns(w, cfg.data.action_dim)
        out[w <= 0] = fixed[w <= 0]
    return out


# R2 方案一：imu_dim>0 时每步传原始姿态（HailoPredictor 内部归一化）
imu_real = np.array([5.0, -3.0] + [0.0] * 6, dtype=np.float32)

# normalize_actions=False 时 normalize_mode 无意义（stats_path=None 即不归一化）
with HailoPredictor(HEF_PATH, stats_path=stats_path,
                    normalize_mode=(norm_mode if norm_mode != "none"
                                    else "zscore")) as p:
    print(f"\ndims: frame_len={p.frame_len} img={p.image_width}x{p.image_height} "
          f"history={p.history_len} action_dim={p.action_dim}")
    t0 = time.perf_counter()
    a = p.predict(imgs_real, hist_real, pred_token=pred_learned, imu=imu_real)
    b = p.predict(imgs_zero, hist_real, pred_token=pred_learned, imu=imu_real)
    c = p.predict(imgs_real, hist_zero, pred_token=pred_learned, imu=imu_real)
    d = p.predict(imgs_zero, hist_zero, pred_token=pred_learned)
    t1 = time.perf_counter()
    print(f"4x predict: {(t1 - t0) / 4 * 1000:.1f} ms/次")
    for name, r in [("real_img+real_hist", a), ("zero_img+real_hist", b),
                    ("real_img+zero_hist", c), ("zero_img+zero_hist", d)]:
        print(f"{name:20s} max_abs={np.abs(r).max():.4f}  out={np.round(r[:6], 3)}...")

    img_sens = np.abs(a - b).max()
    hist_sens = np.abs(a - c).max()
    print(f"\nimg sensitivity  = {img_sens:.4f}（>0.1 视为输入连通）")
    print(f"hist sensitivity = {hist_sens:.4f}（>0.1 视为输入连通）")
    ok = img_sens > 0.1 and hist_sens > 0.1
    if imu_dim > 0:
        e = p.predict(imgs_real, hist_real, pred_token=pred_learned,
                      imu=np.zeros_like(imu_real))
        print(f"imu sensitivity  = {np.abs(a - e).max():.4f}（>0 视为姿态连通）")

    ref = torch_pred(imgs_real, hist_real, True, imu_real)
    ref_zero_pred = torch_pred(imgs_real, hist_real, False, imu_real)
    # torch 参考是模型原始归一化空间输出；HailoPredictor 已反归一化
    # 到原始动作空间，比较前须同空间对齐，否则归一化模型下 max_diff
    # 无意义（onehot 模型 torch_pred 已 decode 为原始量纲，无需再处理）
    if not cfg.data.onehot:
        if norm_mode == "fixed_range":
            from action_norm import denormalize_action
            ref = denormalize_action(ref, cfg.data.action_dim)
            ref_zero_pred = denormalize_action(ref_zero_pred,
                                               cfg.data.action_dim)
        elif stats_path is not None:
            s = np.load(stats_path)
            ref = ref * s[1] + s[0]
            ref_zero_pred = ref_zero_pred * s[1] + s[0]
    print(f"torch learned_pred  : {np.round(ref[:6], 3)}...")
    print(f"torch zero_pred     : {np.round(ref_zero_pred[:6], 3)}...")
    print(f"HEF vs torch(learned) max_diff={np.abs(a - ref).max():.4f}"
          f"（原始动作空间）")

    print("\nPASS: HEF 输入连通，可继续与 torch 对比量化误差"
          if ok else "\nFAIL: HEF 输出对输入不敏感，编译仍有问题")
