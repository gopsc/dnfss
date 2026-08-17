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
import threading
import time

import numpy as np
from PIL import Image

try:
    import cv2                       # 可选：帧归一化+量化合并查表（加速预处理）
    HAS_CV2 = True
except ImportError:
    cv2 = None
    HAS_CV2 = False

from imu_norm import normalize_imu
from action_norm import (normalize_action, denormalize_action,
                      is_range_mode)
from utils import parse_action_history
from bins import TOTAL_BINS, decode as bin_decode, fixed_zero_columns

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# 异步推理缓冲数：≥4 时流水线吞吐趋于饱和（实测 4 缓冲 ~2.7×、6 缓冲 ~2.9×）
_ASYNC_BUFFERS = 4


class _AsyncJob:
    """predict_async 的推理句柄：result() 阻塞返回 (action_dim,) 动作向量"""

    def __init__(self):
        self._done = threading.Event()
        self._result = None
        self._error = None

    def result(self, timeout: float = None) -> np.ndarray:
        if not self._done.wait(timeout):
            raise TimeoutError("async HEF inference timeout")
        if self._error is not None:
            raise self._error
        return self._result

    def _complete(self, result=None, error=None):
        self._result = result
        self._error = error
        self._done.set()


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

    def __init__(self, hef_path: str, stats_path: str = None, vdevice=None,
                 predict_delta: bool = False, pred_token=None,
                 normalize_mode: str = "zscore", column_weights=None):
        """HEF 推理器。

        predict_delta: 模型用 --predict-delta 训练时须置 True，推理输出
            （归一化空间）会加回 history 末步再反归一化，与 predict.py 对齐。
        pred_token: np.ndarray 或 .npy 路径；export_deploy.py --pred-token-out
            产物。缺省时喂零（仅适合 pred_token 恰好全零的模型）。
        normalize_mode: 与训练时 DataConfig.normalize_mode 一致。
            "zscore"（默认）需传 stats_path；"fixed_range" / "log_range"
            免 stats 文件，按固定物理范围归一化（action_norm.py）。
        column_weights: 逐列损失权重（onehot HEF 的权重 0 列按
            bins.FIXED_OUTPUT 固定，防闭环垃圾回写）；None 时用
            action_norm.default_column_weights。
        """
        from hailo_platform import HEF, VDevice

        self._predict_delta = predict_delta
        self._range_mode = (normalize_mode
                            if is_range_mode(normalize_mode) else None)
        self._column_weights = None
        if column_weights is not None:
            self._column_weights = np.asarray(column_weights,
                                              dtype=np.float32)
        self._default_pred_token = None
        if pred_token is not None:
            if isinstance(pred_token, str):
                self._default_pred_token = np.load(pred_token)
            else:
                self._default_pred_token = np.asarray(pred_token,
                                                       dtype=np.float32)

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
            if stats_path is not None and not self._range_mode:
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
            if len(s) == 3 and s[1] == 1:
                pred = i                     # pred_token (1,1,d)
            elif len(s) == 3:
                history = i                  # 动作历史 (1,H_len,D)
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
                f"HEF 输入中未找到动作历史输入（输出维度 "
                f"{tuple(self._out_info.shape)}），"
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

        # cv2.LUT 查表（归一化+量化合并一步）：仅逐帧独立输入布局且
        # 帧边缘为 uint8 时可用；无 cv2 或旧布局回退 float 路径
        from hailo_platform import FormatType
        self._frame_luts = None
        if HAS_CV2 and self._image_layout == "nhwc_frames" and \
                all(f.format.type == FormatType.UINT8
                    for f in self._frame_infos):
            self._frame_luts = self._build_frame_luts()

        # 异步推理缓冲池（predict_async 复用）
        self._async_buffers = [self._configured.create_bindings()
                               for _ in range(_ASYNC_BUFFERS)]
        self._async_flags = [[True] for _ in range(_ASYNC_BUFFERS)]

        h_shape = tuple(self._history_info.shape)
        self.history_len, self.action_dim = h_shape[1], h_shape[2]

        # R2：imu 输入存在时暴露 imu_dim（pybot 闭环鸭子类型据此传 imu=），
        # 无则 None（旧 HEF 保持旧两参调用路径）
        self.imu_dim = tuple(self._imu_info.shape)[-1] \
            if self._imu_info is not None else None

        out_shape = tuple(self._out_info.shape)
        out_dim = out_shape[-1]
        if out_dim == TOTAL_BINS:
            # onehot 模型：输出为多头 logits，predict 时经 bins.decode
            # 解码为 action_dim 原始量纲（无需反归一化）
            self._onehot = True
        else:
            self._onehot = False
            if out_dim != self.action_dim:
                raise ValueError(
                    f"输出动作维度 {out_dim} 与动作历史维度 "
                    f"{self.action_dim} 不一致")

    # ---------------- 生命周期 ----------------

    def close(self):
        self._stack.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ---------------- 推理 ----------------

    def _prepare_inputs(self, images, action_history):
        """输入校验 + 动作历史归一化（predict/predict_async 共用）"""
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

        if self._range_mode:
            action_history = normalize_action(action_history, self.action_dim,
                                              mode=self._range_mode)
        elif self._action_mean is not None:
            action_history = (action_history - self._action_mean) / self._action_std
        return images, action_history

    def _fill_bindings(self, bindings, images, action_history,
                       pred_token=None, imu=None):
        """按 HEF 布局把（已归一化的）输入填充到 bindings 缓冲"""
        from hailo_platform import FormatType

        if self._frame_luts is not None:
            # cv2.LUT 快路径：逐帧独立输入，直接绑定量化缓冲
            for i, (info, img) in enumerate(zip(self._frame_infos, images)):
                buf = self._frame_lut_quant(img, i)
                bindings.input(info.name).set_buffer(buf)
        else:
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
                pred_token = self._default_pred_token
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

    def _postprocess(self, raw_result, history_tail=None) -> np.ndarray:
        """反量化 + 解码/反归一化 → (action_dim,)（predict/predict_async 共用）

        history_tail: 归一化空间的历史末步（predict_delta 残差模式用）。
        """
        from hailo_platform import FormatType
        if self._out_info.format.type == FormatType.UINT8:
            q = self._out_info.quant_info
            raw_result = (raw_result - q.qp_zp) * q.qp_scale
        result = raw_result.reshape(-1)
        if self._onehot:
            # 多头 logits → 期望值解码 → 原始量纲（箱生活在原始空间，
            # 无需反归一化）；权重 0 列固定为常量（与 predict.py 对齐），
            # 防闭环垃圾回写
            result = bin_decode(result, self.action_dim).reshape(-1)
            if self._column_weights is None:
                from action_norm import default_column_weights
                self._column_weights = default_column_weights(
                    self.action_dim)
            w = np.asarray(self._column_weights, dtype=np.float32)
            fixed = fixed_zero_columns(w, self.action_dim)
            result[w[:self.action_dim] <= 0.0] = (
                fixed[w[:self.action_dim] <= 0.0])
        else:
            if self._predict_delta:
                # 残差模式：Δ̂ + a_{t-1}（归一化空间），与 predict.py 对齐
                result = result + history_tail
            if self._range_mode:
                result = denormalize_action(result, self.action_dim,
                                            mode=self._range_mode)
            elif self._action_mean is not None:
                result = result * self._action_std + self._action_mean
        if not np.all(np.isfinite(result)):
            print("WARNING: HEF output contains NaN/Inf, replacing with zeros")
            result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
        return result

    def predict(self, images, action_history: np.ndarray,
                pred_token: np.ndarray = None,
                imu: np.ndarray = None) -> np.ndarray:
        """images: 长度为 frame_len 的 RGB 图像列表（旧→新，末帧为当前帧）
        imu: 可选原始姿态向量（roll,pitch,ax,ay,az,gx,gy,gz，HEF 含 imu
             输入时生效，内部按物理量程归一化；None 等价零向量）

        返回 (action_dim,) float32 动作向量。pred_token 仅新版 5 输入 HEF
        需要；默认喂零，抖动校准的新 HEF 建议喂 checkpoint 学习值。
        """
        images, action_history = self._prepare_inputs(images, action_history)
        bindings = self._configured.create_bindings()
        self._fill_bindings(bindings, images, action_history,
                            pred_token=pred_token, imu=imu)
        self._configured.run([bindings], timeout=10000)
        raw = np.asarray(
            bindings.output(self._out_info.name).get_buffer(),
            dtype=np.float32)
        return self._postprocess(raw, action_history[-1])

    # ---------------- 异步推理 ----------------

    def predict_async(self, images, action_history: np.ndarray,
                      pred_token: np.ndarray = None,
                      imu: np.ndarray = None) -> "_AsyncJob":
        """提交异步推理（run_async 流水线，多缓冲复用）

        与 predict() 语义/输出完全一致，但立即返回 job；调用方可在
        NPU 推理期间并行做其他工作（如抓帧/执行上一步动作），需要结果
        时 job.result() 阻塞获取。连续提交时（≥2 个在途）NPU 保持满负荷，
        实测吞吐可达同步 predict 的 ~2.9×。

        注意：绑定缓冲有限（_ASYNC_BUFFERS 个），在途任务超过缓冲数时
        本调用会阻塞等待空闲缓冲；job.result(timeout) 超时不会释放缓冲，
        缓冲由完成回调释放。
        """
        images, action_history = self._prepare_inputs(images, action_history)
        history_tail = action_history[-1]
        idx = self._acquire_async_buffer()
        bindings = self._async_buffers[idx]
        job = _AsyncJob()
        try:
            self._fill_bindings(bindings, images, action_history,
                                pred_token=pred_token, imu=imu)
        except Exception:
            self._release_async_buffer(idx)
            raise

        def cb(completion_info):
            try:
                exc = getattr(completion_info, "exception", None)
                if exc:
                    raise exc
                raw = np.asarray(
                    bindings.output(self._out_info.name).get_buffer(),
                    dtype=np.float32)
                job._complete(result=self._postprocess(raw, history_tail))
            except Exception as e:          # noqa: BLE001 —— 回调内兜底
                job._complete(error=e)
            finally:
                self._release_async_buffer(idx)

        self._configured.run_async([bindings], callback=cb)
        return job

    def _acquire_async_buffer(self) -> int:
        """取一个空闲绑定缓冲（在途任务超限时阻塞等待）"""
        while True:
            for i in range(len(self._async_buffers)):
                if self._async_flags[i][0]:
                    self._async_flags[i][0] = False
                    return i
            time.sleep(0.0001)

    def _release_async_buffer(self, idx: int):
        self._async_flags[idx][0] = True

    def _frame_float(self, image) -> np.ndarray:
        """Resize → float32 HWC；归一化 HEF（负零偏）再走 ImageNet 归一化"""
        img = Image.fromarray(image) if isinstance(image, np.ndarray) else image
        img = img.convert("RGB").resize(
            (self.image_width, self.image_height), Image.BILINEAR)
        x = np.asarray(img, dtype=np.float32)          # HWC 0..255
        if self._normalize_frames:
            x = (x / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
        return x

    def _build_frame_luts(self) -> list:
        """按帧 quant_info 构建 256 项单通道查表（归一化+量化合并一步）

        OpenCV 5.0 的 cv2.LUT 只接受单通道表，故每帧返回 [R,G,B] 三张表。
        数学上与 float 路径（_frame_float + quantize）等价（实测 0 LSB）。
        """
        luts = []
        for info in self._frame_infos:
            q = info.quant_info
            c = np.arange(256, dtype=np.float32)[:, None]
            if self._normalize_frames:
                v = (c / 255.0 - IMAGENET_MEAN[None, :]) / IMAGENET_STD[None, :]
            else:
                v = np.broadcast_to(c, (256, 3))
            v = v / q.qp_scale + q.qp_zp
            lut = np.clip(np.round(v), 0, 255).astype(np.uint8)   # (256,3)
            luts.append([lut[:, ch] for ch in range(3)])
        return luts

    def _frame_lut_quant(self, image, frame_idx: int = 0) -> np.ndarray:
        """cv2.LUT 直通：原始 uint8 像素 → 量化 uint8 缓冲（免 float 中间态）

        非 uint8 / 非 3 通道 / 尺寸不匹配输入走与 _frame_float 相同的 PIL
        转换，保证两条路径语义一致（RGBA 经 convert 合成、灰度转 RGB）。
        """
        x = np.asarray(image)
        if x.dtype != np.uint8 or x.ndim != 3 or x.shape[2] != 3 or \
                x.shape[:2] != (self.image_height, self.image_width):
            img = Image.fromarray(x).convert("RGB")
            if img.size != (self.image_width, self.image_height):
                img = img.resize((self.image_width, self.image_height),
                                 Image.BILINEAR)
            x = np.asarray(img, dtype=np.uint8)
        luts = self._frame_luts[frame_idx]
        outs = [cv2.LUT(x[..., ch], lut) for ch, lut in enumerate(luts)]
        return np.ascontiguousarray(cv2.merge(outs))

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
                        help="action_stats.npy 路径（zscore 归一化训练才需要）")
    parser.add_argument("--normalize-mode", choices=["zscore", "fixed_range"],
                        default=None,
                        help="与训练时 normalize_mode 一致；fixed_range 免 --stats。"
                             "缺省按 --ckpt 配置自动判定，无 --ckpt 时为 zscore")
    parser.add_argument("--pred-token", default=None,
                        help="pred_token .npy 路径（export_deploy.py "
                             "--pred-token-out 产物；缺省喂零）")
    parser.add_argument("--ckpt", default=None,
                        help="checkpoint .pt 路径（自动检测 predict_delta；"
                             "HEF 部署时需要以恢复训练配置）")
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

    predict_delta = False
    normalize_mode = args.normalize_mode or "zscore"
    if args.ckpt:
        import torch
        ck = torch.load(args.ckpt, map_location="cpu", weights_only=True)
        cfg = ck.get("config", {})
        predict_delta = cfg.get("train", {}).get("predict_delta", False)
        if predict_delta:
            print("predict_delta=True: HEF 输出将加回历史末步（残差模式）")
        if args.normalize_mode is None:
            # 未显式指定时按 checkpoint 配置判定归一化方式（旧 checkpoint 无
            # normalize_mode 键，默认 zscore，行为不变）
            data_cfg = cfg.get("data", {})
            if (data_cfg.get("normalize_actions")
                    and data_cfg.get("normalize_mode") == "fixed_range"):
                normalize_mode = "fixed_range"
                print("normalize_mode=fixed_range: 固定物理范围归一化（免 stats）")

    with HailoPredictor(args.hef, stats_path=args.stats,
                        predict_delta=predict_delta,
                        normalize_mode=normalize_mode) as predictor:
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
