# actpred 模块代码审计报告（Transformer 架构重写后，三轮审查）

> 审查日期：2026-08-12 (Asia/Shanghai)
> 审查人：QoderCN Automated Review
> 审查范围：`actpred/` 全模块（Python，PyTorch 动作预测神经网络，注意力架构）
> 代码基线：第 1/2 轮 — `36feeb4 feat(actpred): 新增 ONNX Runtime 推理后端 OnnxPredictor`；
> 第 3 轮 — `91b3004 refactor(actpred): 模型输入尺寸 image_size → image_width/image_height`
>
> **与上轮审计的关系**：上一份报告 `actpred_CODE_AUDIT_20260808.md` 针对的是
> 旧架构（MLP/GRU 历史编码 + 融合头，~140K 参数）。此后模块经历了注意力架构重写，
> 并新增了
> .mt 预训练（`pretrain_mt.py`）、Hailo NPU 后端（`hailo_infer.py`）、
> ONNX Runtime 后端（`onnx_infer.py`）。本报告为架构重写后的**首轮全量审计**，
> 含三轮审查：第 1 轮整体审计 + 第 2 轮深度复审（边界行为/资源管理/跨文件一致性）
> 见 §二~§七；第 3 轮针对分辨率重构提交（`91b3004`）的增量审计及 N-1 回归的
> 修复验证见 §八。上上轮报告（20260808）的 27 项发现已全部修复，
> 本报告仅记录新发现的问题。

---

## 一、模块概述

| 属性 | 值 |
|------|-----|
| 定位 | 机器人动作预测（多帧图像 + 历史动作 → 下一步动作回归） |
| 语言 | Python 3.12（venv 内置） |
| 框架 | PyTorch ≥ 2.0 |
| 架构 | CNN/MobileNetV2 图像编码 + Transformer（pre-LN）token 融合 + `[PRED]` token 回归 |
| 参数量 | ~450K（simple CNN 默认配置） |
| 入口 | `_run.sh`（train / pretrain / export / predict 子命令分发） |

### 目录结构

```
actpred/
├── config.py          # 三组 dataclass 配置 + CLI 解析 + checkpoint 配置校验
├── model.py           # ActionPredictor（SimpleCNN/MobileNetV2 + Transformer encoder）
├── dataset.py         # ActionDataset + DataRecorder
├── train.py           # 训练主流程（episode 级划分、早停、余弦退火、断点续训）
├── pretrain_mt.py     # 从 .mt 脚本预训练动作动力学（无图像，零帧占位）
├── predict.py         # PyTorch 推理后端
├── onnx_infer.py      # ONNX Runtime CPU 推理后端（零 torch 依赖）
├── hailo_infer.py     # Hailo NPU (HEF) 推理后端（零 torch 依赖）
├── export_onnx.py     # ONNX 导出（opset 18，动态 batch）
├── mt_parser.py       # .mt @Action 矩阵解析器（`-` 前向填充语义）
├── test_actpred.py    # 单元测试（1133 行，含 fake hailo_platform 注入）
├── _run.sh / _set.sh  # 运行 / 环境安装脚本
└── requirements.txt
```

### 总体评价

模块质量**较高**：

- checkpoint 反序列化全部使用 `weights_only=True`，杜绝 pickle RCE；
- `parse_checkpoint_config` 拒绝未知键、`validate_config_values` 限制值域，
  可防恶意/损坏 checkpoint 注入异常维度（OOM 攻击面已收敛）；
- 测试覆盖全面：模型前向、配置校验、.mt 解析、三后端端到端、Hailo fake 注入；
- 三个推理后端接口对齐（`predict(images, action_history)`），维度全部从
  模型/HEF 元数据动态推导，无硬编码。

本轮共发现 **16 项**问题：0 严重、4 中、10 低、2 信息级。

---

## 二、发现清单（汇总）

| # | 严重度 | 位置 | 问题摘要 |
|---|--------|------|---------|
| R1-1 | 中 | `pretrain_mt.py:141` | `data_dir` 被覆盖为 .mt 目录，`--save-config` 导出语义错误的 JSON |
| R1-2 | 中 | `predict.py:92` / `onnx_infer.py:23` / `hailo_infer.py:34` | `parse_action_history` 重复实现三次 |
| R1-3 | 低 | `train.py:14-20` | `split_episodes` 在 n=1 时训练集为空，报错信息不明确 |
| R1-4 | 低 | `dataset.py:137-149` | `DataRecorder` 崩溃时产生孤儿 episode（有图无 actions.npy） |
| R1-5 | 低 | `predict.py:113-114` | `--action-dim` override 参数无用且是陷阱（详见 R2-9） |
| R1-6 | 低 | `_set.sh:8` | PyTorch 安装未固定版本，与 requirements.txt 约束不一致 |
| R1-7 | 信息 | `hailo_infer.py:206-210` | Hailo 后端不做 ImageNet 归一化（依赖 DFC 编译折叠），HEF 与代码强绑定 |
| R1-8 | 低 | 所有 CLI 路径参数 | 无目录遍历校验（本地工具可接受，暴露为服务需加固） |
| R2-1 | 中 | `train.py:158` / `pretrain_mt.py:250` | NaN 验证损失永久阻断 best checkpoint 保存 |
| R2-2 | 中 | `dataset.py:69-71` | `ActionDataset` 加载外部 stats 文件缺 shape 校验（推理端均有） |
| R2-3 | 中 | `train.py:103-105,193-201` | 空验证集时 `best.pt` 从不创建，`--init-from` 微调会找不到文件 |
| R2-4 | 中 | `_set.sh:11` | requirements.txt 重装时可能从镜像解析到 CUDA 版 torch |
| R2-5 | 低 | `dataset.py:77` | 归一化统计量仅从 target 计算，episode 前 start 步动作未参与 |
| R2-6 | 低 | `hailo_infer.py:65` | `HEF` 对象读取 vstream info 后从不释放 |
| R2-7 | 低 | `_run.sh:14-16` | 未知子命令静默回退到 `predict.py`，拼写错误难以发现 |
| R2-8 | 低 | `parse_action_history`（三处） | 非数值输入抛裸 `ValueError`，无 step/列上下文 |
| R2-9 | 低 | `predict.py:120` | `--action-dim` override 后必然在 shape 校验处失败，应删除 |

（编号：R1-x = 第 1 轮发现，R2-x = 第 2 轮复审新发现）

---

## 三、安全审查

### 已具备的防护（良好实践）

1. **pickle 反序列化**：全部 6 处 `torch.load` 均带 `weights_only=True`
   （`train.py:120,140`、`predict.py:15`、`export_onnx.py:14`、
   `pretrain_mt.py:227`、`model.py:50`）。
2. **checkpoint 配置校验**：`parse_checkpoint_config` 拒绝未知键
   （含旧架构遗留键 `history_encoder`/`action_hidden_dim`），
   `validate_config_values` 限制所有维度在合理范围（image_size ≤ 1024、
   attn_dim ≤ 4096 等），防 OOM 注入。
3. **shell 脚本**：`_run.sh`/`_set.sh` 引号使用正确，无命令注入面。

### 残留风险（R1-8）

所有用户输入路径（`--data-dir`、`--model`、`--images`、`--action-stats-path`、
`--mt-dir`）均无目录遍历/白名单校验。当前 actpred 是本地机器人工具，风险可接受；
若未来通过 fsys/dnfs 等暴露为网络服务，需在入口层做路径规范化校验。

---

## 四、问题详述

### R2-1（中）NaN 验证损失永久阻断 best checkpoint 保存

**位置**：`train.py:158`、`pretrain_mt.py:250`

```python
if val_loss < best_val_loss:   # NaN < x 恒为 False
```

**影响**：任一 epoch 出现 NaN（梯度爆炸、数据异常），即使后续 epoch 恢复正常，
best checkpoint 也永远不会再更新；NaN 也不触发 early stopping 的语义保护。

**建议修复**：

```python
if np.isfinite(val_loss) and val_loss < best_val_loss:
    ...
else:
    if not np.isfinite(val_loss):
        print(f"WARNING: val_loss 非有限值（{val_loss}），跳过 best 更新")
```

### R2-2（中）`ActionDataset` 外部 stats 文件缺 shape 校验

**位置**：`dataset.py:65-71`

```python
stats = np.load(action_stats_path)
self._action_mean = stats[0]    # 无 (2, action_dim) 校验
self._action_std = stats[1]
```

**对比**：三个推理后端（`predict.py:57`、`onnx_infer.py:69`、`hailo_infer.py:82`）
均有 `stats.shape != (2, action_dim)` 校验，唯独训练端缺失。错误 shape 会导致
`__getitem__` 中 broadcasting 产生静默错误数据或难懂的报错。

**建议修复**：补上与推理端一致的 shape 校验。

### R2-3（中）空验证集时 `best.pt` 从不创建

**位置**：`train.py:103-105, 158-173, 193-201`

验证集为空时 `validate()` 返回 `inf`，`inf < inf` 为 `False`，训练循环内
从不保存 best；结尾兜底分支只保存 `best_inference.pt`。用户若按 README
用 `best.pt` 做 `--init-from` 微调会得到文件不存在错误。

**建议修复**：空验证集警告中明确说明只会有 `best_inference.pt`，
或兜底同时保存 `best.pt`（与 last.pt 相同的完整结构）。

### R1-1（中）pretrain 覆盖 `data_dir` 为 .mt 目录

**位置**：`pretrain_mt.py:141`

```python
cfg.data.data_dir = args.mt_dir
```

`data_dir` 在预训练流程中未被使用，但 `--save-config` 导出的 JSON 会携带
语义错误的值（.mt 脚本目录被记为图像数据目录），后续加载该配置做微调
会指向错误位置。建议删除该行。

### R1-2（中）`parse_action_history` 三处重复

`predict.py:92`、`onnx_infer.py:23`、`hailo_infer.py:34` 完全相同。
`hailo_infer.py` 注释声明零 torch 依赖是有意为之，但三者均为纯 numpy 实现，
可抽到 `utils.py`（无 torch 导入）共享，同时修 R2-8 时只改一处。

### R2-4（中）`_set.sh` CUDA 版 torch 误装风险

**位置**：`_set.sh:8,11`

先从 CPU index 装 torch，但随后 `pip install -r requirements.txt -i tuna`
在升级/重装场景可能从清华镜像解析到 CUDA 版 torch（requirements.txt 无
CPU-only 约束）。对 Pi 5 等无 CUDA 环境会造成体积膨胀或运行异常。

**建议修复**：requirements.txt 顶部注释警示，或 `_set.sh` 对 torch 依赖
显式指定 `--index-url`（pip 支持 per-requirement index 需借助 constraints）。

### R1-7（信息）Hailo 与 PyTorch/ONNX 图像预处理差异

- PyTorch/ONNX 后端：`Resize → /255 → ImageNet normalize`（float32）；
- Hailo 后端：直接传 uint8 原始像素，归一化由 DFC 编译阶段折叠进网络。

这是 Hailo 官方推荐做法且已有文档说明，但 **HEF 文件与本推理代码强绑定**：
手动替换未按正确 preprocessing 配置编译的 HEF，不会报错但结果全错。
测试已通过 fake `hailo_platform` 验证量化/反量化路径正确。

### 其余低优先级问题

| # | 详述 |
|---|------|
| R1-3 | `split_episodes`：n=1 时 `val_count=max(1,0)=1` 吃掉全部 episode，训练集为空。`train.py:99` 会抛错但信息不明。建议 split 内前置检查 `n >= 2`。 |
| R1-4 | `DataRecorder`：`actions.npy` 仅 `flush()` 时写入，崩溃留下孤儿 episode（加载器静默跳过，数据丢失）。建议 flush 失败时清理目录，或 record 时增量落盘。 |
| R1-5 / R2-9 | `predict.py --action-dim` override：checkpoint 已记录维度，override 后 `parse_action_history` 按新维度解析，随后 `predict()` shape 校验必然失败。建议删除该参数。 |
| R1-6 | `_set.sh` 装 torch 无版本约束，requirements.txt 为 `torch>=2.0,<3.0`，二者可能不一致。建议脚本改为 `pip install "torch>=2.0,<3.0" ...`。 |
| R2-5 | 归一化统计量从样本 target（`actions[start:]`）计算，episode 前 `max(H, F-1)` 步仅出现在历史中未参与统计。数据量大时可忽略。 |
| R2-6 | `hailo_infer.py:65` `HEF` 对象仅用于读 vstream info，之后不释放，可能持有句柄。 |
| R2-7 | `_run.sh` 未知子命令静默执行 `predict.py`，`bash _run.sh trani` 拼写错误报参数错而非提示未知命令。建议 `*) echo unknown; exit 1`。 |
| R2-8 | `parse_action_history` 非数值输入抛裸 `ValueError`，建议包装为带 step/列号的错误。 |

---

## 五、测试评估

**覆盖率**（相对源码 ~1500 行，测试 1133 行）：

- ✅ 模型前向（多帧 / 单帧退化 / 动态 batch / 自定义 attn）
- ✅ 配置值域 + checkpoint 配置解析（未知键、缺段、旧架构拒绝、越界）
- ✅ .mt 解析（前向填充、首行 `-` 填 0、std.mt 跨模块回归锚点、坏文件跳过）
- ✅ 数据集（形状、归一化、帧数不一致警告、维度不匹配）
- ✅ 三后端端到端（PyTorch / ONNX 与 torch 前向对齐 / Hailo fake 注入含量化往返）
- ✅ 断点续训 checkpoint 格式与权重恢复
- ✅ ONNX 导出（缺 onnxscript 时自动跳过）

**缺口**：

- 无全链路集成测试（pretrain → train → export → OnnxPredictor 推理一致性）；
- NaN/inf 损失路径无测试（对应 R2-1）；
- `DataRecorder` 崩溃/部分写入场景无测试（对应 R1-4）。

---

## 六、修复优先级建议

| 优先级 | 问题 | 工作量 |
|--------|------|--------|
| 建议修 | R2-1（NaN 污染 best）、R2-2（stats shape 校验） | 小 |
| 值得修 | R1-1、R2-3、R2-4、R2-7、R1-5/R2-9 | 小 |
| 可选 | R1-2（抽共享 utils）、R1-3、R1-4、R1-6、R2-5、R2-6、R2-8 | 中 |

---

## 七、结论

actpred 在注意力架构重写 + 三后端部署扩展后，整体质量保持在较高水平：
安全基线（weights_only、配置校验）完好，测试体系完备，代码可读性好。
第 1/2 轮未发现严重缺陷；4 项中等问题（NaN best 阻断、stats shape 校验缺失、
空验证集 best.pt 缺失、CUDA torch 误装风险）均属健壮性/部署范畴，
建议在下一次训练迭代前修复。

第 3 轮（分辨率重构）发现 1 项**严重回归**（N-1：既有 checkpoint 无法加载），
已通过重新预训练策略修复并验证，见 §八。

---

## 八、第 3 轮审计：分辨率重构（`91b3004`）与 N-1 修复

### 8.1 变更概述

提交 `91b3004 refactor(actpred): 模型输入尺寸 image_size → image_width/image_height，默认 1920×1080`
配合 `a240377 feat(pybot): Camera 默认采集分辨率 640×480 → 1920×1080`，
将单一方形 `image_size`（默认 224）拆分为 `image_width`/`image_height`（默认 1920×1080），
与相机原生采集分辨率对齐。涉及全部 11 个源文件及测试。

### 8.2 第 3 轮发现

| # | 严重度 | 位置 | 问题摘要 | 状态 |
|---|--------|------|---------|------|
| N-1 | 严重 | `config.py:parse_checkpoint_config` | 旧 checkpoint 的 `image_size` 键被判为未知键，全部既有 checkpoint 无法加载 | ✅ 已修复（重训练策略） |
| N-2 | 中 | 默认 1920×1080 | 训练默认分辨率计算/内存开销大，CPU/Pi 上 batch=32 不可行 | ✅ 已修复（默认 batch_size 32 → 4） |
| N-3 | 低 | `docs/reports/actpred_inference_speed_benchmark_20260813.md` | 5.9ms 基准数据基于旧小分辨率，分辨率变更后失效 | ✅ 已修复（注明 640×480 基线 + 新增 §5 1920×1080 复测） |

### 8.3 N-1 详述与修复

**问题**：`parse_checkpoint_config` 的未知键拒绝机制（本身是安全特性）与配置
字段改名冲突——所有旧 checkpoint 的 `config['data']` 含 `image_size` 键，
新代码判定为未知键：

```
ValueError: checkpoint config['data'] 含未知键: ['image_size']
```

实测确认仓库内 `checkpoints/pretrain_best_inference.pt` 加载失败，波及
`predict.py --model`、`export_onnx.py --model`、`train.py --init-from/--resume`。

**修复策略**：重新训练（而非代码内做旧键迁移）。模型权重与输入分辨率无关
（`SimpleCNN`/`MobileNetEncoder` 均以 `AdaptiveAvgPool2d` 收尾），旧权重本身
可迁移，但选择重训练以获得干净的新格式 checkpoint：

1. 旧 checkpoint 归档至 `checkpoints/legacy_image_size_64/`（不删除，保留追溯）；
2. 以与旧 checkpoint 相同参数重跑预训练：
   `bash _run.sh pretrain --mt-dir ../bot4cj/scripts --epochs 200 --image-width 64 --image-height 64`
   （预训练使用零帧占位图像，图像分支不参与学习，小分辨率仅为提速，
   与旧 checkpoint 的 image_size=64 保持一致）；
3. 训练在 epoch 101 早停（patience=15），best val_loss=205.77
   （旧 checkpoint 为 190.58，同一小数据集上的正常波动）。

**验证结果**：

- 新 checkpoint config 为 `image_width: 64, image_height: 64`，无 `image_size` 键 ✅
- `ActionPredictorInference` 加载成功，推理输出形状 (18,)、数值有限 ✅
- `pretrain_best.pt` / `pretrain_best_inference.pt` / `pretrain_last.pt` 均已重新生成 ✅
- 全量测试套件 71/71 通过 ✅

**附注**：本次修复不改任何代码——`parse_checkpoint_config` 的严格未知键拒绝
是正确的安全设计，不应为兼容旧格式放宽；配置 schema 变更时重新生成 checkpoint
是更干净的做法。归档的 legacy checkpoint 仅供追溯，不应再用于推理。

### 8.4 重构正确性核查（无问题项）

- `transforms.Resize((H, W))`（torchvision 语义）与 PIL `resize((W, H))`
  在 `dataset.py`/`predict.py` 与 `onnx_infer.py`/`hailo_infer.py` 中参数顺序均正确；
- Hailo 4D/5D 形状推导正确放宽方形约束（`_derive_dims` 删除 `s2 == image_size` 检查），
  支持非方形输入；ONNX `_locate_inputs` 同步放宽；
- 重构无遗漏：源码中仅剩测试辅助函数 `_create_dummy_episode` 的局部参数名
  `image_size`（与 config 字段无关，无害）；
- README、配置表、docstring 中的形状标注（`(S,S)` → `(H,W)`）同步更新。

### 8.5 遗留事项（第 3 轮）

- **N-2 已修复**：`config.py` 默认 `batch_size` 32 → 4（1920×1080 单样本
  激活+梯度约数百 MB，batch=4 是 CPU/Pi 8GB 的安全上限；小分辨率可经
  `--batch-size` 调大），README 配置表同步更新；71/71 测试通过；
- **N-3 已修复**：基准报告顶部补注 640×480 基线说明，新增 §5 记录
  1920×1080 复测（x86 参考机：PyTorch 701.6 ms / ONNX 326.3 ms）。
  复测同时暴露新的部署级问题：**1920×1080 全分辨率 CPU 推理远不能
  满足实时闭环**，量产必须依赖 Hailo NPU 量化推理或降低推理分辨率，
  已在报告 §5 给出建议；Pi 5 板载新分辨率数据待补测；
- §六 优先级表中的第 1/2 轮遗留项（R2-1 NaN、R2-2 stats 校验等）仍未修复，
  继续有效。
