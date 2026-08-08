import argparse
import json
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from config import Config, ModelConfig, DataConfig
from model import ActionPredictor


class ActionPredictorInference:
    def __init__(self, checkpoint_path: str, device: str = None):
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        cfg_dict = checkpoint["config"]
        self._data_cfg = DataConfig(**cfg_dict["data"])
        self._model_cfg = ModelConfig(**cfg_dict["model"])

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = torch.device(device)

        self._model = ActionPredictor(self._model_cfg, self._data_cfg).to(self._device)
        self._model.load_state_dict(checkpoint["model_state_dict"])
        self._model.eval()

        self._transform = transforms.Compose([
            transforms.Resize((self._data_cfg.image_size, self._data_cfg.image_size)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

        self._action_mean = None
        self._action_std = None
        stats_path = checkpoint_path.rsplit("/", 1)[0] + "/action_stats.npy"
        try:
            stats = np.load(stats_path)
            self._action_mean = stats[0]
            self._action_std = stats[1]
        except (FileNotFoundError, ValueError):
            pass

    @torch.no_grad()
    def predict(self, image: np.ndarray, action_history: np.ndarray) -> np.ndarray:
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
        assert len(values) == action_dim, \
            f"Expected {action_dim} values per step, got {len(values)}"
        history.append(values)
    return np.array(history, dtype=np.float32)


def main():
    parser = argparse.ArgumentParser(description="actpred inference")
    parser.add_argument("--model", required=True, help="checkpoint path")
    parser.add_argument("--image", required=True, help="image file path")
    parser.add_argument("--action-history", required=True,
                        help="colon-separated action vectors, e.g. '45,0,45,0:45,10,45,10'")
    parser.add_argument("--action-dim", type=int, default=None,
                        help="override action dim (auto-detected from checkpoint if omitted)")
    parser.add_argument("--output-format", choices=["json", "raw"], default="json")
    parser.add_argument("--device", type=str, default=None)
    args = parser.parse_args()

    predictor = ActionPredictorInference(args.model, args.device)
    action_dim = args.action_dim or predictor._data_cfg.action_dim

    image = Image.open(args.image).convert("RGB")
    history = parse_action_history(args.action_history, action_dim)
    predicted = predictor.predict(np.array(image), history)

    if args.output_format == "json":
        print(json.dumps({"predicted_action": predicted.tolist()}, indent=2))
    else:
        print(" ".join(f"{v:.4f}" for v in predicted))


if __name__ == "__main__":
    main()
