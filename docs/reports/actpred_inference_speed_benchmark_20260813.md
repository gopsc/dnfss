# actpred 推理速度基准测试报告（Pi 5 CPU vs Hailo-10 NPU）

日期：2026-08-13
设备：Raspberry Pi 5（4×A76）+ AI HAT 2（Hailo-10H）
模型：actpred（~450K 参数，frame_len=3，history_len=5，action_dim=18）

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

## 参考

- [张高兴的 Raspberry Pi AI 开发指南：将自定义模型编译为 Hailo NPU 的 .hef 模型](https://www.cnblogs.com/zhanggaoxing/articles/18597244.html)
- [Hailo Dataflow 编译器安装](https://blog.csdn.net/qq_40843129/article/details/148557199)
- [Hailo-8 系列——Hailo Dataflow Compiler 使用经验分享](https://blog.csdn.net/stoat04/article/details/137569246)
