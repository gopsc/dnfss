"""actpred 预训练入口：从 .mt 脚本学习动作动力学（无需图像）

用法:
    bash _run.sh pretrain --mt-dir ../bot4cj/scripts --epochs 200

流程:
    1. 解析 .mt 文件中的 @Action 矩阵 → 动作序列
    2. 滑动窗口采样: action_history[H] → target action（下一行）
    3. 用零图像占位训练完整 ActionPredictor（保持 checkpoint 兼容性）
    4. 保存 checkpoint，格式与 train.py 一致，可直接用于推理或微调

数据增强:
    --noise-std 控制高斯噪声标准差。因 .mt 脚本数据量有限，
    适度噪声（如 0.5~2.0）可有效防止过拟合。

图像占位:
    --random-images 用 ImageNet 归一化的均匀噪声替代全零占位。
    全零图像会让 BN running_var 停在初始小值（推理时每级放大 ~50 倍，
    特征达 ±1e6），导致 HEF uint8 量化不可行；微调阶段必须用噪声或
    真实图像恢复 BN 统计。
"""

import json
import os
import random

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm

from config import (Config, build_argparser, apply_overrides,
                    validate_config_values, warn_init_from_mismatch)
from model import ActionPredictor
from mt_parser import parse_mt_directory

_IMAGENET_MEAN = [0.485, 0.456, 0.406]
_IMAGENET_STD = [0.229, 0.224, 0.225]


class MTActionDataset(Dataset):
    """从 .mt @Action 矩阵构建的滑动窗口数据集（无图像）

    每个样本: (action_history[history_len, action_dim], target[action_dim])
    """

    def __init__(self, mt_dir: str, history_len: int, action_dim: int,
                 file_indices: list = None, files: list = None,
                 action_mean=None, action_std=None):
        self.history_len = history_len
        self.action_dim = action_dim
        self._action_mean = action_mean
        self._action_std = action_std
        self.samples = []

        # 复用调用方已解析的结果（避免 train/val 各自重复解析同一目录）
        all_files = files if files is not None else parse_mt_directory(mt_dir)
        if file_indices is not None:
            all_files = [all_files[i] for i in file_indices]

        for path, matrix in all_files:
            n_cols = matrix.shape[1]
            if n_cols < action_dim:
                matrix = np.pad(
                    matrix,
                    ((0, 0), (0, action_dim - n_cols)),
                    mode="constant",
                )
            elif n_cols > action_dim:
                matrix = matrix[:, :action_dim]

            for i in range(len(matrix) - history_len):
                history = matrix[i : i + history_len]
                target = matrix[i + history_len]
                self.samples.append((history.copy(), target.copy()))

        print(f"MTActionDataset: {len(self.samples)} samples "
              f"from {len(all_files)} files "
              f"(history_len={history_len}, action_dim={action_dim})")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        history, target = self.samples[idx]
        history = torch.from_numpy(history.copy())
        target = torch.from_numpy(target.copy())
        if self._action_mean is not None:
            history = (history - self._action_mean) / self._action_std
            target = (target - self._action_mean) / self._action_std
        return {
            "action_history": history,
            "target": target,
        }


def _placeholder_images(batch, frame_len, image_height, image_width,
                        device, random_images: bool):
    """构建 (B,F,3,H,W) 图像占位：全零或 ImageNet 归一化均匀噪声"""
    if not random_images:
        return torch.zeros(batch, frame_len, 3, image_height, image_width,
                           device=device)
    x = torch.rand(batch, frame_len, 3, image_height, image_width,
                   device=device)
    mean = torch.tensor(_IMAGENET_MEAN, device=device).view(1, 1, 3, 1, 1)
    std = torch.tensor(_IMAGENET_STD, device=device).view(1, 1, 3, 1, 1)
    return (x - mean) / std


def pretrain_one_epoch(model, loader, optimizer, device, image_width,
                       image_height, frame_len,
                       max_grad_norm=None, noise_std=0.0,
                       random_images: bool = False):
    model.train()
    total_loss = 0.0
    count = 0
    images = None
    for batch in tqdm(loader, desc="  pretrain", leave=False):
        history = batch["action_history"].to(device)
        target = batch["target"].to(device)
        if noise_std > 0:
            history = history + torch.randn_like(history) * noise_std
            target = target + torch.randn_like(target) * noise_std
        if images is None or images.size(0) != history.size(0):
            images = _placeholder_images(
                history.size(0), frame_len, image_height, image_width,
                device, random_images)
        optimizer.zero_grad()
        pred = model(images, history)
        loss = torch.nn.functional.mse_loss(pred, target)
        loss.backward()
        if max_grad_norm:
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm)
        optimizer.step()
        total_loss += loss.item() * history.size(0)
        count += history.size(0)
    return total_loss / max(count, 1)


@torch.no_grad()
def validate(model, loader, device, image_width, image_height, frame_len,
             random_images: bool = False):
    model.eval()
    total_loss = 0.0
    count = 0
    images = None
    for batch in tqdm(loader, desc="  val  ", leave=False):
        history = batch["action_history"].to(device)
        target = batch["target"].to(device)
        if images is None or images.size(0) != history.size(0):
            images = _placeholder_images(
                history.size(0), frame_len, image_height, image_width,
                device, random_images)
        pred = model(images, history)
        loss = torch.nn.functional.mse_loss(pred, target)
        total_loss += loss.item() * history.size(0)
        count += history.size(0)
    if count == 0:
        return float("inf")
    return total_loss / count


def main():
    parser = build_argparser("actpred pre-training from .mt scripts")
    parser.add_argument("--mt-dir", type=str, required=True,
                        help=".mt 脚本目录（如 ../bot4cj/scripts）")
    parser.add_argument("--noise-std", type=float, default=0.0,
                        help="高斯噪声标准差（数据增强，建议 0.5~2.0）")
    parser.add_argument("--random-images", action="store_true",
                        help="用 ImageNet 归一化均匀噪声替代全零图像占位"
                             "（微调时恢复 BN 统计，避免 HEF 量化退化）")
    args = parser.parse_args()

    cfg = apply_overrides(Config(), args)
    validate_config_values(cfg.data, cfg.model, cfg.train)
    cfg.data.data_dir = args.mt_dir

    os.makedirs(cfg.train.checkpoint_dir, exist_ok=True)

    random.seed(cfg.train.seed)
    np.random.seed(cfg.train.seed)
    torch.manual_seed(cfg.train.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(cfg.train.seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # 按文件级划分 train/val，避免滑动窗口样本重叠导致的数据泄漏
    all_files = parse_mt_directory(args.mt_dir)
    if len(all_files) == 0:
        raise ValueError(
            f"数据集为空。请检查 mt-dir={args.mt_dir} "
            f"及 .mt 文件中 @Action 行数 > history_len={cfg.data.history_len}"
        )

    n_files = len(all_files)
    file_indices = list(range(n_files))
    rng = np.random.RandomState(cfg.train.seed)
    rng.shuffle(file_indices)
    val_file_count = max(1, int(n_files * cfg.train.val_split))
    val_file_indices = file_indices[:val_file_count]
    train_file_indices = file_indices[val_file_count:]

    train_ds = MTActionDataset(
        args.mt_dir, cfg.data.history_len, cfg.data.action_dim,
        file_indices=train_file_indices, files=all_files,
    )

    # 动作归一化（与 train.py 语义一致）：无 stats 文件时按训练集
    # targets 计算并保存到 checkpoint_dir，验证集复用同一 stats
    action_mean = action_std = None
    if cfg.data.normalize_actions:
        if cfg.data.action_stats_path:
            if not os.path.exists(cfg.data.action_stats_path):
                raise FileNotFoundError(
                    f"指定的 action_stats_path 不存在: "
                    f"{cfg.data.action_stats_path}")
            stats = np.load(cfg.data.action_stats_path)
        else:
            # 统计量必须覆盖 history 与 target 并集：只按 targets 算时，
            # targets 中恒定的列 std 落到 1e-8 地板值，而 history 窗口
            # 可能含该列的其他取值（如 .mt 脚本前几行），z-score 会爆炸
            # （实测 (90-45)/1e-8=4.5e9），后续 act_tokens ±1e9 直接
            # 打爆 DFC 量化（NegativeSlopeExponentNonFixable）
            all_actions = np.stack(
                [np.concatenate([s[0], s[1][None]]) for s in train_ds.samples]
            ).reshape(-1, cfg.data.action_dim)
            stats = np.stack([all_actions.mean(axis=0),
                              all_actions.std(axis=0) + 1e-8])
            stats_path = os.path.join(cfg.train.checkpoint_dir,
                                      "action_stats.npy")
            np.save(stats_path, stats)
            # 只存裸文件名：推理端按 checkpoint 目录解析相对路径
            # （predict.py/export_deploy.py/gen_calibration.py 同语义），
            # 存 "./checkpoints/..." 会被二次拼接成 checkpoints/./checkpoints
            cfg.data.action_stats_path = "action_stats.npy"
            print(f"action_stats 已保存: {stats_path}（训练集 targets 统计）")
        if stats.shape != (2, cfg.data.action_dim):
            raise ValueError(
                f"action stats 形状应为 (2, {cfg.data.action_dim})，"
                f"实际为 {stats.shape}")
        action_mean, action_std = stats[0], stats[1]
        train_ds._action_mean = action_mean
        train_ds._action_std = action_std

    val_ds = MTActionDataset(
        args.mt_dir, cfg.data.history_len, cfg.data.action_dim,
        file_indices=val_file_indices, files=all_files,
        action_mean=action_mean, action_std=action_std,
    )

    print(f"Files: {len(train_file_indices)} train, {len(val_file_indices)} val")
    print(f"Samples: {len(train_ds)} train, {len(val_ds)} val")
    if len(train_ds) == 0:
        raise ValueError("训练集为空（样本数不足）")
    if len(val_ds) == 0:
        print("WARNING: 验证集为空，模型选择禁用")

    loader_gen = torch.Generator()
    loader_gen.manual_seed(cfg.train.seed)
    train_loader = DataLoader(
        train_ds, batch_size=cfg.train.batch_size,
        shuffle=True, num_workers=cfg.train.num_workers,
        generator=loader_gen,
    )
    val_loader = DataLoader(
        val_ds, batch_size=cfg.train.batch_size,
        shuffle=False, num_workers=cfg.train.num_workers,
    )

    model = ActionPredictor(cfg.model, cfg.data, pretrained=False).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model params: {n_params:,}")
    print(f"Config: action_dim={cfg.data.action_dim}, "
          f"history_len={cfg.data.history_len}, "
          f"frame_len={cfg.data.frame_len}, "
          f"backbone={cfg.model.cnn_backbone}, "
          f"attn={cfg.model.attn_layers}x{cfg.model.attn_heads}h"
          f"@{cfg.model.attn_dim}")
    print(f"Pre-training: noise_std={args.noise_std}, "
          f"random_images={args.random_images}")

    if args.init_from:
        init_ckpt = torch.load(args.init_from, map_location="cpu",
                               weights_only=True)
        warn_init_from_mismatch(init_ckpt.get("config", {}), cfg)
        missing, unexpected = model.load_state_dict(
            init_ckpt["model_state_dict"], strict=False)
        if missing:
            print(f"  init-from missing keys: {missing}")
        if unexpected:
            print(f"  init-from unexpected keys: {unexpected}")
        print(f"Loaded pretrained weights from: {args.init_from}")

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=cfg.train.lr,
        weight_decay=cfg.train.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.train.epochs
    )

    best_val_loss = float("inf")
    patience_counter = 0
    start_epoch = 1
    best_path = os.path.join(cfg.train.checkpoint_dir, "pretrain_best.pt")
    inference_path = os.path.join(
        cfg.train.checkpoint_dir, "pretrain_best_inference.pt"
    )
    last_path = os.path.join(cfg.train.checkpoint_dir, "pretrain_last.pt")

    if args.resume:
        resume_ckpt = torch.load(args.resume, map_location=device, weights_only=True)
        model.load_state_dict(resume_ckpt["model_state_dict"])
        optimizer.load_state_dict(resume_ckpt["optimizer_state_dict"])
        scheduler.load_state_dict(resume_ckpt["scheduler_state_dict"])
        start_epoch = resume_ckpt["epoch"] + 1
        best_val_loss = resume_ckpt.get("best_val_loss", float("inf"))
        patience_counter = resume_ckpt.get("patience_counter", 0)
        print(f"Resumed from {args.resume} at epoch {start_epoch} "
              f"(best_val_loss={best_val_loss:.6f})")

    for epoch in range(start_epoch, cfg.train.epochs + 1):
        print(f"\nEpoch {epoch}/{cfg.train.epochs}  "
              f"lr={optimizer.param_groups[0]['lr']:.6f}")
        train_loss = pretrain_one_epoch(
            model, train_loader, optimizer, device,
            cfg.data.image_width, cfg.data.image_height, cfg.data.frame_len,
            cfg.train.max_grad_norm, args.noise_std, args.random_images,
        )
        val_loss = validate(model, val_loader, device,
                            cfg.data.image_width, cfg.data.image_height,
                            cfg.data.frame_len, args.random_images)
        scheduler.step()
        print(f"  train_loss={train_loss:.6f}  val_loss={val_loss:.6f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            torch.save(
                {
                    "epoch": epoch,
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "val_loss": val_loss,
                    "config": cfg.to_dict(),
                },
                best_path,
            )
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "config": cfg.to_dict(),
                },
                inference_path,
            )
            print(f"  -> saved best (val_loss={val_loss:.6f})")
        else:
            patience_counter += 1
            if patience_counter >= cfg.train.patience:
                print(f"\nEarly stopping at epoch {epoch} "
                      f"(patience={cfg.train.patience})")
                break

        # 每 epoch 保存 last.pt，支持 --resume 断点续训练
        torch.save(
            {
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "scheduler_state_dict": scheduler.state_dict(),
                "best_val_loss": best_val_loss,
                "patience_counter": patience_counter,
                "config": cfg.to_dict(),
            },
            last_path,
        )

    print(f"\nPre-training done. Best val_loss={best_val_loss:.6f}")

    if best_val_loss == float("inf"):
        torch.save(
            {
                "model_state_dict": model.state_dict(),
                "config": cfg.to_dict(),
            },
            inference_path,
        )
        print("WARNING: saved final weights (no valid validation loss)")
    else:
        print(f"Best pre-trained model: {inference_path}")
        print("Fine-tune: bash _run.sh train --data-dir ./data "
              f"--init-from {inference_path}")

    if args.save_config:
        with open(args.save_config, "w") as f:
            json.dump(cfg.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"Config saved to: {args.save_config}")


if __name__ == "__main__":
    main()
