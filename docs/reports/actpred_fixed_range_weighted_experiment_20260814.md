# actpred 模型 C 实验报告：固定范围归一化 + 逐列加权

> 文档类型：实验报告
> 模块：actpred
> 作者：自动生成
> 日期：2026-08-14
> 前置实验：`pybot_actpred_dataset_training_eval_20260814.md`（模型 A/B）
> 后续实验：模型 D（fixed_range + 加权 + R1-B rollout），训练中

---

## 1. 实验设置

按开发方案步骤 4 训练对照模型 C：固定物理范围归一化 + 默认逐列权重。

| 项 | 值 |
|---|---|
| normalize_mode | fixed_range（[0,1] min-max，`action_norm.py` 常量表） |
| column_weights | 默认：舵机 1.0 / spk 0.2 / 保留位 0.0 |
| 数据 / 种子 / 增强 | 与模型 A/B 相同（763/41 样本，seed 42，ColorJitter） |
| epochs / patience | 100 / 15 |
| checkpoint | `actpred/checkpoints/recorded_run_fixed/` |

## 2. 训练结果（teacher forcing 口径）

- 34 epochs 触发 early stopping
- **best val_loss = 0.000394**（epoch 21）——三方最优（A: 19.02 原始量纲、B: 0.0288）
- train loss 收敛至 ~0.0004-0.0008

**teacher forcing 口径下，方案目标完全达成**：loss 数值稳定（<0.001）、保留位列剔除、spk 列仍保留梯度（w=0.2）。

## 3. 闭环评估（R4）——目标失败

| 指标 | 模型 A（raw） | 模型 B（zscore） | 模型 C（fixed+加权） |
|---|---|---|---|
| overall_mse | 6446.7 | **2445.4** | 19843.5 |
| error_growth | 0.005 | 0.082 | **13.43（发散）** |
| smoothness_ratio | 0.147 | 1.047 | 3.19 |
| oob_rate（物理界） | — | 28.1% | 100% |

**teacher forcing 指标与闭环行为完全背离**：C 的训练/验证 loss 最好，闭环却首次出现发散（error_growth 13.4 > 1）。

### 3.1 逐 episode 闭环 mean_mse

| Episode | A | B | C | C 相对 B |
|---|---|---|---|---|
| 0000 stand | 25.5 | 134.0 | 52882.2 | 395× 恶化 |
| 0001 jump | 20.5 | 221.1 | 35228.6 | 159× 恶化 |
| 0002 left-hand | 21252.2 | 6849.7 | **2420.3** | 2.8× 改善 |
| 0003 left-turn | 276.0 | 641.0 | 3630.5 | 5.7× 恶化 |
| 0004 lie-down | 35.2 | 46.9 | 470.8 | 10× 恶化 |
| 0005 rght-hand | 21340.0 | 8022.9 | **3691.2** | 2.2× 改善 |
| 0007 stdd | 49.8 | 115.9 | 47180.4 | 407× 恶化 |
| 0008 t0 | 644.5 | 553.3 | 3927.6 | 7.1× 恶化 |
| 0009-0012 tilt-* | 29.8-34.2 | 59.6-751.7 | 45111-46268 | 61-775× 恶化 |

C 在 hand 类 episode（spk.F 大跨度）上**三方最优**（2.2-2.8× 优于 B），证明加权确实教好了频率跟踪；但其余 episode 全部严重发散。

## 4. 根因分析

### 4.1 逐列预测定位（episode_0000，76 步闭环滚动）

| 列 | 预测范围 | 真值 | 判定 |
|---|---|---|---|
| col 7 s8 舵机 | 1.6 → **159.0** | 0 | 舵机漂移 159° |
| col 10 spk.F | 27.6 → **1080.2** | 10 | 频率漂移 1080Hz |
| col 11 spk.delay | **-9.8** → 12.6 | 10 | 负时延越界 |
| col 12-17 保留位 | ±0.18 | 0 | 正常（排除权重 0 列失控假设） |

### 4.2 发散机制（尺度放大 + exposure bias）

1. **尺度放大**：fixed_range 下 1 个归一化单位 = spk.F 5000Hz 原始量（z-score 下各列单位方差，单位误差对应原始量级相当）。spk.F 归一化空间漂移 ±0.2 → 原始 ±1000Hz
2. **权重放松**：w=0.2 使训练对 spk.F 施加 1/5 压力，配合 28 倍尺度放大，spk.F 成为闭环最脆弱列
3. **反馈滚雪球**：漂移后的 spk.F 回写历史 → 注意力被污染 → 舵机列（col 7）跟漂 → 误差逐轮放大（episode 内 growth 3.6-430×）
4. **teacher forcing 掩盖一切**：训练/验证喂真值历史，上述回路从未出现，故 val loss 0.000394 最优而闭环最差

### 4.3 直接诱因确认

- 保留位列（权重 0）预测值正常（±0.18）——**排除**"零权重列无梯度失控"假设
- 推理端归一/反归一逻辑正确（`predict.py` fixed_range 分支逐行核对）——**排除**实现 bug
- `denormalize_action` 不 clamp（"保留模型外推能力"的设计决策）放大了越界值的回传，为次要因素

## 5. 结论

1. **逐列加权方案部分有效**：spk.F 跟踪达到三方最优（hand 类 2.2-2.8× 优于 B），方案目标"频率也要跟踪"达成
2. **方案不完整**：缩放方式（fixed_range）与列权重正交解耦后，暴露了**尺度放大 × exposure bias** 的叠加风险——单步 teacher forcing 训练无法抵御闭环反馈
3. **教训**：teacher forcing val loss 不能作为方案验收指标，必须过 R4 闭环评估；本实验正是 R4 的价值证明

## 6. 后续

- **模型 D**（训练中）：fixed_range + 加权 + R1-B rollout（`rollout_len=4, ss_prob=0.5`），让模型在训练期面对自预测反馈，直击 exposure bias
- 若 D 闭环仍不稳，备选路线（已在讨论中评估）：
  - `denormalize_action` 输出 clamp 到物理界（快速缓解）
  - one-hot 离散化 + CE（对数分箱 + 软标签）：消除尺度放大、内置物理界、NPU 友好；代价是序数信息与数据需求
  - spk.F 对数尺度（z-score 前 log 变换）
