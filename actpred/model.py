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


class MLPHistoryEncoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = 64, dropout: float = 0.1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class GRUHistoryEncoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = 64, dropout: float = 0.1):
        super().__init__()
        self.gru = nn.GRU(input_size=input_dim, hidden_size=hidden_dim,
                          num_layers=1, batch_first=True)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, h = self.gru(x)
        return self.dropout(h.squeeze(0))


class FusionHead(nn.Module):
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
    def __init__(self, model_cfg: ModelConfig, data_cfg: DataConfig,
                 pretrained: bool = True):
        super().__init__()
        if model_cfg.cnn_backbone == "mobilenet":
            self.image_encoder = MobileNetEncoder(
                model_cfg.cnn_feature_dim, pretrained=pretrained)
        else:
            self.image_encoder = SimpleCNN(model_cfg.cnn_feature_dim)

        history_input_dim = data_cfg.history_len * data_cfg.action_dim
        if model_cfg.history_encoder == "gru":
            self.action_encoder = GRUHistoryEncoder(
                data_cfg.action_dim, model_cfg.action_hidden_dim, model_cfg.dropout)
        else:
            self.action_encoder = MLPHistoryEncoder(
                history_input_dim, model_cfg.action_hidden_dim, model_cfg.dropout)

        fused_input = model_cfg.cnn_feature_dim + model_cfg.action_hidden_dim
        self.head = FusionHead(
            fused_input, model_cfg.fused_hidden_dim, data_cfg.action_dim, model_cfg.dropout)

        self._use_gru = model_cfg.history_encoder == "gru"
        self._history_len = data_cfg.history_len
        self._action_dim = data_cfg.action_dim

    def forward(self, image: torch.Tensor, action_history: torch.Tensor) -> torch.Tensor:
        img_feat = self.image_encoder(image)
        if self._use_gru:
            act_feat = self.action_encoder(action_history)
        else:
            act_feat = self.action_encoder(action_history.flatten(1))
        return self.head(torch.cat([img_feat, act_feat], dim=-1))
