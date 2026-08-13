# actpred 推理速度基准测试报告（Pi 5 CPU vs Hailo-10 NPU）

日期：2026-08-13
设备：Raspberry Pi 5（4×A76）+ AI HAT 2（Hailo-10H）
模型：actpred（~450K 参数，frame_len=3，history_len=5，action_dim=18）

> ⚠️ **基线分辨率说明（2026-08-12 补注，N-3 修复）**
>
> 本报告 §1~§4 的全部数据均在 **640×480** 输入分辨率下测得。
> 提交 `91b3004`（2026-08-12）已将模块默认输入分辨率改为
> **1920×1080**（`config.py` 默认 `image_width/image_height`，与 pybot
> 相机采集分辨率对齐）。**推理延迟随输入像素量近似线性增长
> （1920×1080 ≈ 640×480 的 6.75 倍像素），本报告数字不能直接外推到
> 新默认分辨率。** x86 参考机上的 1920×1080 复测数据见 §5；
> Pi 5 / Hailo 板载在新分辨率下的数据待补测。

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

## 参考

- [张高兴的 Raspberry Pi AI 开发指南：将自定义模型编译为 Hailo NPU 的 .hef 模型](https://www.cnblogs.com/zhanggaoxing/articles/18597244.html)
- [Hailo Dataflow 编译器安装](https://blog.csdn.net/qq_40843129/article/details/148557199)
- [Hailo-8 系列——Hailo Dataflow Compiler 使用经验分享](https://blog.csdn.net/stoat04/article/details/137569246)
