"""actpred ONNX Runtime 推理封装 —— CPU 后端（Pi 5 实测 5.9ms/次，PyTorch 的 ~5 倍）

接口对齐 predict.ActionPredictorInference：predict(images, action_history, imu=None)。

模型维度从 ONNX session 输入元数据动态推导（images: [1,F,3,H,W]，
action_history: [1,H,D]；R2 imu_dim>0 的模型另有 imu: [1,imu_dim]），
无需 checkpoint。图像预处理复现训练时的 dataset.py 流水线：
Resize → /255 → ImageNet 归一化。

背景见 docs/reports/actpred_inference_speed_benchmark_20260813.md。
"""

import argparse
import json
import os

import numpy as np
from PIL import Image

from imu_norm import normalize_imu

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


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


class OnnxPredictor:
    """ONNX Runtime 动作预测推理器（CPU 后端）

    用法：
        with OnnxPredictor("model.onnx") as predictor:
            action = predictor.predict(images, action_history)
    """

    def __init__(self, onnx_path: str, stats_path: str = None,
                 providers=None):
        import onnxruntime as ort

        if providers is None:
            providers = ["CPUExecutionProvider"]
        ort.set_default_logger_severity(3)    # 抑制 GPU 探测等无害警告
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=providers)

        inputs = {i.name: [d if isinstance(d, int) else -1
                           for d in i.shape] for i in self._session.get_inputs()}
        img_shape, hist_shape, imu_shape = self._locate_inputs(inputs)
        _, self.frame_len, _, self.image_height, self.image_width = img_shape
        _, self.history_len, self.action_dim = hist_shape
        self._image_name = next(n for n, s in inputs.items() if len(s) == 5)
        self._history_name = next(n for n, s in inputs.items() if len(s) == 3)
        # R2 方案一：2 维输入 [1, imu_dim] 为姿态（无则 None）。
        # pybot 闭环鸭子类型约定：None 时按旧两参调用。
        self.imu_dim = imu_shape[-1] if imu_shape is not None else None
        self._imu_name = (next(n for n, s in inputs.items() if len(s) == 2)
                          if imu_shape is not None else None)

        self._action_mean = None
        self._action_std = None
        if stats_path is not None:
            if not os.path.exists(stats_path):
                raise FileNotFoundError(f"统计文件不存在: {stats_path}")
            stats = np.load(stats_path)
            if stats.shape != (2, self.action_dim):
                raise ValueError(
                    f"action_stats.npy 形状应为 (2, {self.action_dim})，"
                    f"实际为 {stats.shape}: {stats_path}")
            self._action_mean = stats[0].astype(np.float32)
            self._action_std = stats[1].astype(np.float32)

    @staticmethod
    def _locate_inputs(inputs):
        """按维度数识别图像(5维)/动作历史(3维)/姿态(2维,可选)输入，返回各自形状"""
        img = [s for s in inputs.values() if len(s) == 5]
        hist = [s for s in inputs.values() if len(s) == 3]
        imu = [s for s in inputs.values() if len(s) == 2]
        if len(img) != 1 or len(hist) != 1 or len(imu) > 1:
            raise ValueError(
                "ONNX 输入应为 1 个 5 维图像 + 1 个 3 维动作历史"
                "（R2 可再加 1 个 2 维 imu），"
                f"实际: {inputs}")
        if img[0][2] != 3:
            raise ValueError(
                f"图像输入形状应为 [1,F,3,H,W]，实际为 {img[0]}")
        return img[0], hist[0], (imu[0] if imu else None)

    # ---------------- 生命周期 ----------------

    def close(self):
        self._session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ---------------- 推理 ----------------

    def predict(self, images, action_history: np.ndarray,
                imu: np.ndarray = None) -> np.ndarray:
        """images: 长度为 frame_len 的 RGB 图像列表（旧→新，末帧为当前帧）
        imu: 可选原始姿态向量（roll,pitch,ax,ay,az,gx,gy,gz，模型含 imu
             输入时生效，内部按物理量程归一化；None 等价零向量）

        返回 (action_dim,) float32 动作向量。
        """
        if self._session is None:
            raise RuntimeError("predictor 已关闭")
        if not isinstance(images, (list, tuple)):
            images = [images]
        if len(images) != self.frame_len:
            raise ValueError(
                f"images 应为 {self.frame_len} 帧，实际为 {len(images)} 帧。"
                "冷启动时可重复传入同一帧。")
        action_history = np.asarray(action_history, dtype=np.float32)
        expected = (self.history_len, self.action_dim)
        if action_history.shape != expected:
            raise ValueError(
                f"action_history 形状应为 {expected}，实际为 {action_history.shape}")

        if self._action_mean is not None:
            action_history = (action_history - self._action_mean) / self._action_std

        frames = np.stack([self._preprocess(img) for img in images])
        feeds = {
            self._image_name: frames[np.newaxis],                 # (1,F,3,S,S)
            self._history_name: action_history[np.newaxis],       # (1,H,D)
        }
        if self._imu_name is not None:
            feeds[self._imu_name] = normalize_imu(
                imu, self.imu_dim)[np.newaxis]                    # (1,imu_dim)
        result = self._session.run(None, feeds)[0].reshape(-1).astype(np.float32)
        if self._action_mean is not None:
            result = result * self._action_std + self._action_mean
        return result

    def _preprocess(self, image) -> np.ndarray:
        """Resize → /255 → ImageNet 归一化 → (3, H, W) float32"""
        img = Image.fromarray(image) if isinstance(image, np.ndarray) else image
        img = img.convert("RGB").resize(
            (self.image_width, self.image_height), Image.BILINEAR)
        x = np.asarray(img, dtype=np.float32) / 255.0     # HWC
        x = x.transpose(2, 0, 1)                          # CHW
        return (x - IMAGENET_MEAN[:, None, None]) / IMAGENET_STD[:, None, None]


def main():
    parser = argparse.ArgumentParser(description="actpred ONNX Runtime inference")
    parser.add_argument("--model", required=True, help="ONNX 模型路径")
    parser.add_argument("--images", required=True, nargs="+",
                        help="按时间顺序（旧→新）传入 frame_len 个图像路径")
    parser.add_argument("--action-history", required=True,
                        help="冒号分隔各步，每步 action_dim 个值")
    parser.add_argument("--stats", default=None,
                        help="action_stats.npy 路径（训练时启用归一化才需要）")
    parser.add_argument("--imu", type=str, default=None,
                        help="可选姿态向量（逗号分隔，原始量纲：roll,pitch 度 / "
                             "ax..gz int16 原始值）；模型含 imu 输入且缺省时按零向量")
    parser.add_argument("--output-format", choices=["json", "raw"],
                        default="json")
    args = parser.parse_args()

    with OnnxPredictor(args.model, stats_path=args.stats) as predictor:
        if len(args.images) != predictor.frame_len:
            raise SystemExit(
                f"--images 需传入 {predictor.frame_len} 个路径（旧→新），"
                f"实际为 {len(args.images)} 个")
        images = [np.array(Image.open(p).convert("RGB")) for p in args.images]
        history = parse_action_history(args.action_history, predictor.action_dim)
        imu = None
        if args.imu is not None:
            imu = np.array([float(v) for v in args.imu.split(",")],
                           dtype=np.float32)
        predicted = predictor.predict(images, history, imu=imu)

    if args.output_format == "json":
        print(json.dumps({"predicted_action": predicted.tolist()}, indent=2))
    else:
        print(" ".join(f"{v:.4f}" for v in predicted))


if __name__ == "__main__":
    main()
