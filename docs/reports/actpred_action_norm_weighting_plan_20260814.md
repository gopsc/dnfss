# actpred 动作归一化与逐列权重开发方案

> 文档类型：开发方案
> 模块：actpred（联动 pybot 数据契约）
> 作者：自动生成
> 日期：2026-08-14
> 前置实验：`pybot_actpred_dataset_training_eval_20260814.md`

---

## 1. 问题陈述

闭环评估（R4）暴露：z-score 归一化把 18 列方差拉平，隐式实现"全列等权"，导致**舵机精度 ↔ spk.F 频率跟踪的权衡**——未归一化模型舵机准但 spk.F 崩（21k MSE），归一化模型 spk.F 改善 3× 但简单动作恶化 1.3-25×。

本质：**缩放方式（数值稳定性）与列重要性（优化方向）是两个正交维度**，当前被隐式耦合。方案目标：固定物理范围归一化（数值稳定、免 stats 文件）+ 显式逐列权重（舵机优先），两者解耦。

## 2. 方案设计

### 2.1 固定物理范围 min-max 归一化（actpred 侧）

位置决策：**归一化保留在 actpred 侧，pybot 继续记录原始量纲**。理由：

- **raw at rest**：episode 是数据资产，原始角度/频率可复用于逆运动学、碰撞检测等分析，归一化不可逆
- **反归一化链条**：若 pybot 侧归一化，`--loop` 执行前需反归一化，min/max 表要同时存在于 recorder 与 loop 两端；现状 `predict()` 返回原始量纲，pybot 无感知，边界更干净
- **代码库先例**：IMU 即"pybot 记录原始、actpred `imu_norm.py` 按物理量程归一化"，动作走同一条路保持一致
- **pybot 零依赖承诺**：归一化逻辑入 recorder 会加深 pybot 对 numpy 的依赖面

固定范围常量表（新增 `actpred/action_norm.py`，类比 `imu_norm.py`）：

| 列 | 物理范围 | 依据 |
|---|---|---|
| 0-9 舵机角 | **[0, 180]** | `pybot/hd/servo.py` `MAX_ANGLE=180.0`（校准后全行程 0-180°） |
| 10 spk.F | [0, 5000] | `@Speaker max_f=5000` |
| 11 spk.delay | [0, 2000] | 实用上限（脚本用 10-500ms） |
| 12-17 保留位 | 恒 0 | 不参与归一化，损失权重 0 |

min-max 映射：`x_norm = (x - lo) / (hi - lo)` → [0,1]（或映射到 [-1,1]，与 IMU 归一化输出区间一致，待实现时定）。

优点：免 action_stats.npy（消除 stats 路径类 bug 的整类风险）、零状态部署、数值区间有界。风险：min-max 对离群值敏感——若真实数据某列越界，需 clamp 到 [0,1] 并告警（防御口径与 `_action_bounds` 一致）。

可选细化（后续）：按各舵机 `|max|`（s1=90°、s2=180°…）分列归一化，90° 舵机能用满 [0,1]，分辨率更高；需要读取 @Servo 块的 max 列构建表。

### 2.2 逐列加权损失

```python
COLUMN_WEIGHTS = np.array([
    1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,   # 0-9 舵机（安全关键）
    0.2, 0.2,                                             # 10-11 spk.F / spk.delay（声音与节奏，不影响运动）
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0,                         # 12-17 保留位（恒 0，纯噪声）
], dtype=np.float32)
# loss = mean(w * (pred - target)^2) / mean(w)
```

实施点：

- `train.py` / `pretrain_mt.py` 的 `train_one_epoch` / `validate`：MSE 处乘权重向量（权重存 DataConfig，随 checkpoint 序列化，推理端无需感知）
- 权重可经 CLI `--column-weights` 覆盖（默认用上表）
- 训练集/验证集用同一权重；`evaluate.py` 的逐列误差报告不受影响（报告仍按无权重口径，便于对照）

### 2.3 归一化与权重的组合矩阵

| 配置 | 数值稳定性 | 舵机精度 | spk.F 跟踪 | 预期适用 |
|---|---|---|---|---|
| raw + 等权（模型 A） | 差（loss 数百量级） | 好 | 崩 | 仅演示/对照 |
| z-score + 等权（模型 B） | 好 | 差 | 好 | 现状 |
| **固定范围 + 加权（本方案）** | 好 | 好 | 中（w=0.2 保留梯度） | **目标** |

## 3. 配套改进（同批实施）

### 3.1 R4 评估器修正（指标失效修复）

1. **oob_rate 恒值列退化**：span<阈值（如 1e-3）的列不参与越界判定；或改用固定物理界（舵机 [0,180]、spk.F [0,5000]）。推荐后者——与 §2.1 常量表共用，且"越物理界"比"越经验界"更符合安全语义
2. **error_growth 长度混叠**：按 episode 归一化后聚合（每 episode 自身首末段比值取中位数），避免短 episode 尖峰主导曲线

### 3.2 R1-B rollout + scheduled sampling

闭环误差累积源于 teacher forcing 训练与自预测推理的不匹配。已实现的 `--rollout-len K --ss-prob p` 直接对标：建议 `K=4, p=0.5`，与加权损失同批训练对照。

### 3.3 数据扩展

- **实机录制**（根本瓶颈）：dry-run 帧为噪声、IMU 全零，视觉/姿态通路无真实信号。实机 `--record` 后复跑本实验
- 验证集 1 个 episode 选模噪声大：val_split 0.15→0.3
- 补充站立/保持类长序列（当前 std 类仅 1-81 步）

## 4. 实施步骤

| 步骤 | 内容 | 产出 |
|---|---|---|
| 1 | 新增 `actpred/action_norm.py`：固定范围常量表 + normalize/denormalize + clamp 防御 | 独立纯 numpy 模块 + 单测 |
| 2 | DataConfig 增 `normalize_mode`（`zscore`/`fixed_range`）与 `column_weights`；dataset/train 接入 | 训练链路支持 + 单测 |
| 3 | `evaluate.py` oob 改物理界、error_growth 按 episode 归一化 | 评估器修正 + 单测 |
| 4 | 训练对照：固定范围+加权 vs z-score+等权（同种子、同数据） | 模型 C/D checkpoint |
| 5 | 复跑 R4 闭环评估，验证权衡解耦 | 对比报告 |

步骤 1-3 可并行；步骤 4-5 依赖前置完成。预计步骤 1-3 改动集中在 `actpred/`（`action_norm.py` 新增、`config.py`/`dataset.py`/`train.py`/`evaluate.py` 小改），不影响 pybot 与部署链（归一化在推理端内部完成，`predict()` 输出仍为原始量纲）。

## 5. 决策记录

- **归一化位置**：讨论过"移入 pybot 按列 min/max 归一化"。结论：不采纳（raw at rest、反归一化链条变长、IMU 先例不一致），但采纳其"固定物理范围"内核，落在 actpred 侧常量表实现
- **舵机范围**：±180° 为误述，实际全行程 0-180°（`servo.py MAX_ANGLE=180.0`），归一化表按 [0,180]
- **spk.F 对数尺度**：备选未定。若加权后 spk.F 跟踪仍不足，再考虑 log 变换（频率感知是对数的，10→1000 跨两个数量级）
