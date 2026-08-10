import torch
import torch.nn as nn
from config import DataConfig, ModelConfig


class SimpleCNN(nn.Module):
    def __init__(self, feature_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(3, 16, 3, stride=2, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, 32, 3, stride=2, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 64, 3, stride=2, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.Conv2d(64, feature_dim, 3, stride=2, padding=1),
            nn.BatchNorm2d(feature_dim),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).flatten(1)


class MobileNetEncoder(nn.Module):
    def __init__(self, feature_dim: int = 128, pretrained: bool = True):
        super().__init__()
        from torchvision.models import mobilenet_v2, MobileNet_V2_Weights
        weights = MobileNet_V2_Weights.DEFAULT if pretrained else None
        try:
            backbone = mobilenet_v2(weights=weights)
        except Exception as e:
            if not pretrained:
                raise RuntimeError("加载 MobileNetV2 架构失败") from e
            # 主源下载失败，尝试加载已缓存的权重文件
            try:
                import os
                hub_dir = torch.hub.get_dir()
                cache_dir = os.path.join(hub_dir, "checkpoints")
                weight_file = getattr(weights, "file_name",
                                      "mobilenet_v2-b0353104.pth")
                cached_path = os.path.join(cache_dir, weight_file)
                if os.path.exists(cached_path):
                    print(f"INFO: 使用缓存权重 {cached_path}")
                    backbone = mobilenet_v2(weights=None)
                    backbone.load_state_dict(torch.load(
                        cached_path, map_location="cpu",
                        weights_only=True))
                else:
                    raise FileNotFoundError(cached_path)
            except Exception:
                raise RuntimeError(
                    "MobileNetV2 预训练权重下载失败，可能是网络受限。"
                    "请在联网环境中预下载权重"
                    "（运行 torchvision.models.mobilenet_v2(weights='IMAGENET1K_V1')），"
                    "并将 ~/.cache/torch/hub/checkpoints/ 下的权重文件"
                    "拷贝到目标机器同路径。"
                ) from e
        self.features = backbone.features
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.proj = nn.Linear(1280, feature_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.pool(x).flatten(1)
        return self.proj(x)


class PredictionHead(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int, dropout: float = 0.1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(inplace=True),
            nn.Linear(hidden_dim // 2, output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class ActionPredictor(nn.Module):
    """多帧图像 + 历史动作的 Transformer 注意力融合预测器

    token 序列：[img×F, act×H, PRED]，长度固定为 F+H+1，
    取 PRED 位置输出进预测头回归下一时间步动作。
    除 batch 维外全部维度静态，保证 ONNX/NPU 部署友好。
    """

    def __init__(self, model_cfg: ModelConfig, data_cfg: DataConfig,
                 pretrained: bool = True):
        super().__init__()
        if model_cfg.cnn_backbone == "mobilenet":
            self.image_encoder = MobileNetEncoder(
                model_cfg.cnn_feature_dim, pretrained=pretrained)
        else:
            self.image_encoder = SimpleCNN(model_cfg.cnn_feature_dim)

        d = model_cfg.attn_dim
        self.img_proj = nn.Linear(model_cfg.cnn_feature_dim, d)
        self.act_proj = nn.Linear(data_cfg.action_dim, d)

        seq_len = data_cfg.frame_len + data_cfg.history_len + 1
        self.pred_token = nn.Parameter(torch.zeros(1, 1, d))
        self.pos_embed = nn.Parameter(torch.zeros(1, seq_len, d))
        nn.init.trunc_normal_(self.pred_token, std=0.02)
        nn.init.trunc_normal_(self.pos_embed, std=0.02)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d, nhead=model_cfg.attn_heads,
            dim_feedforward=d * 2, dropout=model_cfg.dropout,
            batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(
            encoder_layer, num_layers=model_cfg.attn_layers,
            enable_nested_tensor=False)

        self.head = PredictionHead(
            d, model_cfg.fused_hidden_dim, data_cfg.action_dim, model_cfg.dropout)

        self._frame_len = data_cfg.frame_len
        self._history_len = data_cfg.history_len
        self._action_dim = data_cfg.action_dim
        self._image_size = data_cfg.image_size
        self._cnn_feature_dim = model_cfg.cnn_feature_dim

    def forward(self, images: torch.Tensor, action_history: torch.Tensor) -> torch.Tensor:
        # images: (B, F, 3, S, S)  action_history: (B, H, D)
        b = images.size(0)
        frames = images.reshape(
            b * self._frame_len, 3, self._image_size, self._image_size)
        img_feat = self.image_encoder(frames).reshape(
            b, self._frame_len, self._cnn_feature_dim)
        img_tokens = self.img_proj(img_feat)
        act_tokens = self.act_proj(action_history)
        pred_token = self.pred_token.expand(b, -1, -1)
        tokens = torch.cat([img_tokens, act_tokens, pred_token], dim=1)
        tokens = tokens + self.pos_embed
        out = self.encoder(tokens)
        return self.head(out[:, -1])
