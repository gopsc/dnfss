import argparse
import torch
from config import DataConfig, ModelConfig
from model import ActionPredictor


def main():
    parser = argparse.ArgumentParser(description="Export actpred model to ONNX")
    parser.add_argument("--model", required=True, help="checkpoint path (best_inference.pt)")
    parser.add_argument("--output", default="model.onnx", help="output ONNX file path")
    args = parser.parse_args()

    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    cfg_dict = checkpoint["config"]
    data_cfg = DataConfig(**cfg_dict["data"])
    model_cfg = ModelConfig(**cfg_dict["model"])

    model = ActionPredictor(model_cfg, data_cfg)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    dummy_image = torch.randn(1, 3, data_cfg.image_size, data_cfg.image_size)
    dummy_history = torch.randn(1, data_cfg.history_len, data_cfg.action_dim)

    torch.onnx.export(
        model, (dummy_image, dummy_history),
        args.output,
        input_names=["image", "action_history"],
        output_names=["predicted_action"],
        dynamic_axes={
            "image": {0: "batch"},
            "action_history": {0: "batch"},
            "predicted_action": {0: "batch"},
        },
        opset_version=17,
    )
    print(f"ONNX model exported to: {args.output}")


if __name__ == "__main__":
    main()
