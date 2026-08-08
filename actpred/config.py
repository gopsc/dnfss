import argparse
from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class DataConfig:
    image_size: int = 64
    action_dim: int = 8
    history_len: int = 5
    data_dir: str = "./data"
    normalize_actions: bool = False
    action_stats_path: Optional[str] = None


@dataclass
class ModelConfig:
    cnn_backbone: str = "simple"
    cnn_feature_dim: int = 128
    action_hidden_dim: int = 64
    fused_hidden_dim: int = 128
    dropout: float = 0.1
    history_encoder: str = "mlp"


@dataclass
class TrainConfig:
    batch_size: int = 32
    epochs: int = 100
    lr: float = 1e-3
    weight_decay: float = 1e-4
    val_split: float = 0.15
    checkpoint_dir: str = "./checkpoints"
    patience: int = 15
    num_workers: int = 2


@dataclass
class Config:
    data: DataConfig = field(default_factory=DataConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    train: TrainConfig = field(default_factory=TrainConfig)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(
            data=DataConfig(**d.get("data", {})),
            model=ModelConfig(**d.get("model", {})),
            train=TrainConfig(**d.get("train", {})),
        )


def build_argparser(description: str = "") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--image-size", type=int, default=None)
    parser.add_argument("--action-dim", type=int, default=None)
    parser.add_argument("--history-len", type=int, default=None)
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--normalize-actions", action="store_true", default=None)
    parser.add_argument("--action-stats-path", type=str, default=None)
    parser.add_argument("--backbone", type=str, default=None, choices=["simple", "mobilenet"])
    parser.add_argument("--cnn-feature-dim", type=int, default=None)
    parser.add_argument("--action-hidden-dim", type=int, default=None)
    parser.add_argument("--fused-hidden-dim", type=int, default=None)
    parser.add_argument("--dropout", type=float, default=None)
    parser.add_argument("--history-encoder", type=str, default=None, choices=["mlp", "gru"])
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--weight-decay", type=float, default=None)
    parser.add_argument("--val-split", type=float, default=None)
    parser.add_argument("--checkpoint-dir", type=str, default=None)
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument("--num-workers", type=int, default=None)
    parser.add_argument("--save-config", type=str, default=None, help="保存配置到 JSON 文件")
    return parser


def apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    mapping = {
        "image_size": ("data", "image_size"),
        "action_dim": ("data", "action_dim"),
        "history_len": ("data", "history_len"),
        "data_dir": ("data", "data_dir"),
        "normalize_actions": ("data", "normalize_actions"),
        "action_stats_path": ("data", "action_stats_path"),
        "backbone": ("model", "cnn_backbone"),
        "cnn_feature_dim": ("model", "cnn_feature_dim"),
        "action_hidden_dim": ("model", "action_hidden_dim"),
        "fused_hidden_dim": ("model", "fused_hidden_dim"),
        "dropout": ("model", "dropout"),
        "history_encoder": ("model", "history_encoder"),
        "batch_size": ("train", "batch_size"),
        "epochs": ("train", "epochs"),
        "lr": ("train", "lr"),
        "weight_decay": ("train", "weight_decay"),
        "val_split": ("train", "val_split"),
        "checkpoint_dir": ("train", "checkpoint_dir"),
        "patience": ("train", "patience"),
        "num_workers": ("train", "num_workers"),
    }
    args_dict = vars(args)
    for arg_name, (section, field) in mapping.items():
        val = args_dict.get(arg_name)
        if val is not None:
            setattr(getattr(cfg, section), field, val)
    return cfg
