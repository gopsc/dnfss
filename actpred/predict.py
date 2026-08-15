import argparse
import json
import os
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from config import parse_checkpoint_config
from imu_norm import normalize_imu
from action_norm import (normalize_action, denormalize_action,
                      is_range_mode)
from bins import decode as bin_decode
from model import ActionPredictor
from utils import parse_action_history


class ActionPredictorInference:
    def __init__(self, checkpoint_path: str, device: str = None):
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        cfg = parse_checkpoint_config(checkpoint["config"])
        self._data_cfg = cfg.data
        self._model_cfg = cfg.model

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = torch.device(device)

        self._model = ActionPredictor(
            self._model_cfg, self._data_cfg, pretrained=False).to(self._device)
        self._model.load_state_dict(checkpoint["model_state_dict"])
        self._model.eval()

        self.action_dim = self._data_cfg.action_dim
        self.history_len = self._data_cfg.history_len
        self.frame_len = self._data_cfg.frame_len
        # R2 方案一：imu_dim>0 时 predict 接受 imu= 原始姿态向量（内部归一化）。
        # pybot 闭环鸭子类型约定：此属性为 None 时按旧两参调用，
        # 故 imu_dim=0（旧 checkpoint）必须映射为 None。
        self.imu_dim = self._data_cfg.imu_dim or None
        self._onehot = self._data_cfg.onehot
        # R1-C 残差预测：checkpoint 记录 predict_delta=True 时，模型输出 Δ̂，
        # 需在归一化空间加回 a_{t-1}（history 末步）得到绝对动作。
        # 旧 checkpoint 无该字段，getattr 默认 False，行为与旧版一致。
        self._predict_delta = getattr(cfg.train, "predict_delta", False)

        self._transform = transforms.Compose([
            transforms.Resize((self._data_cfg.image_height,
                               self._data_cfg.image_width)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

        self._action_mean = None
        self._action_std = None
        # fixed_range / log_range：固定物理范围 → [0,1]（免 stats 文件）；
        # 旧 checkpoint 无 normalize_mode 键，parse_checkpoint_config
        # 默认 "zscore"，行为不变
        self._range_mode = (self._data_cfg.normalize_mode
                            if (self._data_cfg.normalize_actions
                                and is_range_mode(self._data_cfg.normalize_mode))
                            else None)
        if self._data_cfg.normalize_actions and not self._range_mode:
            # 优先使用 checkpoint config 记录的 stats 路径（相对路径按 checkpoint 目录解析），
            # 旧 checkpoint 无该字段时回退到 checkpoint 同目录的 action_stats.npy
            stats_path = self._data_cfg.action_stats_path
            if stats_path:
                if not os.path.isabs(stats_path):
                    stats_path = os.path.join(
                        os.path.dirname(os.path.abspath(checkpoint_path)), stats_path)
            else:
                stats_path = os.path.join(
                    os.path.dirname(os.path.abspath(checkpoint_path)),
                    "action_stats.npy")
            if not os.path.exists(stats_path):
                raise FileNotFoundError(
                    f"模型训练时启用了动作归一化，但未找到统计文件: {stats_path}")
            stats = np.load(stats_path)
            if stats.shape != (2, self._data_cfg.action_dim):
                raise ValueError(
                    f"action_stats.npy 形状应为 (2, {self._data_cfg.action_dim})，"
                    f"实际为 {stats.shape}，请检查文件: {stats_path}")
            self._action_mean = torch.from_numpy(stats[0].astype(np.float32)).to(self._device)
            self._action_std = torch.from_numpy(stats[1].astype(np.float32)).to(self._device)

    @torch.no_grad()
    def predict(self, images, action_history: np.ndarray,
                imu: np.ndarray = None) -> np.ndarray:
        """images: 长度为 frame_len 的 RGB 图像序列（旧→新，末帧为当前帧）
        imu: 可选原始姿态向量（roll,pitch,ax,ay,az,gx,gy,gz，imu_dim>0 时
             生效，内部按物理量程归一化；None 等价零向量 = 无姿态先验）
        """
        if not isinstance(images, (list, tuple)):
            images = [images]
        if len(images) != self._data_cfg.frame_len:
            raise ValueError(
                f"images 应为 {self._data_cfg.frame_len} 帧，实际为 {len(images)} 帧。"
                f"冷启动时可重复传入同一帧。")
        expected_shape = (self._data_cfg.history_len, self._data_cfg.action_dim)
        if action_history.shape != expected_shape:
            raise ValueError(
                f"action_history 形状应为 {expected_shape}，实际为 {action_history.shape}")
        frames = []
        for image in images:
            img = Image.fromarray(image) if isinstance(image, np.ndarray) else image
            frames.append(self._transform(img))
        img_tensor = torch.stack(frames).unsqueeze(0).to(self._device)  # (1, F, 3, S, S)
        history = torch.tensor(action_history, dtype=torch.float32)
        if self._range_mode:
            history = torch.from_numpy(
                normalize_action(action_history, self.action_dim,
                                 mode=self._range_mode))
        elif self._action_mean is not None:
            history = (history - self._action_mean) / self._action_std
        history = history.unsqueeze(0).to(self._device)
        imu_tensor = None
        if self._data_cfg.imu_dim > 0:
            imu_tensor = torch.from_numpy(
                normalize_imu(imu, self._data_cfg.imu_dim)
            ).unsqueeze(0).to(self._device)
        pred = self._model(img_tensor, history, imu_tensor).squeeze(0)  # 归一化空间
        if self._onehot:
            # 多头 logits → 期望值解码 → 原始量纲（箱生活在原始空间）
            result = bin_decode(pred.cpu().numpy(), self.action_dim)
            if not np.all(np.isfinite(result)):
                result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
            return result
        if self._predict_delta:
            # 残差：Δ̂ 加回 a_{t-1}（history 末步，同为归一化空间）→ 绝对动作
            pred = history[0, -1] + pred
        if self._range_mode:
            result = denormalize_action(pred.cpu().numpy(), self.action_dim,
                                        mode=self._range_mode)
        else:
            if self._action_mean is not None:
                pred = pred * self._action_std + self._action_mean
            result = pred.cpu().numpy()
        if not np.all(np.isfinite(result)):
            print("WARNING: predict output contains NaN/Inf, replacing with zeros")
            result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
        return result


def main():
    parser = argparse.ArgumentParser(description="actpred inference")
    parser.add_argument("--model", required=True, help="checkpoint path")
    parser.add_argument("--images", required=True, nargs="+",
                        help="按时间顺序（旧→新）传入 frame_len 个图像路径；"
                             "冷启动时可重复传同一帧路径")
    parser.add_argument("--action-history", required=True,
                        help="冒号分隔各步，每步 action_dim 个值，共 history_len 步。"
                             "e.g. '0,0,0,0:1,0,0,0' (action_dim=4, history_len=2)")
    parser.add_argument("--imu", type=str, default=None,
                        help="可选姿态向量（逗号分隔，原始量纲：roll,pitch 度 / "
                             "ax..gz int16 原始值）；模型 imu_dim>0 且缺省时按零向量")
    parser.add_argument("--output-format", choices=["json", "raw"], default="json")
    parser.add_argument("--device", type=str, default=None)
    args = parser.parse_args()

    predictor = ActionPredictorInference(args.model, args.device)
    action_dim = predictor.action_dim

    if len(args.images) != predictor.frame_len:
        raise SystemExit(
            f"--images 需传入 {predictor.frame_len} 个路径（旧→新），"
            f"实际为 {len(args.images)} 个。冷启动时可重复传同一帧路径。")
    images = [np.array(Image.open(p).convert("RGB")) for p in args.images]
    history = parse_action_history(args.action_history, action_dim)
    imu = None
    if args.imu is not None:
        imu = np.array([float(v) for v in args.imu.split(",")], dtype=np.float32)
    predicted = predictor.predict(images, history, imu=imu)

    if args.output_format == "json":
        print(json.dumps({"predicted_action": predicted.tolist()}, indent=2))
    else:
        print(" ".join(f"{v:.4f}" for v in predicted))


if __name__ == "__main__":
    main()
