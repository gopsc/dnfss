"""R4 离线评估基准：开环滚动预测，量化闭环代理指标

现状只有开环单步 MSE（teacher forcing），无法反映真实闭环表现。本脚本在留出
episode 上复现 pybot 闭环的开环滚动（图像用真值滚动、动作用自预测回写），
输出三类闭环代理指标：

  累积误差   逐步 MSE 及随步数的增长曲线（衡量误差累积/发散）
  平滑度     预测动作相邻差分方差（越低越平滑，对物理舵机越友好），与真值对比
  物理可行性 预测动作落在训练数据经验范围外的比例（越界率，越高越危险）

用法：
    python evaluate.py --model checkpoints/best_inference.pt --data-dir ./data
    python evaluate.py --model A.pt --compare B.pt --data-dir ./data  # A/B 对比
    python evaluate.py --model m.pt --data-dir ./data --episodes 3 --horizon 50 \
        --output report.json
"""
import argparse
import json
import numpy as np
from PIL import Image

from dataset import ActionDataset
from predict import ActionPredictorInference


def _episode_ids(data_dir):
    return [d.name for d in ActionDataset._valid_episode_dirs(data_dir)]


def _load_episode(data_dir, ep_name):
    """加载单个 episode 的图像路径（自然序）、动作真值 (T, D)、姿态真值 (T, imu_dim)

    imu.npy 缺失时第三项返回 None（旧数据兼容，滚动时按零向量喂入）。
    姿态保持原始量纲——predict 端内部归一化，与 pybot 闭环口径一致。
    """
    ep_dir = [d for d in ActionDataset._valid_episode_dirs(data_dir)
              if d.name == ep_name][0]
    from dataset import _natural_key
    images_dir = ep_dir / "images"
    image_files = sorted(list(images_dir.glob("*.jpg")) +
                         list(images_dir.glob("*.png")), key=_natural_key)
    actions = np.load(ep_dir / "actions.npy").astype(np.float32)
    n = min(len(image_files), len(actions))
    imus = None
    imu_path = ep_dir / "imu.npy"
    if imu_path.exists():
        imus = np.load(imu_path).astype(np.float32)
        if imus.ndim != 2:
            raise ValueError(f"{imu_path} 形状应为 (T, imu_dim)，实际为 {imus.shape}")
        imus = imus[:n]
    return image_files[:n], actions[:n], imus


def _action_bounds(action_dim):
    """固定物理界作为越界判据（与 action_norm.py 常量表共用）

    旧版用数据经验界（min/max+margin），恒值列 span≈0 时任何非零预测都
    算越界（unnormalized 模型 oob_rate 曾恒 100%）。"越物理界"比"越经验界"
    更符合安全语义：舵机 [0,180]、spk.F [0,5000]、spk.delay [0,2000]。
    """
    from action_norm import action_bounds
    return action_bounds(action_dim)


@np.errstate(all="ignore")
def rollout_episode(predictor, image_files, actions, horizon=None,
                    lo=None, hi=None, imus=None):
    """单 episode 开环滚动，复现闭环口径（图像真值滚动、动作自预测回写）。

    从 t0 = max(history_len, frame_len-1) 起步（窗口完整处），每步：
      帧窗 = 真值图像 [t-frame_len+1, t]（旧→新）
      历史 = 前 H 步动作（首步用真值填充，之后用自预测回写，复刻 run_loop）
      姿态 = imus[t]（predictor.imu_dim 非 None 时传入；imus 为 None 或
             长度不足按零向量 = 无姿态先验）
      预测 â_t → 与真值 a_t 比较 → 回写历史
    返回逐步记录 dict。horizon=None 表示滚到 episode 末尾。
    """
    F, H = predictor.frame_len, predictor.history_len
    use_imu = getattr(predictor, "imu_dim", None) is not None
    t0 = max(H, F - 1)
    n = len(actions)
    if horizon is None:
        horizon = n - t0
    horizon = min(horizon, n - t0)
    if horizon <= 0:
        return None

    cache = {}

    def load(t):
        if t not in cache:
            cache[t] = np.array(Image.open(image_files[t]).convert("RGB"))
        return cache[t]

    history = [actions[t0 - H + k].copy() for k in range(H)]  # 真值冷启动
    steps = []
    for k in range(horizon):
        t = t0 + k
        frames = [load(t - F + 1 + i) for i in range(F)]
        kwargs = {}
        if use_imu:
            kwargs["imu"] = (imus[t] if imus is not None and t < len(imus)
                             else None)
        pred = np.asarray(
            predictor.predict(frames, np.array(history), **kwargs),
            dtype=np.float32).reshape(-1)
        true = actions[t]
        err = pred - true
        rec = {
            "t": int(t),
            "mse": float(np.mean(err ** 2)),
            "pred": pred,
            "true": true,
        }
        if lo is not None:
            rec["oob"] = bool(np.any((pred < lo) | (pred > hi)))
        steps.append(rec)
        history = history[1:] + [pred.copy()]  # 自预测回写（复刻 run_loop）
    return steps


def _smoothness(seq):
    """相邻差分方差（多维取均值）；序列 <2 返回 0。越低越平滑。"""
    seq = np.asarray(seq, dtype=np.float32)
    if len(seq) < 2:
        return 0.0
    return float(np.mean(np.var(np.diff(seq, axis=0), axis=0)))


def summarize(all_steps, lo, hi):
    """汇总多个 episode 的滚动记录为报告指标

    error_growth 改为按 episode 归一化后取中位数：
        每 episode 内部算 (末25% MSE / 首25% MSE) → 中位数聚合。
    避免短 episode 尖峰被长 episode 均值淹没，也更鲁棒于长度差异。
    """
    if not all_steps:
        raise ValueError("无可评估的滚动步（episode 太短或 horizon 过小）")
    flat = [s for steps in all_steps for s in steps]
    mses = np.array([s["mse"] for s in flat], dtype=np.float32)

    # 误差随步数增长曲线：按相对步位置对齐各 episode，取每位置的平均 MSE
    max_len = max(len(s) for s in all_steps)
    curve = []
    for k in range(max_len):
        vals = [s[k]["mse"] for s in all_steps if k < len(s)]
        if vals:
            curve.append(float(np.mean(vals)))

    # 逐 episode 归一化 error_growth：每 episode 自身首末段比值 → 中位数
    growths = []
    for steps in all_steps:
        n = len(steps)
        if n < 2:
            continue
        m = np.array([s["mse"] for s in steps], dtype=np.float32)
        front = np.mean(m[:max(1, n // 4)])
        back = np.mean(m[max(0, n * 3 // 4):])
        growths.append(back / max(front, 1e-12))
    error_growth = float(np.median(growths)) if growths else 1.0

    preds = np.array([s["pred"] for s in flat], dtype=np.float32)
    trues = np.array([s["true"] for s in flat], dtype=np.float32)
    oob = [s.get("oob", False) for s in flat]

    return {
        "n_episodes": len(all_steps),
        "n_steps": len(flat),
        "overall_mse": float(np.mean(mses)),
        # 末段（后 25% 步）相对首段（前 25%）的误差比，>1 表示误差随步数发散
        "error_growth": error_growth,
        "error_curve": curve,
        "pred_smoothness": _smoothness(preds),
        "true_smoothness": _smoothness(trues),
        # 平滑度比：<1 表示预测比真值还平滑（好），>1 表示预测更抖（坏）
        "smoothness_ratio": _smoothness(preds) / max(_smoothness(trues), 1e-12),
        "oob_rate": float(np.mean(oob)) if lo is not None else None,
    }


def evaluate(checkpoint, data_dir, episodes=None, horizon=None, device=None):
    predictor = ActionPredictorInference(checkpoint, device)
    ep_names = _episode_ids(data_dir)
    if episodes is not None:
        ep_names = ep_names[-episodes:]  # 取尾部 episode 作为留出集
    lo, hi = _action_bounds(predictor.action_dim)
    all_steps = []
    for name in ep_names:
        image_files, actions, imus = _load_episode(data_dir, name)
        steps = rollout_episode(predictor, image_files, actions, horizon,
                                lo, hi, imus=imus)
        if steps:
            all_steps.append(steps)
    rep = summarize(all_steps, lo, hi)
    rep["checkpoint"] = checkpoint
    rep["frame_len"] = predictor.frame_len
    rep["history_len"] = predictor.history_len
    rep["imu_dim"] = getattr(predictor, "imu_dim", None)
    rep["predict_delta"] = getattr(predictor, "_predict_delta", False)
    return rep


def _fmt(rep):
    lines = [
        f"checkpoint      : {rep['checkpoint']}",
        f"predict_delta   : {rep['predict_delta']}",
        f"imu_dim         : {rep.get('imu_dim')}",
        f"episodes/steps  : {rep['n_episodes']} / {rep['n_steps']}",
        f"overall_mse     : {rep['overall_mse']:.6f}",
        f"error_growth    : {rep['error_growth']:.3f}  (>1 = 误差随步数发散)",
        f"smoothness_ratio: {rep['smoothness_ratio']:.3f}  (<1 = 比真值更平滑)",
        f"  pred/true     : {rep['pred_smoothness']:.4f} / {rep['true_smoothness']:.4f}",
    ]
    if rep["oob_rate"] is not None:
        lines.append(f"oob_rate        : {rep['oob_rate']:.4f}  (越界率, 越低越安全)")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="actpred 离线闭环代理评估（R4）")
    p.add_argument("--model", required=True, help="主 checkpoint 路径")
    p.add_argument("--data-dir", required=True, help="episode 数据目录")
    p.add_argument("--compare", default=None, help="可选对比 checkpoint（A/B）")
    p.add_argument("--episodes", type=int, default=None,
                   help="评估用尾部 episode 数（默认全部）")
    p.add_argument("--horizon", type=int, default=None,
                   help="每 episode 滚动步数上限（默认滚到末尾）")
    p.add_argument("--device", default=None)
    p.add_argument("--output", default=None, help="保存 JSON 报告路径")
    args = p.parse_args()

    rep = evaluate(args.model, args.data_dir, args.episodes, args.horizon,
                   args.device)
    print("== 主模型 ==")
    print(_fmt(rep))

    result = {"main": rep}
    if args.compare:
        rep_b = evaluate(args.compare, args.data_dir, args.episodes,
                         args.horizon, args.device)
        print("\n== 对比模型 ==")
        print(_fmt(rep_b))
        # 关键指标对比（负值=主模型更优）
        result["compare"] = rep_b
        result["delta"] = {
            "overall_mse": rep["overall_mse"] - rep_b["overall_mse"],
            "error_growth": rep["error_growth"] - rep_b["error_growth"],
            "smoothness_ratio": rep["smoothness_ratio"] - rep_b["smoothness_ratio"],
        }
        d = result["delta"]
        print("\n== 主 − 对比 (负值=主模型更优) ==")
        print(f"overall_mse     : {d['overall_mse']:+.6f}")
        print(f"error_growth    : {d['error_growth']:+.3f}")
        print(f"smoothness_ratio: {d['smoothness_ratio']:+.3f}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n报告已保存: {args.output}")


if __name__ == "__main__":
    main()
