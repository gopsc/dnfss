import os
import re
import numpy as np
from pathlib import Path
from PIL import Image

import torch
from torch.utils.data import Dataset
from torchvision import transforms


def _natural_key(p):
    """自然排序键：'1.jpg' < '2.jpg' < ... < '10.jpg'"""
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', p.name)]


class ActionDataset(Dataset):
    def __init__(self, data_dir: str, history_len: int, action_dim: int,
                 image_size: int = 64, normalize_actions: bool = False,
                 action_stats_path: str = None, episode_indices: list = None,
                 frame_len: int = 3):
        self.history_len = history_len
        self.action_dim = action_dim
        self.image_size = image_size
        self.frame_len = frame_len

        self.transform = transforms.Compose([
            transforms.Resize((image_size, image_size)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

        episodes = ActionDataset._valid_episode_dirs(data_dir)
        if episode_indices is not None:
            episodes = [episodes[i] for i in episode_indices]

        self.samples = []
        for ep_dir in episodes:
            actions_path = ep_dir / "actions.npy"
            images_dir = ep_dir / "images"
            if not actions_path.exists() or not images_dir.is_dir():
                continue
            actions = np.load(actions_path).astype(np.float32)
            if actions.ndim != 2 or actions.shape[1] != action_dim:
                raise ValueError(
                    f"{actions_path} 形状应为 (T, {action_dim})，实际为 {actions.shape}。"
                    f"请检查该 episode 的动作维度与 --action-dim 是否一致。")
            n_frames = len(actions)
            image_files = sorted(list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png")),
                                 key=_natural_key)
            n_images = len(image_files)
            if n_frames != n_images:
                print(f"WARNING: {ep_dir} 动作帧数 {n_frames} 与图像数 {n_images} 不一致，"
                      f"按较少者（{min(n_frames, n_images)}）使用，请检查录制完整性")
            n = min(n_frames, n_images)
            # 每个样本：帧 [t-frame_len+1, t]（旧→新）+ 动作历史 [t-history_len, t) → 动作 t
            start = max(history_len, frame_len - 1)
            for t in range(start, n):
                self.samples.append((image_files[t - frame_len + 1: t + 1],
                                     actions[t - history_len:t], actions[t]))

        self._action_mean = None
        self._action_std = None
        if normalize_actions:
            if action_stats_path:
                if not os.path.exists(action_stats_path):
                    raise FileNotFoundError(
                        f"指定的 action_stats_path 不存在: {action_stats_path}")
                stats = np.load(action_stats_path)
                self._action_mean = stats[0]
                self._action_std = stats[1]
            else:
                if not self.samples:
                    raise ValueError(
                        f"数据集为空（0 样本），无法计算归一化统计量。"
                        f"请检查 data_dir={data_dir} 中的 episode 数据。")
                all_actions = np.stack([s[2] for s in self.samples])
                self._action_mean = all_actions.mean(axis=0)
                self._action_std = all_actions.std(axis=0) + 1e-8

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        image_paths, action_history, target = self.samples[idx]
        frames = []
        for p in image_paths:
            image = Image.open(p).convert("RGB")
            frames.append(self.transform(image))
        images = torch.stack(frames)  # (F, 3, S, S)
        action_history = torch.tensor(action_history, dtype=torch.float32)
        target = torch.tensor(target, dtype=torch.float32)
        if self._action_mean is not None:
            action_history = (action_history - self._action_mean) / self._action_std
            target = (target - self._action_mean) / self._action_std
        return {"images": images, "action_history": action_history, "target": target}

    def get_action_stats(self):
        if self._action_mean is not None:
            return np.stack([self._action_mean, self._action_std])
        return None

    @staticmethod
    def _valid_episode_dirs(data_dir: str) -> list:
        data_path = Path(data_dir)
        return sorted([d for d in data_path.iterdir()
                       if d.is_dir() and (d / "actions.npy").exists() and (d / "images").is_dir()])

    @staticmethod
    def get_episode_count(data_dir: str) -> int:
        # 与 __init__ 的过滤条件保持一致，避免 split_episodes 索引错位
        return len(ActionDataset._valid_episode_dirs(data_dir))


class DataRecorder:
    def __init__(self, output_dir: str, action_dim: int):
        self.output_dir = Path(output_dir)
        self.action_dim = action_dim
        self._frame_idx = 0
        self._actions = []
        ep_idx = 0
        for d in self.output_dir.glob("episode_*"):
            try:
                ep_idx = max(ep_idx, int(d.name.split("_")[1]) + 1)
            except (ValueError, IndexError):
                pass
        # 原子创建目录，避免并发 Recorder 分配到同一 episode
        while True:
            self.episode_dir = self.output_dir / f"episode_{ep_idx:04d}"
            self.images_dir = self.episode_dir / "images"
            try:
                self.images_dir.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                ep_idx += 1

    def record(self, image: np.ndarray, action: np.ndarray):
        if action.shape != (self.action_dim,):
            raise ValueError(
                f"action 维度应为 ({self.action_dim},)，实际为 {action.shape}")
        img = Image.fromarray(image)
        img.save(self.images_dir / f"{self._frame_idx:04d}.jpg", quality=90)
        self._actions.append(action.astype(np.float32))
        self._frame_idx += 1

    def flush(self):
        if self._actions:
            actions = np.array(self._actions, dtype=np.float32)
            np.save(self.episode_dir / "actions.npy", actions)
        return str(self.episode_dir)
