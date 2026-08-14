import argparse
from dataclasses import dataclass, asdict, field
from typing import List, Optional


@dataclass
class DataConfig:
    image_width: int = 640
    image_height: int = 480
    action_dim: int = 18
    history_len: int = 5
    frame_len: int = 3
    data_dir: str = "./data"
    normalize_actions: bool = True
    action_stats_path: Optional[str] = None
    # 归一化方式（仅 normalize_actions=True 时生效）：
    #   "zscore"      数据统计 z-score（现状，依赖 action_stats.npy）
    #   "fixed_range" 固定物理范围 min-max → [0,1]（免 stats 文件，零状态部署，
    #                 常量表见 action_norm.py；越界值 clamp 防御）
    normalize_mode: str = "zscore"
    # 逐列损失权重（仅训练损失用，推理端无感知）：None → action_norm
    # .default_column_weights(action_dim)（舵机 1.0 / spk 0.2 / 保留位 0.0）。
    # 显式指定时长度须等于 action_dim。
    column_weights: Optional[List[float]] = None
    # R2 方案一：MPU6050 姿态作为独立 IMU token 注入（序列 F+H+2）。
    # 0 = 关闭（默认，序列保持 F+H+1，旧 checkpoint 完全兼容）；
    # 8×N = N 个传感器的 roll,pitch,ax,ay,az,gx,gy,gz 按 pybot 脚本
    # @MPU6050_BIND 行顺序拼接（最多 4 个 → 32，pybot recorder 的 imu.npy 格式）。
    imu_dim: int = 0


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
    # 默认输入分辨率 640×480（1080p 采集的预处理缩放在实机测速中主导
    # 端到端延迟，见测速报告 §6.1）；更高分辨率训练时需调小 batch
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
    augment: bool = False
    # R1-C 残差预测：True 时 target 改为 Δ=actions[t]-actions[t-1]，
    # 推理端 â_t = a_{t-1} + Δ̂（惯性先验，平滑动作）。默认 False 兼容旧 checkpoint。
    predict_delta: bool = False
    # R1-B 多步展开训练：rollout_len>1 时 dataset 返回 K 步连续序列，
    # 训练用 scheduled sampling（前 ss_prob 比例步用真历史，其余用自预测回传），
    # 治 exposure bias。rollout_len=1 时退化为现状单步。
    rollout_len: int = 1
    ss_prob: float = 1.0


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
    parser.add_argument("--normalize-mode", type=str, default=None,
                        choices=["zscore", "fixed_range"],
                        help="动作归一化方式：zscore=数据统计（依赖 action_stats.npy）；"
                             "fixed_range=固定物理范围 min-max→[0,1]（免 stats 文件）")
    parser.add_argument("--column-weights", type=str, default=None,
                        help="逐列损失权重（逗号分隔 action_dim 个浮点），"
                             "缺省用默认表（舵机1.0/spk0.2/保留位0.0）；仅影响训练损失")
    parser.add_argument("--imu-dim", type=int, default=None,
                        help="R2 方案一：MPU6050 姿态维度（0=关闭，默认；"
                             "8×N=N 个传感器拼接（≤4 个），序列变 F+H+2）")
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
    parser.add_argument("--augment", action=argparse.BooleanOptionalAction, default=None,
                        help="训练时启用安全图像增强（ColorJitter：亮度/对比度/饱和度/色调）")
    parser.add_argument("--predict-delta", action=argparse.BooleanOptionalAction, default=None,
                        help="R1-C 残差预测：target 改为 Δ=actions[t]-actions[t-1]，"
                             "推理端 â_t=a_{t-1}+Δ̂（惯性先验平滑动作，默认关兼容旧 checkpoint）")
    parser.add_argument("--rollout-len", type=int, default=None,
                        help="R1-B 多步展开训练步数 K（默认 1=单步；K>1 时 scheduled sampling）")
    parser.add_argument("--ss-prob", type=float, default=None,
                        help="scheduled sampling 真实历史概率（仅 rollout-len>1 生效，默认 1.0=全真）")
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
        ("imu_dim", data_cfg.imu_dim, 0, 64, int),
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

    if data_cfg.normalize_mode not in ("zscore", "fixed_range"):
        raise ValueError(
            f"normalize_mode={data_cfg.normalize_mode!r} 非法，"
            f"应为 'zscore' 或 'fixed_range'")

    if data_cfg.column_weights is not None:
        w = data_cfg.column_weights
        if len(w) != data_cfg.action_dim:
            raise ValueError(
                f"column_weights 长度 {len(w)} 应等于 action_dim="
                f"{data_cfg.action_dim}")
        if any((not isinstance(v, (int, float))) or isinstance(v, bool)
               or not (0.0 <= v <= 1e6) for v in w):
            raise ValueError(
                f"column_weights 元素应为 [0, 1e6] 内的数值，实际: {w}")
        if not any(v > 0 for v in w):
            raise ValueError("column_weights 全 0：损失恒 0，训练无梯度")

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
            ("rollout_len", train_cfg.rollout_len, 1, 1024, int),
            ("ss_prob", train_cfg.ss_prob, 0.0, 1.0, float),
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
    init_mode = init_data.get("normalize_mode", "zscore")  # 旧 checkpoint 无此键 → zscore
    if init_norm and current.data.normalize_actions and init_mode != current.data.normalize_mode:
        print(f"WARNING: init-from checkpoint 的 normalize_mode={init_mode} "
              f"与当前运行 {current.data.normalize_mode} 不一致，"
              f"归一化空间不同会损失预训练收益")
    init_dim = init_data.get("action_dim")
    if init_dim is not None and init_dim != current.data.action_dim:
        print(f"WARNING: init-from checkpoint 的 action_dim={init_dim} "
              f"与当前运行 {current.data.action_dim} 不一致，"
              f"act_proj 权重将随机初始化")
    init_imu = init_data.get("imu_dim", 0)      # 旧 checkpoint 无此键 → 0
    if init_imu != current.data.imu_dim:
        print(f"WARNING: init-from checkpoint 的 imu_dim={init_imu} "
              f"与当前运行 {current.data.imu_dim} 不一致，"
              f"imu_proj/pos_embed 权重将随机初始化")


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
        "normalize_mode": ("data", "normalize_mode"),
        "column_weights": ("data", "column_weights"),
        "imu_dim": ("data", "imu_dim"),
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
        "augment": ("train", "augment"),
        "predict_delta": ("train", "predict_delta"),
        "rollout_len": ("train", "rollout_len"),
        "ss_prob": ("train", "ss_prob"),
    }
    args_dict = vars(args)
    for arg_name, (section, field) in mapping.items():
        val = args_dict.get(arg_name)
        if val is not None:
            if field == "column_weights" and isinstance(val, str):
                val = [float(v) for v in val.split(",")]
            setattr(getattr(cfg, section), field, val)
    return cfg
