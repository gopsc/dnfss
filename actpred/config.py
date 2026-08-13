import argparse
from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class DataConfig:
    image_width: int = 1920
    image_height: int = 1080
    action_dim: int = 18
    history_len: int = 5
    frame_len: int = 3
    data_dir: str = "./data"
    normalize_actions: bool = False
    action_stats_path: Optional[str] = None


@dataclass
class ModelConfig:
    cnn_backbone: str = "simple"
    cnn_feature_dim: int = 128
    fused_hidden_dim: int = 128
    dropout: float = 0.1
    attn_dim: int = 128
    attn_heads: int = 4
    attn_layers: int = 2


@dataclass
class TrainConfig:
    # 默认输入分辨率 1920×1080，batch=32 在 CPU/Pi 上内存不可行；
    # 高分辨率训练取小 batch，小分辨率可用 --batch-size 调大
    batch_size: int = 4
    epochs: int = 100
    lr: float = 1e-3
    weight_decay: float = 1e-4
    val_split: float = 0.15
    checkpoint_dir: str = "./checkpoints"
    patience: int = 15
    num_workers: int = 2
    seed: int = 42
    max_grad_norm: float = 1.0


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
    parser.add_argument("--image-width", type=int, default=None)
    parser.add_argument("--image-height", type=int, default=None)
    parser.add_argument("--action-dim", type=int, default=None)
    parser.add_argument("--history-len", type=int, default=None)
    parser.add_argument("--frame-len", type=int, default=None)
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--normalize-actions", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--action-stats-path", type=str, default=None)
    parser.add_argument("--backbone", type=str, default=None, choices=["simple", "mobilenet"])
    parser.add_argument("--cnn-feature-dim", type=int, default=None)
    parser.add_argument("--fused-hidden-dim", type=int, default=None)
    parser.add_argument("--dropout", type=float, default=None)
    parser.add_argument("--attn-dim", type=int, default=None)
    parser.add_argument("--attn-heads", type=int, default=None)
    parser.add_argument("--attn-layers", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--weight-decay", type=float, default=None)
    parser.add_argument("--val-split", type=float, default=None)
    parser.add_argument("--checkpoint-dir", type=str, default=None)
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument("--num-workers", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--max-grad-norm", type=float, default=None)
    parser.add_argument("--init-from", type=str, default=None,
                        help="预训练 checkpoint 路径，作为微调起点（strict=False 加载兼容权重）")
    parser.add_argument("--resume", type=str, default=None,
                        help="从 last.pt 断点续训（恢复 model/optimizer/scheduler/epoch）")
    parser.add_argument("--save-config", type=str, default=None, help="保存配置到 JSON 文件")
    return parser


def validate_config_values(data_cfg: DataConfig, model_cfg: ModelConfig,
                           train_cfg: TrainConfig = None):
    """校验从 checkpoint 恢复或 CLI 传入的配置值域合理性

    防止恶意/损坏 checkpoint 注入异常值（如超大维度导致 OOM），
    也防止训练 CLI 越界参数（如 --history-len 0 / --lr -1）静默进入训练。
    """
    checks = [
        ("image_width", data_cfg.image_width, 1, 4096, int),
        ("image_height", data_cfg.image_height, 1, 4096, int),
        ("history_len", data_cfg.history_len, 1, 1024, int),
        ("frame_len", data_cfg.frame_len, 1, 1024, int),
        ("action_dim", data_cfg.action_dim, 1, 1024, int),
        ("cnn_feature_dim", model_cfg.cnn_feature_dim, 1, 4096, int),
        ("fused_hidden_dim", model_cfg.fused_hidden_dim, 1, 4096, int),
        ("dropout", model_cfg.dropout, 0.0, 1.0, float),
        ("attn_dim", model_cfg.attn_dim, 1, 4096, int),
        ("attn_heads", model_cfg.attn_heads, 1, 64, int),
        ("attn_layers", model_cfg.attn_layers, 1, 64, int),
    ]
    for name, val, lo, hi, typ in checks:
        if typ is int:
            ok = isinstance(val, int) and not isinstance(val, bool) and lo <= val <= hi
        else:
            ok = isinstance(val, (int, float)) and not isinstance(val, bool) and lo <= val <= hi
        if not ok:
            raise ValueError(
                f"checkpoint config {name}={val} 超出合理范围 [{lo}, {hi}]")

    if model_cfg.attn_dim % model_cfg.attn_heads != 0:
        raise ValueError(
            f"attn_dim={model_cfg.attn_dim} 必须能被 attn_heads="
            f"{model_cfg.attn_heads} 整除")

    if train_cfg is not None:
        t_checks = [
            ("batch_size", train_cfg.batch_size, 1, 65536, int),
            ("epochs", train_cfg.epochs, 1, 100000, int),
            ("lr", train_cfg.lr, 1e-9, 10.0, float),
            ("weight_decay", train_cfg.weight_decay, 0.0, 1.0, float),
            ("val_split", train_cfg.val_split, 0.0, 1.0, float),
            ("patience", train_cfg.patience, 0, 100000, int),
            ("num_workers", train_cfg.num_workers, 0, 256, int),
            ("max_grad_norm", train_cfg.max_grad_norm, 0.0, 1e6, float),
        ]
        for name, val, lo, hi, typ in t_checks:
            if typ is int:
                ok = isinstance(val, int) and not isinstance(val, bool) and lo <= val <= hi
            else:
                ok = isinstance(val, (int, float)) and not isinstance(val, bool) and lo <= val <= hi
            if not ok:
                raise ValueError(
                    f"配置 {name}={val} 超出合理范围 [{lo}, {hi}]")


def warn_init_from_mismatch(init_cfg: dict, current: Config):
    """--init-from 加载预训练权重前的兼容性警告（train.py / pretrain_mt.py 共用）

    normalize_actions 不一致时输入分布与权重不匹配，预训练收益大打折扣；
    action_dim 不一致时 act_proj 无法加载对应权重（strict=False 静默跳过，
    该层随机初始化）。
    """
    if not isinstance(init_cfg, dict):
        return
    init_data = init_cfg.get("data") or {}
    init_norm = init_data.get("normalize_actions", False)
    if init_norm != current.data.normalize_actions:
        print(f"WARNING: init-from checkpoint 的 normalize_actions="
              f"{init_norm} 与当前运行 {current.data.normalize_actions} "
              f"不一致，输入分布不匹配会损失预训练收益（见 README 预训练说明）")
    init_dim = init_data.get("action_dim")
    if init_dim is not None and init_dim != current.data.action_dim:
        print(f"WARNING: init-from checkpoint 的 action_dim={init_dim} "
              f"与当前运行 {current.data.action_dim} 不一致，"
              f"act_proj 权重将随机初始化")


def parse_checkpoint_config(cfg_dict: dict) -> Config:
    """从 checkpoint 恢复 Config，校验键集合与值域

    - 顶层缺 data/model 键 → ValueError（带明确信息）
    - data/model/train 段含未知键 → ValueError（列出未知键，避免裸 TypeError）
    - 值域非法 → ValueError（复用 validate_config_values）
    """
    if not isinstance(cfg_dict, dict):
        raise ValueError(f"checkpoint config 应为 dict，实际为 {type(cfg_dict).__name__}")
    section_dataclasses = {
        "data": DataConfig,
        "model": ModelConfig,
        "train": TrainConfig,
    }
    for section, dataclass in section_dataclasses.items():
        if section not in cfg_dict:
            if section in ("data", "model"):
                raise ValueError(f"checkpoint config 缺少 '{section}' 段")
            continue
        section_dict = cfg_dict[section]
        if not isinstance(section_dict, dict):
            raise ValueError(
                f"checkpoint config['{section}'] 应为 dict，"
                f"实际为 {type(section_dict).__name__}")
        unknown = set(section_dict) - set(dataclass.__dataclass_fields__)
        if unknown:
            raise ValueError(
                f"checkpoint config['{section}'] 含未知键: {sorted(unknown)}")
    cfg = Config.from_dict(cfg_dict)
    validate_config_values(cfg.data, cfg.model, cfg.train)
    return cfg


def apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    mapping = {
        "image_width": ("data", "image_width"),
        "image_height": ("data", "image_height"),
        "action_dim": ("data", "action_dim"),
        "history_len": ("data", "history_len"),
        "frame_len": ("data", "frame_len"),
        "data_dir": ("data", "data_dir"),
        "normalize_actions": ("data", "normalize_actions"),
        "action_stats_path": ("data", "action_stats_path"),
        "backbone": ("model", "cnn_backbone"),
        "cnn_feature_dim": ("model", "cnn_feature_dim"),
        "fused_hidden_dim": ("model", "fused_hidden_dim"),
        "dropout": ("model", "dropout"),
        "attn_dim": ("model", "attn_dim"),
        "attn_heads": ("model", "attn_heads"),
        "attn_layers": ("model", "attn_layers"),
        "batch_size": ("train", "batch_size"),
        "epochs": ("train", "epochs"),
        "lr": ("train", "lr"),
        "weight_decay": ("train", "weight_decay"),
        "val_split": ("train", "val_split"),
        "checkpoint_dir": ("train", "checkpoint_dir"),
        "patience": ("train", "patience"),
        "num_workers": ("train", "num_workers"),
        "seed": ("train", "seed"),
        "max_grad_norm": ("train", "max_grad_norm"),
    }
    args_dict = vars(args)
    for arg_name, (section, field) in mapping.items():
        val = args_dict.get(arg_name)
        if val is not None:
            setattr(getattr(cfg, section), field, val)
    return cfg
