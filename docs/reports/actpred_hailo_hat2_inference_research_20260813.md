# actpred × 树莓派 AI HAT 2（Hailo-10H）推理调研报告

日期：2026-08-13
设备：Raspberry Pi 5 + Raspberry Pi AI HAT 2（Hailo-10H，8GB RAM，40 TOPS）

## 1. 目标

调研如何利用 AI HAT 2 扩展板资源对 actpred 动作预测模型做推理加速，
验证板载软件栈可用性，并给出部署路径。

## 2. 环境实测结果

### 2.1 已安装的软件栈（apt 包）

| 包 | 版本 | 说明 |
|---|---|---|
| h10-hailort | 5.1.1 | Hailo 运行时库 |
| h10-hailort-pcie-driver | 5.1.1 | PCIe 驱动与固件 |
| hailo-h10-all | 5.1.1 | Hailo-10 元包 |
| hailo-tappas-core | 5.1.0 | TAPPAS 核心 |
| python3-h10-hailort | 5.1.1 | HailoRT Python API（`hailo_platform` 模块） |
| python3-hailo-tappas | 5.1.0 | TAPPAS Python 绑定 |
| rpicam-apps-hailo-postprocess | 1.12.0 | rpicam-apps 的 AI HAT 后处理插件 |
| hailo-models | 1.0.0 | 预装 HEF 模型集 |

> 注意：Python 模块名是 `hailo_platform`，**不是** `hailort`
> （`import hailort` 会 ModuleNotFoundError）。

### 2.2 硬件识别与基准

```bash
hailortcli scan                    # Device: 0001:01:00.0（正常识别）
hailortcli benchmark /usr/share/hailo-models/resnet_v1_50_h10.hef
# → resnet_v1_50: FPS: 307.59（温度均值 47.65°C）
```

### 2.3 预装 HEF 模型

`/usr/share/hailo-models/` 下预装了 `_h8l`/`_h10` 两套 HEF。
本机为 Hailo-10H，应使用 `_h10` 后缀模型：
resnet_v1_50、yolov8m、yolov8m_pose、yolov8s_pose、yolov5n_seg、yolov11m 等。

## 3. Python 推理 API 实测（关键发现）

hailort 5.x 对 Hailo-10 设备只支持**新版 infer API**：

- ❌ 旧版 `VDevice().configure(hef)` / `InferVStreams(hef, vdev)`
  在 Hailo-10 上报 `HAILO_NOT_IMPLEMENTED (error 7)`（实测）
- ✅ 新版 `VDevice().create_infer_model(hef_path)` → `configure()` →
  `create_bindings()` → `run(bindings, timeout)`（实测通过）

实测打通的最小推理流程：

```python
import numpy as np
from hailo_platform import VDevice, HEF

hef_path = "/usr/share/hailo-models/resnet_v1_50_h10.hef"
hef = HEF(hef_path)
in_info = hef.get_input_vstream_infos()[0]     # (224,224,3) uint8
out_info = hef.get_output_vstream_infos()[0]   # (1,1,1000) 量化 uint8

with VDevice() as vdev:
    im = vdev.create_infer_model(hef_path)
    with im.configure() as c:
        b = c.create_bindings()
        b.input(in_info.name).set_buffer(img_uint8_hwc)
        b.output(out_info.name).set_buffer(
            np.zeros(tuple(out_info.shape), dtype=np.uint8))
        c.run([b], timeout=10000)
        q = np.asarray(b.output(out_info.name).get_buffer()).astype(np.float32)
        res = (q - out_info.quant_info.qp_zp) * out_info.quant_info.qp_scale
```

### 踩坑记录

1. `bindings.input(name)` / `bindings.output(name)` 不支持下标访问；
   `get_buffer()` 在未 `set_buffer` 前返回 None，需先按
   `HEF.get_input/output_vstream_infos()` 的 shape/dtype 自建 numpy 缓冲区。
2. 输出缓冲区 dtype/大小必须匹配 HEF 边缘格式（量化 uint8），
   传 float32 会报 `Output buffer size ... different than expected`；
   需手动用 `quant_info.qp_zp/qp_scale` 反量化。
3. `VStreamInfo` 无 `format_type` 属性，shape 与量化参数从
   `get_input/output_vstream_infos()` 获取。
4. 摄像头实时推理走 `rpicam-hello/rpicam-vid --post-process-file ...`
   （rpicam-apps-hailo-postprocess 提供）。
5. `hailo_platform.genai` 提供 LLM / VLM / Speech2Text 接口——
   AI HAT 2（Hailo-10H + 8GB RAM）支持本地大模型，是与
   AI HAT+（Hailo-8L）相比的最大增量。

## 4. actpred 部署路径

Hailo 板**不能直接运行 ONNX/PyTorch**，必须编译为 HEF（含量化）：

```
actpred checkpoint (best_inference.pt)
  → export_onnx.py → model.onnx
  → Hailo DFC（Dataflow Compiler，需 x86 Linux 主机或云端 DFC）
      量化校准（用 data/ 中的帧做 calibration set）
  → model.hef（拷回树莓派）
  → hailo_platform 推理（封装见 actpred/hailo_infer.py）
```

风险与注意点：

- **DFC 不在树莓派本机**（无 hailo/dfc 命令），需在 x86 主机安装或
  使用 Hailo 云端 DFC 服务。
- actpred 是 Transformer 结构（多头自注意力），编译前需确认所用 DFC
  版本对 attention 类算子的支持；遇不支持算子可拆分模型或换 op。
- actpred 仅 ~450K 参数，Pi 5 CPU（4×A76）直接 PyTorch/ONNX Runtime
  推理也在可接受延迟范围；HAT 迁移的主要收益是释放 CPU 给 pybot
  主控循环与图像采集。
- HEF 的输入/输出名与量化参数由 DFC 编译决定，推理封装按
  `get_input/output_vstream_infos()` 动态适配，不硬编码名称。

## 5. 结论与产出

1. AI HAT 2 软件栈完整可用，新 API 推理链路实测打通（resnet50 307 FPS）。
2. actpred HEF 推理封装：`actpred/hailo_infer.py`（`HailoPredictor`），
   接口对齐 `predict.ActionPredictorInference`。
3. 待办：在 x86 主机上用 DFC 将 actpred ONNX 编译为 HEF 并做量化校准，
   随后用 `HailoPredictor` 替换 CPU 推理端做端到端验证。

## 参考

- [Raspberry-Pi-AI-HAT+ 使用教程](https://spotpear.cn/index.php/wiki/Raspberry-Pi-5-AI-HAT-Plus-Hailo-8L-13-26-Tops-PCIe-M.2.html)
- [树莓派5 Raspberry Pi AI HAT+2 官方 Hailo-10H 8GBRAM 40TOPS](https://spotpear.cn/index.php/shop/Raspberry-Pi-5-AI-HAT-Plus-2-Hailo-10H-40-Tops.html)
- [AI HAT+ 文档](https://shumeipai.nxez.com/documentation/accessories/ai-hat-plus.html)
- [Raspberry Pi AI Kit](https://www.waveshare.net/wiki/Raspberry_Pi_AI_Kit)
- [onnx 转为 hef 用树莓派 hailo 使用](https://wenku.csdn.net/answer/1e0n2rmysd)
