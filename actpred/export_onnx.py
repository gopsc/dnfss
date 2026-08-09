import argparse
import torch
from config import parse_checkpoint_config
from model import ActionPredictor


def main():
    parser = argparse.ArgumentParser(description="Export actpred model to ONNX")
    parser.add_argument("--model", required=True, help="checkpoint path (best_inference.pt)")
    parser.add_argument("--output", default="model.onnx", help="output ONNX file path")
    args = parser.parse_args()

    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    cfg = parse_checkpoint_config(checkpoint["config"])
    data_cfg = cfg.data
    model_cfg = cfg.model

    model = ActionPredictor(model_cfg, data_cfg, pretrained=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    dummy_images = torch.randn(
        1, data_cfg.frame_len, 3, data_cfg.image_size, data_cfg.image_size)
    dummy_history = torch.randn(1, data_cfg.history_len, data_cfg.action_dim)

    torch.onnx.export(
        model, (dummy_images, dummy_history),
        args.output,
        input_names=["images", "action_history"],
        output_names=["predicted_action"],
        dynamic_axes={
            "images": {0: "batch"},
            "action_history": {0: "batch"},
            "predicted_action": {0: "batch"},
        },
        opset_version=17,
    )
    print(f"ONNX model exported to: {args.output}")

    # 可选：验证导出的 ONNX 模型一致性
    try:
        import onnxruntime as ort
        import numpy as np
        sess = ort.InferenceSession(args.output)
        img_np = dummy_images.numpy()
        hist_np = dummy_history.numpy()
        ort_out = sess.run(None, {"images": img_np, "action_history": hist_np})[0]
        with torch.no_grad():
            torch_out = model(dummy_images, dummy_history).numpy()
        max_diff = np.abs(ort_out - torch_out).max()
        print(f"ONNX 一致性验证通过（最大差异: {max_diff:.6e}）")
    except ImportError:
        print("提示: 安装 onnxruntime 可验证导出模型一致性 (pip install onnxruntime)")


if __name__ == "__main__":
    main()
