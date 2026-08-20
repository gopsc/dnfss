# pybot×actpred 数据集录制与训练闭环评估实验报告

> 文档类型：实验报告
> 模块：pybot → actpred
> 作者：自动生成
> 日期：2026-08-14

---

## 1. 概述

### 1.1 背景

在微动作分解与自监督录制特性（见 `pybot_micro_action_and_recording_20260813.md`）基础上，本实验走通完整链路：dry-run 批量录制全部 `.mt` 动作脚本 → actpred 训练（未归一化 vs z-score 归一化两个版本）→ R4 离线闭环评估，并基于评估结果定位下一步开发方向（固定范围归一化 + 逐列加权，后续已落地为 `actpred/action_norm.py`）。

### 1.2 实验目标

1. 批量录制全部 12 个动作脚本，产出 actpred 格式数据集
2. 训练两个对照模型：`normalize_actions=False`（旧默认）与 `=True`（新默认，commit `a2c3a57`）
3. 用 `evaluate.py`（R4 闭环代理评估）对比两模型，定位误差主导列
4. 暴露并修复训练/评估链路的缺陷

### 1.3 关键结论速览

- **闭环舵机角无发散**，误差主导列是 `spk.F`（扬声器频率，10→1000Hz 大跨度）
- 归一化使聚合 MSE 降低 2.6×（6447→2445），但存在**舵机精度 ↔ 频率跟踪的权衡**
- `oob_rate` / `error_growth` 两个指标在含恒值列的退化数据集上失效，需修正（见开发方案 §3）
- 修复 1 个潜伏 bug：`normalize_actions=True` 时 val_ds 找不到 action_stats（commit `7ec8c9d`）

---

## 2. 数据集批量录制

### 2.1 方法

`scripts/*.mt` 均无 `@Camera` 块（该块为 pybot 新增，原脚本与 bot4cj 共享故未改）。采用**注入式录制**：不改动原脚本，为每个 `.mt` 在 `@Action` 前注入 `@Camera`（640×480@30fps）生成变体到 `data/pybot_record_scripts/`，再逐个 dry-run 录制。批处理脚本：`data/record_all.sh`（可重复执行）。

新增录制脚本 `bot4cj/scripts/record_stand.mt`（commit `af0ff56`）：基于 std.mt 扩展 @Camera + MPU6050 绑定 + 5 行 @Action（delay=200），产出 81 步插值序列。

### 2.2 录制结果

dry-run 下帧为合成随机噪声、IMU 全零（Mock I2C 读零）。共 13 个 episode、865 帧、804 个训练样本（history_len=5, frame_len=3）：

| Episode | 脚本 | 步数 | Episode | 脚本 | 步数 |
|---|---|---|---|---|---|
| 0000 | record_stand | 81 | 0007 | stdd | 46 |
| 0001 | jump | 27 | 0008 | t0 | 211 |
| 0002 | left-hand | 123 | 0009 | tilt-b | 46 |
| 0003 | left-turn | 58 | 0010 | tilt-f | 46 |
| 0004 | lie-down | 11 | 0011 | tilt-l | 46 |
| 0005 | rght-hand | 123 | 0012 | tilt-r | 46 |
| 0006 | std | 1 | | | |

注：episode_0006 仅 1 步（单行 @Action 首行不分解，属设计行为），对训练无贡献。仅 episode_0000 有 imu.npy（全零），其余无 IMU 文件，`ActionDataset` 按零向量处理。

### 2.3 数据验证

- `ActionDataset`（imu_dim=0 与 =8 两种模式）加载通过，804 样本 0 形状异常
- 微动作插值验证：s1 舵机 45°→60° 按 20 微步线性插值（每步 0.75°），`spk.delay` 微步期 10ms，与设计一致
- 每步帧独立（`bfa6d8d` 同步 capture 修复生效）

---

## 3. 训练实验

### 3.1 实验设置

| 项 | 值 |
|---|---|
| 分辨率 | 640×480（不降分辨率） |
| 增强 | ColorJitter（--augment） |
| batch / workers | 4 / 2（CPU 训练，~4 分钟/epoch） |
| epochs / patience | 100 / 15（early stopping） |
| 训练/验证 | 12 / 1 episode（763 / 41 样本，seed 42） |

### 3.2 模型 A：未归一化（normalize_actions=False，旧默认）

- 50 epochs 触发 early stopping，**best val_loss = 19.02**（原始量纲 MSE，数百量级）
- loss 轨迹：4344（epoch 1）→ 296（epoch 50），val 收敛至 ~20-30

### 3.3 模型 B：z-score 归一化（normalize_actions=True，新默认）

- 26 epochs 触发 early stopping，**best val_loss = 0.0288**（归一化量纲 <1.0，符合 commit `a2c3a57` 预期）
- loss 轨迹：0.188（epoch 1）→ 0.0288（epoch 11 最优）

### 3.4 训练链路缺陷修复

归一化默认开启后暴露潜伏 bug：`train.py` 将 action_stats 保存到 checkpoint_dir 后，cfg 只留裸文件名 "action_stats.npy"，val_ds 按 cwd 解析 → `FileNotFoundError`。修复：验证集构建用完整路径，checkpoint 配置存裸文件名（推理端按 checkpoint 目录解析）。同步修正 `test_warn_init_from_mismatch` 对旧默认值的隐式依赖（commit `7ec8c9d`，141 测试全过）。

### 3.5 推理冒烟

模型 B 加载正常，接口契约 `frame_len=3 / history_len=5 / action_dim=18 / imu_dim=None` 与 pybot 闭环鸭子类型约定一致；单次推理 ~84ms（x64 CPU）。真实帧+真实历史预测 t=5 动作：s1 误差 1.78°（模型 A 为 4.39°），数值范围合理。

---

## 4. R4 闭环评估

### 4.1 聚合指标对比

| 指标 | 模型 A（未归一化） | 模型 B（归一化） |
|---|---|---|
| overall_mse | 6446.7 | **2445.4**（2.6× 优） |
| oob_rate | 100% | **28.1%** |
| smoothness_ratio | 0.147（近常数输出，过度平滑） | **1.047**（≈真值变化，正常） |
| error_growth | 0.005 | 0.082（均 <1，无发散） |

### 4.2 逐 episode 对比（闭环滚动 mean_mse）

| Episode | 步数 | 模型 A | 模型 B | 比值 |
|---|---|---|---|---|
| 0000 stand | 76 | 25.5 | 134.0 | 5.26× 恶化 |
| 0001 jump | 22 | 20.5 | 221.1 | 10.78× 恶化 |
| **0002 left-hand** | 118 | 21252.2 | **6849.7** | **0.32× 改善** |
| 0003 left-turn | 53 | 276.0 | 641.0 | 2.32× 恶化 |
| 0004 lie-down | 6 | 35.2 | 46.9 | 1.33× 恶化 |
| **0005 rght-hand** | 118 | 21340.0 | **8022.9** | **0.38× 改善** |
| 0007 stdd | 41 | 49.8 | 115.9 | 2.33× 恶化 |
| 0008 t0 | 206 | 644.5 | 553.3 | 0.86× 改善 |
| 0009-0012 tilt-* | 41×4 | 29.8-34.2 | 59.6-751.7 | 1.74-25.12× 恶化 |

### 4.3 误差主导列定位

对 left-hand 逐列分析（模型 A，mean_mse 21252）：

- **舵机列（0-9）预测全部在合理范围**（35-85°），无物理危险
- **列 10（spk.F）是 MSE 主导源**：该脚本真值 10→1000Hz，模型预测 ~340Hz 折中值 → 单列平方误差 10万-44万
- 列 8、12-17 为恒值列（s9=90°、保留位 0），预测有微小偏差但绝对值无害

### 4.4 指标退化发现

1. **oob_rate=100%（模型 A）不可信**：数据集中 7 个恒值列（span=1e-6）经验界宽度为零，任何微小偏差都计越界。模型 B 的 28.1% 改善主要来自 spk.F 落入经验界，指标本身仍需修正（见开发方案 §3）
2. **error_growth 有误导性**：不同长度 episode 混叠使曲线非单调（中期尖峰来自 123 帧 hand episode，结束后回落），末段/首段比值掩盖了中期爆涨

### 4.5 核心结论

归一化把各列方差拉平，隐式实现了"全列等权"，带来 spk.F 跟踪改善（hand 类 3× 提升），代价是舵机角跟踪精度下降（简单动作 1.3-25× 恶化）。缩放方式与列重要性是**正交**的两个维度，需要显式逐列权重解耦——后续以固定范围归一化 + 逐列加权落地（`actpred/action_norm.py`）。

---

## 5. 产物与提交

| 产物 | 位置 |
|---|---|
| 数据集（865 帧 / 804 样本） | `data/pybot_record/`（231MB，不入 git） |
| 模型 A checkpoint | `actpred/checkpoints/recorded_run/` |
| 模型 B checkpoint | `actpred/checkpoints/recorded_run_norm/` |
| 评估报告 JSON | `data/eval_recorded.json` / `data/eval_recorded_norm.json` |
| 批录制脚本 | `data/record_all.sh` |

相关提交：`af0ff56`（record_stand.mt）、`7ec8c9d`（stats 路径 bug 修复）。

## 6. 局限

- 帧为合成噪声、IMU 全零：视觉/姿态通路未学习到真实信号，结论仅覆盖动作动力学部分
- 验证集仅 1 个 episode（41 样本）：模型选择噪声大，最优 epoch 有偶然性
- 两个模型各 13 万步级样本、CPU 训练 2-3 小时：结论在更大数据与实机数据上需复核
