# actpred — 动作预测模块

输入多帧摄像头图像 + 历史动作序列，预测下一时间步的连续动作（回归）。

纯神经网络实现（PyTorch）：轻量 CNN 或 MobileNetV2 逐帧编码，图像/动作 token 进轻量
Transformer（pre-LN）自注意力融合，取 [PRED] token 回归下一步动作。
默认配置总参数量 ~450K。

## 架构

```
F 帧图像 (F×3×H×W) ─► 共享 CNN 编码器 ─► F 个 token ─► proj → d_model ┐
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
| `--image-width` | 1920 | 图像缩放宽度 |
| `--image-height` | 1080 | 图像缩放高度 |
| `--action-dim` | 18 | 动作向量维度（对齐 bot4cj 18 列：10 舵机 + 2 云台 + 扬声器频率 + 延时 + 4 预留）|
| `--history-len` | 5 | 历史动作窗口长度 |
| `--frame-len` | 3 | 输入图像帧数（含当前帧，旧帧在前）|
| `--backbone` | simple | CNN 类型 (simple/mobilenet) |
| `--attn-dim` | 128 | 注意力 token 维度 d_model（须能被 attn-heads 整除）|
| `--attn-heads` | 4 | 注意力头数 |
| `--attn-layers` | 2 | TransformerEncoder 层数 |
| `--epochs` | 100 | 训练轮数 |
| `--lr` | 0.001 | 学习率 |
| `--batch-size` | 4 | 批次大小（默认分辨率 1920×1080 下 CPU/Pi 内存受限；小分辨率可调大） |
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

## ONNX Runtime 后端（CPU 加速）

`onnx_infer.py` 的 `OnnxPredictor` 走 ONNX Runtime CPU 推理，接口与
`ActionPredictorInference` 对齐，维度从 ONNX 输入元数据自动推导（无需
checkpoint）。Pi 5 实测（224×224，2026-08-13 复测）11.4ms/次含预处理
（纯推理 10.7ms），PyTorch 21ms，约 2 倍加速，与 torch 前向输出差异
<1e-5。数据见
[测速报告](../docs/reports/actpred_inference_speed_benchmark_20260813.md) §6。

```bash
bash _run.sh export --model checkpoints/best_inference.pt --output model.onnx
```

```python
from onnx_infer import OnnxPredictor

with OnnxPredictor("model.onnx") as predictor:    # stats_path= 仅归一化模型需要
    action = predictor.predict(images, action_history)
```

```bash
python3 onnx_infer.py --model model.onnx --images f1.jpg f2.jpg f3.jpg \
    --action-history "45,0,...:45,0,..." [--stats action_stats.npy]
```

## Hailo 部署（AI HAT 2 / Hailo-10H）

模型需先经 Hailo DFC（仅 x86 Linux，`venv_dfc`）编译为 HEF。
训练态 ONNX（5D 输入、AdaptiveAvgPool、MultiheadAttention 的 head
reshape）**无法被 DFC 解析**，必须用 `export_deploy.py` 的部署版导出
（按帧展开 CNN、固定 kernel AvgPool、注意力按头展开为独立 Linear、
全程 (1,1,S,d) 无 transpose 布局，权重数值不变）。

当前 NPU 目标分辨率为 **224×224**（pretrain checkpoint 配置），HEF 输入
为 frame0~2 各 (1,224,224,3) NHWC + action_history (1,1,5,18) +
pred_token (1,1,1,128)，形状与量化参数由 `hailo_infer.py` 从 vstream
info 自动推导，不硬编码。

```bash
# x86 主机（venv_dfc 内已装 hailo-sdk 5.3）：
# 1. 导出部署版 ONNX + 学习到的 pred_token（推理端喂入用）
python3 export_deploy.py --model checkpoints/pretrain_best_inference.pt \
    --pred-token-out checkpoints/actpred_deploy_pred_token.npy
# 2. 生成校准集（pred_token 按学习值 ±30% 抖动，避免常量量化退化）
python3 gen_calibration.py --deploy --model checkpoints/pretrain_best_inference.pt
# 3. 一键编译（parser → optimize → compiler，各步自动做输入连通性检查）
python3 dfc_compile.py            # 产物 checkpoints/actpred_pretrain.hef
# 4. 将 HEF + actpred_deploy_pred_token.npy 拷回树莓派
```

已验证：完整 110 层网络（含 matmul/softmax/layernorm 注意力）解析、
量化、编译全部通过；hailo10h 目标。
编译要点与踩坑记录见 `dfc_compile.py` 文档字符串。

> **警告（2026-08-13 树莓派实测）**：首版 HEF 在 NPU 上**输出对全部
> 5 个输入均不敏感**（全 0 / 全 255 / 真实数据输出完全一致，对照
> resnet HEF 同 API 验证输入通道有效）。驱动升级 5.3.0 后复测依旧，
> 判定为 DFC 编译侧问题（输入在编译图中被断开/常量化），并非推理封装
> 问题。**该 HEF 不可用于推理**。已做如下修复，待 x86 重编译后复验：
> - `_onnx_validate.py`：编译前数值验证 `actpred_deploy.onnx` 输入
>   敏感性与 PyTorch 一致性（本次已 PASS，差异 1.5e-5）
> - `dfc_compile.py`：parser/optimize 后自动检查输入层连通性，断开即报错
> - `gen_calibration.py`：pred_token 常量校准退化（scale=18571.7，任何
>   输入都被量化成 0）已改抖动校准，推理端喂学习值（`--pred-token`）
> - `hailo_infer.py`：帧输入按 input_layerN 排序绑定（vstream 报告顺序
>   实测为 1,5,4,3,2，按报告顺序绑定会 frame1/frame2 错位）
>
> 重编译后用 `_npu_test.py` 做 4 角输入敏感性复验（6.3ms/次的封装
> 性能基线已在新驱动 5.3.0 下测通）。

`hailo_infer.py` 的 `HailoPredictor` 适配 5 输入 HEF，输入布局与量化
参数（含负零偏→ImageNet 归一化识别）从 vstream info 自动推导，单元测试
含 fake hailo_platform 的 5 输入与乱序报告顺序用例。CLI 支持
`--pred-token` 喂入学习值、`--stats` 喂入归一化统计。
环境调研与 API 踩坑记录见
[docs/reports/actpred_hailo_hat2_inference_research_20260813.md](../docs/reports/actpred_hailo_hat2_inference_research_20260813.md)。
