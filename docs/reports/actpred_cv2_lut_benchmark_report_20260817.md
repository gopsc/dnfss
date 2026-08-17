# actpred Hailo 推理速度测试报告：cv2.LUT 预处理优化验收

> 文档类型：测试报告
> 模块：actpred（hailo_infer.py cv2.LUT 快路径）
> 作者：自动生成
> 日期：2026-08-17
> 前置：`actpred_hailo_preprocess_optimization_plan_20260817.md`（优化方案，已实施）、
> `actpred_inference_speed_benchmark_20260813.md`（旧基准）

---

## 1. 测试环境

| 项 | 值 |
|---|---|
| 设备 | Raspberry Pi 5（4×A76）+ AI HAT 2（Hailo-10H，/dev/h1x-0） |
| 软件 | hailo_platform 5.3.0、opencv-python-headless 5.0.0、torch CPU 2.x |
| 模型 | actpred_v4.hef（onehot 1864 logits，fixed_range，640×480×3 帧输入） |
| 数据 | 新录 640×480 数据集 episode_0000（图像与模型输入同尺寸，免 resize） |
| 输入 | 3 帧 640×480 RGB + 5×18 动作历史（stand 姿态，spk 列 0/10 契约） |

## 2. 测试方法

- 预热 10 次后连续 200 次取均值（端到端 `predict` 调用，含预处理/绑定/NPU/解码）
- 分段计时：预处理（LUT 量化 + 绑定）/ NPU run / 后处理（反量化 + 1864 logits 解码）
- 对照：PyTorch CPU 同模型同输入（200 次均值）

## 3. 结果

### 3.1 端到端延迟

| 后端 | 延迟 | 相对优化前 |
|---|---|---|
| HEF（cv2.LUT 快路径） | **16.1 ms** | 52.4 → 16.1 ms（**3.3×**） |
| PyTorch CPU | 107.9 ms | — |
| 加速比（HEF vs CPU） | **6.7×** | 优化前 2.1× |

### 3.2 流水线分段（HEF）

| 阶段 | 耗时 | 占比 |
|---|---|---|
| 预处理（cv2.LUT + 输入绑定） | 3.0 ms | 19% |
| NPU run（HEF 纯推理） | 12.5 ms | 78% |
| 后处理（反量化 + decode） | 0.6 ms | 4% |

**瓶颈已从 Python 预处理转移到 NPU 本身**——预处理由优化前 26.5ms 降至 3.0ms（8.8×）。

### 3.3 闭环控制预算

| 控制率 | 周期预算 | HEF 占用 |
|---|---|---|
| 10 Hz | 100 ms | 16% |
| 20 Hz | 50 ms | 32% |
| 30 Hz | 33 ms | 48% |

10Hz 闭环下 NPU 仅占 16% 预算；上探 30Hz 也只占一半，余量充足。

## 4. 一致性验收

### 4.1 cv2.LUT vs float 路径输出（NPU 实测，max_diff）

| 输入类型 | max_diff |
|---|---|
| 640×480 RGB（主路径直通） | 0.0000 |
| 224×224 RGB（PIL resize） | 0.0000 |
| 全零帧 / 全 255 帧 | 0.0000 |
| 灰度 (H,W)（PIL 转 RGB） | 0.0000 |
| RGBA (H,W,4)（PIL 合成） | 0.0000 |
| 奇数尺寸 333×777（PIL resize） | 0.0000 |
| 随机帧 + 随机历史 × 8 seed | 0.0000（worst） |

### 4.2 无 cv2 回退

`HAS_CV2=False` 时 `_frame_luts=None` → float 路径，输出与 cv2 路径 max_diff = 0.0。

### 4.3 回归验收

| 项 | 结果 |
|---|---|
| `_npu_test.py` 4 角（真实/全零帧 × 真实/全零历史） | PASS（img/hist 敏感度 > 0.1） |
| HEF vs torch（原始动作空间） | 0.30°（预算 ≤1°） |
| 全套单测（test_actpred.py） | 188 tests OK |

## 5. 结论

1. cv2.LUT 快路径端到端 **16.1ms**，相比优化前 52.4ms 提升 3.3×；预处理不再是瓶颈
2. 与 float 路径**逐点一致**（7 类输入 + 8 seed 随机全部 0 差异），无隐藏分支
3. 无 cv2 环境自动回退，行为不变；量化误差（vs torch 0.30°）未回归
4. 10Hz 闭环预算占用 16%，支持控制率上探至 30Hz

## 6. 附注

- 优化前数据（52.4ms/26.5ms）来自 2026-08-17 上午 640×480 数据集基准
- cv2.LUT 的"归一化+量化"合并表数学上与 float 路径等价（0 LSB 取整差）
- 依赖：opencv-python-headless>=4.9（已入 requirements.txt；缺失仅损失加速，功能正常）
