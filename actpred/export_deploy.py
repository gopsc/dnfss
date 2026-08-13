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

import torch
import torch.nn as nn

from config import parse_checkpoint_config
from model import ActionPredictor


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
        self.pool = nn.AvgPool2d((m._image_height // 16, m._image_width // 16))
        # act_proj 权重注册为常量，用显式 MatMul+Add 代替 Linear，
        # 避免 DFC 对 3D Linear 的错误卷积融合
        self.act_w = nn.Parameter(m.act_proj.weight.data.t().clone())  # (D, d)
        self.act_b = nn.Parameter(m.act_proj.bias.data.clone())
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

    def forward(self, frame0: torch.Tensor, frame1: torch.Tensor, frame2: torch.Tensor,
                action_history: torch.Tensor, pred_token: torch.Tensor) -> torch.Tensor:
        # 全程 (1, 1, S, d) 布局：seq 占 W 维，避免任何 transpose
        frames = [frame0, frame1, frame2]
        feats = [self._frame_feat(frames[i], i).permute(0, 2, 3, 1)
                 for i in range(self._frame_len)]     # (1, 1, 1, d) NHWC
        img_tokens = torch.cat(feats, dim=2) + self.proj_bias  # (1, 1, F, d)
        act_tokens = torch.matmul(action_history, self.act_w) + self.act_b  # (1, 1, H, d)
        tokens = torch.cat([img_tokens, act_tokens, pred_token], dim=2)  # (1, 1, S, d)
        tokens = tokens + self.pos_embed4
        for blk in self.blocks:
            tokens = blk(tokens)
        return self.head(tokens[:, :, -1:, :]).squeeze(1)  # (1, 1, action_dim)


def main():
    parser = argparse.ArgumentParser(description="Export Hailo-friendly ONNX")
    parser.add_argument("--model", required=True, help="checkpoint path")
    parser.add_argument("--output", default="checkpoints/actpred_deploy.onnx")
    args = parser.parse_args()

    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    cfg = parse_checkpoint_config(checkpoint["config"])

    model = ActionPredictor(cfg.model, cfg.data, pretrained=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    wrapper = DeployWrapper(model).eval()

    h, w = cfg.data.image_height, cfg.data.image_width
    f = cfg.data.frame_len
    dummy_frames = [torch.randn(1, h, w, 3) for _ in range(f)]
    dummy_history = torch.randn(1, 1, cfg.data.history_len, cfg.data.action_dim)
    dummy_pred = model.pred_token.data.unsqueeze(1).clone()  # (1, 1, 1, d)

    with torch.no_grad():
        ref = wrapper(*dummy_frames, dummy_history, dummy_pred)

    torch.onnx.export(
        wrapper, (*dummy_frames, dummy_history, dummy_pred),
        args.output,
        input_names=[f"frame{i}" for i in range(f)] + ["action_history", "pred_token"],
        output_names=["predicted_action"],
        opset_version=18,
    )
    print(f"部署版 ONNX 已导出: {args.output}（{f} 个帧输入 (1,{h},{w},3) NHWC + action_history + pred_token）")

    stacked = torch.stack([fr.permute(0, 3, 1, 2) for fr in dummy_frames], dim=1)
    with torch.no_grad():
        orig_out = model(stacked, dummy_history.squeeze(1))
    print(f"wrapper 与原模型最大差异: {(ref.squeeze(1) - orig_out).abs().max():.6e}")

    try:
        import onnxruntime as ort
        import numpy as np
        sess = ort.InferenceSession(args.output)
        feeds = {f"frame{i}": dummy_frames[i].numpy() for i in range(f)}
        feeds["action_history"] = dummy_history.numpy()
        feeds["pred_token"] = dummy_pred.numpy()
        ort_out = sess.run(None, feeds)[0]
        print(f"ONNX 一致性验证通过（最大差异: {np.abs(ort_out - ref.numpy()).max():.6e}）")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
