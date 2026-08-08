# actpred — 动作预测模块

输入摄像头图像 + 历史动作序列，预测下一个连续动作（回归）。

纯神经网络实现（PyTorch），支持轻量 CNN 或 MobileNetV2 图像编码，MLP/GRU 动作历史编码。总参数量 ~140K（默认）至 ~2.3M。

## 架构

```
图像 (3×64×64)  ──►  CNN 编码器  ──►  img_feat (128)
                                          │
                                          ├─ concat ──►  融合头  ──►  预测动作 (action_dim)
                                          │
动作历史 (H×D)  ──►  MLP/GRU    ──►  act_feat (64)
```

## 快速开始

```bash
# 安装
cd actpred && bash _set.sh

# 训练
bash _run.sh train --data-dir ./data --epochs 100 --action-dim 8

# 推理
bash _run.sh --model checkpoints/best_inference.pt --image frame.jpg \
    --action-history "45,0,45,0,45,0,45,0:45,10,45,10,45,10,45,10"

# 导出 ONNX
bash _run.sh export --model checkpoints/best_inference.pt --output model.onnx
```

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

每个 timestep 的样本：当前图像 + 前 `history_len` 步动作 → 当前步动作。

### 数据采集

```python
from dataset import DataRecorder

recorder = DataRecorder("./data", action_dim=8)
for image, action in robot_loop():
    recorder.record(image, action)
recorder.flush()
```

## 配置

所有超参数通过 CLI 参数覆盖，默认值见 `config.py`：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--image-size` | 64 | 图像缩放尺寸 |
| `--action-dim` | 8 | 动作向量维度 |
| `--history-len` | 5 | 历史动作窗口长度 |
| `--backbone` | simple | CNN 类型 (simple/mobilenet) |
| `--history-encoder` | mlp | 历史编码器 (mlp/gru) |
| `--epochs` | 100 | 训练轮数 |
| `--lr` | 0.001 | 学习率 |
| `--batch-size` | 32 | 批次大小 |

## 编程 API

```python
from predict import ActionPredictorInference

predictor = ActionPredictorInference("checkpoints/best_inference.pt")
action = predictor.predict(image_rgb, action_history)  # np.ndarray (action_dim,)
```
