# actpred 模块代码审计报告

> 审查日期：2026-08-08 (Asia/Shanghai)
> 审查人：Automated Review
> 审查范围：`actpred/` 全模块（Python，PyTorch 动作预测神经网络）

---

## 一、模块概述

| 属性 | 值 |
|------|-----|
| 定位 | 机器人动作预测（图像 + 历史动作 → 下一步动作回归） |
| 语言 | Python 3 |
| 框架 | PyTorch ≥ 2.0 |
| 架构 | CNN/MobileNetV2 图像编码 + MLP/GRU 动作历史编码 + 融合头 |
| 参数量 | ~140K（simple CNN）至 ~2.3M（MobileNetV2） |
| 入口 | `_run.sh`（train / export / predict 子命令分发） |

### 依赖项

| 库 | 用途 | 版本要求 |
|----|------|---------|
| torch | 神经网络训练/推理 | ≥ 2.0 |
| torchvision | MobileNetV2 预训练模型、图像变换 | ≥ 0.15 |
| numpy | 动作向量运算、归一化统计 | ≥ 1.24 |
| Pillow | 图像加载 | ≥ 9.0 |
| tqdm | 训练进度条 | ≥ 4.60 |

### 目录结构

```
actpred/
├── config.py          # 三组 dataclass 配置 + CLI 参数解析 + 覆盖逻辑
├── model.py           # ActionPredictor 主模型（编码器 + 融合头）
├── dataset.py         # ActionDataset 数据集 + DataRecorder 数据采集器
├── train.py           # 训练主流程（episode 级划分、早停、余弦退火）
├── predict.py         # ActionPredictorInference 推理类 + CLI
├── export_onnx.py    # ONNX 导出（opset 17，动态 batch）
├── _run.sh            # 运行脚本（train/export/predict 分发）
├── _set.sh            # 环境安装脚本
├── requirements.txt   # 依赖声明
└── README.md          # 模块文档
```

---

## 二、审查发现

### 🔴 严重问题 (P0)

#### [ACT-S01] Episode 索引错位：split_episodes 与 \_\_init\_\_ 过滤条件不一致（已修复 ✅）

- **文件**: `dataset.py:26` vs `dataset.py:77-85`
- **描述**: `split_episodes` 基于 `get_episode_count` → `_valid_episode_dirs` 计算有效 episode 数量（同时检查 `actions.npy` 存在且 `images/` 为目录），生成的索引范围为 `[0, valid_count)`。但 `ActionDataset.__init__` 收集的是**所有子目录**（仅检查 `d.is_dir()`），再用 `episode_indices` 索引。两处过滤条件不一致，当 data_dir 中存在无效 episode 目录（如录制中断、缺少 `actions.npy`）时，索引指向错误的 episode。

代码注释 `# 与 __init__ 的过滤条件保持一致` 声明了一致性，但实际实现并不一致：

```python
# __init__ — 仅检查 is_dir()，包含无效目录
episodes = sorted([d for d in data_path.iterdir() if d.is_dir()])
if episode_indices is not None:
    episodes = [episodes[i] for i in episode_indices]  # 索引可能指向无效目录

# _valid_episode_dirs — 额外检查 actions.npy 和 images/
return sorted([d for d in data_path.iterdir()
             if d.is_dir() and (d / "actions.npy").exists() and (d / "images").is_dir()])
```

- **触发条件**: data_dir 中存在不含 `actions.npy` 或 `images/` 的子目录（如 DataRecorder 创建目录后未调用 `flush()` 即中断）
- **影响**: 训练集/验证集划分错位——验证集可能选中无效 episode（0 样本），有效 episode 被遗漏。模型选择基于错误的验证集，保存的"最优"模型并非真正最优
- **修复建议**: `__init__` 改用 `_valid_episode_dirs` 收集目录：
```python
episodes = ActionDataset._valid_episode_dirs(data_dir)
if episode_indices is not None:
    episodes = [episodes[i] for i in episode_indices]
```

#### [ACT-S02] 推理归一化静默失败（已修复 ✅）

- **文件**: `predict.py:34-43`
- **描述**: `ActionPredictorInference.__init__` 通过尝试加载 `action_stats.npy` 来决定是否进行归一化，但**不检查 checkpoint 中 `config.normalize_actions` 的值**。如果模型训练时启用了 `normalize_actions=True` 但推理时 stats 文件缺失，归一化被静默跳过，预测结果完全错误（输出仍在归一化空间中），且无任何警告。

```python
# 当前：仅靠文件是否存在决定是否归一化，不检查 config
try:
    stats = np.load(stats_path)
    self._action_mean = stats[0]
    self._action_std = stats[1]
except (FileNotFoundError, ValueError):
    pass  # 静默失败——模型训练时用了归一化，但推理时没归一化
```

- **影响**: 部署时遗漏 `action_stats.npy` 文件 → 预测值无意义且无报错，这是最危险的部署陷阱
- **修复建议**: 检查 config 中的 `normalize_actions` 标志，若为 True 但 stats 文件不存在则报错：
```python
if self._data_cfg.normalize_actions:
    if not os.path.exists(stats_path):
        raise FileNotFoundError(
            f"模型训练时启用了动作归一化，但未找到统计文件: {stats_path}")
    stats = np.load(stats_path)
    self._action_mean = stats[0]
    self._action_std = stats[1]
```

### 🟡 中等问题 (P1)

#### [ACT-S03] 空训练集无校验，train_loss=0.0 误导用户（已修复 ✅）

- **文件**: `train.py:82-128`
- **描述**: 当 data_dir 无有效 episode 或 split 后训练集为空时，`train_one_epoch` 循环执行 0 次，返回 `total_loss / max(count, 1) = 0.0 / 1 = 0.0`。训练日志显示 `train_loss=0.000000`，看似完美。且空验证集有 WARNING（第 98-100 行），但空训练集没有任何警告。未训练的模型可能被保存为 `best_inference.pt`。

- **触发条件**: `data_dir` 无有效 episode；或仅 1 个 episode 且 `val_split > 0`（split 后训练集为空）
- **影响**: 用户误以为训练成功，部署未训练模型
- **修复建议**:
```python
if len(train_ds) == 0:
    raise ValueError(f"训练集为空（0 样本）。请检查 data_dir={cfg.data.data_dir} "
                     f"及 episode 数据完整性。")
```

#### [ACT-S04] 图像文件名字符串排序问题（已修复 ✅）

- **文件**: `dataset.py:38-39`
- **描述**: 图像文件按 `p.name`（文件名字符串）排序。对于 DataRecorder 生成的 `%04d.jpg` 格式（如 "0001.jpg"）排序正确。但若用户导入的外部数据无前导零（如 "1.jpg", "2.jpg", ..., "10.jpg"），字符串排序结果为 "1.jpg" < "10.jpg" < "2.jpg"，导致图像与动作序列错位。

```python
image_files = sorted(list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png")),
                     key=lambda p: p.name)  # 字符串排序，无前导零时乱序
```

- **影响**: 外部数据图像-动作对齐错乱，训练学到错误的映射
- **修复建议**: 使用自然排序：
```python
import re
def natural_key(p):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', p.name)]
image_files = sorted(..., key=natural_key)
```

#### [ACT-S05] 空样本列表导致 np.stack 崩溃（已修复 ✅）

- **文件**: `dataset.py:53`
- **描述**: 当 `normalize_actions=True` 且未提供 `action_stats_path` 时，代码从 `self.samples` 计算归一化统计量。若 samples 为空（episode 过短或无有效数据），`np.stack([])` 抛出 `ValueError: need at least one array to stack`，无友好错误信息。

```python
all_actions = np.stack([s[2] for s in self.samples])  # 空列表 → 崩溃
```

- **修复建议**:
```python
if not self.samples:
    raise ValueError(f"数据集为空，无法计算归一化统计量。"
                     f"请检查 data_dir={data_dir} 中的 episode 数据。")
```

#### [ACT-S06] 编程 API 未校验 action_history 形状（已修复 ✅）

- **文件**: `predict.py:46-56`
- **描述**: `ActionPredictorInference.predict()` 接受任意 `np.ndarray` 作为 `action_history`，不校验形状是否为 `(history_len, action_dim)`。形状不匹配时，错误来自 PyTorch 内部，信息不明确。

- **影响**: 用户传入错误形状的历史动作，得到不明确的报错或静默错误结果
- **修复建议**:
```python
expected_shape = (self._data_cfg.history_len, self._data_cfg.action_dim)
if action_history.shape != expected_shape:
    raise ValueError(
        f"action_history 形状应为 {expected_shape}，实际为 {action_history.shape}")
```

#### [ACT-S07] MobileNetV2 预训练权重在线下载依赖（已修复 ✅）

- **文件**: `model.py:32-33`
- **描述**: `MobileNetEncoder` 使用 `MobileNet_V2_Weights.DEFAULT`，首次使用时从 PyTorch 服务器下载预训练权重（~14MB）。在离线环境或受限网络中会失败，且无预下载机制或离线回退。

- **影响**: 离线/受限网络环境无法使用 MobileNet backbone
- **修复建议**: 1) 文档说明预下载方式 2) 支持 `TORCH_HOME` 环境变量预置权重 3) 提供本地权重路径参数

#### [ACT-S08] 指定 action_stats_path 但文件不存在时验证集统计量独立计算（已修复 ✅）

- **文件**: `dataset.py:48-55`, `train.py:86-91`
- **描述**: 当用户通过 CLI 指定 `--action-stats-path` 但文件不存在时，`dataset.py:48` 的 `os.path.exists` 检查会 fallback 到从当前数据集自身计算统计量。而 `train.py` 的 `if cfg.data.normalize_actions and not cfg.data.action_stats_path` 条件为 False（path 已被指定），不会触发保存-复用逻辑。结果：训练集和验证集各自独立计算归一化统计量，验证集的归一化空间与训练集不同，val loss 不可信。

- **影响**: 模型选择基于不可信的 val loss，可能保存非最优模型
- **修复建议**: 指定了 `action_stats_path` 但文件不存在时应报错，而非静默 fallback：
```python
if action_stats_path:
    if not os.path.exists(action_stats_path):
        raise FileNotFoundError(f"指定的 action_stats_path 不存在: {action_stats_path}")
```

### 🟢 轻微问题 (P2)

#### [ACT-Q01] README 推理示例格式错误

- **文件**: `README.md:28`
- **描述**: 推理示例的 `--action-history` 值 `"45,0,45,0,45,0,45,0,45,10,45,10,45,10,45,10"` 未使用冒号分隔步骤，且值数量与默认 `action_dim=8` 不匹配。正确格式应为冒号分隔的多步骤，如 `"45,0,45,0,45,0,45,0:..."`（共 `history_len=5` 步，每步 `action_dim=8` 个值）。

#### [ACT-Q02] predict.py 访问私有属性

- **文件**: `predict.py:83`
- **描述**: `main()` 中 `predictor._data_cfg.action_dim` 直接访问实例私有属性。应提供公共属性或方法。

#### [ACT-Q03] DataRecorder episode 索引 O(n) 遍历

- **文件**: `dataset.py:94-96`
- **描述**: `DataRecorder.__init__` 使用 while 循环逐个检查 episode 目录是否存在，O(n) 次 stat 调用。episode 数量大时效率低。

#### [ACT-Q04] requirements.txt 无版本上限

- **文件**: `requirements.txt`
- **描述**: 所有依赖使用 `>=` 无上界，不同环境安装不同大版本可能导致行为差异。

#### [ACT-Q05] 无单元测试

- **文件**: 全局
- **描述**: 模块无任何测试代码。数据加载、归一化、模型前向、ONNX 导出等关键路径完全依赖手工验证。

#### [ACT-Q06] _set.sh 固定安装 CPU-only PyTorch

- **文件**: `_set.sh:7`
- **描述**: `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` 始终安装 CPU 版本，有 GPU 的环境无法利用。未在 README 说明 GPU 安装替代方案。

#### [ACT-Q07] CLI --normalize-actions 无法显式关闭

- **文件**: `config.py:64`
- **描述**: `action="store_true"` + `default=None` 设计使得该参数只能设为 True 或使用默认值，无法通过 CLI 显式设为 False。当配置文件中 `normalize_actions=True` 时，CLI 无法覆盖关闭。

#### [ACT-Q08] ONNX 导出无运行时验证

- **文件**: `export_onnx.py`
- **描述**: 导出 ONNX 后仅打印路径，不验证导出模型能否正确加载和推理。建议用 `onnxruntime` 做一致性检查。

---

## 三、统计汇总

| 等级 | 数量 | 问题 ID |
|------|------|---------|
| 🔴 Critical | 2 | S01, S02 |
| 🟡 Medium | 6 | S03, S04, S05, S06, S07, S08 |
| 🟢 Low | 8 | Q01-Q08 |
| **合计** | **16** | |

### 按类别统计

| 类别 | 问题数 | 占比 |
|------|--------|------|
| 数据正确性（索引错位、归一化） | 3 | 19% |
| 输入校验（形状、空集、路径） | 4 | 25% |
| 可靠性（崩溃、静默失败） | 3 | 19% |
| 环境/部署（下载依赖、CPU-only） | 2 | 12% |
| 代码质量/文档 | 4 | 25% |

---

## 四、修复优先级建议

### 第一阶段（紧急，预计 0.5 天）

1. **ACT-S01**: 修复 episode 索引错位——`__init__` 改用 `_valid_episode_dirs`（一行改动，消除数据错位风险）
2. **ACT-S02**: 推理归一化检查——读取 `config.normalize_actions`，缺失 stats 文件时报错

### 第二阶段（重要，预计 1 天）

3. **ACT-S03**: 空训练集校验——添加 `len(train_ds) == 0` 检查并报错
4. **ACT-S05**: 空样本列表防御——计算统计量前检查 samples 非空
5. **ACT-S06**: 编程 API 形状校验——`predict()` 入口校验 action_history 形状
6. **ACT-S08**: 指定 stats 路径不存在时报错，而非静默 fallback

### 第三阶段（优化，预计 1 天）

7. **ACT-S04**: 自然排序图像文件名
8. **ACT-S07**: MobileNet 权重离线支持
9. **ACT-Q01-Q08**: 文档修正、代码质量优化

---

## 五、亮点

| 亮点 | 说明 |
|------|------|
| Episode 级 train/val 划分 | 按完整 episode 划分而非随机样本，避免相邻帧数据泄露 |
| 归一化统计量隔离 | 训练集计算 stats → 保存 → 验证集复用，避免验证集信息泄露 |
| 安全 checkpoint 加载 | `weights_only=True` 防止反序列化攻击 |
| 双 checkpoint 策略 | `best.pt`（含 optimizer，可恢复训练）+ `best_inference.pt`（仅权重+config，用于部署） |
| 空验证集保底处理 | `validate()` 返回 `inf`，`best_val_loss` 不被更新，训练结束保底保存最终权重 |
| 梯度裁剪 + 余弦退火 | `max_grad_norm` + `CosineAnnealingLR`，训练稳定性有保障 |
| 全局可复现 | `random`/`numpy`/`torch`/`DataLoader generator` 全部设种子 |
| 懒加载 MobileNet 依赖 | `import` 在 `__init__` 内，不强制要求 torchvision 预训练模块 |

---

## 六、附录

### 审查工具

- 人工静态分析
- 代码路径追踪
- 边界条件推演

### 参考文档

- [PyTorch torch.load 安全加载](https://docs.pytorch.org/docs/stable/generated/torch.load.html)
- [PyTorch ONNX 导出](https://docs.pytorch.org/docs/stable/onnx.html)
- [torchvision MobileNetV2](https://docs.pytorch.org/vision/stable/models/mobilenetv2.html)

### 审查人

- 自动化审计助手
- 审查时间：2026-08-08

---

*本报告由自动化代码审计生成，供开发团队参考。修复建议需结合实际场景评估。*
