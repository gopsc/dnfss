"""actpred HEF（Hailo）推理封装 —— AI HAT 2 / Hailo-10H 部署后端

接口对齐 predict.ActionPredictorInference：predict(images, action_history)。

HEF 由 actpred ONNX 经 Hailo DFC 编译生成（量化校准后）。本封装不硬编码
vstream 名称/形状/量化参数，全部从 HEF 的 input/output vstream info 动态推导，
支持两种布局：

    新版 5/6 输入（export_deploy.py + dfc_compile.py 产物）：
        frame0~2 各 (H,W,3) NHWC uint8（ImageNet 归一化校准，负零偏自动识别）
        + action_history (1,H_len,D) + pred_token (1,1,d)
        + imu (1,1,1,imu_dim)（R2 方案一可选输入，原始姿态内部归一化）
    旧版单图像输入：
        (1, F, 3, H, W) → NHWC uint8 (1, F, H, W, 3)
        或 (1, H, W, F*3) → 帧沿通道拼接；action_history (1, H, D)

uint8 边缘按 quant_info 量化/反量化；pred_token 输入默认喂零，
新编译 HEF（抖动校准，见 gen_calibration.py --pred-jitter）应喂
checkpoint 的学习值（--pred-token npy 或 predict(pred_token=...)），
否则该 token 语义与训练时不一致。

背景与踩坑记录见
docs/reports/actpred_hailo_hat2_inference_research_20260813.md：
    - hailort 5.x 对 Hailo-10 仅支持新 API（create_infer_model），
      旧版 VDevice.configure(hef) 报 HAILO_NOT_IMPLEMENTED；
    - 绑定缓冲区 dtype/大小必须匹配 HEF 边缘格式（量化 uint8）。
"""

import argparse
import contextlib
import json
import os
import re

import numpy as np
from PIL import Image

from imu_norm import normalize_imu

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

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


def _input_layer_index(name: str) -> int:
    m = re.search(r"input_layer(\d+)$", name)
    return int(m.group(1)) if m else 0


class HailoPredictor:
    """HEF 动作预测推理器（Hailo NPU 后端）

    支持新版 5 输入 HEF（逐帧输入 + pred_token）与旧版单图像输入 HEF，
    布局与量化参数从 vstream info 自动推导。

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
            self._out_info = self._out_infos[0]
            self._frame_infos, self._history_info, self._pred_info, \
                self._imu_info = self._classify_inputs()
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

    # ---------------- 输入分类与形状推导 ----------------

    def _classify_inputs(self):
        """将 HEF 输入分为 帧列表 / 动作历史 / pred_token / imu（后两者可为 None）

        多帧输入的绑定顺序按 input_layerN 数字后缀排序，而非 vstream
        报告顺序：hailort 5.3.0 实测报告顺序被打乱（1,5,4,3,2），
        按报告顺序绑定会导致 frame1/frame2 错位。
        imu 为 R2 方案一新增输入（1,1,1,imu_dim），旧 HEF 无此输入。
        """
        ins = list(self._in_infos)
        out_dim = tuple(self._out_info.shape)[-1]
        # 新版：每帧独立 (H,W,3) 输入
        frames = [i for i in ins if len(i.shape) == 3
                  and i.shape[2] == 3 and i.shape[0] > 3]
        if frames:
            frames.sort(key=lambda i: _input_layer_index(i.name))
        else:
            # 旧版：单一 4/5 维图像输入
            frames = [i for i in ins if len(i.shape) in (4, 5)]
        rest = [i for i in ins if i not in frames]
        history, pred, imu = None, None, None
        for i in rest:
            s = tuple(i.shape)
            if len(s) == 3 and s[-1] == out_dim:
                history = i
            elif len(s) == 3 and s[1] == 1:
                pred = i
            elif len(s) == 4 and s[0] == 1 and s[1] == 1 and s[2] == 1:
                imu = i                     # R2：imu (1,1,1,imu_dim)
            else:
                raise ValueError(
                    f"无法识别 HEF 输入 {i.name} 形状 {s}（期望动作历史 "
                    f"(1,H_len,D)、pred_token (1,1,d) 或 imu (1,1,1,imu_dim)）")
        if not frames:
            raise ValueError("HEF 输入中未找到图像输入")
        if history is None:
            raise ValueError(
                f"HEF 输入中未找到动作历史输入（输出维度 {out_dim}），"
                f"实际: {[(i.name, tuple(i.shape)) for i in ins]}")
        return frames, history, pred, imu

    def _derive_dims(self):
        # 帧输入布局
        if len(self._frame_infos) == 1 and len(self._frame_infos[0].shape) in (4, 5):
            shape = tuple(self._frame_infos[0].shape)
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
        else:
            # 新版：每帧独立 (H,W,3) 输入，形状须一致
            shapes = [tuple(i.shape) for i in self._frame_infos]
            if any(len(s) != 3 or s[2] != 3
                   or s[0] != shapes[0][0] or s[1] != shapes[0][1]
                   for s in shapes):
                raise ValueError(f"帧输入形状不一致: {shapes}")
            self.image_height, self.image_width, _ = shapes[0]
            self.frame_len = len(self._frame_infos)
            self._image_layout = "nhwc_frames"
        self._frame_quant = self._frame_infos[0].quant_info
        # 帧输入是否需 ImageNet 归一化，由量化网格判定：DFC 的 zp 恒非负
        # （zp = round(-min/scale)），码 0 反量化值 dequant(0) = -zp*scale。
        # 原始像素输入（min=0, zp=0）下为 0；归一化输入（min≈-2.1）下
        # ≈ -2.1（实测 HEF zp=114, scale=0.0187）。旧启发式 zp*scale<0
        # 把归一化输入误判为原始像素直通，图像路径被饱和破坏。
        self._normalize_frames = \
            (0 - self._frame_quant.qp_zp) * self._frame_quant.qp_scale < -0.5

        h_shape = tuple(self._history_info.shape)
        self.history_len, self.action_dim = h_shape[1], h_shape[2]

        # R2：imu 输入存在时暴露 imu_dim（pybot 闭环鸭子类型据此传 imu=），
        # 无则 None（旧 HEF 保持旧两参调用路径）
        self.imu_dim = tuple(self._imu_info.shape)[-1] \
            if self._imu_info is not None else None

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

    def predict(self, images, action_history: np.ndarray,
                pred_token: np.ndarray = None,
                imu: np.ndarray = None) -> np.ndarray:
        """images: 长度为 frame_len 的 RGB 图像列表（旧→新，末帧为当前帧）
        imu: 可选原始姿态向量（roll,pitch,ax,ay,az,gx,gy,gz，HEF 含 imu
             输入时生效，内部按物理量程归一化；None 等价零向量）

        返回 (action_dim,) float32 动作向量。pred_token 仅新版 5 输入 HEF
        需要；默认喂零，抖动校准的新 HEF 建议喂 checkpoint 学习值。
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

        frame_float = np.stack([self._frame_float(img) for img in images])
        self._bind_frames(bindings, frame_float)

        h_shape = tuple(self._history_info.shape)
        if self._history_info.format.type == FormatType.UINT8:
            q = self._history_info.quant_info
            buf = np.clip(np.round(action_history / q.qp_scale + q.qp_zp),
                          0, 255).astype(np.uint8).reshape(h_shape)
        else:
            buf = action_history.astype(np.float32).reshape(h_shape)
        bindings.input(self._history_info.name).set_buffer(buf)

        if self._pred_info is not None:
            p_shape = tuple(self._pred_info.shape)
            if pred_token is None:
                values = np.zeros(p_shape, dtype=np.float32)
            else:
                values = np.asarray(pred_token, dtype=np.float32).reshape(p_shape)
            if self._pred_info.format.type == FormatType.UINT8:
                q = self._pred_info.quant_info
                values = np.clip(np.round(values / q.qp_scale + q.qp_zp),
                                 0, 255).astype(np.uint8)
            bindings.input(self._pred_info.name).set_buffer(values)

        if self._imu_info is not None:
            i_shape = tuple(self._imu_info.shape)
            # 原始姿态 → 物理量程归一化（与训练 dataset 同口径）
            values = normalize_imu(imu, i_shape[-1]).reshape(i_shape)
            if self._imu_info.format.type == FormatType.UINT8:
                q = self._imu_info.quant_info
                values = np.clip(np.round(values / q.qp_scale + q.qp_zp),
                                 0, 255).astype(np.uint8)
            bindings.input(self._imu_info.name).set_buffer(values)

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

    def _frame_float(self, image) -> np.ndarray:
        """Resize → float32 HWC；归一化 HEF（负零偏）再走 ImageNet 归一化"""
        img = Image.fromarray(image) if isinstance(image, np.ndarray) else image
        img = img.convert("RGB").resize(
            (self.image_width, self.image_height), Image.BILINEAR)
        x = np.asarray(img, dtype=np.float32)          # HWC 0..255
        if self._normalize_frames:
            x = (x / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
        return x

    def _bind_frames(self, bindings, frame_float: np.ndarray):
        """(F,H,W,3) float 帧 → HEF 帧输入缓冲区（按布局与量化）"""
        from hailo_platform import FormatType

        def quantize(buf, q):
            return np.clip(np.round(buf / q.qp_scale + q.qp_zp),
                           0, 255).astype(np.uint8)

        if self._image_layout == "ncfhw":
            buf = frame_float.transpose(0, 3, 1, 2)[np.newaxis]   # (1,F,3,H,W)
            info = self._frame_infos[0]
            if info.format.type == FormatType.UINT8:
                buf = quantize(buf, info.quant_info)
            bindings.input(info.name).set_buffer(buf)
        elif self._image_layout == "nhwc_fused":
            buf = frame_float.reshape(
                1, self.image_height, self.image_width, self.frame_len * 3)
            info = self._frame_infos[0]
            if info.format.type == FormatType.UINT8:
                buf = quantize(buf, info.quant_info)
            bindings.input(info.name).set_buffer(buf)
        else:  # nhwc_frames：每帧独立输入
            for i, info in enumerate(self._frame_infos):
                buf = frame_float[i]
                if info.format.type == FormatType.UINT8:
                    buf = quantize(buf, info.quant_info)
                bindings.input(info.name).set_buffer(buf)


def main():
    parser = argparse.ArgumentParser(description="actpred HEF (Hailo) inference")
    parser.add_argument("--hef", required=True, help="HEF 模型路径")
    parser.add_argument("--images", required=True, nargs="+",
                        help="按时间顺序（旧→新）传入 frame_len 个图像路径")
    parser.add_argument("--action-history", required=True,
                        help="冒号分隔各步，每步 action_dim 个值")
    parser.add_argument("--stats", default=None,
                        help="action_stats.npy 路径（训练时启用归一化才需要）")
    parser.add_argument("--pred-token", default=None,
                        help="pred_token .npy 路径（export_deploy.py "
                             "--pred-token-out 产物；缺省喂零）")
    parser.add_argument("--imu", type=str, default=None,
                        help="可选姿态向量（逗号分隔，原始量纲：roll,pitch 度 / "
                             "ax..gz int16 原始值）；HEF 含 imu 输入且缺省时按零向量")
    parser.add_argument("--output-format", choices=["json", "raw"],
                        default="json")
    args = parser.parse_args()

    pred_token = np.load(args.pred_token) if args.pred_token else None
    imu = None
    if args.imu is not None:
        imu = np.array([float(v) for v in args.imu.split(",")],
                       dtype=np.float32)
    with HailoPredictor(args.hef, stats_path=args.stats) as predictor:
        if len(args.images) != predictor.frame_len:
            raise SystemExit(
                f"--images 需传入 {predictor.frame_len} 个路径（旧→新），"
                f"实际为 {len(args.images)} 个")
        images = [np.array(Image.open(p).convert("RGB")) for p in args.images]
        history = parse_action_history(args.action_history, predictor.action_dim)
        predicted = predictor.predict(images, history, pred_token=pred_token,
                                      imu=imu)

    if args.output_format == "json":
        print(json.dumps({"predicted_action": predicted.tolist()}, indent=2))
    else:
        print(" ".join(f"{v:.4f}" for v in predicted))


if __name__ == "__main__":
    main()
