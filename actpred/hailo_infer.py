"""actpred HEF（Hailo）推理封装 —— AI HAT 2 / Hailo-10H 部署后端

接口对齐 predict.ActionPredictorInference：predict(images, action_history)。

HEF 由 actpred ONNX 经 Hailo DFC 编译生成（量化校准后）。本封装不硬编码
vstream 名称/形状/量化参数，全部从 HEF 的 input/output vstream info 动态推导：

    图像输入:  (1, F, 3, H, W) → NHWC uint8 (1, F, H, W, 3)
               或 (1, H, W, F*3) → 帧沿通道拼接
    动作输入:  (1, H, D) float32，uint8 边缘时按 quant_info 量化
    动作输出:  (1, D)，uint8 边缘时按 quant_info 反量化

背景与踩坑记录见
docs/reports/actpred_hailo_hat2_inference_research_20260813.md：
    - hailort 5.x 对 Hailo-10 仅支持新 API（create_infer_model），
      旧版 VDevice.configure(hef) 报 HAILO_NOT_IMPLEMENTED；
    - 绑定缓冲区 dtype/大小必须匹配 HEF 边缘格式（量化 uint8）；
    - 图像输入按 Hailo 惯例为 uint8 原始像素（ImageNet 归一化由
      DFC 编译时折叠进网络）。
"""

import argparse
import contextlib
import json
import os

import numpy as np
from PIL import Image

# 与 predict.parse_action_history 相同逻辑；此处内联以保持
# 本模块零 torch 依赖（HAT 部署环境只装 hailo_platform + numpy + PIL）


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


class HailoPredictor:
    """HEF 动作预测推理器（Hailo NPU 后端）

    用法：
        with HailoPredictor("actpred.hef") as predictor:
            action = predictor.predict(images, action_history)
    """

    def __init__(self, hef_path: str, stats_path: str = None, vdevice=None):
        from hailo_platform import HEF, VDevice

        self._stack = contextlib.ExitStack()
        if vdevice is None:
            vdevice = self._stack.enter_context(VDevice())
        self._vdevice = vdevice
        self._infer_model = self._vdevice.create_infer_model(str(hef_path))
        self._configured = self._stack.enter_context(
            self._infer_model.configure())
        try:
            hef = HEF(str(hef_path))
            self._in_infos = hef.get_input_vstream_infos()
            self._out_infos = hef.get_output_vstream_infos()
            if len(self._out_infos) != 1:
                raise ValueError(
                    f"HEF 应只有一个输出 vstream，实际 {len(self._out_infos)}")
            self._image_info = self._find_image_input()
            self._history_info = self._find_history_input()
            self._out_info = self._out_infos[0]
            self._derive_dims()

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
        except Exception:
            self._stack.close()       # 初始化失败也释放设备，避免 fd 泄漏
            raise

    # ---------------- 形状推导 ----------------

    def _find_image_input(self):
        cands = [i for i in self._in_infos if len(i.shape) in (4, 5)]
        if len(cands) != 1:
            raise ValueError(
                "HEF 输入中无法唯一识别图像 vstream（期望一个 4/5 维输入），"
                f"实际: {[(i.name, tuple(i.shape)) for i in self._in_infos]}")
        return cands[0]

    def _find_history_input(self):
        cands = [i for i in self._in_infos
                 if len(i.shape) == 3 and i is not self._image_info]
        if len(cands) != 1:
            raise ValueError(
                "HEF 输入中无法唯一识别动作历史 vstream（期望一个 3 维输入），"
                f"实际: {[(i.name, tuple(i.shape)) for i in self._in_infos]}")
        return cands[0]

    def _derive_dims(self):
        shape = tuple(self._image_info.shape)
        if len(shape) == 5:
            _, self.frame_len, c, self.image_height, self.image_width = shape
            if c != 3:
                raise ValueError(f"图像输入通道维应为 3，实际形状 {shape}")
            self._image_layout = "ncfhw"
        else:  # (1, H, W, F*3) NHWC 帧拼通道
            _, self.image_height, self.image_width, c = shape
            if c % 3 != 0 or c == 3:
                raise ValueError(
                    f"无法从 4 维图像输入推导帧数（期望 (1,H,W,F*3)），"
                    f"实际形状 {shape}")
            self.frame_len = c // 3
            self._image_layout = "nhwc_fused"

        h_shape = tuple(self._history_info.shape)
        self.history_len, self.action_dim = h_shape[1], h_shape[2]

        out_shape = tuple(self._out_info.shape)
        out_dim = out_shape[-1]
        if out_dim != self.action_dim:
            raise ValueError(
                f"输出动作维度 {out_dim} 与动作历史维度 {self.action_dim} 不一致")

    # ---------------- 生命周期 ----------------

    def close(self):
        self._stack.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ---------------- 推理 ----------------

    def predict(self, images, action_history: np.ndarray) -> np.ndarray:
        """images: 长度为 frame_len 的 RGB 图像列表（旧→新，末帧为当前帧）

        返回 (action_dim,) float32 动作向量。
        """
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

        from hailo_platform import FormatType
        bindings = self._configured.create_bindings()

        frames = np.stack([self._preprocess(img) for img in images])
        bindings.input(self._image_info.name).set_buffer(
            self._image_buffer(frames))

        h_shape = tuple(self._history_info.shape)
        if self._history_info.format.type == FormatType.UINT8:
            q = self._history_info.quant_info
            buf = np.clip(np.round(action_history / q.qp_scale + q.qp_zp),
                          0, 255).astype(np.uint8).reshape(h_shape)
        else:
            buf = action_history.astype(np.float32).reshape(h_shape)
        bindings.input(self._history_info.name).set_buffer(buf)

        out_shape = tuple(self._out_info.shape)
        if self._out_info.format.type == FormatType.UINT8:
            out_buf = np.zeros(out_shape, dtype=np.uint8)
        else:
            out_buf = np.zeros(out_shape, dtype=np.float32)
        bindings.output(self._out_info.name).set_buffer(out_buf)

        self._configured.run([bindings], timeout=10000)

        result = np.asarray(
            bindings.output(self._out_info.name).get_buffer(),
            dtype=np.float32)
        if self._out_info.format.type == FormatType.UINT8:
            q = self._out_info.quant_info
            result = (result - q.qp_zp) * q.qp_scale
        result = result.reshape(-1)
        if self._action_mean is not None:
            result = result * self._action_std + self._action_mean
        return result

    def _preprocess(self, image) -> np.ndarray:
        img = Image.fromarray(image) if isinstance(image, np.ndarray) else image
        img = img.convert("RGB").resize(
            (self.image_width, self.image_height), Image.BILINEAR)
        return np.asarray(img, dtype=np.uint8)      # HWC uint8

    def _image_buffer(self, frames: np.ndarray) -> np.ndarray:
        """(F, H, W, 3) → HEF 图像输入缓冲区（按编译后布局）"""
        if self._image_layout == "ncfhw":
            return frames.transpose(0, 3, 1, 2)[np.newaxis]  # (1,F,3,H,W)
        # nhwc_fused: 帧沿通道拼接 (1, H, W, F*3)
        return frames.reshape(1, self.image_height, self.image_width,
                              self.frame_len * 3)


def main():
    parser = argparse.ArgumentParser(description="actpred HEF (Hailo) inference")
    parser.add_argument("--hef", required=True, help="HEF 模型路径")
    parser.add_argument("--images", required=True, nargs="+",
                        help="按时间顺序（旧→新）传入 frame_len 个图像路径")
    parser.add_argument("--action-history", required=True,
                        help="冒号分隔各步，每步 action_dim 个值")
    parser.add_argument("--stats", default=None,
                        help="action_stats.npy 路径（训练时启用归一化才需要）")
    parser.add_argument("--output-format", choices=["json", "raw"],
                        default="json")
    args = parser.parse_args()

    with HailoPredictor(args.hef, stats_path=args.stats) as predictor:
        if len(args.images) != predictor.frame_len:
            raise SystemExit(
                f"--images 需传入 {predictor.frame_len} 个路径（旧→新），"
                f"实际为 {len(args.images)} 个")
        images = [np.array(Image.open(p).convert("RGB")) for p in args.images]
        history = parse_action_history(args.action_history, predictor.action_dim)
        predicted = predictor.predict(images, history)

    if args.output_format == "json":
        print(json.dumps({"predicted_action": predicted.tolist()}, indent=2))
    else:
        print(" ".join(f"{v:.4f}" for v in predicted))


if __name__ == "__main__":
    main()
