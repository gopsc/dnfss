# actpred 模型 B 方案文档——zscore 等权稳定基线

> 文档类型：方案文档
> 模块：actpred
> 作者：自动生成
> 日期：2026-08-15
> 相关实验：`pybot_actpred_dataset_training_eval_20260814.md`（A/B 对照）、
> `actpred_fixed_range_weighted_experiment_20260814.md`（C/D/E 失败系列）、
> `actpred_onehot_multihead_dev_plan_20260814.md`（F 系列结构性方案）

---

## 1. 定位

模型 B 是六模型闭环评估（A/B/C/D/E/F）中**唯一稳定**的配置：overall_mse 2445、error_growth 0.082，远优于其余五个（6447~968459）。本文档固化其完整方案，作为：

1. 部署与实机测试的**首选基线**
2. 后续所有实验的**对照基准**
3. 归一化系列失败教训的**参照系**

## 2. 完整配置（checkpoint 实读）

来源：`actpred/checkpoints/recorded_run_norm/best_inference.pt`（epoch 26 早停，best val=0.0288）

### 2.1 数据侧（DataConfig）

| 项 | 值 |
|---|---|
| 分辨率 | 640×480 |
| action_dim / history_len / frame_len | 18 / 5 / 3 |
| normalize_actions | **True（z-score）** |
| action_stats_path | `action_stats.npy`（272B，逐列 (2,18) mean/std，训练集统计） |
| imu_dim | 0 |
| column_weights | None（**等权**） |

### 2.2 训练侧（TrainConfig）

| 项 | 值 |
|---|---|
| 数据 | v1（`data/pybot_record`，804 样本，12/1 episode 划分，seed 42） |
| 训练范式 | **单步 teacher forcing**（rollout_len=1, ss_prob=1.0） |
| 增强 | ColorJitter（--augment） |
| batch / lr / patience | 4 / 1e-3 / 15 |
| predict_delta | False |

### 2.3 模型侧（ModelConfig）

SimpleCNN(128) → Transformer(d=128, 4 头, 2 层, pre-LN) → MLP 头。409K 参数。

### 2.4 闭环表现（v1 数据，R4）

- overall_mse **2445.4**，error_growth **0.082**（<1 无发散），oob_rate 28.1%
- 逐 episode：stand 134 / jump 221 / hand 6850·8023 / tilt 60-752（唯一全部 episode 不爆表的模型）

## 3. 为什么 zscore 稳定（失败系列的对照结论）

| 方案 | 操作点 | 闭环行为 | 机理 |
|---|---|---|---|
| raw 等权（A） | 原始量纲 | 稳定但 spk.F 崩 | 舵机误差数值占优，spk.F 被忽略 |
| **zscore（B）** | **数据均值 0** | **稳定** | **各列单位方差，归一化误差等量纲；误差收缩** |
| fixed_range（C/D） | [0,1] 低端聚集 | 发散（growth 13.4/3.4） | 低端精度错配：小绝对误差=大相对误差 |
| log_range（E） | log 空间 | 爆炸（growth 157） | 指数反变换在高端放大：0.81→1.0 = 1000→5000Hz |
| onehot（F） | 箱（有界） | 部分稳定（oob=0，MSE 33K） | 有界生效，但 spk 无监督头闭环污染 |

**结论**：反馈回路需要**误差有界且收缩**的映射。z-score 的零均值单位方差恰好满足；固定范围类方案各自引入一端（低/高）的放大；one-hot 的有界性正确但需配 spk 列固定（见 F2）。

## 4. 已知局限

1. **依赖 stats 文件**：推理端需随 checkpoint 携带 action_stats.npy（与 fixed_range/onehot 的零状态部署相反）
2. **训练于 v1 数据**：delay 语义混杂（首行 200ms 未分解），spk.delay 权重 0.2——两者已被后续修复（v2 均匀数据 + delay 权重 0）
3. **spk.F 仍弱**：hand 类 episode 8023/6850 的 MSE 主导源是 spk.F（10→1000Hz 大跨度，等权下被均值化）

## 5. 演进建议：B2

模型 B 的两处已知缺陷（v1 数据、delay 权重）都已有修复就绪，B2 = **zscore + v2 数据 + 新权重（spk 列 0）**：

- 预期：闭环 MSE 进一步下降（delay 垃圾不再进历史）
- 若 B2 稳定，作为部署基线导出 ONNX/HEF
- 若 B2 需要 spk.F 频率跟踪（未来语音反馈需求），再叠加加权或 one-hot 方案

## 6. 复现命令

```bash
# 模型 B（v1 数据，旧默认权重）
./venv/bin/python train.py --data-dir /opt/micro_servers/data/pybot_record \
    --checkpoint-dir checkpoints/recorded_run_norm --augment --epochs 100

# B2（v2 数据，新权重：spk 两列 0）
./venv/bin/python train.py --data-dir /opt/micro_servers/data/pybot_record_v2 \
    --checkpoint-dir checkpoints/recorded_run_norm_v2 --augment --epochs 100
```
