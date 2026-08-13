"""生成 DFC 量化校准数据集

两种模式：
  默认模式（data/calibration.npz）：为旧版 dfc_compile.py 的 ClientModel
  流程准备，目前保留兼容。
  --deploy 模式（data/calibration_deploy.npz）：为 export_deploy.py 导出
  的部署版 ONNX 生成校准集，键名匹配 HN 层名 actpred_deploy/input_layerN，
  形状匹配 HEF 输入：frame0~2 (N,H,W,3) 已归一化 float32、
  action_history (N,1,H_len,D)、pred_token (N,1,1,d) 学习值 ± 抖动
  （常量校准会使量化范围退化，见 --pred-jitter）。
  图像分辨率与 --model checkpoint 的配置一致。

数据源：
  - 图像：无真实 episode 时用 [0,255] 均匀采样像素 + ImageNet 归一化
  - 动作历史：从 bot4cj/scripts/*.mt 的真实 @Action 矩阵滑窗采样
    （列不足 18 补零，与 pretrain_mt.py 语义一致）

真实数据到位后应替换为真实帧重新校准。

用法:
    python gen_calibration.py --deploy --model checkpoints/pretrain_best_inference.pt
"""

import argparse

import numpy as np

from config import DataConfig, parse_checkpoint_config
from mt_parser import parse_mt_directory

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def build_action_history_samples(mt_dir, n_samples, history_len, action_dim, rng):
    sequences = []
    for _path, matrix in parse_mt_directory(mt_dir):
        n_cols = matrix.shape[1]
        if n_cols < action_dim:
            matrix = np.pad(matrix, ((0, 0), (0, action_dim - n_cols)))
        elif n_cols > action_dim:
            matrix = matrix[:, :action_dim]
        if len(matrix) >= history_len:
            sequences.append(matrix)
    if not sequences:
        raise ValueError(f"{mt_dir} 中没有可用的 @Action 序列")

    windows = []
    for matrix in sequences:
        for i in range(len(matrix) - history_len + 1):
            windows.append(matrix[i:i + history_len])
    idx = rng.integers(0, len(windows), size=n_samples)
    return np.stack([windows[i] for i in idx]).astype(np.float32)


def build_image_samples(n_samples, frame_len, height, width, rng):
    # 存 uint8 原始像素值（0~255），加载时再做 ToTensor+Normalize，节省磁盘
    return rng.integers(0, 256,
                        size=(n_samples, frame_len, 3, height, width),
                        dtype=np.uint8)


def build_deploy_set(args):
    import torch
    from model import ActionPredictor

    ckpt = torch.load(args.model, map_location="cpu", weights_only=True)
    cfg = parse_checkpoint_config(ckpt["config"]).data
    rng = np.random.default_rng(args.seed)

    h, w = cfg.image_height, cfg.image_width
    raw = rng.integers(0, 256, size=(args.samples, h, w, 3),
                       dtype=np.uint8).astype(np.float32) / 255.0
    norm = ((raw - IMAGENET_MEAN) / IMAGENET_STD).astype(np.float32)
    frames = [norm.copy() for _ in range(cfg.frame_len)]

    history = build_action_history_samples(
        args.mt_dir, args.samples, cfg.history_len, cfg.action_dim, rng)
    history = history[:, None, :, :]  # (N, 1, H_len, D)

    model = ActionPredictor(parse_checkpoint_config(ckpt["config"]).model,
                            cfg, pretrained=False)
    model.load_state_dict(ckpt["model_state_dict"])
    d = model.pred_token.shape[-1]
    v = model.pred_token.data.unsqueeze(1).numpy()  # (1, 1, d) 学习到的常量
    # 常量校准会使量化范围退化（上次实测 scale=18571.7，任何输入都被
    # 量化成 0）；按学习值 ±相对抖动生成校准样本，运行时喂学习值即可
    # 无损还原（量化误差 ~1e-4）
    jitter = v * rng.uniform(-args.pred_jitter, args.pred_jitter,
                             size=(args.samples, 1, 1, d)).astype(np.float32)
    jitter = jitter + rng.normal(0.0, args.pred_jitter_abs,
                                 size=jitter.shape).astype(np.float32)
    pred = np.broadcast_to(v, (args.samples, 1, 1, d)).astype(np.float32) + jitter

    np.savez_compressed(
        args.output,
        **{f"actpred_deploy/input_layer{i + 1}": arr
           for i, arr in enumerate([*frames, history, pred])})
    print(f"部署校准集已保存: {args.output}")
    print(f"  frame0~2: {frames[0].shape}（分辨率 {w}x{h}，与 checkpoint 一致）")
    print(f"  action_history: {history.shape}  pred_token: {pred.shape}"
          f"（学习值 ±{args.pred_jitter:.0%} 抖动）")


def main():
    parser = argparse.ArgumentParser(description="Generate DFC calibration dataset")
    parser.add_argument("--deploy", action="store_true",
                        help="生成部署版（export_deploy.py）校准集")
    parser.add_argument("--model", default="checkpoints/pretrain_best_inference.pt",
                        help="--deploy 模式的 checkpoint（决定分辨率与 pred_token）")
    parser.add_argument("--mt-dir", default="../bot4cj/scripts")
    parser.add_argument("--output", default=None)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pred-jitter", type=float, default=0.3,
                        help="pred_token 校准相对抖动幅度（默认 ±30%）")
    parser.add_argument("--pred-jitter-abs", type=float, default=0.005,
                        help="pred_token 校准绝对抖动标准差（近零元素保底）")
    args = parser.parse_args()

    if args.deploy:
        args.output = args.output or "data/calibration_deploy.npz"
        build_deploy_set(args)
        return

    args.output = args.output or "data/calibration.npz"
    cfg = DataConfig()
    rng = np.random.default_rng(args.seed)

    history = build_action_history_samples(
        args.mt_dir, args.samples, cfg.history_len, cfg.action_dim, rng)
    images = build_image_samples(
        args.samples, cfg.frame_len, cfg.image_height, cfg.image_width, rng)

    np.savez_compressed(args.output, images=images, action_history=history)
    print(f"校准集已保存: {args.output}")
    print(f"  images: {images.shape} dtype={images.dtype}")
    print(f"  action_history: {history.shape} dtype={history.dtype}")


if __name__ == "__main__":
    main()
