import json
import os
import random
import numpy as np
import torch
from torch.utils.data import DataLoader, Subset
from tqdm import tqdm

from config import Config, build_argparser, apply_overrides, validate_config_values
from model import ActionPredictor
from dataset import ActionDataset


def split_episodes(data_dir: str, val_ratio: float, seed: int = 42):
    n = ActionDataset.get_episode_count(data_dir)
    indices = list(range(n))
    rng = np.random.RandomState(seed)
    rng.shuffle(indices)
    val_count = max(1, int(n * val_ratio))
    return indices[val_count:], indices[:val_count]


def train_one_epoch(model, loader, optimizer, device, max_grad_norm: float = None):
    model.train()
    total_loss = 0.0
    count = 0
    for batch in tqdm(loader, desc="  train", leave=False):
        images = batch["images"].to(device)
        history = batch["action_history"].to(device)
        target = batch["target"].to(device)
        optimizer.zero_grad()
        pred = model(images, history)
        loss = torch.nn.functional.mse_loss(pred, target)
        loss.backward()
        if max_grad_norm:
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm)
        optimizer.step()
        total_loss += loss.item() * images.size(0)
        count += images.size(0)
    return total_loss / max(count, 1)


@torch.no_grad()
def validate(model, loader, device):
    model.eval()
    total_loss = 0.0
    count = 0
    for batch in tqdm(loader, desc="  val  ", leave=False):
        images = batch["images"].to(device)
        history = batch["action_history"].to(device)
        target = batch["target"].to(device)
        pred = model(images, history)
        loss = torch.nn.functional.mse_loss(pred, target)
        total_loss += loss.item() * images.size(0)
        count += images.size(0)
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
        cfg.data.action_stats_path, train_indices, cfg.data.frame_len)
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
        cfg.data.action_stats_path, val_indices, cfg.data.frame_len)

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
                              generator=loader_gen)
    val_loader = DataLoader(val_ds, batch_size=cfg.train.batch_size,
                            shuffle=False, num_workers=cfg.train.num_workers)

    model = ActionPredictor(cfg.model, cfg.data).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model params: {n_params:,}")

    if args.init_from:
        init_ckpt = torch.load(args.init_from, map_location="cpu", weights_only=True)
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
                                     cfg.train.max_grad_norm)
        val_loss = validate(model, val_loader, device)
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
