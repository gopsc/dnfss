# actpred — 动作预测模块

输入多帧摄像头图像 + 历史动作序列，预测下一时间步的连续动作（回归）。

纯神经网络实现（PyTorch）：轻量 CNN 或 MobileNetV2 逐帧编码，图像/动作 token 进轻量
Transformer（pre-LN）自注意力融合，取 [PRED] token 回归下一步动作。
默认配置总参数量 ~450K。

## 架构

```
F 帧图像 (F×3×H×W) ─► 共享 CNN 编码器 ─► F 个 token ─► proj → d_model ┐
                                                                          ├─► [img×F, act×H, (IMU), PRED]
动作历史 (H×D)      ─► Linear proj → d_model ─► H 个 token ────────┤        │        + 位置编码
IMU 姿态 (imu_dim) ─► Linear proj → 1 个 token（R2，可选）────────┤        ▼
可学习 [PRED] token ────────────────────────────────────────┘  TransformerEncoder (pre-LN, 2 层, 4 头, d_model=128)
                                                                                 │
                                             取 PRED 位置输出 ─► MLP 头 ─► 预测动作 (action_dim)
```

token 序列长度固定为 F+H+1（默认 3+5+1=9；`--imu-dim`>0 时 IMU token 插到
PRED 之前，长度 F+H+2），除 batch 外无动态 shape，对 NPU（如树莓派
AI 扩展板）部署友好。

## 快速开始

```bash
# 安装（默认 CPU-only；如需 GPU 支持，编辑 _set.sh 按注释说明操作）
cd actpred && bash _set.sh

# 训练
bash _run.sh train --data-dir ./data --epochs 100 --action-dim 18

# 断点续训练（从 last.pt 恢复 model/optimizer/scheduler/epoch）
bash _run.sh train --data-dir ./data --epochs 100 --resume checkpoints/last.pt

# R1 改进：残差预测 + 多步展开训练（可叠加）
# --predict-delta  预测 Δ=actions[t]-actions[t-1]（惯性先验，平滑动作；推理端自动加回 a_{t-1}）
# --rollout-len K  多步展开训练（K>1 时 scheduled sampling，治 exposure bias）
# --ss-prob P      scheduled sampling 真实历史概率（默认 1.0=全真；0.5 表示半数步用自预测回传）
bash _run.sh train --data-dir ./data --epochs 100 --action-dim 18 \
    --predict-delta --rollout-len 4 --ss-prob 0.7

# R2 姿态反馈：--imu-dim 8 启用独立 IMU token（数据需含 imu.npy，见"数据格式"）
bash _run.sh train --data-dir ./data --epochs 100 --action-dim 18 --imu-dim 8

# 推理（--images 按时间顺序旧→新传入 frame_len 个图像路径，冷启动时可重复传同一帧；
#      --action-history 格式：冒号分隔各步，每步 action_dim 个值，共 history_len 步）
bash _run.sh --model checkpoints/best_inference.pt \
    --images frame_t-2.jpg frame_t-1.jpg frame_t.jpg \
    --action-history "45,0,45,0,45,0,45,0,90,90,10,200,0,0,0,0,0,0:45,0,45,0,45,0,45,0,90,90,0,20,0,0,0,0,0,0:45,0,45,0,45,0,45,0,90,90,0,0,0,0,0,0,0,0:45,0,45,0,45,0,45,0,90,90,0,0,0,0,0,0,0,0:45,0,45,0,45,0,45,0,90,90,0,0,0,0,0,0,0,0"

# 导出 ONNX（如安装了 onnxruntime，导出后自动验证一致性）
bash _run.sh export --model checkpoints/best_inference.pt --output model.onnx

# R4 离线评估：开环滚动预测，量化闭环代理指标（误差发散/平滑度/越界率）
python evaluate.py --model checkpoints/best_inference.pt --data-dir ./data
# A/B 对比两个 checkpoint（负值 = 主模型更优）
python evaluate.py --model checkpoints/r1.pt --compare checkpoints/baseline.pt \
    --data-dir ./data --episodes 3 --horizon 50 --output report.json

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

# 用预训练权重作为微调起点（pretrain 也支持 --init-from，行为同 train）
bash _run.sh train --data-dir ./data --epochs 50 \
    --init-from checkpoints/pretrain_best_inference.pt
```

| 参数 | 说明 |
|------|------|
| `--mt-dir` | .mt 脚本目录（如 `../bot4cj/scripts`） |
| `--noise-std` | 高斯噪声标准差（数据增强，建议 0.5~2.0；与 `--normalize-actions` 联用时按 z-score 单位，建议 0.05~0.2） |
| `--random-images` | 用 ImageNet 归一化均匀噪声替代全零图像占位，**恢复 BN 统计**（详见下方警告） |
| `--init-from` | 预训练 checkpoint 路径，作为微调起点（strict=False；与当前运行动作空间不一致时打印 WARNING） |

> **注意**：.mt 脚本列数（12）与 `action_dim`（18）不一致时自动补零。预训练保存到 `checkpoints/pretrain_best_inference.pt`。
> 预训练与微调必须使用**同一动作空间**（`--normalize-actions` 同时开或同时关），
> 否则输入分布与权重不匹配，预训练收益会大打折扣。

> **警告（2026-08-13 实测）**：全零图像占位会让 BN `running_var` 停在初始小值，
> 推理时特征放大到 ±1e6，**HEF uint8 量化不可行**（输出对输入不敏感）。
> 面向 NPU 部署的预训练/微调请务必使用 `--random-images`（或真实数据微调），
> 导出时 `export_deploy.py` 的 `check_token_ranges` 会拦截退化的 checkpoint。
> 详见 [根因分析报告](../docs/reports/actpred_hef_insensitivity_rootcause_20260813.md)。
> 建议同时开启 `--normalize-actions`：z-score 后 act_tokens 与 img_tokens 同量级，
> 避免 HEF merge 点共享量化 scale 粗量化图像路径。

## 数据格式

```
data/
    episode_001/
        images/
            0000.jpg
            0001.jpg
            ...
        actions.npy       # shape: (T, action_dim) float32
        imu.npy           # 可选，shape: (T, 8×N) float32（N 个 MPU6050 姿态拼接，R2）
    episode_002/
        ...
```

每个 timestep 的样本：帧 `[t-frame_len+1, t]`（旧→新）+ 前 `history_len` 步动作 → 当前步动作。
采样起点为 `max(history_len, frame_len-1)`。

`imu.npy` 每传感器占 8 槽 `[roll, pitch, ax, ay, az, gx, gy, gz]`（roll/pitch
角度制，其余为 MPU6050 原始 int16），多传感器（最多 4 个）按脚本
`@MPU6050_BIND` 行顺序拼接，由 pybot `--record` 逐帧同步落盘。**旧 episode
没有姿态数据，训练 IMU 模型需用新版 pybot 重新录制**；`--imu-dim`>0 时缺失
`imu.npy` 的 episode 按零向量兜底（向后兼容，但无姿态先验收益）。
姿态归一化按物理量程固定缩放（roll/pitch ÷180，原始 int16 ÷32768，8 槽模式
按传感器数平铺），训练与推理共用 `imu_norm.py`，无需统计文件。
录制/训练/闭环脚本的传感器行顺序必须一致，否则槽位错位。

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
| `--image-width` | 640 | 图像缩放宽度 |
| `--image-height` | 480 | 图像缩放高度 |
| `--action-dim` | 18 | 动作向量维度（对齐 bot4cj 18 列：10 舵机 + 2 云台 + 扬声器频率 + 延时 + 4 预留）|
| `--history-len` | 5 | 历史动作窗口长度 |
| `--frame-len` | 3 | 输入图像帧数（含当前帧，旧帧在前）|
| `--imu-dim` | 0 | R2 姿态输入维度（>0 启用独立 IMU token；8×N，N=传感器数≤4）|
| `--backbone` | simple | CNN 类型 (simple/mobilenet) |
| `--attn-dim` | 128 | 注意力 token 维度 d_model（须能被 attn-heads 整除）|
| `--attn-heads` | 4 | 注意力头数 |
| `--attn-layers` | 2 | TransformerEncoder 层数 |
| `--epochs` | 100 | 训练轮数 |
| `--lr` | 0.001 | 学习率 |
| `--batch-size` | 4 | 批次大小（默认分辨率 640×480；更高分辨率需调小，小分辨率可调大） |
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
| `--predict-delta` | False | R1-C 残差预测（target 改 Δ，推理端自动加回 a_{t-1}） |
| `--rollout-len` | 1 | R1-B 多步展开步数 K（>1 时 scheduled sampling） |
| `--ss-prob` | 1.0 | scheduled sampling 真实历史概率（仅 rollout-len>1 生效） |

## 离线评估（R4）

`evaluate.py` 在留出 episode 上**开环滚动预测**（复现 pybot 闭环口径：图像用真值滚动、
动作用自预测回写），输出闭环代理指标。单步 MSE（teacher forcing）看不见闭环误差累积，
本评估补上这一盲区：

| 指标 | 含义 | 判读 |
|------|------|------|
| `overall_mse` | 全部滚动步的平均 MSE | 越低越好 |
| `error_growth` | 末段(后25%)误差 / 首段(前25%)误差 | >1 表示误差随步数发散（闭环不稳） |
| `error_curve` | 误差随步数的增长曲线 | 看发散拐点 |
| `smoothness_ratio` | 预测平滑度 / 真值平滑度 | <1 表示比真值更平滑（残差模式的预期收益） |
| `oob_rate` | 预测落在训练数据经验范围外的比例 | 越高越危险（舵机越界风险） |

物理可行性用数据分布的经验界（min/max + 10% margin）而非硬编码范围——18 列混含
舵机角度/频率/延时/预留，无统一硬限位。`--compare` 用于 A/B 对比（如 R1 开/关）。
滚动复现闭环口径：模型带 imu 输入时按 episode 的 `imu.npy` 真值姿态逐步喂入
（缺失则零向量，与 pybot 闭环无 `@MPU6050_BIND` 块时的行为一致）。

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
# 模型含 IMU 输入时（predictor.imu_dim 非 None）可传 imu= 原始姿态向量
# （roll,pitch,ax,ay,az,gx,gy,gz；内部自动归一化，None 等价零向量）
action = predictor.predict(images, action_history, imu=imu_vec)
```

## ONNX Runtime 后端（CPU 加速）

`onnx_infer.py` 的 `OnnxPredictor` 走 ONNX Runtime CPU 推理，接口与
`ActionPredictorInference` 对齐（含可选 `imu=` 入参），维度从 ONNX 输入元数据
自动推导（无需 checkpoint）。Pi 5 实测（224×224，2026-08-13 复测）11.4ms/次含预处理
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
pred_token (1,1,1,128)（`--imu-dim`>0 时追加 imu (1,1,1,imu_dim)，共 6 输入），
形状与量化参数由 `hailo_infer.py` 从 vstream
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

> **验证记录（2026-08-13 树莓派实测）**：归一化轮次（`--normalize-actions`
> + `--random-images` 预训练，224×224）重编译的 HEF 已通过 `_npu_test.py`
> 实机复验：img 敏感性 22.3、hist 敏感性 38.9（阈值 0.1），HEF vs torch
> max_diff 0.37（原始动作空间），约 12ms/次（含预处理）。此前两轮失败的
> 根因与修复均已闭环：
> - BN 统计退化 → `--random-images` 预训练 + `export_deploy.py`
>   `check_token_ranges` 导出前拦截
> - pred_token 常量校准退化（scale=18571.7）→ 抖动校准，推理端喂学习值
> - 输入层断连 → `dfc_compile.py` parser/optimize 后连通性检查
> - vstream 报告顺序乱序 → `hailo_infer.py` 按 `input_layerN` 排序绑定
> - 帧归一化判定错误（把归一化帧当原始像素喂，图像路径饱和破坏）→
>   按量化网格 dequant(0) 判定，见 hailo_infer.py 注释
>
> 验证链：`export_deploy.py`（token 量级）→ `_onnx_validate.py`（ONNX
> 数值）→ `dfc_compile.py`（输入连通性）→ `_npu_test.py`（4 角敏感性）。
> 遗留：全零历史角（z≈-3.5）超出校准输入范围（-2.7）会被截断，
> 该角仅作连通性验证，非真实部署场景。

`hailo_infer.py` 的 `HailoPredictor` 适配 5/6 输入 HEF（含可选 imu 输入，
按形状自动归类绑定），输入布局与量化
参数（帧归一化由量化网格 dequant(0) 自动判定）从 vstream info 自动推导，
单元测试含 fake hailo_platform 的 5 输入与乱序报告顺序用例。CLI 支持
`--pred-token` 喂入学习值、`--stats` 喂入归一化统计、`--imu` 喂入姿态。
环境调研与 API 踩坑记录见
[docs/reports/actpred_hailo_hat2_inference_research_20260813.md](../docs/reports/actpred_hailo_hat2_inference_research_20260813.md)。
