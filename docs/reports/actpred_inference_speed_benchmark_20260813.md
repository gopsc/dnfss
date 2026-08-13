# actpred 推理速度基准测试报告（Pi 5 CPU vs Hailo-10 NPU）

日期：2026-08-13
设备：Raspberry Pi 5（4×A76）+ AI HAT 2（Hailo-10H）
模型：actpred（~450K 参数，frame_len=3，history_len=5，action_dim=18）

> ⚠️ **基线分辨率说明（2026-08-13 更新）**
>
> 本报告 §1~§4 的全部数据均在 **640×480** 输入分辨率下测得。
> 提交 `91b3004`（2026-08-12）曾将模块默认输入分辨率改为
> **1920×1080**（与 pybot 相机采集分辨率对齐）；2026-08-13 实机测速
> 发现 1080p 采集的预处理缩放（约 45ms）主导端到端延迟（§6.1），
> 默认分辨率**改回 640×480**（`config.py` 默认
> `image_width/image_height`），§1~§4 数据重新适用。
> 1920×1080 的 x86 复测数据见 §5，仅作分辨率影响趋势参考。

## 1. 测试方法

- **PyTorch CPU**：`ActionPredictorInference(checkpoints/best_inference.pt)`，
  输入 3 帧 640×480 随机图 + 全零动作历史，预热 3 次后取 20 次均值（含 PIL 预处理）
- **ONNX Runtime CPU**：`export_onnx.py` 导出后 ORT 加载，纯模型推理，
  预热 3 次后取 50 次均值
- **Hailo-10 NPU**：板载 `resnet_v1_50_h10.hef`（25M 参数，比 actpred 大 ~55 倍），
  新版 `create_infer_model` API 串行单帧，预热 10 次后取 200 次均值；
  另有 `hailortcli benchmark` pipelined 吞吐

## 2. 结果

| 方案 | 延迟 | 吞吐 | 备注 |
|---|---|---|---|
| PyTorch CPU | 28.8 ms | 34.8 FPS | 含 PIL 图像预处理 |
| ONNX Runtime CPU | 5.9 ms | 168.7 FPS | 纯推理，float32 输入 |
| Hailo-10 NPU（resnet50，串行单帧） | 4.29 ms | 233 FPS | 含 Python/PCIe 开销 |
| Hailo-10 NPU（resnet50，pipelined） | — | 307 FPS | hailortcli benchmark |

## 3. 解读

1. resnet50 参数量约为 actpred 的 55 倍，NPU 上 actpred HEF 推理预计
   **亚毫秒级**，NPU 不会成为瓶颈；串行单帧 4.29ms 中 Python/PCIe
   开销占比高，闭环可用 `run_async` 流水线进一步压低。
2. 不迁移 NPU 也完全够用：ONNX Runtime 比 PyTorch 快近 5 倍（169 FPS），
   是短期最优 CPU 方案。
3. HAT 的真实价值在于**把推理从 CPU 卸载**，让 4×A76 专注 pybot
   主控循环与图像采集，而非绝对延迟。

## 4. 部署建议（按优先级）

1. **立即可用**：给 actpred 加 ONNX Runtime 后端（改动小，收益 ~5 倍）
2. **有 x86 环境后**：DFC 编译 HEF，走 `actpred/hailo_infer.py`
   的 `HailoPredictor` 端到端验证
3. DFC 编译环境的说明：官方仅提供 x86_64 Linux wheel
   （`hailo_dataflow_compiler-*-linux_x86_64.whl`，无 arm64 构建），
   但 HEF 产物与主机架构无关，x86 上编译一次即可在树莓派长期使用；
   无本机 x86 时可用云服务器临时实例或 QEMU 模拟（后者极慢，不推荐）。
   编译环境调研详见
   [actpred_hailo_hat2_inference_research_20260813.md](actpred_hailo_hat2_inference_research_20260813.md)

## 5. 1920×1080 复测（2026-08-12，x86 参考机）

分辨率重构（`91b3004`）后在新默认分辨率下补测 CPU 部分
（Hailo 板载待有硬件环境后补测）：

| 方案 | 输入分辨率 | 延迟 | 备注 |
|---|---|---|---|
| PyTorch CPU | 1920×1080 | 701.6 ms | 含 PIL 预处理，预热 2 次取 5 次均值 |
| ONNX Runtime CPU | 1920×1080 | 326.3 ms | 纯推理，预热 2 次取 10 次均值 |

测试机：Intel i3-3240（2 核 4 线程，2012 年款），7.7 GB 内存；
与 Pi 5（4×A76）**不可直接横向对比绝对值**，仅用于观察分辨率影响趋势。

**结论**：

1. ONNX 相对 PyTorch 仍快约 2.2 倍，§4 的部署优先级方向不变；
2. 即便在 x86 桌面 CPU 上，1920×1080 单帧推理也需数百毫秒，
   **远不能满足实时闭环**（原 640×480 报告推算 Pi 5 上将超过 1 秒）。
   1920×1080 全分辨率闭环推理必须依赖：
   - Hailo HAT 2 NPU（uint8 量化 + 硬件加速，预期可压到几十毫秒级），或
   - 训练/推理时降低实际分辨率（如 960×540 或 640×360），或
   - 裁剪输入（ROI）后再推理；
3. 建议在 Pi 5 + HAT 2 环境补测 1920×1080（ONNX CPU / HEF NPU），
   并据此确定量产部署分辨率。

## 6. 224×224 复测（2026-08-13，Pi 5，当前 NPU 部署分辨率）

NPU 部署轮次以 224×224 的 `pretrain_best_inference.pt` 为准（HEF 编译
目标分辨率），据此补测 Pi 5 CPU 基线（3 帧 + 全零历史；episode 原始
图像即 224×224，无重采样开销）：

| 方案 | 输入分辨率 | 延迟 | 备注 |
|---|---|---|---|
| PyTorch CPU | 224×224 | 21.0 ms | 含预处理，预热 3 次取 20 次均值 |
| ONNX Runtime CPU | 224×224 | 11.4 ms | 含预处理，预热 3 次取 50 次均值 |
| ONNX Runtime CPU | 224×224 | 10.7 ms | 纯 `session.run`（预缩放输入） |
| Hailo-10 NPU（actpred HEF，首版） | 64×64 | 6.3 ms | 含预处理 + 量化/反量化，驱动 5.3.0 |

### 6.1 归一化轮次 HEF 复测（2026-08-13 同日，输入连通性验证通过后）

同一轮次对通过 `_npu_test.py` 验收的归一化轮次 HEF
（224×224，`hailo_infer.py` 帧归一化判定修复后）实测，预热 5 次取
50 次均值（纯 NPU run 取 200 次）：

| 方案 | 输入分辨率 | 延迟 | 备注 |
|---|---|---|---|
| Hailo-10 NPU 端到端 predict | 224×224 | 10.5 ms | 含预处理 + 量化/反量化 |
| Hailo-10 NPU 纯 run（绑定后循环） | — | 4.4 ms | Python API/PCIe 开销主导，HEF 计算本身远小于此 |
| Hailo-10 NPU 预处理（3 帧 resize+归一化） | 224×224 | 3.4 ms | PIL resize 为主 |
| PyTorch CPU（同机复测） | 224×224 | 22.4 ms | 含预处理 |
| ONNX Runtime CPU（同机复测） | 224×224 | 11.0 ms | 含预处理，重新导出的当前 checkpoint |

**结论**：

1. 224×224 下 NPU 端到端（10.5ms）与 ONNX Runtime（11.0ms）基本持平，
   纯 NPU run 的 4.4ms 是 Python 绑定/PCIe 同步开销主导（对照 §2
   resnet50 串行单帧同为 4.29ms，而 actpred 计算量仅其 ~1/55）。
   后续可用 `run_async` 流水线把预处理与 NPU 执行重叠。
2. NPU 的价值不在绝对延迟而是**把推理从 CPU 卸载**，让 4×A76 专注
   pybot 主控循环；CPU 侧 ORT 仍是有效的纯软件方案。
3. **1080p 采集是最大瓶颈**：预处理随输入像素量增长，实测
   640×480 输入 11.8ms、960×540 输入 15.7ms、1080p 输入 45.0ms
   （端到端分别 18.6 / 22.8 / 51.7ms）。部署建议摄像头以
   640×480 或更低分辨率采集（或 224×224 中心裁剪 ROI），
   而不是全分辨率后缩放。

## 参考

- [张高兴的 Raspberry Pi AI 开发指南：将自定义模型编译为 Hailo NPU 的 .hef 模型](https://www.cnblogs.com/zhanggaoxing/articles/18597244.html)
- [Hailo Dataflow 编译器安装](https://blog.csdn.net/qq_40843129/article/details/148557199)
- [Hailo-8 系列——Hailo Dataflow Compiler 使用经验分享](https://blog.csdn.net/stoat04/article/details/137569246)
