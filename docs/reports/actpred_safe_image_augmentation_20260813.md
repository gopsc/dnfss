# actpred 训练时安全图像增强

> 文档类型：特性报告
> 模块：actpred
> 日期：2026-08-13

---

## 1. 背景

pybot dry-run 录制的 episode 数据样本量有限（11 个脚本 → 12 个 episode → ~728 样本），训练容易过拟合。直接增大数据集需要更多 `.mt` 脚本或更多轮次录制，成本较高。

图像增强（data augmentation）是低成本扩充样本的标准手段，但机器人动作预测场景有特殊约束：**空间变换（翻转/旋转/裁剪）会改变图像的语义坐标，而动作标签未随之变换，导致训练对错误**。例如水平翻转后，左右舵机角度应互换，但标签不变，形成错误训练信号。

## 2. 方案

### 2.1 增强范围

仅启用**颜色空间变换**（ColorJitter），不涉及任何空间变换：

| 变换 | 参数 | 说明 |
|------|------|------|
| 亮度 | `±20%`（factor ∈ [0.8, 1.2]） | 模拟不同光照条件 |
| 对比度 | `±20%`（factor ∈ [0.8, 1.2]） | 模拟不同摄像头增益 |
| 饱和度 | `±20%`（factor ∈ [0.8, 1.2]） | 模拟不同色温环境 |
| 色调 | `±5%`（factor ∈ [-0.05, 0.05]） | 轻微色偏，避免模型过依赖颜色 |

**不使用的变换**（因需同步变换动作标签）：

| 变换 | 原因 |
|------|------|
| 水平/垂直翻转 | 左右舵机角度需互换，否则标签错误 |
| 旋转 | 重力方向变化，姿态语义改变 |
| 裁剪/平移 | 视野变化导致动作-画面对应关系改变 |
| 透视变换 | 改变空间关系 |

### 2.2 同一样本内帧间参数一致性

一个训练样本包含 `frame_len` 帧连续图像（默认 3 帧，间隔 10ms）。真实场景中连续帧的光照条件不变，因此**同一样本内所有帧共享同一组增强参数**，而非每帧独立随机：

```python
if self.augment:
    bf = random.uniform(0.8, 1.2)   # 亮度因子，所有帧共用
    cf = random.uniform(0.8, 1.2)   # 对比度因子
    sf = random.uniform(0.8, 1.2)   # 饱和度因子
    hf = random.uniform(-0.05, 0.05) # 色调因子
for p in image_paths:
    image = Image.open(p).convert("RGB")
    if self.augment:
        image = TF.adjust_brightness(image, bf)
        image = TF.adjust_contrast(image, cf)
        image = TF.adjust_saturation(image, sf)
        image = TF.adjust_hue(image, hf)
    frames.append(self.transform(image))
```

不同样本（不同 `__getitem__` 调用）使用独立随机参数，保证整个数据集的增强多样性。

### 2.3 自适应 frame_len

增强逻辑在 `for p in image_paths` 循环前生成参数、循环内逐帧施加。`image_paths` 的长度由 `frame_len` 决定（`image_files[t - frame_len + 1 : t + 1]`），循环自动适配。**`frame_len` 为 1、2、3、5、10 等任意值时均无需修改代码**。

### 2.4 训练/验证隔离

| 数据集 | 增强 | 说明 |
|--------|------|------|
| 训练集 | `augment=cfg.train.augment` | CLI `--augment` 控制 |
| 验证集 | `augment=False`（默认） | 验证 loss 反映真实分布表现 |

推理端（`predict.py` / `onnx_infer.py` / `hailo_infer.py`）不涉及增强，使用原始图像。

## 3. CLI 接口

```bash
# 训练时启用图像增强
bash _run.sh train --data-dir ./data --epochs 100 --augment

# 显式关闭（默认关闭）
bash _run.sh train --data-dir ./data --epochs 100 --no-augment
```

`--augment` 使用 `BooleanOptionalAction`，支持 `--augment` / `--no-augment` 双向开关。

## 4. 验证

### 4.1 功能验证

```
dataset size: 728 samples
augment diff (should be >0): 0.3767        # 增强前后图像有差异
two augment calls diff (should be >0): 0.2626  # 两次增强参数不同（随机性）
action_history diff (should be 0): 0.0     # 动作标签不受影响
target diff (should be 0): 0.0
```

### 4.2 端到端训练

`--augment --epochs 3`（64×64，batch=8）训练正常完成，loss 正常下降。

### 4.3 回归测试

全量单测 90/90 通过，无回归。

## 5. 改动文件

| 文件 | 改动 |
|------|------|
| `actpred/dataset.py` | `ActionDataset` 新增 `augment` 参数；`__getitem__` 在 Resize 前施加 ColorJitter（`torchvision.transforms.functional`） |
| `actpred/config.py` | `TrainConfig` 新增 `augment: bool = False`；`build_argparser` 新增 `--augment`；`apply_overrides` 新增映射 |
| `actpred/train.py` | 训练集构造传 `augment=cfg.train.augment`，验证集默认不增强 |

## 6. 后续方向

- **空间增强（需同步标签变换）**：水平翻转时自动交换左右舵机角度（s1↔s2, s3↔s4, s5↔s6, s7↔s8），可将训练数据量翻倍。需要了解舵机配对关系后实现。
- **动作噪声增强**：`pretrain_mt.py` 已支持 `--noise-std`，`train.py` 可同样引入，对 action_history 和 target 施加高斯扰动。
- **更多 `.mt` 脚本**：从根本上扩大动作多样性，是提升模型泛化能力的最有效手段。
