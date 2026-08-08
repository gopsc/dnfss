import argparse
import json
import os
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from config import parse_checkpoint_config
from model import ActionPredictor


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

        self._transform = transforms.Compose([
            transforms.Resize((self._data_cfg.image_size, self._data_cfg.image_size)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

        self._action_mean = None
        self._action_std = None
        if self._data_cfg.normalize_actions:
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
            self._action_mean = stats[0].astype(np.float32)
            self._action_std = stats[1].astype(np.float32)

    @torch.no_grad()
    def predict(self, image: np.ndarray, action_history: np.ndarray) -> np.ndarray:
        expected_shape = (self._data_cfg.history_len, self._data_cfg.action_dim)
        if action_history.shape != expected_shape:
            raise ValueError(
                f"action_history 形状应为 {expected_shape}，实际为 {action_history.shape}")
        img = Image.fromarray(image) if isinstance(image, np.ndarray) else image
        img_tensor = self._transform(img).unsqueeze(0).to(self._device)
        history = torch.tensor(action_history, dtype=torch.float32)
        if self._action_mean is not None:
            history = (history - self._action_mean) / self._action_std
        history = history.unsqueeze(0).to(self._device)
        pred = self._model(img_tensor, history).squeeze(0).cpu().numpy()
        if self._action_mean is not None:
            pred = pred * self._action_std + self._action_mean
        return pred


def parse_action_history(s: str, action_dim: int) -> np.ndarray:
    steps = s.split(":")
    history = []
    for step in steps:
        values = [float(v) for v in step.split(",")]
        if len(values) != action_dim:
            raise ValueError(
                f"Expected {action_dim} values per step, got {len(values)}")
        history.append(values)
    return np.array(history, dtype=np.float32)


def main():
    parser = argparse.ArgumentParser(description="actpred inference")
    parser.add_argument("--model", required=True, help="checkpoint path")
    parser.add_argument("--image", required=True, help="image file path")
    parser.add_argument("--action-history", required=True,
                        help="冒号分隔各步，每步 action_dim 个值，共 history_len 步。"
                             "e.g. '0,0,0,0:1,0,0,0' (action_dim=4, history_len=2)")
    parser.add_argument("--action-dim", type=int, default=None,
                        help="override action dim (auto-detected from checkpoint if omitted)")
    parser.add_argument("--output-format", choices=["json", "raw"], default="json")
    parser.add_argument("--device", type=str, default=None)
    args = parser.parse_args()

    predictor = ActionPredictorInference(args.model, args.device)
    action_dim = args.action_dim or predictor.action_dim

    image = Image.open(args.image).convert("RGB")
    history = parse_action_history(args.action_history, action_dim)
    predicted = predictor.predict(np.array(image), history)

    if args.output_format == "json":
        print(json.dumps({"predicted_action": predicted.tolist()}, indent=2))
    else:
        print(" ".join(f"{v:.4f}" for v in predicted))


if __name__ == "__main__":
    main()
