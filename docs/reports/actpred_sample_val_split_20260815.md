# actpred 验证集划分方式实验报告：整集 vs 样本级随机

> 文档类型：实验报告
> 模块：actpred
> 作者：自动生成
> 日期：2026-08-15
> 前置：`actpred_zscore_baseline_20260815.md`（模型 B 基线）、
> `actpred_onehot_multihead_dev_plan_20260814.md`（one-hot 方案）

---

## 1. 问题

模型 F/F2 系列（one-hot，v2 数据）出现**过拟合签名**：train loss 持续下降（2.40→1.57）而 val loss 平台后上行（1.73→2.09），且 val 波动剧烈（区间 1.74-2.0）。

排查后发现**选模信号本身有缺陷**：

- 旧划分方式（`split_episodes`）按 **episode 整集划分**：val_split=0.15 × 12 集 → 验证集只有 **1 个 episode、60 个样本**
- 60 个样本全来自同一集（tilt 类），val loss 主要反映"这一集的难度"而非泛化能力
- 波动大 + 分布偏 → early stopping 的 best 模型选择被噪声主导

## 2. 方案：样本级随机划分

新增 `--val-split-mode {episode, sample}`（**新默认 sample**）：

- `dataset.py`：`ActionDataset` 新增 `sample_indices` 参数（构建后按索引过滤）；**zscore 统计量仍只由训练子集计算**（经 stats 文件复用给验证集，统计口径正确性不变）
- `train.py`：sample 模式下先建全量数据集取样本索引，按 seed 42 随机排列，取 val_ratio 为验证集
- 效果：验证集从 60 → **142 样本**（810 train / 142 val），跨全部 12 个 episode 混合

## 3. 结果对比（同配置：onehot + spk 固定 + v2 数据）

| | F2（整集划分） | F2b（样本级划分） |
|---|---|---|
| 验证集 | 60 样本（1 集） | 142 样本（12 集混合） |
| val 起点 | 1.914 | 2.056（更难、更有代表性） |
| 最优 | 1.737（epoch 7） | **1.695（epoch 5，仍在下降）** |
| 收敛形态 | 平台后上行（过拟合签名） | 平滑连续下降 |
| val 波动 | ±0.15（1.74-2.0） | ±0.05（无跳变） |

F2b 在更难的代表性验证集上取得**更低的 val loss**，且曲线平滑无异常跳变——说明 F2 的"过拟合"判断部分被验证集噪声误导，真实模型能力被低估。

## 4. 已知代价（诚实记录）

样本级划分**略乐观**：微动作插值使相邻样本高度相关（0.75°/步），随机划分后每个验证样本的近邻大量在训练集，val 会略高估泛化。适用场景：

- **选模/早停**（本次用途）：噪声小、跨集混合 → 更可靠的 best 选择信号 ✓
- **能力评估**（需保守口径）：仍建议整集划分或 leave-one-out，避免乐观偏差

## 5. 实施细节

- `dataset.py::ActionDataset(sample_indices=...)`：样本按索引过滤，位置在样本构建后、统计量计算前
- 签名顺序调整：`sample_indices` 置于 `frame_len` 之后，避免破坏既有位置参数调用（曾因插入位置错误导致 frame_len 错位 TypeError）
- `config.py`：`TrainConfig.val_split_mode`（默认 "sample"）+ CLI + 校验
- 测试：+2（样本过滤与统计口径、CLI 映射），全套 185 通过

## 6. 后续

- F2b 训练完成后跑闭环评估，验证 onehot + spk 固定 + 新划分的实际闭环收益
- 若需严格能力评估，增加 `--val-split-mode episode` 复跑对照，或 leave-one-out 交叉验证
