import json
import os
import random
import numpy as np
import torch
from torch.utils.data import DataLoader, Subset
from tqdm import tqdm

from config import (Config, build_argparser, apply_overrides,
                    validate_config_values, warn_init_from_mismatch)
from model import ActionPredictor
from dataset import ActionDataset


def split_episodes(data_dir: str, val_ratio: float, seed: int = 42):
    n = ActionDataset.get_episode_count(data_dir)
    indices = list(range(n))
    rng = np.random.RandomState(seed)
    rng.shuffle(indices)
    val_count = max(1, int(n * val_ratio))
    return indices[val_count:], indices[:val_count]


def _collate(batch):
    """多步样本（{"steps":[...]}）逐 k 聚合成 batch 列表；单步样本走默认 collate。

    rollout_len==1 时 __getitem__ 返回标准 dict，直接默认 collate；
    rollout_len>1 时返回 {"steps":[step_0..step_{K-1}]}，把每个 step_k 跨样本堆叠，
    返回 {"steps":[batch_0..batch_{K-1}]}，供 train_one_epoch 按步 scheduled sampling。
    """
    from torch.utils.data._utils.collate import default_collate
    if batch and isinstance(batch[0], dict) and "steps" in batch[0]:
        k = len(batch[0]["steps"])
        return {"steps": [default_collate([b["steps"][i] for b in batch])
                          for i in range(k)]}
    return default_collate(batch)


def _shift_history(history: torch.Tensor, new_action: torch.Tensor) -> torch.Tensor:
    """动作历史左移一步、追加 new_action 到末尾（scheduled sampling 回传自预测用）。

    history: (B, H, D)，new_action: (B, D) → (B, H, D)
    """
    return torch.cat([history[:, 1:], new_action.unsqueeze(1)], dim=1)


def train_one_epoch(model, loader, optimizer, device, max_grad_norm: float = None,
                    rollout_len: int = 1, ss_prob: float = 1.0,
                    predict_delta: bool = False):
    model.train()
    total_loss = 0.0
    count = 0
    rng = np.random.RandomState()
    for batch in tqdm(loader, desc="  train", leave=False):
        optimizer.zero_grad()
        if rollout_len == 1:
            images = batch["images"].to(device)
            history = batch["action_history"].to(device)
            target = batch["target"].to(device)
            imu = batch["imu"].to(device) if "imu" in batch else None
            pred = model(images, history, imu)
            loss = torch.nn.functional.mse_loss(pred, target)
            bs = images.size(0)
        else:
            # 多步 scheduled sampling：首步必用真历史；后续步以 ss_prob 用真历史、
            # 否则用上一步模型预测（detach 截断，避免梯度跨步爆炸）回传构造历史。
            loss = 0.0
            bs = 0
            history = None
            for k, step in enumerate(batch["steps"]):
                images = step["images"].to(device)
                true_history = step["action_history"].to(device)
                target = step["target"].to(device)
                prev_action = step["prev_action"].to(device)
                imu = step["imu"].to(device) if "imu" in step else None
                if k == 0 or rng.random_sample() < ss_prob:
                    history = true_history
                # 否则沿用上一轮回传的 history（自预测构造）
                pred = model(images, history, imu)
                loss = loss + torch.nn.functional.mse_loss(pred, target)
                bs = images.size(0)
                # 回传：残差模式先把 Δ̂ 加回 a_{t-1} 得到绝对动作，再移入历史
                with torch.no_grad():
                    abs_action = prev_action + pred if predict_delta else pred
                    history = _shift_history(true_history, abs_action.detach())
            loss = loss / len(batch["steps"])
        loss.backward()
        if max_grad_norm:
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm)
        optimizer.step()
        total_loss += loss.item() * bs
        count += bs
    return total_loss / max(count, 1)


@torch.no_grad()
def validate(model, loader, device, rollout_len: int = 1, predict_delta: bool = False):
    model.eval()
    total_loss = 0.0
    count = 0
    for batch in tqdm(loader, desc="  val  ", leave=False):
        if rollout_len == 1:
            images = batch["images"].to(device)
            history = batch["action_history"].to(device)
            target = batch["target"].to(device)
            imu = batch["imu"].to(device) if "imu" in batch else None
            pred = model(images, history, imu)
            loss = torch.nn.functional.mse_loss(pred, target)
            bs = images.size(0)
        else:
            # 验证固定全真历史（确定性，不带 scheduled sampling 随机性），
            # 对 K 步累加，保证 best 模型选择口径稳定可比。
            loss = 0.0
            bs = 0
            for step in batch["steps"]:
                images = step["images"].to(device)
                history = step["action_history"].to(device)
                target = step["target"].to(device)
                imu = step["imu"].to(device) if "imu" in step else None
                pred = model(images, history, imu)
                loss = loss + torch.nn.functional.mse_loss(pred, target)
                bs = images.size(0)
            loss = loss / len(batch["steps"])
        total_loss += loss.item() * bs
        count += bs
    if count == 0:
        return float("inf")  # 空验证集不参与比较，避免假 best
    return total_loss / count


def main():
    parser = build_argparser("actpred training")
    args = parser.parse_args()
    cfg = apply_overrides(Config(), args)
    validate_config_values(cfg.data, cfg.model, cfg.train)

    os.makedirs(cfg.train.checkpoint_dir, exist_ok=True)

    # 全局随机种子，保证训练可复现
    random.seed(cfg.train.seed)
    np.random.seed(cfg.train.seed)
    torch.manual_seed(cfg.train.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(cfg.train.seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    train_indices, val_indices = split_episodes(
        cfg.data.data_dir, cfg.train.val_split, seed=cfg.train.seed)
    print(f"Episodes: {len(train_indices)} train, {len(val_indices)} val")

    train_ds = ActionDataset(
        cfg.data.data_dir, cfg.data.history_len, cfg.data.action_dim,
        cfg.data.image_width, cfg.data.image_height, cfg.data.normalize_actions,
        cfg.data.action_stats_path, train_indices, cfg.data.frame_len,
        augment=cfg.train.augment,
        predict_delta=cfg.train.predict_delta, rollout_len=cfg.train.rollout_len,
        imu_dim=cfg.data.imu_dim)
    if cfg.data.normalize_actions and not cfg.data.action_stats_path:
        # 归一化统计量必须只用训练集计算，再供验证集复用，否则 val loss 不可信
        stats_path = os.path.join(cfg.train.checkpoint_dir, "action_stats.npy")
        np.save(stats_path, train_ds.get_action_stats())
        print(f"Action stats saved to: {stats_path}")
        # 只存裸文件名：推理端按 checkpoint 目录解析相对路径
        cfg.data.action_stats_path = "action_stats.npy"
    val_ds = ActionDataset(
        cfg.data.data_dir, cfg.data.history_len, cfg.data.action_dim,
        cfg.data.image_width, cfg.data.image_height, cfg.data.normalize_actions,
        cfg.data.action_stats_path, val_indices, cfg.data.frame_len,
        predict_delta=cfg.train.predict_delta, rollout_len=cfg.train.rollout_len,
        imu_dim=cfg.data.imu_dim)

    print(f"Samples: {len(train_ds)} train, {len(val_ds)} val")
    if len(train_ds) == 0:
        raise ValueError(
            f"训练集为空（0 样本）。请检查 data_dir={cfg.data.data_dir} "
            f"及 episode 数据完整性。")
    if len(val_ds) == 0:
        print("WARNING: validation set is empty (episodes too short/few); "
              "model selection is disabled, last epoch will be saved.")

    loader_gen = torch.Generator()
    loader_gen.manual_seed(cfg.train.seed)
    train_loader = DataLoader(train_ds, batch_size=cfg.train.batch_size,
                              shuffle=True, num_workers=cfg.train.num_workers,
                              generator=loader_gen, collate_fn=_collate)
    val_loader = DataLoader(val_ds, batch_size=cfg.train.batch_size,
                            shuffle=False, num_workers=cfg.train.num_workers,
                            collate_fn=_collate)

    model = ActionPredictor(cfg.model, cfg.data).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model params: {n_params:,}")

    if args.init_from:
        init_ckpt = torch.load(args.init_from, map_location="cpu", weights_only=True)
        warn_init_from_mismatch(init_ckpt.get("config", {}), cfg)
        missing, unexpected = model.load_state_dict(
            init_ckpt["model_state_dict"], strict=False)
        if missing:
            print(f"  init-from missing keys: {missing}")
        if unexpected:
            print(f"  init-from unexpected keys: {unexpected}")
        print(f"Loaded pretrained weights from: {args.init_from}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.train.lr,
                                  weight_decay=cfg.train.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=cfg.train.epochs)

    best_val_loss = float("inf")
    patience_counter = 0
    start_epoch = 1
    best_path = os.path.join(cfg.train.checkpoint_dir, "best.pt")
    last_path = os.path.join(cfg.train.checkpoint_dir, "last.pt")

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
        print(f"\nEpoch {epoch}/{cfg.train.epochs}  lr={optimizer.param_groups[0]['lr']:.6f}")
        train_loss = train_one_epoch(model, train_loader, optimizer, device,
                                     cfg.train.max_grad_norm,
                                     rollout_len=cfg.train.rollout_len,
                                     ss_prob=cfg.train.ss_prob,
                                     predict_delta=cfg.train.predict_delta)
        val_loss = validate(model, val_loader, device,
                            rollout_len=cfg.train.rollout_len,
                            predict_delta=cfg.train.predict_delta)
        scheduler.step()
        print(f"  train_loss={train_loss:.6f}  val_loss={val_loss:.6f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_loss": val_loss,
                "config": cfg.to_dict(),
            }, best_path)
            inference_path = os.path.join(cfg.train.checkpoint_dir, "best_inference.pt")
            torch.save({
                "model_state_dict": model.state_dict(),
                "config": cfg.to_dict(),
            }, inference_path)
            print(f"  -> saved best (val_loss={val_loss:.6f})")
        else:
            patience_counter += 1
            if patience_counter >= cfg.train.patience:
                print(f"\nEarly stopping at epoch {epoch} (patience={cfg.train.patience})")
                break

        # 每 epoch 保存 last.pt，支持 --resume 断点续训
        torch.save({
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
            "best_val_loss": best_val_loss,
            "patience_counter": patience_counter,
            "config": cfg.to_dict(),
        }, last_path)

    print(f"\nTraining done. Best val_loss={best_val_loss:.6f}")

    if best_val_loss == float("inf"):
        # 验证集为空时无 best 可选，保底保存最终权重
        torch.save({
            "model_state_dict": model.state_dict(),
            "config": cfg.to_dict(),
        }, os.path.join(cfg.train.checkpoint_dir, "best_inference.pt"))
        print("WARNING: saved final weights as best_inference.pt (no valid validation loss)")
    else:
        print(f"Best model saved to: {best_path}")

    if args.save_config:
        with open(args.save_config, "w") as f:
            json.dump(cfg.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"Config saved to: {args.save_config}")


if __name__ == "__main__":
    main()
