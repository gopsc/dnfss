import os
import random
import re
import numpy as np
from pathlib import Path
from PIL import Image

import torch
from torch.utils.data import Dataset
from torchvision import transforms
from torchvision.transforms import functional as TF

from imu_norm import imu_scales
from action_norm import normalize_action, is_range_mode


def _natural_key(p):
    """自然排序键：'1.jpg' < '2.jpg' < ... < '10.jpg'"""
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', p.name)]


class ActionDataset(Dataset):
    def __init__(self, data_dir: str, history_len: int, action_dim: int,
                 image_width: int = 640, image_height: int = 480,
                 normalize_actions: bool = False,
                 action_stats_path: str = None, episode_indices: list = None,
                 frame_len: int = 3, augment: bool = False,
                 predict_delta: bool = False, rollout_len: int = 1,
                 imu_dim: int = 0, normalize_mode: str = "zscore"):
        self.history_len = history_len
        self.action_dim = action_dim
        # 固定范围类归一化（fixed_range / log_range，免 stats 文件）；
        # 仅 normalize_actions=True 生效
        self._range_mode = (normalize_mode
                            if (normalize_actions and is_range_mode(normalize_mode))
                            else None)
        # R2 方案一：>0 时样本带 "imu" 键（归一化姿态向量，缺失 episode 补零）
        self.imu_dim = imu_dim
        self.image_width = image_width
        self.image_height = image_height
        self.frame_len = frame_len
        self.augment = augment
        # R1-C 残差预测：target 改为 Δ=actions[t]-actions[t-1]（归一化空间内求差）
        self.predict_delta = predict_delta
        # R1-B 多步展开：rollout_len>1 时每个样本返回 K 步连续 (images,history,target)
        self.rollout_len = max(1, rollout_len)

        self.transform = transforms.Compose([
            # Resize 不保持宽高比（直接拉伸）是有意为之：推理端
            # （predict/onnx_infer/_onnx_validate）使用完全相同的预处理，
            # 训练/推理口径必须一致；改为 letterbox 会使既有 checkpoint
            # 输入分布失配，如需变更须训练/推理同步修改并重训（P2-d）
            transforms.Resize((image_height, image_width)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

        episodes = ActionDataset._valid_episode_dirs(data_dir)
        if episode_indices is not None:
            episodes = [episodes[i] for i in episode_indices]

        # 样本存 (image_files, actions, t_start)：t_start 是 rollout 第一步的目标时刻，
        # __getitem__ 按 rollout_len 展开 K 步。存原始数组引用避免重复加载/拷贝。
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
            imus = self._load_imu(ep_dir, n)
            # 第 k 步（k=0..K-1）目标时刻 t=t_start+k：帧 [t-frame_len+1, t]、
            # 历史 [t-history_len, t)、目标 actions[t]。t_start 下界保证第 0 步窗口完整，
            # 上界保证第 K-1 步目标不越界。
            start = max(history_len, frame_len - 1)
            for t_start in range(start, n - (self.rollout_len - 1)):
                self.samples.append((image_files, actions, imus, t_start))

        self._action_mean = None
        self._action_std = None
        if normalize_actions and not self._range_mode:
            if action_stats_path:
                if not os.path.exists(action_stats_path):
                    raise FileNotFoundError(
                        f"指定的 action_stats_path 不存在: {action_stats_path}")
                stats = np.load(action_stats_path)
                if stats.shape != (2, self.action_dim):
                    raise ValueError(
                        f"action stats 形状应为 (2, {self.action_dim})，"
                        f"实际为 {stats.shape}: {action_stats_path}")
                self._action_mean = stats[0]
                self._action_std = stats[1]
            else:
                if not self.samples:
                    raise ValueError(
                        f"数据集为空（0 样本），无法计算归一化统计量。"
                        f"请检查 data_dir={data_dir} 中的 episode 数据。")
                # 归一化统计基于绝对动作（与 Δ 无关）：覆盖所有样本 rollout 范围内
                # 出现的 history 与 target 动作，保持与旧版一致的统计口径。
                all_actions = np.stack([
                    s[1][s[3] - self.history_len: s[3] + self.rollout_len]
                    for s in self.samples
                ]).reshape(-1, self.action_dim)
                self._action_mean = all_actions.mean(axis=0)
                self._action_std = all_actions.std(axis=0) + 1e-8

    def __len__(self):
        return len(self.samples)

    def _norm(self, a):
        """动作归一化（numpy → torch 前在 numpy 空间做，保持 float32）

        fixed_range / log_range：固定物理范围 → [0,1]（clamp 防御，免 stats）；
        否则 z-score（_action_mean 为 None 时直通原始值）。
        """
        if self._range_mode:
            return normalize_action(a, self.action_dim, mode=self._range_mode)
        if self._action_mean is None:
            return a
        return (a - self._action_mean) / self._action_std

    def _load_frames(self, image_files, t):
        """加载时刻 t 的 F 帧窗口 [t-frame_len+1, t]（旧→新），augment 对整个窗口一致"""
        paths = image_files[t - self.frame_len + 1: t + 1]
        if self.augment:
            bf = random.uniform(0.8, 1.2)
            cf = random.uniform(0.8, 1.2)
            sf = random.uniform(0.8, 1.2)
            hf = random.uniform(-0.05, 0.05)
        frames = []
        for p in paths:
            image = Image.open(p).convert("RGB")
            if self.augment:
                image = TF.adjust_brightness(image, bf)
                image = TF.adjust_contrast(image, cf)
                image = TF.adjust_saturation(image, sf)
                image = TF.adjust_hue(image, hf)
            frames.append(self.transform(image))
        return torch.stack(frames)  # (F, 3, H, W)

    def _step_target(self, actions, t):
        """时刻 t 的监督目标（归一化空间）；predict_delta 时为 Δ=actions[t]-actions[t-1]"""
        if self.predict_delta:
            return self._norm(actions[t]) - self._norm(actions[t - 1])
        return self._norm(actions[t])

    def _load_imu(self, ep_dir, n):
        """加载 episode 的 imu.npy 并按物理量程归一化（R2）

        imu_dim=0（未启用）或文件缺失 → None（样本侧补零向量，等价无姿态
        先验，混合数据集可训）。形状 pad/truncate 到 (n, imu_dim)。
        """
        if self.imu_dim <= 0:
            return None
        path = ep_dir / "imu.npy"
        if not path.exists():
            print(f"WARNING: {ep_dir} 无 imu.npy，该 episode 姿态按零向量处理")
            return None
        imus = np.load(path).astype(np.float32)
        if imus.ndim != 2:
            raise ValueError(
                f"{path} 形状应为 (T, imu_dim)，实际为 {imus.shape}")
        if imus.shape[1] != self.imu_dim:
            fixed = np.zeros((imus.shape[0], self.imu_dim), dtype=np.float32)
            m = min(imus.shape[1], self.imu_dim)
            fixed[:, :m] = imus[:, :m]
            imus = fixed
        if len(imus) < n:                       # 与帧数对齐：不足补零
            imus = np.concatenate([
                imus, np.zeros((n - len(imus), self.imu_dim), dtype=np.float32)])
        return imus[:n] / imu_scales(self.imu_dim)

    def _imu_at(self, imus, t):
        """时刻 t 的归一化姿态向量（与最新帧对齐）；无数据 → 零向量"""
        if imus is None:
            return torch.zeros(self.imu_dim, dtype=torch.float32)
        return torch.tensor(imus[t], dtype=torch.float32)

    def __getitem__(self, idx):
        image_files, actions, imus, t_start = self.samples[idx]
        if self.rollout_len == 1:
            # 单步：与旧版输出结构完全一致（imu_dim>0 时附加 "imu" 键）
            images = self._load_frames(image_files, t_start)
            history = torch.tensor(self._norm(
                actions[t_start - self.history_len: t_start]), dtype=torch.float32)
            target = torch.tensor(self._step_target(actions, t_start),
                                  dtype=torch.float32)
            out = {"images": images, "action_history": history, "target": target}
            if self.imu_dim > 0:
                out["imu"] = self._imu_at(imus, t_start)
            return out

        # 多步：返回 K 步序列，scheduled sampling 在 train 循环内按步取用
        steps = []
        for k in range(self.rollout_len):
            t = t_start + k
            step = {
                "images": self._load_frames(image_files, t),
                # 真实历史（scheduled sampling 的真值来源；首步必用）
                "action_history": torch.tensor(
                    self._norm(actions[t - self.history_len: t]), dtype=torch.float32),
                "target": torch.tensor(self._step_target(actions, t), dtype=torch.float32),
                # 上一步真实动作（Δ 残差的基准 a_{t-1}，归一化空间）
                "prev_action": torch.tensor(self._norm(actions[t - 1]), dtype=torch.float32),
            }
            if self.imu_dim > 0:
                step["imu"] = self._imu_at(imus, t)
            steps.append(step)
        return {"steps": steps}

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
