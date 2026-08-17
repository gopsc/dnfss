# actpred Hailo 推理预处理优化：cv2.LUT 查表合并归一化与量化（已实施）

> 文档类型：开发方案（已实施）
> 模块：actpred（部署链 hailo_infer.py）
> 作者：自动生成
> 日期：2026-08-17（原型实测 + 集成实施）
> 前置：`actpred_inference_speed_benchmark_20260813.md`（速度基准）、
> 2026-08-17 新 640×480 数据集重测（本方案 §2 数据）

> 📌 **版本历程**
> 1. 初版：numpy 查表法为核心，预期端到端 18-20ms
> 2. 原型实测（§3.4）：**numpy 高级索引查表在 Pi5 上无加速**（fancy
>    indexing 的 gather 开销与逐元素 float 运算相当），预期不成立；
>    合并系数乘加仅 -5ms；**cv2.LUT 实测 15.8× 加速**（§3.4）
> 3. **已实施（§5）**：`hailo_infer.py` 集成 cv2.LUT 快路径（可选依赖，
>    无 cv2 自动回退 float 路径），端到端 52.4 → **16.0 ms**，输出与
>    float 路径逐点一致（max_diff = 0）

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
仍有约 36ms 的优化空间——但空间比初版估计小（见 §3.4 实测）。

## 2. 瓶颈分析（100 次均值分段计时）

| 子步骤 | 耗时 | 说明 |
|---|---|---|
| `_frame_float`（3 帧） | **27.2 ms** | PIL fromarray/convert + resize（同尺寸 no-op 仍有开销）+ `(x/255-mean)/std` float 运算（92 万像素×3 通道） |
| `create_bindings` + 帧量化绑定 | **9.1 ms** | `_bind_frames`：float 归一化帧 → `round(x/q.scale+q.zp)` 再量化回 uint8 |
| history/pred 绑定 | 0.07 ms | 可忽略 |

**关键冗余**：当前流程先做 float 归一化（`_frame_float`），随后又量化回 uint8（`_bind_frames`）——
中间 float 帧是纯中间态，归一化与量化可以**合并为一步**。

## 3. 优化方案

### 3.1 主方案：合并系数乘加（纯 numpy，无新依赖）

HEF 帧输入是 uint8 量化值：`q = round(((x/255 - mean)/std) / q_scale + q_zp)`。
把 4 步运算合并为**一次乘加**（系数预计算，按通道）：

```
q = round(x * A[c] + B[c])
A[c] = 1 / (255 * std[c] * q_scale)
B[c] = -mean[c] / (std[c] * q_scale) + q_zp
```

- 现流程 4 次全数组运算（`/255`、`-mean`、`/std`、`/q_scale+q_zp`）→ 1 次乘加
- 数值完全等价（实测 max_diff = 0 LSB，见 §3.4）
- 直通模式（`_normalize_frames=False`）同理：`A=1/q_scale, B=q_zp`

### 3.2 附带优化（与主方案正交）

1. **免 PIL 路径**：输入已是 np.ndarray 时直接运算，跳过 `Image.fromarray/convert`
2. **resize no-op 跳过**：`image.size == (W,H)` 时不调用 `resize()`
3. **缓冲复用**：`np.multiply/np.add` 带 `out=` 参数，减少临时数组分配

### 3.3 已实施快路径：cv2.LUT（可选依赖，无 cv2 自动回退）

`cv2.LUT(img, lut)` 是 C 实现的 uint8→uint8 查表，单次遍历，无 float 中间态——
把"归一化+量化"合并为一步。集成要点（`hailo_infer.py`）：

- 惰性导入 cv2（`HAS_CV2`）；**无 cv2 或非逐帧输入布局（ncfhw/nhwc_fused）自动回退 float 路径**
- 加载时按每帧 `quant_info` 构建 256 项表（归一化/直通两种，`_normalize_frames` 判定）
- **OpenCV 5.0 的 cv2.LUT 只接受单通道表**（(256,3) 会断言失败），逐通道应用后 `cv2.merge`
- 输入尺寸与模型不一致时先 PIL resize（仅此情况走 PIL）
- 依赖安装：`opencv-python-headless`（5.0.0，清华镜像；官方源过慢）
  - 注：本方案实测后发现**合并系数乘加（§3.1）未实施**——cv2.LUT 收益已远超，
    避免冗余代码；§3.1 保留作为无 cv2 环境下的潜在备选

### 3.4 原型实测评估（2026-08-17，100-200 次均值，640×480×3 帧）

| 方案 | 3 帧耗时 | 对比现 float 28.5ms | 数值差异 |
|---|---|---|---|
| 现 float 路径 | 28.5 ms | 基线 | — |
| numpy 高级索引查表 | 26.6 ms | **无加速（1.0x）** | 0 LSB |
| 合并系数乘加 | 23.2 ms | 快 19%（-5ms） | 0 LSB |
| **cv2.LUT（逐通道）** | **1.68 ms** | **快 15.8×** | **0 LSB** |

**结论**：
- numpy fancy indexing 的 gather 开销与逐元素 float 运算相当，查表法**无收益**
- 纯 numpy 可达的最佳是合并乘加（-5ms），受限于 92 万像素 float32 数组的内存吞吐
- cv2.LUT 的 C 查表把帧预处理降一个数量级，且数值完全等价（0 LSB）

### 3.5 实测收益（已实施，集成后 NPU 实测）

| 指标 | 优化前 | 优化后（cv2.LUT） |
|---|---|---|
| 帧预处理（3 帧） | 26.5 ms | **3.0 ms（分段实测含绑定）** |
| 端到端 predict | 52.4 ms | **16.1 ms（3.3×）**（200 次均值，见测试报告） |
| 输出一致性 | — | 与 float 路径 max_diff = **0.0000**（7 类输入 + 8 seed 随机） |
| HEF vs torch | 0.50° | 0.30°（4 角复验 PASS） |
| vs PyTorch CPU | 2.1× | **6.7×**（16.1 vs 107.9 ms） |

闭环 10Hz（100ms 预算）占用率 52% → **16%**，20Hz 32%，30Hz（33ms）48%。

> 完整验收数据见 `actpred_cv2_lut_benchmark_report_20260817.md`（测试报告）。

## 4. 一致性约束（关键）

- **训练侧不动**：`dataset.py` 的 `Resize+ToTensor+Normalize` 保持 float 路径——
  合并乘加只是推理侧对"归一化+量化"的合并实现，数值等价（实测 0 LSB）
- **三后端口径**：`predict.py`（PyTorch，float 输入不量化）、`onnx_infer.py`（ORT，float）
  不受影响；优化仅作用于 `hailo_infer.py` 的 uint8 量化输入路径
- **帧输入判定不变**：`_normalize_frames`（量化网格负零偏判定）决定走
  归一化还是直通，逻辑与现状一致
- 旧 5 输入 / 旧版单图像 HEF 布局同样适用（按各自 quant_info 算系数）

## 5. 实施步骤（已完成 ✅）

| 步骤 | 内容 | 状态 |
|---|---|---|
| 1 | `hailo_infer.py`：加载时按每帧 quant_info 构建 256 项 LUT（归一化/直通两种） | ✅ `_build_frame_luts` |
| 2 | `predict` 的 nhwc_frames 分支走 cv2.LUT 快路径（尺寸不匹配时 PIL resize）；无 cv2 回退 float | ✅ `_frame_lut_quant` + `HAS_CV2` |
| 3 | NPU 实测：640×480 数据集 100 次均值 | ✅ 16.0 ms（52.4 → 16.0） |
| 4 | `_npu_test.py` 4 角 + HEF vs torch 对比复验 | ✅ PASS，0.30°（≤1° 预算） |
| 5 | 全套单测（TestHailoPredictor 等） | ✅ 188 tests OK |
| 6 | 依赖安装：opencv-python-headless 5.0.0（清华镜像） | ✅ venv 已装 |

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 查表取整与 float 路径存在差异 | 实测 0 LSB（表项 float32 与现路径同精度）；NPU 输出 vs float 路径 max_diff=0，vs torch 0.30° |
| 无 cv2 环境 | 惰性导入 + 自动回退 float 路径，行为与优化前完全一致 |
| OpenCV 5.0 多通道 LUT 断言失败 | 逐通道单表 + `cv2.merge`（实测无性能损失） |
| 非 nhwc_frames 布局（旧 HEF） | 仅该布局启用 cv2 路径，其余走原 float 路径 |
| 未来输入尺寸变更 | LUT 与尺寸无关（逐像素映射）；尺寸不一致时才走 PIL resize |

## 7. 决策记录

- **查表法（初版核心）实测证伪**：numpy 高级索引 gather 在 Pi5 上与逐元素 float
  运算同速（26.6 vs 28.5ms），"查表降为内存索引即快"的假设不成立——教训：
  性能方案须原型实测，不能凭直觉估计
- **最终方案选 cv2.LUT**：C 实现查表把帧预处理从 26.5ms 降到 1.7ms（15.8×），
  端到端 52.4 → 16.0ms；数值 0 LSB 等价。合并系数乘加（-5ms）收益被碾压，
  不再实施（§3.1 保留为无 cv2 环境的备选）
- **依赖取舍**：opencv-python-headless 作为可选依赖（惰性导入 + 自动回退），
  有 cv2 环境收益 3.3×，无 cv2 环境行为不变——收益远大于依赖成本
- **训练侧不改**：训练用 float 归一化（增强/数值精度），推理侧查表实现——两侧
  口径在"归一化值"层面一致，量化差异由部署链固有预算吸收（HEF vs torch 0.30° 余量充足）
