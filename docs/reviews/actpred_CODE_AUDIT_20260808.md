# actpred 模块代码审计报告（第 1 轮 + 第 2 轮复审）

> 审查日期：2026-08-08 (Asia/Shanghai)
> 审查人：Automated Review
> 审查范围：`actpred/` 全模块（Python，PyTorch 动作预测神经网络）
>
> **文档合并说明**：本文档为两轮审计的合并版——第 1 轮（16 项发现，全部修复）见 §二~§七；
> 第 2 轮复审（11 项发现，含 3 项上轮修复不完整，全部修复）见 §八。

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

#### [ACT-Q01] README 推理示例格式错误（已修复 ✅）

- **文件**: `README.md:28`
- **描述**: 推理示例的 `--action-history` 值 `"45,0,45,0,45,0,45,0,45,10,45,10,45,10,45,10"` 未使用冒号分隔步骤，且值数量与默认 `action_dim=8` 不匹配。正确格式应为冒号分隔的多步骤，如 `"45,0,45,0,45,0,45,0:..."`（共 `history_len=5` 步，每步 `action_dim=8` 个值）。

#### [ACT-Q02] predict.py 访问私有属性（已修复 ✅）

- **文件**: `predict.py:83`
- **描述**: `main()` 中 `predictor._data_cfg.action_dim` 直接访问实例私有属性。应提供公共属性或方法。

#### [ACT-Q03] DataRecorder episode 索引 O(n) 遍历（已修复 ✅）

- **文件**: `dataset.py:94-96`
- **描述**: `DataRecorder.__init__` 使用 while 循环逐个检查 episode 目录是否存在，O(n) 次 stat 调用。episode 数量大时效率低。

#### [ACT-Q04] requirements.txt 无版本上限（已修复 ✅）

- **文件**: `requirements.txt`
- **描述**: 所有依赖使用 `>=` 无上界，不同环境安装不同大版本可能导致行为差异。

#### [ACT-Q05] 无单元测试（已修复 ✅）

- **文件**: 全局
- **描述**: 模块无任何测试代码。数据加载、归一化、模型前向、ONNX 导出等关键路径完全依赖手工验证。

#### [ACT-Q06] _set.sh 固定安装 CPU-only PyTorch（已修复 ✅）

- **文件**: `_set.sh:7`
- **描述**: `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` 始终安装 CPU 版本，有 GPU 的环境无法利用。未在 README 说明 GPU 安装替代方案。

#### [ACT-Q07] CLI --normalize-actions 无法显式关闭（已修复 ✅）

- **文件**: `config.py:64`
- **描述**: `action="store_true"` + `default=None` 设计使得该参数只能设为 True 或使用默认值，无法通过 CLI 显式设为 False。当配置文件中 `normalize_actions=True` 时，CLI 无法覆盖关闭。

#### [ACT-Q08] ONNX 导出无运行时验证（已修复 ✅）

- **文件**: `export_onnx.py`
- **描述**: 导出 ONNX 后仅打印路径，不验证导出模型能否正确加载和推理。建议用 `onnxruntime` 做一致性检查。

---

## 三、统计汇总

| 等级 | 数量 | 问题 ID | 状态 |
|------|------|---------|------|
| 🔴 Critical | 2 | S01, S02 | 全部修复 ✅ |
| 🟡 Medium | 6 | S03-S08 | 全部修复 ✅ |
| 🟢 Low | 8 | Q01-Q08 | 全部修复 ✅ |
| **合计** | **16** | | **全部修复 ✅** |

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

### 第一阶段（紧急） ✅ 已完成

1. **ACT-S01**: 修复 episode 索引错位——`__init__` 改用 `_valid_episode_dirs`
2. **ACT-S02**: 推理归一化检查——读取 `config.normalize_actions`，缺失 stats 文件时报错

### 第二阶段（重要） ✅ 已完成

3. **ACT-S03**: 空训练集校验——添加 `len(train_ds) == 0` 检查并报错
4. **ACT-S05**: 空样本列表防御——计算统计量前检查 samples 非空
5. **ACT-S06**: 编程 API 形状校验——`predict()` 入口校验 action_history 形状
6. **ACT-S08**: 指定 stats 路径不存在时报错，而非静默 fallback

### 第三阶段（优化） ✅ 已完成

7. **ACT-S04**: 自然排序图像文件名
8. **ACT-S07**: MobileNet 权重下载失败时给出明确的预下载指引
9. **ACT-Q01-Q08**: 文档修正、代码质量优化、单元测试、版本约束等

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

## 六、修复总结

> 修复时间：2026-08-08
> 全部 16 项问题已于审查当日修复完毕，17 项单元测试全部通过。

### 修改文件清单

| 文件 | 修复项 | 说明 |
|------|--------|------|
| `dataset.py` | S01, S04, S05, S08, Q03 | episode 索引一致性、自然排序、空集防御、stats 路径校验、glob 优化 |
| `predict.py` | S02, S06, Q02 | 归一化检查、形状校验、公共属性 |
| `train.py` | S03 | 空训练集校验 |
| `model.py` | S07 | MobileNet 下载错误提示 |
| `config.py` | Q07 | BooleanOptionalAction 双向开关 |
| `export_onnx.py` | Q08 | ONNX 一致性验证 |
| `requirements.txt` | Q04 | 版本上限约束 |
| `_set.sh` | Q06 | GPU 安装说明 |
| `README.md` | Q01 | 推理示例修正 |
| `test_actpred.py` | Q05 | 新建，17 项单元测试 |

### 测试结果

```
Ran 17 tests in 0.070s

OK
```

测试覆盖：自然排序、动作历史解析、模型前向传播（MLP/GRU/动态 batch）、数据录制、数据集加载（含空集/无效路径报错）、配置与 CLI 参数。

---

## 七、附录

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

---

## 八、第 2 轮复审（2026-08-08）

> 前置文档：[第 1 轮安全审计](../security/actpred_SECURITY_AUDIT_20260808.md)（10 项发现，全部标记已修复 ✅）

### 8.1 复审方法

本轮采用**静态通读 + 动态验证**：除逐文件复查全部源码外，对疑点编写了针对性复现脚本（checkpoint 畸形 config、值域校验边界、数据形状不匹配、CLI 越界参数等），**所有结论均以实测输出为准**，不采信审计文档的"已修复"标记。

实测环境：Intel Xeon E3-1200 v2, Linux 24.04, PyTorch CPU-only。第 1 轮审计后测试已扩展至 **39 用例（38 通过 + 1 环境性跳过）**。

### 8.2 复审结论摘要

| 类别 | 数量 | 说明 |
|------|------|------|
| A 类：上轮"已修复"标记与实现不符（修复不完整/未覆盖） | 3 | ACT-R01 ~ ACT-R03，均涉及安全审计 F-3 / F-8 的修复遗漏 |
| B 类：新发现问题 | 8 | ACT-R04 ~ ACT-R11，数据校验、CLI 校验、部署约定、文档缺口 |
| **合计** | **11** | 无 P0/P1，全部为 P3 级；安全评级维持 **B+**；**全部已修复 ✅** |

模块仍为离线 CLI 工具（不监听端口、不联网、输入均来自本机可信路径），无远程可利用漏洞。本轮问题集中在**输入健壮性、修复覆盖完整性、部署约定文档化**。

### 8.3 A 类：上轮修复项复审（3 项）

#### [ACT-R01] checkpoint config 多余键仍抛裸 TypeError（F-8 声称已修复，实际未修复）🟡 P3 ✅ 已修复

- **位置**: `predict.py`、`export_onnx.py`（`DataConfig(**cfg_dict["data"])` / `ModelConfig(**cfg_dict["model"])`）
- **上轮声明**: 安全审计 F-8 描述为"多余键触发裸 TypeError"，修复项为新增 `validate_config_values()`，结论"已修复 ✅"
- **实测**: 向 checkpoint config 的 `data` 段注入未知键 `evil_extra_key`，加载时抛 `TypeError: DataConfig.__init__() got an unexpected keyword argument 'evil_extra_key'`。`validate_config_values` 在 `DataConfig(**...)` **之后**才被调用，未知键在构造阶段即崩溃，校验函数根本无法执行。
- **影响**: 畸形/损坏 checkpoint（或未来 config 结构演进产生的多余键）报错信息不含文件名与上下文；上轮"已修复"结论与实现不符。
- **修复**: 新增 `parse_checkpoint_config()`，构造前校验键集合，未知键/缺段/非 dict 抛带键名的 ValueError。

#### [ACT-R02] validate_config_values 遗漏 fused_hidden_dim，恶意 checkpoint 可致 OOM（F-8 修复不完整）🟡 P3 ✅ 已修复

- **位置**: `config.py`
- **实测**: 设 `model.fused_hidden_dim = 10**9`，`validate_config_values` **通过**；构造 `ActionPredictor` 时 `FusionHead` 分配内存抛 `RuntimeError ... DefaultCPUAllocator: can't allocate`。另有 `dropout`（可 <0 或 >1）也未被校验。
- **影响**: 与 F-8 防护目标（恶意/损坏 checkpoint 防内存耗尽）直接冲突——攻击面同类、防护漏了 `fused_hidden_dim`。
- **修复**: 校验表补充 `fused_hidden_dim ∈ [1, 4096]`、`dropout ∈ [0, 1]`，并扩展 train 字段校验（lr/val_split/batch_size/epochs/patience/num_workers/max_grad_norm/weight_decay）。

#### [ACT-R03] pretrain_mt.py 未传 pretrained=False，mobilenet 离线预训练仍失败（F-3 修复不完整）🟡 P3 ✅ 已修复

- **位置**: `pretrain_mt.py`
- **实测**: `model = ActionPredictor(cfg.model, cfg.data)` **未传 `pretrained=False`**。`--backbone mobilenet` 时构造即尝试下载 ImageNet 权重（~14MB）：① 离线/受限网络环境下预训练直接 `RuntimeError`；② 零图像预训练会用 `load_state_dict` 全程覆盖这些权重，下载纯属浪费。
- **影响**: F-3 的修复只覆盖了"推理/导出"两个入口，遗漏了第三个模型构造入口（预训练）。当前默认 backbone=simple 未触发，属潜伏问题。
- **修复**: 构造模型传 `pretrained=False`。

### 8.4 B 类：新发现问题（8 项）

#### [ACT-R04] ActionDataset 不校验 actions.npy 列数与 action_dim 一致性 🟡 P3 ✅ 已修复

- **位置**: `dataset.py`
- **实测**: 构造 6 列 `actions.npy` 配 `action_dim=4`，数据集**加载成功**，样本 `action_history` 形状 (3, 6)；直到训练 `DataLoader` collate 阶段才因形状不一致报错，错误远离数据源头且信息不友好。
- **修复**: 加载后校验 `actions.ndim == 2 and actions.shape[1] == action_dim`，否则抛带 episode 路径的 ValueError。

#### [ACT-R05] 图像/动作帧数不匹配时静默截断，无任何警告 🟡 P3 ✅ 已修复

- **位置**: `dataset.py`（`n = min(n_frames, n_images)`）
- **实测**: 12 帧动作 + 8 张图像，数据集静默按 8 帧使用，**尾部 4 帧动作被丢弃且无提示**。
- **修复**: `n_frames != n_images` 时打印 WARNING（含 episode 路径与数量）。

#### [ACT-R06] 训练 CLI 参数无值域校验，越界值静默接受 🟡 P3 ✅ 已修复

- **位置**: `config.py`（`apply_overrides`）、`train.py`
- **实测**: `--history-len 0`、`--val-split 2.0`、`--lr -1` **全部静默接受**并进入训练：`history_len=0` 产生空 history 样本；`val_split=2.0` 验证集吞掉全部 episode；`lr=-1` loss 发散。
- **对比**: `validate_config_values()` 只被推理/导出路径调用，**训练 CLI 完全不校验**——同一份参数两套待遇。
- **修复**: train.py / pretrain_mt.py 在 `apply_overrides` 后调用 `validate_config_values(cfg.data, cfg.model, cfg.train)`。

#### [ACT-R07] predict.py 加载 action_stats.npy 不校验形状 🟡 P3 ✅ 已修复

- **位置**: `predict.py`
- **实测**: `action_stats.npy` 形状错误（如 (2, 7) 配 action_dim=4）时加载成功，推理阶段抛难以定位的广播错误 `ValueError: operands could not be broadcast together with shapes (3,4) (7,)`。
- **修复**: 加载后校验 `stats.shape == (2, action_dim)`，并强制转 float32（兼容 float64 stats）。

#### [ACT-R08] 推理端硬编码 stats 路径约定，config 中 action_stats_path 为死字段 🟡 P3 ✅ 已修复

- **位置**: `predict.py` vs `train.py`、`config.py`
- **描述**: 训练时 `action_stats_path` 被写入 checkpoint config；但推理端**从不读取该字段**，硬编码从 checkpoint 同目录找 `action_stats.npy`。部署约定（stats 必须与 checkpoint 同目录、同名）未在任何文档说明。
- **修复**: 推理端优先解析 config 记录的 `action_stats_path`（相对路径按 checkpoint 目录解析），旧 checkpoint 回退同目录约定；README 新增「部署注意」章节。

#### [ACT-R09] README 未说明 ONNX 导出硬依赖 onnxscript，按文档执行直接失败 🟡 P3 ✅ 已修复

- **位置**: `README.md`、`export_onnx.py`、`_set.sh`
- **实测**: 当前 venv（torch 2.13）执行 `bash _run.sh export` 抛 `ModuleNotFoundError: No module named 'onnxscript'`——torch ≥2.12 的 `torch.onnx.export` 走 dynamo 路径，onnxscript 为硬依赖。
- **修复**: `_set.sh` 增加 onnxscript/onnxruntime 安装；README 补充依赖说明。

#### [ACT-R10] 预训练-微调归一化语义不匹配（方法论）🟡 P3 ✅ 已修复

- **位置**: `pretrain_mt.py`（全程无归一化）vs `train.py`（`--normalize-actions` 支持）
- **描述**: 预训练始终在**非归一化**动作空间训练；若微调时开启 `--normalize-actions`，`--init-from` 加载的预训练权重将在**归一化空间**继续训练，输入分布突变。
- **修复**: README 微调示例注明「预训练权重搭配非归一化微调使用」。

#### [ACT-R11] MTActionDataset 命名误导 + parse_mt_directory 重复解析 3 次 🟡 P3 ✅ 已修复

- **位置**: `pretrain_mt.py`（`n_rows = matrix.shape[1]` 实为**列数**）
- **描述**: ① 变量名 `n_rows` 取自 `shape[1]`（列数），与直觉相反，易在后续维护中引入行列颠倒 bug；② `main()` 先 `parse_mt_directory` 一次做文件级划分，train_ds / val_ds 构造时又各解析一次全部文件，**同一目录共解析 3 次**。
- **修复**: 改名 `n_cols`；`MTActionDataset` 新增 `files` 参数复用预解析结果，解析次数 3 次 → 1 次。

### 8.5 测试覆盖缺口（复审确认）

复审时 `test_actpred.py` 39 用例，以下路径无覆盖：checkpoint config 异常结构（多余键/缺段）、`validate_config_values` 的 model/train 字段、`ActionDataset` 归一化正常路径、`actions.npy` 列数不匹配/帧数不匹配、`split_episodes` 边界、`train.py`/`pretrain_mt.py` 的 `main()` 端到端、stats 形状错误。

### 8.6 修复记录（全部 ✅）

> 修复时间：2026-08-08（复审当日）
> 全部 11 项问题已修复并验证，单元测试由 39 用例扩展至 **51 用例（50 通过 + 1 环境性跳过）**。

| 问题 | 修复内容 | 文件 |
|------|----------|------|
| ACT-R01 | 新增 `parse_checkpoint_config()`：校验 config 键集合，未知键/缺段/非 dict 抛带键名的 ValueError | config.py、predict.py、export_onnx.py |
| ACT-R02 | 校验表补充 `fused_hidden_dim ∈ [1,4096]`、`dropout ∈ [0,1]`，并扩展 train 字段校验 | config.py |
| ACT-R03 | `pretrain_mt.py` 构造模型传 `pretrained=False`，消除零图像预训练的无谓下载与离线失败 | pretrain_mt.py |
| ACT-R04 | `actions.npy` 加载后校验 `ndim==2 且 shape[1]==action_dim`，否则抛带 episode 路径的 ValueError | dataset.py |
| ACT-R05 | 帧数不匹配打印 WARNING（含 episode 路径与数量） | dataset.py |
| ACT-R06 | train.py / pretrain_mt.py 在 `apply_overrides` 后调用 `validate_config_values(cfg.data, cfg.model, cfg.train)` | train.py、pretrain_mt.py |
| ACT-R07 | stats 加载后校验形状 `(2, action_dim)`，并强制转 float32（兼容 float64 stats） | predict.py |
| ACT-R08 | 推理端优先解析 checkpoint config 记录的 `action_stats_path`（相对路径按 checkpoint 目录解析），旧 checkpoint 回退同目录约定；README 新增「部署注意」 | predict.py、README.md |
| ACT-R09 | `_set.sh` 增加 onnxscript/onnxruntime 安装；README 补充依赖说明 | _set.sh、README.md |
| ACT-R10 | README 微调示例注明「预训练权重搭配非归一化微调使用」 | README.md |
| ACT-R11 | `n_rows` 改名 `n_cols`；`MTActionDataset` 新增 `files` 参数复用预解析结果，解析次数 3 次 → 1 次 | pretrain_mt.py |

### 8.7 验证记录

- 全部 51 项单元测试通过（`Ran 51 tests ... OK (skipped=1)`）
- 复现脚本确认：多余键 → ValueError（含键名）；fused_hidden_dim=10⁹ → 加载期拦截（不再 OOM）；stats 形状错误 → ValueError（含路径与期望形状）；`--lr -1` → CLI 拦截
- 预训练端到端（3 个 .mt、3 epochs）正常，空验证集保底保存路径正确
- 推理端 stats 双路径（config 相对路径 / 同目录回退）均验证通过
- 上轮声称的亮点机制（weights_only、文件级划分、stats 隔离、保底保存等）经复查**均属实**

---

*本文档为两轮审计合并版：第 1 轮（§二~§七）+ 第 2 轮复审（§八），全部 27 项发现均已修复，修复项均经回归验证。*
