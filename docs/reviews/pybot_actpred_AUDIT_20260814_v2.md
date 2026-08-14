# pybot & actpred 审计报告（第二版 —— 归一化+逐列加权 落地后）

> 审查日期：2026-08-14 (Asia/Shanghai)
> 审查人：AI Assistant（SOLO Coder）
> 审查范围：`pybot/`（机器人运行时）与 `actpred/`（动作预测）两模块
> 代码基线：归一化+逐列加权方案落地后（12 个文件改动，531 行新增）
>
> 本报告对照三份历史审计报告追踪修复状态，并审查本轮新增代码。

---

## 一、审计结论摘要

| 级别 | 新发现 | 历史遗留 | 说明 |
|------|--------|---------|------|
| **P0（崩溃/数据损坏）** | 2 | 0 | 见 §二 |
| P1（功能风险） | 0 | 5 | 见 §三 |
| P2（代码质量） | 3 | 9 | 见 §四 |

**重点：2 个 P0 必须在提交前修复。**

---

## 二、P0 新发现

### P0-1 pybot HailoPredictor 缺失 normalize_mode 传递

**位置**：[pybot/main.py:180-183](../../pybot/pybot/main.py#L180-L183)

```python
return _P(checkpoint, stats_path=stats_path,
          predict_delta=predict_delta,
          pred_token=pred_token)
```

**问题**：`HailoPredictor.__init__` 的 `normalize_mode` 参数默认 `"zscore"`，pybot 调用时未传入此参数。对于 `fixed_range` 训练的 HEF，推理端应按 `action_norm.py` 固定物理范围对历史动作做 min-max→[0,1] 归一化，但 pybot 未传参导致 `_fixed_range=False`，历史动作以原始量纲送入 HEF。

**影响**：`fixed_range` 模式训练的 HEF 在 pybot 闭环中推理输出完全错误（舵机会被推到安全范围外）。`zscore` 模式不受影响（默认值偶然正确）。

**修复建议**：pybot 从同目录 checkpoint 读取 `normalize_mode` 并传入 `HailoPredictor`：

```python
# 在 _load_predictor 中，从 companion checkpoint 读取 normalize_mode
normalize_mode = "zscore"
for name in ("pretrain_best_inference.pt", "best_inference.pt"):
    ckpt_path = os.path.join(hef_dir, name)
    if os.path.exists(ckpt_path):
        try:
            import torch
            ck = torch.load(ckpt_path, map_location="cpu", weights_only=True)
            normalize_mode = ck.get("config", {}).get("data", {}).get(
                "normalize_mode", "zscore")
        except Exception:
            pass
        break

return _P(checkpoint, stats_path=stats_path,
          predict_delta=predict_delta,
          pred_token=pred_token,
          normalize_mode=normalize_mode)
```

### P0-2 DataConfig normalize_actions 默认值变更，与 README 不一致

**位置**：[actpred/config.py:14](../../actpred/config.py#L14)

```python
normalize_actions: bool = True   # 旧默认值：False
```

**问题**：默认值从 `False` 改为 `True`，但 [README.md 配置表](../../actpred/README.md#L163) 仍标注 `False`。CLI 不经 `--normalize-actions` 参数时，`apply_overrides` 会使用 dataclass 默认值 `True`，导致行为变更：
- 旧脚本（`bash _run.sh train --data-dir ./data --epochs 100`）之前不归一化，现在开启 zscore 归一化，且会自动计算 `action_stats.npy`。
- 对预训练（`pretrain_mt.py`）同样影响：预训练数据从 .mt 脚本提取，zscore 归一化后分布与旧 checkpoint 不同。

**影响**：所有无 `--normalize-actions` 或无 `--no-normalize-actions` 的训练命令行为改变；旧 checkpoint 做 `--init-from` 时 `warn_init_from_mismatch` 会警告但不会阻止。

**修复建议**：二选一：
- **方案 A**：恢复默认值 `False`（保持向后兼容，推荐）；
- **方案 B**：改 README 为 `True`，并在 CHANGELOG 中标注 breaking change。

---

## 三、P1 历史遗留（未修复）

| # | 来源 | 位置 | 问题 | 优先级 |
|---|------|------|------|--------|
| P1-3 | 闭环审计 | [actpred/mt_parser.py:33](../../actpred/mt_parser.py#L33) | `read_text(encoding="utf-8")` 不剥离 BOM，`@Action` 为首块时表头列名可能粘附 `﻿` | 低（低概率触发） |
| P2-a | 闭环审计 | [pybot/main.py:143](../../pybot/pybot/main.py#L143) | `_load_predictor` 仅用 `endswith(".hef")` 区分后端，大小写敏感，无 ONNX 分支 | 中 |
| R2-1 | actpred 审计 | [actpred/train.py:270](../../actpred/train.py#L270) | NaN val_loss 永久阻断 best checkpoint 保存（`NaN < x` 恒 False） | 中 |
| R1-1 | actpred 审计 | [actpred/pretrain_mt.py:185](../../actpred/pretrain_mt.py#L185) | `cfg.data.data_dir = args.mt_dir` 覆盖，`--save-config` 导出语义错误 JSON | 低 |
| R2-7 | actpred 审计 | [actpred/_run.sh:14-16](../../actpred/_run.sh#L14-L16) | 未知子命令静默回退到 `predict.py`，拼写错误难以发现 | 低 |

---

## 四、P2 历史遗留（未修复）

| # | 来源 | 位置 | 问题 |
|---|------|------|------|
| P2-b | 闭环审计 | pybot/robot.py 等 3 处 | numpy 在闭环路径 3 个函数各自 `import` |
| P2-c | 闭环审计 | pybot/tests/test_loop.py | 未覆盖闭环 + recorder 集成、predictor 异常清理 |
| P2-d | 闭环审计 | actpred/dataset.py | 图像 Resize 不保持宽高比 |
| P2-e | 闭环审计 | actpred/train.py | resume 时改 `--epochs` 会重算 lr scheduler |
| P2-f | 闭环审计 | actpred/export_deploy.py | ManualAttnBlock 依赖 PyTorch 内部布局约定 |
| R2-3 | actpred 审计 | actpred/train.py | 空验证集时 `best.pt` 从不创建 |
| R2-4 | actpred 审计 | actpred/_set.sh | CUDA 版 torch 误装风险 |
| R1-2 | actpred 审计 | predict/onnx_infer/hailo_infer | `parse_action_history` 三处重复 |
| R2-9 | actpred 审计 | actpred/predict.py | `--action-dim` override 参数无用且是陷阱 |

---

## 五、已完成修复的审计项

| # | 来源 | 修复内容 |
|---|------|---------|
| P1-1 | 闭环审计 | 闭环节拍 overrun 告警已实现（[robot.py:307-320](../../pybot/pybot/robot.py#L307-L320)），含比例统计 |
| P1-2 | 闭环审计 | 闭环帧源改走 `_loop_recent_frames`（[robot.py:323-338](../../pybot/pybot/robot.py#L323-L338)），从守护线程队列取帧 |
| P1-4 | 闭环审计 | DataRecorder output_dir 已有 `mkdir(parents=True, exist_ok=True)`（[recorder.py:46](../../pybot/pybot/recorder.py#L46)） |
| R2-2 | actpred 审计 | ActionDataset 外部 stats 文件已补 shape 校验（[dataset.py:92-95](../../actpred/dataset.py#L92-L95)） |

---

## 六、本轮新增代码审查（归一化+逐列加权方案）

### 6.1 action_norm.py — 通过，有 1 处 P2 建议

**总体评价**：常量表清晰、文档完善、clamp 防御到位。18 个单测覆盖正常值/边界/越界/多维度。

**P2-a1（建议）**：`default_column_weights` 的 `action_dim` 默认为 18，但与 `action_bounds` 的行为不同——`action_bounds` 对 `action_dim > 18` 自动补零，`default_column_weights` 对此类情况返回全零权重（因为 `min(10, action_dim)` 和 `min(12, action_dim)` 仅覆盖前 12 列，其余列保持 `np.zeros` 初始值）。建议对齐行为：`action_dim > 18` 时对超出列也赋默认权重（如 0.2）。

### 6.2 config.py — 通过，有 1 处 P2

**总体评价**：`normalize_mode` 和 `column_weights` 的 CLI 解析、值域校验、checkpoint 兼容性警告均实现完整。

**P2-a2（建议）**：`warn_init_from_mismatch` 中 `init_mode = init_data.get("normalize_mode", "zscore")` 对旧 checkpoint 正确回退。但旧 checkpoint 的 `normalize_actions=False` 时，`normalize_mode` 无意义，此时警告 `init_mode != current.data.normalize_mode` 是误报。建议仅在 `init_norm and current.data.normalize_actions` 时比较 `normalize_mode`。

### 6.3 dataset.py — 通过

固定范围归一化集成正确：`_fixed_range` 标志位在 `__init__` 中设置，`_norm` 方法按标志位分发。stats shape 校验（R2-2 修复）已补。

### 6.4 train.py — 通过，有 1 处 P2 行为变更

**总体评价**：`_weighted_mse` 实现正确，`weights.mean()` 归一化保证加权 Loss 与无权重 Loss 量级可比。`column_weights` 为 None 时自动取 `default_column_weights`。

**P2-a3（行为变更）**：`column_weights` 现在**始终生效**（None 时取默认值），即使 `normalize_actions=False`。这意味着不归一化训练的模型也会按列加权损失——模型对舵机列（权重 1.0）的优化力度高于 spk 列（0.2）。这并非 bug，但行为变更需在 README 注明。

### 6.5 evaluate.py — 通过

物理界 OOB 与 `action_norm.py` 常量表共用（`_action_bounds` 调用 `action_bounds`）。error_growth 按 episode 分别计算取中位数，消除长短 episode 混杂偏差。实现正确。

### 6.6 推理/部署链 — 通过，P0-1 除外

| 文件 | 审查结论 |
|------|---------|
| predict.py | `_fixed_range` 从 checkpoint config 读取，normalize/denormalize 正确 |
| onnx_infer.py | `normalize_mode` 构造参数，predict 中 normalize/denormalize 正确 |
| hailo_infer.py | `normalize_mode` 构造参数，predict 中 normalize/denormalize 正确 |
| gen_calibration.py | `fixed_range` 模式校准数据走 `normalize_action` 变换，与推理端一致 |
| export_deploy.py | `check_token_ranges` 按 `normalize_mode` 在正确空间采样，stats_path 逻辑正确 |

**唯独 pybot 的 `_load_predictor` 未传 `normalize_mode` 给 HailoPredictor（P0-1）**。

### 6.7 test_actpred.py — 通过

新增 `TestFixedRangeNorm` 类 18 个测试用例，覆盖：
- 已知值归一化（舵机中位/边界、spk 中位）
- 越界 clamp（>180→1.0、<0→0.0）
- denormalize 往返一致性
- 默认列权重结构
- 自定义列权重
- 多维度批次归一化
- 全 0 输出验证

全量 148 passed / 11 skipped。

---

## 七、修复优先级

| 优先级 | 项 | 理由 |
|--------|-----|------|
| **P0** | P0-1 HailoPredictor normalize_mode 缺失 | `fixed_range` HEF 在 pybot 闭环中推理完全错误 |
| **P0** | P0-2 normalize_actions 默认值变更 | 所有无此参数的命令行为改变，README 不一致 |
| P1 | R2-1 NaN val_loss | 训练健壮性，一行 isfinite 修复 |
| P1 | P2-a 后端检测 | 路径大小写敏感，ONNX 无分支 |
| P2 | P2-a2 误报警告 | 旧 checkpoint 的不必要警告 |
| P2 | P2-a1 action_dim>18 | 未来扩展性 |
| P2 | P2-a3 行为变更文档 | README 注明 column_weights 始终生效 |

---

*本报告为归一化+逐列加权方案落地后的专项审计。建议先修复 2 个 P0 再提交。*