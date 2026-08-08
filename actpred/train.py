import json
import os
import numpy as np
import torch
from torch.utils.data import DataLoader, Subset
from tqdm import tqdm

from config import Config, build_argparser, apply_overrides
from model import ActionPredictor
from dataset import ActionDataset


def split_episodes(data_dir: str, val_ratio: float, seed: int = 42):
    n = ActionDataset.get_episode_count(data_dir)
    indices = list(range(n))
    rng = np.random.RandomState(seed)
    rng.shuffle(indices)
    val_count = max(1, int(n * val_ratio))
    return indices[val_count:], indices[:val_count]


def train_one_epoch(model, loader, optimizer, device):
    model.train()
    total_loss = 0.0
    count = 0
    for batch in tqdm(loader, desc="  train", leave=False):
        image = batch["image"].to(device)
        history = batch["action_history"].to(device)
        target = batch["target"].to(device)
        optimizer.zero_grad()
        pred = model(image, history)
        loss = torch.nn.functional.mse_loss(pred, target)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * image.size(0)
        count += image.size(0)
    return total_loss / max(count, 1)


@torch.no_grad()
def validate(model, loader, device):
    model.eval()
    total_loss = 0.0
    count = 0
    for batch in tqdm(loader, desc="  val  ", leave=False):
        image = batch["image"].to(device)
        history = batch["action_history"].to(device)
        target = batch["target"].to(device)
        pred = model(image, history)
        loss = torch.nn.functional.mse_loss(pred, target)
        total_loss += loss.item() * image.size(0)
        count += image.size(0)
    return total_loss / max(count, 1)


def main():
    parser = build_argparser("actpred training")
    args = parser.parse_args()
    cfg = apply_overrides(Config(), args)

    os.makedirs(cfg.train.checkpoint_dir, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    train_indices, val_indices = split_episodes(
        cfg.data.data_dir, cfg.train.val_split)
    print(f"Episodes: {len(train_indices)} train, {len(val_indices)} val")

    train_ds = ActionDataset(
        cfg.data.data_dir, cfg.data.history_len, cfg.data.action_dim,
        cfg.data.image_size, cfg.data.normalize_actions,
        cfg.data.action_stats_path, train_indices)
    val_ds = ActionDataset(
        cfg.data.data_dir, cfg.data.history_len, cfg.data.action_dim,
        cfg.data.image_size, cfg.data.normalize_actions,
        cfg.data.action_stats_path, val_indices)

    print(f"Samples: {len(train_ds)} train, {len(val_ds)} val")

    train_loader = DataLoader(train_ds, batch_size=cfg.train.batch_size,
                              shuffle=True, num_workers=cfg.train.num_workers)
    val_loader = DataLoader(val_ds, batch_size=cfg.train.batch_size,
                            shuffle=False, num_workers=cfg.train.num_workers)

    model = ActionPredictor(cfg.model, cfg.data).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model params: {n_params:,}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.train.lr,
                                  weight_decay=cfg.train.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=cfg.train.epochs)

    best_val_loss = float("inf")
    patience_counter = 0
    best_path = os.path.join(cfg.train.checkpoint_dir, "best.pt")

    for epoch in range(1, cfg.train.epochs + 1):
        print(f"\nEpoch {epoch}/{cfg.train.epochs}  lr={optimizer.param_groups[0]['lr']:.6f}")
        train_loss = train_one_epoch(model, train_loader, optimizer, device)
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

    print(f"\nTraining done. Best val_loss={best_val_loss:.6f}")
    print(f"Best model saved to: {best_path}")

    action_stats = train_ds.get_action_stats()
    if action_stats is not None:
        stats_path = os.path.join(cfg.train.checkpoint_dir, "action_stats.npy")
        np.save(stats_path, action_stats)
        print(f"Action stats saved to: {stats_path}")

    if args.save_config:
        with open(args.save_config, "w") as f:
            json.dump(cfg.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"Config saved to: {args.save_config}")


if __name__ == "__main__":
    main()
