"""导出 Hailo 部署版 ONNX：DFC 友好图结构

训练态 ONNX 对 DFC 不友好：5D 图像输入、AdaptiveAvgPool2d（被
torch.export 展开成 Loop 节点）、MultiheadAttention 的 head 拆分
Reshape 序列。此脚本逐项改造（权重数值完全不变）：
  1. 图像输入拆为 F 个独立 (1,H,W,3) NHWC 输入，按帧展开 CNN 分支，
     各分支持有独立权重副本（共享 initializer 会被 DFC 误融合）
  2. AdaptiveAvgPool2d 换成等价的固定 kernel AvgPool2d
  3. 自注意力手工展开为每头独立 Linear（128→32），无 head reshape；
     pre-LN 残差结构与训练态 nn.TransformerEncoderLayer(norm_first=True)
     逐位等价
"""

import argparse
import os
import sys

import numpy as np
import torch
import torch.nn as nn

from config import parse_checkpoint_config
from model import ActionPredictor

# 一致性校验阈值：wrapper 改造/ORT 转换与原模型输出差异超过即拦截退出，
# 防止 ManualAttnBlock 依赖的 PyTorch 布局约定变更静默穿透（v3-P2-2）
MAX_TOLERATED_DIFF = 1e-4

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

MAX_TOKEN_ABS = 100.0
MERGE_RATIO_WARN = 32.0


class ManualAttnBlock(nn.Module):
    """等价替换 nn.TransformerEncoderLayer(d=128, nhead=4, ff=256, norm_first=True)"""

    def __init__(self, layer: nn.TransformerEncoderLayer):
        super().__init__()
        d = layer.self_attn.embed_dim
        nh = layer.self_attn.num_heads
        self.head_dim = d // nh
        self.nh = nh
        self.scale = self.head_dim ** -0.5

        ipw = layer.self_attn.in_proj_weight  # (3d, d) = [Wq; Wk; Wv]
        ipb = layer.self_attn.in_proj_bias
        self.q_proj = nn.ModuleList()
        self.k_proj = nn.ModuleList()
        self.v_proj = nn.ModuleList()
        for h in range(nh):
            sl = slice(h * self.head_dim, (h + 1) * self.head_dim)
            slices = (sl, slice(d + sl.start, d + sl.stop),
                      slice(2 * d + sl.start, 2 * d + sl.stop))
            for proj, ws in ((self.q_proj, slices[0]), (self.k_proj, slices[1]),
                             (self.v_proj, slices[2])):
                lin = nn.Linear(d, self.head_dim)
                lin.weight.data.copy_(ipw[ws])
                proj.append(lin)
        for h in range(nh):
            self.q_proj[h].bias.data.copy_(ipb[h * self.head_dim:(h + 1) * self.head_dim])
            self.k_proj[h].bias.data.copy_(ipb[d + h * self.head_dim:d + (h + 1) * self.head_dim])
            self.v_proj[h].bias.data.copy_(ipb[2 * d + h * self.head_dim:2 * d + (h + 1) * self.head_dim])

        self.out_proj = layer.self_attn.out_proj
        self.norm1 = layer.norm1
        self.norm2 = layer.norm2
        self.fc1 = layer.linear1
        self.fc2 = layer.linear2

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (1, 1, S, d)，seq 占 W 维，pre-LN 自注意力 + 残差
        n = self.norm1(x)
        heads = []
        for h in range(self.nh):
            q = self.q_proj[h](n)
            k = self.k_proj[h](n)
            v = self.v_proj[h](n)
            s = torch.matmul(q, k.transpose(-1, -2)) * self.scale
            a = torch.softmax(s, dim=-1)
            heads.append(torch.matmul(a, v))
        attn = self.out_proj(torch.cat(heads, dim=3))  # feature 维 concat
        x = x + attn
        x = x + self.fc2(torch.relu(self.fc1(self.norm2(x))))
        return x


class DeployWrapper(nn.Module):
    def __init__(self, model: ActionPredictor):
        super().__init__()
        import copy
        m = model
        if not hasattr(m.image_encoder, "net"):
            raise ValueError(
                "部署版导出仅支持 backbone=simple（MobileNet 的 DFC 改造未实现）")
        ref_cnn = nn.Sequential(*list(m.image_encoder.net.children())[:-1])
        self.cnns = nn.ModuleList(
            [copy.deepcopy(ref_cnn) for _ in range(m._frame_len)])
        # img_proj ∘ AvgPool == AvgPool ∘ Conv1x1（均为线性算子，可交换）。
        # 把 img_proj 的权重折进每分支末尾的 1×1 卷积，偏置单独用加法，
        # 避免 DFC 对 3D Linear 的错误卷积融合
        self.proj_convs = nn.ModuleList()
        for _ in range(m._frame_len):
            c = nn.Conv2d(m.img_proj.in_features, m.img_proj.out_features, 1, bias=False)
            c.weight.data.copy_(m.img_proj.weight.data.view(
                m.img_proj.out_features, m.img_proj.in_features, 1, 1))
            self.proj_convs.append(c)
        self.proj_bias = nn.Parameter(m.img_proj.bias.data.clone())
        # 固定 kernel AvgPool 须与训练态 AdaptiveAvgPool2d(1) 逐值等价：
        # kernel 取 ref_cnn 的真实输出尺寸。H//16 只在分辨率能被 16 整除
        # 时成立（1080 高度下卷积输出 68 行，按 67 取 kernel 会静默丢弃
        # 末行，破坏与训练图的数值一致）
        with torch.no_grad():
            probe = ref_cnn(torch.zeros(
                1, 3, m._image_height, m._image_width))
        self.pool = nn.AvgPool2d((probe.shape[2], probe.shape[3]))
        # act_proj 权重注册为常量，用显式 MatMul+Add 代替 Linear，
        # 避免 DFC 对 3D Linear 的错误卷积融合
        self.act_w = nn.Parameter(m.act_proj.weight.data.t().clone())  # (D, d)
        self.act_b = nn.Parameter(m.act_proj.bias.data.clone())
        # R2 方案一：IMU 独立 token（imu_dim>0 时追加图输入，同 MatMul+Add 手法）
        self._imu_dim = m._imu_dim
        if self._imu_dim > 0:
            self.imu_w = nn.Parameter(m.imu_proj.weight.data.t().clone())
            self.imu_b = nn.Parameter(m.imu_proj.bias.data.clone())
        # pred_token 改为图输入（常量 initializer 会被 DFC 误转置），
        # 位置编码经 concat 后的 ew_add 广播可被正确推断
        self.pos_embed4 = nn.Parameter(m.pos_embed.data.unsqueeze(1).clone())  # (1, 1, S, d)
        self.blocks = nn.ModuleList(
            [ManualAttnBlock(l) for l in model.encoder.layers])
        self.head = m.head
        self._frame_len = m._frame_len

    def _frame_feat(self, frame_nhwc: torch.Tensor, i: int) -> torch.Tensor:
        x = self.cnns[i](frame_nhwc.permute(0, 3, 1, 2))
        x = self.proj_convs[i](x)                     # 1x1 conv ≡ 池化后的 img_proj
        return self.pool(x)                           # (1, d, 1, 1) NCHW

    def forward(self, *inputs: torch.Tensor) -> torch.Tensor:
        # inputs = frame0..frame{F-1}, action_history, pred_token, [imu]
        # 全程 (1, 1, S, d) 布局：seq 占 W 维，避免任何 transpose
        frames = list(inputs[:self._frame_len])
        action_history = inputs[self._frame_len]
        pred_token = inputs[self._frame_len + 1]
        imu = inputs[self._frame_len + 2] if self._imu_dim > 0 else None
        feats = [self._frame_feat(frames[i], i).permute(0, 2, 3, 1)
                 for i in range(self._frame_len)]     # (1, 1, 1, d) NHWC
        img_tokens = torch.cat(feats, dim=2) + self.proj_bias  # (1, 1, F, d)
        act_tokens = torch.matmul(action_history, self.act_w) + self.act_b  # (1, 1, H, d)
        parts = [img_tokens, act_tokens]
        if self._imu_dim > 0:
            # imu: (1, 1, 1, imu_dim) 归一化姿态 → (1, 1, 1, d)，位于 PRED 之前
            parts.append(torch.matmul(imu, self.imu_w) + self.imu_b)
        parts.append(pred_token)
        tokens = torch.cat(parts, dim=2)                       # (1, 1, S, d)
        tokens = tokens + self.pos_embed4
        for blk in self.blocks:
            tokens = blk(tokens)
        return self.head(tokens[:, :, -1:, :]).squeeze(1)  # (1, 1, action_dim)


def check_token_ranges(wrapper: "DeployWrapper", frame_len: int, height: int,
                       width: int, history_len: int, action_dim: int,
                       pred_token: torch.Tensor, max_abs: float = MAX_TOKEN_ABS,
                       action_stats_path: str = None,
                       normalize_mode: str = "zscore"):
    """导出前检查 token 量级：幅度过大时 uint8 量化会把小信号压成 0

    预训练用全零图像占位 → BN running_var 停在初始小值 → 推理时每级
    BN 放大 ~50 倍，图像 token 可达 ±2e5。DFC 在 token merge 点按最大
    幅度分配共享量化 scale（实测 scale=1965.92，pred_token/act_tokens
    全部量化成 0），HEF 输出对任何输入恒定。必须在编译前拦截。

    归一化模型（normalize_actions）的 HEF 输入是归一化后的历史，
    检查输入须与推理端一致（zscore → z 空间采样；fixed_range /
    log_range → [0,1]）。
    """
    rng = np.random.default_rng(42)
    raw = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    norm = (raw.astype(np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
    frames = [torch.tensor(norm[None]) for _ in range(frame_len)]
    history = rng.uniform(0.0, 500.0, size=(1, 1, history_len, action_dim))
    from action_norm import is_range_mode
    if is_range_mode(normalize_mode):
        # fixed_range / log_range 归一化空间即 [0,1]，直接在归一化空间采样
        history = rng.uniform(0.0, 1.0, size=(1, 1, history_len, action_dim))
    elif action_stats_path is not None:
        stats = np.load(action_stats_path)
        if stats.shape != (2, action_dim):
            raise ValueError(
                f"action stats 形状应为 (2, {action_dim})，"
                f"实际为 {stats.shape}: {action_stats_path}")
        # z-score 归一化模型直接在 z 空间采样：raw 均匀采样会在零方差列上
        # 产生天文数字 z 值（实际推理中这些列恒为 0，z 恒为 0）
        history = rng.normal(0.0, 1.5, size=(1, 1, history_len, action_dim))
    history = torch.tensor(history, dtype=torch.float32)
    with torch.no_grad():
        feats = [wrapper._frame_feat(f, i).permute(0, 2, 3, 1)
                 for i, f in enumerate(frames)]
        img_tokens = torch.cat(feats, dim=2) + wrapper.proj_bias
        act_tokens = torch.matmul(history, wrapper.act_w) + wrapper.act_b
    img_max = img_tokens.abs().max().item()
    act_max = act_tokens.abs().max().item()
    pred_max = pred_token.abs().max().item()
    print(f"token 量级检查: |img|={img_max:.4g} |act|={act_max:.4g} "
          f"|pred|={pred_max:.4g}（图像上限 {max_abs}）")
    if img_max > max_abs:
        raise SystemExit(
            f"错误: 图像 token 幅度 {img_max:.3g} 超过可量化上限 {max_abs}。"
            f"checkpoint 的 BN 统计退化（预训练用全零图像占位，running_var "
            f"停在初始小值），uint8 量化会把小信号全部压成 0、HEF 输出恒定。"
            f"请先用真实/随机图像数据微调后再导出。")
    if act_max > MERGE_RATIO_WARN * max(img_max, 1.0):
        print(f"WARNING: act_tokens 幅度 {act_max:.3g} 远大于 img_tokens "
              f"{img_max:.3g}，merge 点共享 scale 会粗量化图像路径；"
              f"建议用 --normalize-actions 训练（动作归一化）压小 act_tokens")


def main():
    parser = argparse.ArgumentParser(description="Export Hailo-friendly ONNX")
    parser.add_argument("--model", required=True, help="checkpoint path")
    parser.add_argument("--output", default="checkpoints/actpred_deploy.onnx")
    parser.add_argument("--pred-token-out", default=None,
                        help="保存学习到的 pred_token 到 .npy（HEF 推理端喂入用，"
                             "配合抖动校准避免常量量化退化）")
    args = parser.parse_args()

    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    cfg = parse_checkpoint_config(checkpoint["config"])

    model = ActionPredictor(cfg.model, cfg.data, pretrained=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    wrapper = DeployWrapper(model).eval()

    h, w = cfg.data.image_height, cfg.data.image_width
    f = cfg.data.frame_len
    stats_path = None
    from action_norm import is_range_mode
    if cfg.data.normalize_actions and not is_range_mode(cfg.data.normalize_mode):
        # 与 predict.py 语义一致：相对路径按 checkpoint 目录解析
        # （fixed_range / log_range 模式免 stats 文件，跳过）
        stats_path = cfg.data.action_stats_path
        if not stats_path:
            stats_path = os.path.join(
                os.path.dirname(os.path.abspath(args.model)),
                "action_stats.npy")
        elif not os.path.isabs(stats_path):
            stats_path = os.path.join(
                os.path.dirname(os.path.abspath(args.model)), stats_path)
        if not os.path.exists(stats_path):
            raise FileNotFoundError(
                f"模型训练时启用了动作归一化，但未找到统计文件: {stats_path}")
    check_token_ranges(wrapper, f, h, w, cfg.data.history_len,
                       cfg.data.action_dim, model.pred_token.data.unsqueeze(1),
                       action_stats_path=stats_path,
                       normalize_mode=cfg.data.normalize_mode)
    dummy_frames = [torch.randn(1, h, w, 3) for _ in range(f)]
    dummy_history = torch.randn(1, 1, cfg.data.history_len, cfg.data.action_dim)
    dummy_pred = model.pred_token.data.unsqueeze(1).clone()  # (1, 1, 1, d)
    imu_dim = cfg.data.imu_dim
    # imu_dim>0 时追加归一化姿态输入（均匀采样的归一化值域）
    dummy_imu = torch.randn(1, 1, 1, imu_dim) if imu_dim > 0 else None

    extra_inputs = (dummy_imu,) if imu_dim > 0 else ()
    extra_names = ["imu"] if imu_dim > 0 else []

    with torch.no_grad():
        ref = wrapper(*dummy_frames, dummy_history, dummy_pred, *extra_inputs)

    torch.onnx.export(
        wrapper, (*dummy_frames, dummy_history, dummy_pred, *extra_inputs),
        args.output,
        input_names=[f"frame{i}" for i in range(f)] + ["action_history", "pred_token"] + extra_names,
        output_names=["predicted_action"],
        opset_version=18,
    )
    print(f"部署版 ONNX 已导出: {args.output}（{f} 个帧输入 (1,{h},{w},3) NHWC + action_history + pred_token"
          f"{' + imu' if imu_dim > 0 else ''}）")

    if args.pred_token_out:
        import numpy as np
        np.save(args.pred_token_out, model.pred_token.data.unsqueeze(1).numpy())
        print(f"pred_token 已保存: {args.pred_token_out} (1,1,1,{model.pred_token.shape[-1]})")

    stacked = torch.stack([fr.permute(0, 3, 1, 2) for fr in dummy_frames], dim=1)
    with torch.no_grad():
        orig_out = model(stacked, dummy_history.squeeze(1),
                         dummy_imu.squeeze(1).squeeze(1) if imu_dim > 0 else None)
    wrap_diff = (ref.squeeze(1) - orig_out).abs().max().item()
    print(f"wrapper 与原模型最大差异: {wrap_diff:.6e}")
    if wrap_diff > MAX_TOLERATED_DIFF:
        sys.exit(f"错误: wrapper 与原模型差异 {wrap_diff:.3e} > "
                 f"{MAX_TOLERATED_DIFF:.0e}（ManualAttnBlock 布局约定可能已失效）")

    try:
        import onnxruntime as ort
        import numpy as np
        sess = ort.InferenceSession(args.output)
        feeds = {f"frame{i}": dummy_frames[i].numpy() for i in range(f)}
        feeds["action_history"] = dummy_history.numpy()
        feeds["pred_token"] = dummy_pred.numpy()
        if imu_dim > 0:
            feeds["imu"] = dummy_imu.numpy()
        ort_out = sess.run(None, feeds)[0]
        ort_diff = np.abs(ort_out - ref.numpy()).max()
        if ort_diff > MAX_TOLERATED_DIFF:
            sys.exit(f"错误: ONNX 与 wrapper 差异 {ort_diff:.3e} > "
                     f"{MAX_TOLERATED_DIFF:.0e}")
        print(f"ONNX 一致性验证通过（最大差异: {ort_diff:.6e}）")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
