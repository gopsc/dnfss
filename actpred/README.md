# actpred — 动作预测模块

输入多帧摄像头图像 + 历史动作序列，预测下一时间步的连续动作（回归）。

纯神经网络实现（PyTorch）：轻量 CNN 或 MobileNetV2 逐帧编码，图像/动作 token 进轻量
Transformer（pre-LN）自注意力融合，取 [PRED] token 回归下一步动作。
默认配置总参数量 ~450K。

## 架构

```
F 帧图像 (F×3×224×224) ─► 共享 CNN 编码器 ─► F 个 token ─► proj → d_model ┐
                                                                          ├─► [img×F, act×H, PRED] + 位置编码
动作历史 (H×D)      ─► Linear proj → d_model ─► H 个 token ────────┤        │
可学习 [PRED] token ────────────────────────────────────────┘        ▼
                                       TransformerEncoder (pre-LN, 2 层, 4 头, d_model=128)
                                                                                 │
                                             取 PRED 位置输出 ─► MLP 头 ─► 预测动作 (action_dim)
```

token 序列长度固定为 F+H+1（默认 3+5+1=9），除 batch 外无动态 shape，对 NPU（如树莓派
AI 扩展板）部署友好。

## 快速开始

```bash
# 安装（默认 CPU-only；如需 GPU 支持，编辑 _set.sh 按注释说明操作）
cd actpred && bash _set.sh

# 训练
bash _run.sh train --data-dir ./data --epochs 100 --action-dim 18

# 断点续训练（从 last.pt 恢复 model/optimizer/scheduler/epoch）
bash _run.sh train --data-dir ./data --epochs 100 --resume checkpoints/last.pt

# 推理（--images 按时间顺序旧→新传入 frame_len 个图像路径，冷启动时可重复传同一帧；
#      --action-history 格式：冒号分隔各步，每步 action_dim 个值，共 history_len 步）
bash _run.sh --model checkpoints/best_inference.pt \
    --images frame_t-2.jpg frame_t-1.jpg frame_t.jpg \
    --action-history "45,0,45,0,45,0,45,0,90,90,10,200,0,0,0,0,0,0:45,0,45,0,45,0,45,0,90,90,0,20,0,0,0,0,0,0:45,0,45,0,45,0,45,0,90,90,0,0,0,0,0,0,0,0:45,0,45,0,45,0,45,0,90,90,0,0,0,0,0,0,0,0:45,0,45,0,45,0,45,0,90,90,0,0,0,0,0,0,0,0"

# 导出 ONNX（如安装了 onnxruntime，导出后自动验证一致性）
bash _run.sh export --model checkpoints/best_inference.pt --output model.onnx

# 运行单元测试
python test_actpred.py
```

> **注意**：torch ≥ 2.12 的 ONNX 导出依赖 `onnxscript`，`_set.sh` 已一并安装；
> 如单独使用 requirements.txt 安装，请额外执行 `pip install onnxscript onnxruntime`。

## 预训练（从 .mt 脚本，无需图像）

从 bot4cj 的 `.mt` 动作脚本中提取 `@Action` 矩阵，用全零多帧图像占位预训练动作动力学（history → next action），
保存的 checkpoint 与正常训练格式完全兼容，可直接用于推理或作为微调起点。

```bash
# 预训练（解析 ../bot4cj/scripts/*.mt）
bash _run.sh pretrain --mt-dir ../bot4cj/scripts --epochs 200 --noise-std 1.0

# 预训练断点续训
bash _run.sh pretrain --mt-dir ../bot4cj/scripts --epochs 200 --noise-std 1.0 \
    --resume checkpoints/pretrain_last.pt

# 用预训练权重作为微调起点
# 注意：预训练在非归一化动作空间进行，微调时请勿开启 --normalize-actions，
# 否则输入分布与预训练权重不匹配，预训练收益会大打折扣
bash _run.sh train --data-dir ./data --epochs 50 \
    --init-from checkpoints/pretrain_best_inference.pt
```

| 参数 | 说明 |
|------|------|
| `--mt-dir` | .mt 脚本目录（如 `../bot4cj/scripts`） |
| `--noise-std` | 高斯噪声标准差（数据增强，建议 0.5~2.0） |

> **注意**：.mt 脚本列数（12）与 `action_dim`（18）不一致时自动补零。预训练保存到 `checkpoints/pretrain_best_inference.pt`。

## 数据格式

```
data/
    episode_001/
        images/
            0000.jpg
            0001.jpg
            ...
        actions.npy       # shape: (T, action_dim) float32
    episode_002/
        ...
```

每个 timestep 的样本：帧 `[t-frame_len+1, t]`（旧→新）+ 前 `history_len` 步动作 → 当前步动作。
采样起点为 `max(history_len, frame_len-1)`。

### 数据采集

```python
from dataset import DataRecorder

recorder = DataRecorder("./data", action_dim=18)
for image, action in robot_loop():
    recorder.record(image, action)
recorder.flush()
```

## 配置

所有超参数通过 CLI 参数覆盖，默认值见 `config.py`：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--image-size` | 224 | 图像缩放尺寸 |
| `--action-dim` | 18 | 动作向量维度（对齐 bot4cj 18 列：10 舵机 + 2 云台 + 扬声器频率 + 延时 + 4 预留）|
| `--history-len` | 5 | 历史动作窗口长度 |
| `--frame-len` | 3 | 输入图像帧数（含当前帧，旧帧在前）|
| `--backbone` | simple | CNN 类型 (simple/mobilenet) |
| `--attn-dim` | 128 | 注意力 token 维度 d_model（须能被 attn-heads 整除）|
| `--attn-heads` | 4 | 注意力头数 |
| `--attn-layers` | 2 | TransformerEncoder 层数 |
| `--epochs` | 100 | 训练轮数 |
| `--lr` | 0.001 | 学习率 |
| `--batch-size` | 32 | 批次大小 |
| `--normalize-actions` | False | 动作归一化（`--no-normalize-actions` 关闭） |
| `--dropout` | 0.1 | Dropout 率 |
| `--weight-decay` | 0.0001 | 权重衰减 |
| `--val-split` | 0.15 | 验证集比例 |
| `--patience` | 15 | 早停耐心值 |
| `--max-grad-norm` | 1.0 | 梯度裁剪上限 |
| `--seed` | 42 | 随机种子 |
| `--num-workers` | 2 | DataLoader 工作进程数 |
| `--checkpoint-dir` | ./checkpoints | 检查点保存目录 |
| `--init-from` | 无 | 预训练 checkpoint 路径，作为微调起点（strict=False） |
| `--resume` | 无 | 从 last.pt 断点续训练（恢复 model/optimizer/scheduler/epoch） |
| `--save-config` | 无 | 保存配置到 JSON 文件 |

## 部署注意

- 启用 `--normalize-actions` 训练的模型，其 `action_stats.npy` 保存在 checkpoint 同目录；
  部署时**必须将 stats 文件与 checkpoint 一起拷贝**（推理端优先按 checkpoint 内记录的路径查找，
  找不到时回退到 checkpoint 同目录的 `action_stats.npy`）。
- `actions.npy` 的列数必须与 `--action-dim` 一致；图像与动作帧数不一致时训练会打印 WARNING 并按较少者使用。

## 编程 API

```python
from predict import ActionPredictorInference

predictor = ActionPredictorInference("checkpoints/best_inference.pt")
print(predictor.action_dim, predictor.history_len, predictor.frame_len)  # 公共属性
# images: 长度为 frame_len 的 RGB 图像列表（旧→新，末帧为当前帧；冷启动可重复同一帧）
action = predictor.predict(images, action_history)  # np.ndarray (action_dim,)
# action_history 形状须为 (history_len, action_dim)
```

## Hailo 部署（AI HAT 2 / Hailo-10H）

树莓派 AI HAT 2 上通过 `hailo_infer.py` 的 `HailoPredictor` 走 NPU 推理，
接口与 `ActionPredictorInference` 对齐。模型需先经 Hailo DFC 编译为 HEF：

```bash
# 1. 导出 ONNX
bash _run.sh export --model checkpoints/best_inference.pt --output model.onnx
# 2. 在 x86 主机（或 Hailo 云端 DFC）上编译 + 量化校准 → model.hef，拷回树莓派
# 3. 推理（零 torch 依赖，仅需 hailo_platform + numpy + PIL）
```

```python
from hailo_infer import HailoPredictor

with HailoPredictor("model.hef") as predictor:      # stats_path= 仅归一化模型需要
    action = predictor.predict(images, action_history)
```

```bash
python3 hailo_infer.py --hef model.hef --images f1.jpg f2.jpg f3.jpg \
    --action-history "45,0,...:45,0,..." [--stats action_stats.npy]
```

HEF 的 vstream 名称/形状/量化参数全部从 `HEF.get_input/output_vstream_infos()`
动态推导，不硬编码；图像输入按 Hailo 惯例为 uint8 原始像素
（ImageNet 归一化由 DFC 编译时折叠进网络）。
环境调研与 API 踩坑记录见
[docs/reports/actpred_hailo_hat2_inference_research_20260813.md](../docs/reports/actpred_hailo_hat2_inference_research_20260813.md)。
