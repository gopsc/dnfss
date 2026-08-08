import os
import numpy as np
from pathlib import Path
from PIL import Image

import torch
from torch.utils.data import Dataset
from torchvision import transforms


class ActionDataset(Dataset):
    def __init__(self, data_dir: str, history_len: int, action_dim: int,
                 image_size: int = 64, normalize_actions: bool = False,
                 action_stats_path: str = None, episode_indices: list = None):
        self.history_len = history_len
        self.action_dim = action_dim
        self.image_size = image_size

        self.transform = transforms.Compose([
            transforms.Resize((image_size, image_size)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

        data_path = Path(data_dir)
        episodes = sorted([d for d in data_path.iterdir() if d.is_dir()])
        if episode_indices is not None:
            episodes = [episodes[i] for i in episode_indices]

        self.samples = []
        for ep_dir in episodes:
            actions_path = ep_dir / "actions.npy"
            images_dir = ep_dir / "images"
            if not actions_path.exists() or not images_dir.is_dir():
                continue
            actions = np.load(actions_path).astype(np.float32)
            n_frames = len(actions)
            image_files = sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.png"))
            n_images = len(image_files)
            n = min(n_frames, n_images)
            for t in range(history_len, n):
                self.samples.append((image_files[t], actions[t - history_len:t], actions[t]))

        self._action_mean = None
        self._action_std = None
        if normalize_actions:
            if action_stats_path and os.path.exists(action_stats_path):
                stats = np.load(action_stats_path)
                self._action_mean = stats[0]
                self._action_std = stats[1]
            else:
                all_actions = np.stack([s[2] for s in self.samples])
                self._action_mean = all_actions.mean(axis=0)
                self._action_std = all_actions.std(axis=0) + 1e-8

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        image_path, action_history, target = self.samples[idx]
        image = Image.open(image_path).convert("RGB")
        image = self.transform(image)
        action_history = torch.tensor(action_history, dtype=torch.float32)
        target = torch.tensor(target, dtype=torch.float32)
        if self._action_mean is not None:
            action_history = (action_history - self._action_mean) / self._action_std
            target = (target - self._action_mean) / self._action_std
        return {"image": image, "action_history": action_history, "target": target}

    def get_action_stats(self):
        if self._action_mean is not None:
            return np.stack([self._action_mean, self._action_std])
        return None

    @staticmethod
    def get_episode_count(data_dir: str) -> int:
        data_path = Path(data_dir)
        return len([d for d in data_path.iterdir() if d.is_dir()])


class DataRecorder:
    def __init__(self, output_dir: str, action_dim: int):
        self.output_dir = Path(output_dir)
        self.action_dim = action_dim
        self._frame_idx = 0
        self._actions = []
        ep_idx = 0
        while (self.output_dir / f"episode_{ep_idx:04d}").exists():
            ep_idx += 1
        self.episode_dir = self.output_dir / f"episode_{ep_idx:04d}"
        self.images_dir = self.episode_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def record(self, image: np.ndarray, action: np.ndarray):
        img = Image.fromarray(image)
        img.save(self.images_dir / f"{self._frame_idx:04d}.jpg", quality=90)
        self._actions.append(action.astype(np.float32))
        self._frame_idx += 1

    def flush(self):
        if self._actions:
            actions = np.array(self._actions, dtype=np.float32)
            np.save(self.episode_dir / "actions.npy", actions)
        return str(self.episode_dir)
