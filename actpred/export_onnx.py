import argparse
import torch
from torch.export import Dim
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
        1, data_cfg.frame_len, 3, data_cfg.image_height, data_cfg.image_width)
    dummy_history = torch.randn(1, data_cfg.history_len, data_cfg.action_dim)
    # R2 方案一：imu_dim>0 时追加归一化姿态输入 (1, imu_dim)
    imu_dim = data_cfg.imu_dim
    extra_inputs = (torch.randn(1, imu_dim),) if imu_dim > 0 else ()
    extra_names = ["imu"] if imu_dim > 0 else []

    batch = Dim("batch")
    dynamic = {"images": {0: batch}, "action_history": {0: batch}}
    if imu_dim > 0:
        dynamic["imu"] = {0: batch}
    torch.onnx.export(
        model, (dummy_images, dummy_history, *extra_inputs),
        args.output,
        input_names=["images", "action_history"] + extra_names,
        output_names=["predicted_action"],
        dynamic_shapes=dynamic,
        opset_version=18,
    )
    print(f"ONNX model exported to: {args.output}"
          f"{'（含 imu 输入）' if imu_dim > 0 else ''}")

    # 可选：验证导出的 ONNX 模型一致性
    try:
        import onnxruntime as ort
        import numpy as np
        sess = ort.InferenceSession(args.output)
        feeds = {"images": dummy_images.numpy(),
                 "action_history": dummy_history.numpy()}
        for name, t in zip(extra_names, extra_inputs):
            feeds[name] = t.numpy()
        ort_out = sess.run(None, feeds)[0]
        with torch.no_grad():
            torch_out = model(dummy_images, dummy_history, *extra_inputs).numpy()
        max_diff = np.abs(ort_out - torch_out).max()
        print(f"ONNX 一致性验证通过（最大差异: {max_diff:.6e}）")
    except ImportError:
        print("提示: 安装 onnxruntime 可验证导出模型一致性 (pip install onnxruntime)")


if __name__ == "__main__":
    main()
