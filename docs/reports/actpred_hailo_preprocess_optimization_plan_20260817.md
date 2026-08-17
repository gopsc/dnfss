# actpred Hailo 推理预处理优化方案：查表法合并归一化与量化

> 文档类型：开发方案
> 模块：actpred（部署链 hailo_infer.py）
> 作者：自动生成
> 日期：2026-08-17
> 前置：`actpred_inference_speed_benchmark_20260813.md`（速度基准）、
> 2026-08-17 新 640×480 数据集重测（本方案 §2 数据）

---

## 1. 背景与动机

2026-08-17 用新录制的 640×480 数据集（免 resize 训练样本）重测 HEF 推理速度：

| 阶段 | 640×480 输入 | 224×224 输入（旧数据） |
|---|---|---|
| 端到端 predict | **52.4 ms** | 60.0 ms |
| 预处理 | **35.9 ms** | 43.1 ms |
| NPU run | 12.5 ms | 12.9 ms |
| 后处理 | 0.6 ms | 0.6 ms |

输入尺寸已与模型一致（resize 变 no-op），**预处理仍是端到端延迟的主导项**（68%）。
旧基准报告（2026-08-13）已记录同类问题：1080p 采集的预处理缩放约 45ms 主导端到端延迟，
当时通过把默认分辨率改回 640×480 缓解；本次分析证明 640×480 下 Python 端预处理
仍有约 36ms 的优化空间。

## 2. 瓶颈分析（100 次均值分段计时）

| 子步骤 | 耗时 | 说明 |
|---|---|---|
| `_frame_float`（3 帧） | **27.2 ms** | PIL fromarray/convert + resize（同尺寸 no-op 仍有开销）+ `(x/255-mean)/std` float 运算（92 万像素×3 通道） |
| `create_bindings` + 帧量化绑定 | **9.1 ms** | `_bind_frames`：float 归一化帧 → `round(x/q.scale+q.zp)` 再量化回 uint8 |
| history/pred 绑定 | 0.07 ms | 可忽略 |

**关键冗余**：当前流程先做 float 归一化（`_frame_float`），随后又量化回 uint8（`_bind_frames`）——
中间 float 帧是纯中间态，归一化与量化可以**合并为一步**。

## 3. 优化方案：查表法（LUT）合并归一化+量化

### 3.1 核心思路

HEF 帧输入是 uint8 量化值：`q = round(((x/255 - mean)/std) / q_scale + q_zp)`。
对 uint8 原始像素 x∈[0,255]，该映射是**单变量函数**——预计算 256 项查表：

```
lut[c] = clip(round(((c/255 - mean[c])/std[c]) / q_scale + q_zp), 0, 255)   # c = 0..255
```

推理时对每个通道：`out = lut[x]`（numpy 高级索引，纯内存搬运 + 索引，无 float 运算）。

- 数学上与现流程完全等价（仅合并了中间 float 步骤，取整路径不变）
- 三张表（RGB 通道 mean/std 不同），HEF 的 `quant_info`（qp_scale/qp_zp）每输入一个
- 表随 HEF 加载时构建（`_normalize_frames` 判定负零偏后决定走查表还是直通）

### 3.2 附带优化

1. **免 PIL 路径**：输入已是 np.ndarray 时直接用 `lut[x]` 索引，跳过
   `Image.fromarray/convert`；仅需 resize 时（尺寸不一致）才走 PIL
2. **resize no-op 跳过**：`image.size == (W,H)` 时不调用 `resize()`
3. 直通模式（`_normalize_frames=False`，原始像素校准的 HEF）同样受益：
   `out = clip(round(x/q_scale+q_zp))` 也是查表

### 3.3 预期收益

| 阶段 | 现状 | 优化后（估） |
|---|---|---|
| 帧预处理（float+量化） | 27.2 + 9.1 ms | **~3-6 ms**（三帧查表 + 绑定） |
| 端到端 predict | 52.4 ms | **~18-20 ms** |

闭环 10Hz（100ms 预算）下占用率从 52% 降至 ~20%，为 20Hz 控制率留出空间。

## 4. 一致性约束（关键）

- **训练侧不动**：`dataset.py` 的 `Resize+ToTensor+Normalize` 保持 float 路径——
  查表只是推理侧对"归一化+量化"的合并实现，数值等价（±1 LSB 取整差）
- **三后端口径**：`predict.py`（PyTorch，float 输入不量化）、`onnx_infer.py`（ORT，float）
  不受影响；查表仅作用于 `hailo_infer.py` 的 uint8 量化输入路径
- **帧输入判定不变**：`_normalize_frames`（量化网格负零偏判定）决定走
  归一化查表还是直通查表，逻辑与现状一致
- 旧 5 输入 / 旧版单图像 HEF 布局同样适用（按各自 quant_info 建表）

## 5. 实施步骤

| 步骤 | 内容 | 产出 |
|---|---|---|
| 1 | `hailo_infer.py`：加载时构建 `_frame_luts`（按通道 × 256，归一化/直通两种） | 查表构建 + 单测（256 表项与 float 路径逐项比对 ≤1 LSB） |
| 2 | `_frame_float` / `_bind_frames` 改为查表路径（ndarray 直通、PIL 仅 resize 时用） | 推理链路改造 + 单测（与现 float 路径输出逐帧比对） |
| 3 | NPU 实测：新 640×480 数据集 100 次均值 + 分段计时 | 基准对比（52.4 → ~20ms） |
| 4 | `_npu_test.py` 4 角 + HEF vs torch 对比复验 | 量化误差不回归（≤1°） |
| 5 | 全套单测（TestHailoPredictor 等） | 188 tests OK |

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 查表取整与 float 路径存在 ±1 LSB 差异 | 数值等价性单测：表项逐点比对，差异仅取整边界；NPU 输出 vs torch 误差预算不变（≤1°） |
| 非 256 深度的特殊量化网格 | 表仅适用于 8-bit 输入（HEF 帧输入恒 uint8，`FormatType.UINT8` 分支已断言） |
| 未来输入尺寸变更 | 查表与尺寸无关（逐像素映射）；resize 仍需走 PIL，仅影响附带优化 2 |

## 7. 决策记录

- **查表法 vs 直接 float 合并**：`x*a+b` 一次乘加也能省一步，但仍是 92 万像素×3 通道的
  float 运算（Pi5 上约 10-15ms）；查表把该运算降为内存索引（预计 1-3ms），且同时覆盖直通路径
- **训练侧不改**：训练用 float 归一化（增强/数值精度），推理侧查表——两侧口径在
  "归一化值"层面一致，量化差异由部署链固有预算吸收（HEF vs torch 0.5° 实测余量充足）
