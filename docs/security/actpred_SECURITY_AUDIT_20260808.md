# actpred 安全审计报告

**审计日期**: 2026-08-08
**审计范围**: `actpred/`（config.py / model.py / dataset.py / train.py / pretrain_mt.py / mt_parser.py / predict.py / export_onnx.py / test_actpred.py / _run.sh / _set.sh / requirements.txt，共 ~1100 行）
**审计轮次**: 第 1 轮
**安全评级**: B+

---

## 1. 审计概述

actpred 是动作预测模块（图像 + 动作历史 → 下一动作回归，PyTorch），为**离线 CLI 工具**：不监听端口、不接入 pymsg/HTTP，无对外网络暴露面。攻击面收敛为三类输入：

1. **模型 checkpoint**（`torch.load`，pickle 反序列化风险点）
2. **训练数据**（`actions.npy` / 图像文件 / `.mt` 脚本）
3. **依赖供应链**（pip 安装、MobileNetV2 预训练权重下载）

本轮发现 **10 项问题（0 P0 / 0 P1 / 3 P2 / 7 P3）**，全部已修复；另确认 8 项已有良好机制。由于模块不联网、输入均来自本机可信路径，未发现可远程利用的漏洞，问题集中在**功能正确性与方法论**层面。

---

## 2. 发现问题

### F-1: 微调闭环缺失 —— pretrain 完成后打印的命令无法执行 ✅ 已修复

- **等级**: P2（功能性）
- **位置**: `pretrain_mt.py` L258-259、`train.py`（全文）、`README.md` L46-47
- **问题**: 预训练结束时提示 `Fine-tune: bash _run.sh train --data-dir ./data --model <path>`，但 `train.py` 的 argparser（`config.build_argparser`）**没有 `--model` 参数**，也没有任何加载预训练权重的逻辑——照提示执行会直接 argparse 报错退出。README 也承认"需修改 train.py 加载逻辑，或手动加载"。预训练产物（`pretrain_best_inference.pt`）目前只能用于直接推理，无法进入微调流程，预训练的核心价值（初始化 action_encoder / fusion_head）落不了地。
- **修复**: `config.build_argparser` 新增 `--init-from` 参数；`train.py` 模型构造后以 `torch.load(weights_only=True)` + `load_state_dict(strict=False)` 加载预训练权重（打印 missing/unexpected keys）；`pretrain_mt.py` 完成提示 `--model` → `--init-from`；README 微调示例同步更新。验证：现有 pretrain checkpoint 加载后 missing=0、unexpected=0，完全兼容。

### F-2: 预训练 train/val 按样本级随机划分，滑动窗口重叠导致数据泄漏 ✅ 已修复

- **等级**: P2（方法论）
- **位置**: `pretrain_mt.py` L153-162
- **问题**: `MTActionDataset` 用滑动窗口采样（步长 1），相邻样本共享 `history_len-1 = 4` 行数据；随后按**样本索引**随机划分 train/val。验证集中几乎每个样本都与某个训练样本高度重叠 → **val_loss 系统性偏乐观**，早停与模型选择基于失真信号（上次预训练的 best val_loss=190.58 可信度打折）。对照 `train.py` 是按 **episode 级**划分（正确做法），预训练未沿用。
- **修复**: `MTActionDataset` 新增 `file_indices` 参数，只加载指定索引的 .mt 文件；`main()` 改为先 `parse_mt_directory` 获取文件列表，按文件级随机划分（train/val 文件不重叠），再分别构造两个 dataset——每个文件的全部滑动窗口只进一个集合。移除不再使用的 `Subset` 导入。验证：13 个有效 .mt 文件 → 12 train / 1 val（20+2 样本），无跨集窗口重叠。

### F-3: 推理加载 mobilenet checkpoint 会触发 ImageNet 权重网络下载（离线失败 + 无谓供应链面） ✅ 已修复

- **等级**: P2
- **位置**: `model.py` L29-45（`MobileNetEncoder.__init__`）、`predict.py` L24 / `export_onnx.py` L18
- **问题**: `MobileNetEncoder` 构造时无条件下载 `MobileNet_V2_Weights.DEFAULT`（ImageNet 预训练权重）。推理/导出路径（`ActionPredictorInference`、`export_onnx`）构造模型后紧接着 `load_state_dict` **完全覆盖**这些权重——下载纯属浪费，且引入两个问题：① 离线/受限网络环境下，backbone=mobilenet 的 checkpoint **无法推理**（构造即抛 RuntimeError）；② 推理路径引入不必要的外部权重下载（供应链面扩大）。当前默认 backbone=simple 未触发，属潜伏问题。
- **修复**: `MobileNetEncoder` 与 `ActionPredictor` 新增 `pretrained: bool = True` 参数；`pretrained=False` 时传 `weights=None`（不下载 ImageNet 权重，仅初始化架构）。`predict.py` 和 `export_onnx.py` 构造模型时传 `pretrained=False`，训练路径保持默认 `True` 不变。

### F-4: mt_parser 行列不一致静默容错，解析失败静默跳过整个文件 ✅ 已修复

- **等级**: P3（数据完整性）
- **位置**: `mt_parser.py` L61-67、L85-90
- **问题**: ① 数据行比表头**短**时，缺失列静默保持 0.0（错误数据混入训练集无告警）；数据行比表头**长**时 `matrix[i, j]` 直接 IndexError；② `parse_mt_directory` 对 `ValueError` 一律 `pass`（无 @Action 块、数值非法等），文件被**静默丢弃**——训练数据缺失无任何日志，且 IndexError 不在捕获范围会直接崩溃。
- **修复**: 行列不一致时抛带行号的 ValueError；数值解析失败抛带行列号的 ValueError；`parse_mt_directory` 捕获 `Exception`（含 IndexError）并打印 `WARNING: 跳过 .mt 文件 <path>: <reason>`。

### F-5: mt_parser 首行 `-` 占位符填 0.0，与 bot4cj 语义不符 ✅ 已修复

- **等级**: P3（数据保真度）
- **位置**: `mt_parser.py` L64-65
- **问题**: bot4cj 中 `-` 表示"沿用上一状态"，首行的上一状态是 `@Servo` 块的初始角度；actpred 解析器对首行 `-` 一律填 0.0。实测 `jump.mt` 首行 `-` 恰好对应 0 值舵机未失真，但这是巧合——初始角非 0 的脚本首行样本会引入错误的(history, target)对。
- **修复**: 新增 `_parse_servo_init()` 解析 `@Servo` 块的 `max` 列（舵机初始角度）；`parse_mt_action` 根据列名（如 `s1.A` → 查 `s1`）为每列计算初始值，首行 `-` 从对应舵机初始角填充，无映射的列（如 `spk.F`）填 0.0。验证：`jump.mt` 首行 s2.A=180、s4.A=-180 与 @Servo 定义一致。

### F-6: parse_action_history 用 assert 做输入校验，`python -O` 下失效 ✅ 已修复

- **等级**: P3
- **位置**: `predict.py` L72-73
- **问题**: 维度校验依赖 `assert`，优化模式（`python -O`）下被剥离，畸形输入将以错误形状进入 `np.array`（可能产生 object 数组或错误广播）。
- **修复**: 改为显式 `raise ValueError`；同步更新 `test_actpred.py` 中 `test_wrong_dim_raises` 的断言类型（AssertionError → ValueError）。

### F-7: DataRecorder 不校验 action 维度，episode 目录分配非原子 ✅ 已修复

- **等级**: P3
- **位置**: `dataset.py` L100-120
- **问题**: ① 构造时接收 `action_dim` 但 `record()` 从不校验 `action.shape`——录错维度的动作会在 `flush()` 时静默写出畸形 `actions.npy`（或 `np.array` 构造 object 数组），下游训练才暴露；② episode 索引通过 glob 扫描 + max 计算，两个并发 Recorder 会分配到同一目录互相覆盖。
- **修复**: `record()` 增加 `action.shape != (action_dim,)` 校验抛 ValueError；目录创建改为 `mkdir(exist_ok=False)` + `FileExistsError` 重试递增 ep_idx，保证原子性。

### F-8: checkpoint 内嵌 config 未做值域校验 ✅ 已修复

- **等级**: P3（低风险）
- **位置**: `predict.py` L16-18、`export_onnx.py` L14-16
- **问题**: `torch.load(weights_only=True)` 已阻断 pickle 代码执行（✅），但 checkpoint 中的 config 字典仍被直接用于建模：恶意/损坏 checkpoint 可设置超大 `image_size`/`history_len`/`cnn_feature_dim` 造成内存耗尽，或多余键触发裸 TypeError。本模块 checkpoint 来源为本机训练产物，实际风险低。
- **修复**: `config.py` 新增 `validate_config_values()` 函数，对 image_size/history_len/action_dim/cnn_feature_dim/action_hidden_dim 做合理性上限校验（[1,1024] 或 [1,4096]）；`predict.py` 和 `export_onnx.py` 加载 config 后调用校验。

### F-9: venv 实际依赖违反 requirements.txt 版本约束 ✅ 已修复

- **等级**: P3（供应链/一致性）
- **位置**: `_set.sh` L8-11、`requirements.txt` L5
- **问题**: 实测 venv 中 `Pillow==12.2.0`，违反 `Pillow>=9.0,<12.0` 上限。原因是 `_set.sh` 先从 PyTorch 源装 torch/torchvision（torchvision 无约束地拉入最新 Pillow），再装 requirements 时未强制降级（或安装顺序导致约束未生效）。声明与实际环境脱节，版本上限形同虚设；且无 hash 锁定。
- **修复**: `requirements.txt` 中 Pillow 上限放宽至 `<14.0`（torchvision 依赖管理，加注释说明）；`_set.sh` 末尾新增 `pip check` 验证依赖一致性。

### F-10: `--seed` 对 episode 划分不生效 ✅ 已修复

- **等级**: P3（可复现性）
- **位置**: `train.py` L78-79
- **问题**: `split_episodes(cfg.data.data_dir, cfg.train.val_split)` 未传 `cfg.train.seed`，函数内硬编码默认 `seed=42`。用户指定 `--seed 123` 时权重初始化、DataLoader 洗牌均变化，唯独 train/val episode 划分不变，可复现性语义不完整（也可视为"划分稳定"的特性，但与参数预期不符）。
- **修复**: `split_episodes()` 调用处显式传入 `seed=cfg.train.seed`。

---

## 3. 已确认的良好机制

| # | 机制 | 位置 |
|---|------|------|
| 1 | `torch.load(..., weights_only=True)`，阻断 checkpoint pickle 反序列化 RCE | predict.py L15、export_onnx.py L13 |
| 2 | `np.load` 默认 `allow_pickle=False`（numpy ≥1.16.3），npy 文件无 pickle 风险 | dataset.py L41、predict.py L45 |
| 3 | 归一化统计量只用训练集计算再供验证集复用，避免统计泄漏 | train.py L86-91 |
| 4 | 空验证集返回 `inf` 不参与 best 比较，避免假 best；并有保底保存 | train.py L56-57、L158-164 |
| 5 | 图像文件自然排序（`1.jpg < 10.jpg`），避免帧序错乱 | dataset.py L12-14 |
| 6 | 全局种子固定（random/numpy/torch/cuda + DataLoader generator）、梯度裁剪、早停 | train.py L69-73、L106-110 |
| 7 | ONNX 导出后自动做数值一致性验证（如安装 onnxruntime） | export_onnx.py L40-52 |
| 8 | 推理/训练双 checkpoint 分离（best.pt 含 optimizer / best_inference.pt 纯权重），格式互相兼容 | train.py、pretrain_mt.py |

---

## 4. 测试覆盖缺口 ✅ 已补齐

首轮审计时 `test_actpred.py` 仅 17 用例，覆盖自然排序、动作历史解析、模型前向（simple + mlp/gru）、DataRecorder、ActionDataset、config 覆盖机制。以下缺口已全部补齐（现 **39 用例，38 通过 + 1 环境性跳过**）：

- ✅ `mt_parser.py`（新增 7 用例）：正常解析 + `-` 前向填充、首行 `-` 从 @Servo 初始角填充、无 @Action 块 / 仅表头 / 行列不一致 / 数值非法各自抛 ValueError、`parse_mt_directory` 跳过坏文件并打印 WARNING
- ✅ `pretrain_mt.py`（新增 4 用例）：MTActionDataset 滑动窗口样本数与 target 值、列数 < action_dim 补零、> 截断、`file_indices` 文件级过滤
- ✅ `ActionPredictorInference` 端到端（新增 6 用例）：checkpoint 保存 → 加载 → 推理（形状/有限性/确定性）、加载权重与保存权重逐张量一致、history 形状错误抛 ValueError、归一化缺 action_stats.npy 抛 FileNotFoundError、恒等统计量（mean=0/std=1）归一化路径与非归一化输出一致、checkpoint config 值域非法抛 ValueError
- ✅ `export_onnx.py`（新增 1 用例）：端到端导出并校验产物非空；因当前 venv 缺 `onnxscript`（torch 2.13 的 `torch.onnx.export` 走 dynamo 路径的硬依赖），用 `skipUnless` 环境检测跳过——安装 onnxscript 后自动生效
- 另补 `validate_config_values`（2 用例）与 DataRecorder 校验/目录原子分配（2 用例）

> 注：当前环境 `torch.onnx.export` 实测报 `ModuleNotFoundError: No module named 'onnxscript'`，即 F-8 之外的独立环境问题——ONNX 导出功能在该 venv 不可用，如需导出请 `pip install onnxscript onnxruntime`。

---

## 5. 结论与修复优先级

actpred 无网络暴露面，无 P0/P1 级安全漏洞；`weights_only=True` 与 `np.load` 默认行为已覆盖两个主要反序列化风险点。核心问题是**功能正确性**：

1. **已修复**（P2）: ✅ F-1 微调加载、✅ F-2 预训练 val 泄漏、✅ F-3 mobilenet 推理离线不可用
2. **已修复**（P3）: ✅ F-4 解析器静默容错、✅ F-5 首行 @Servo 填充、✅ F-6 assert 校验、✅ F-7 DataRecorder 校验、✅ F-8 config 值域校验、✅ F-9 依赖一致性、✅ F-10 seed 传递
3. **全部 10 项问题已修复，无遗留项**
