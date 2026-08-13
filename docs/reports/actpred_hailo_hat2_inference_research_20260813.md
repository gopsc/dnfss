# actpred × 树莓派 AI HAT 2（Hailo-10H）推理调研报告

日期：2026-08-13（同日更新：驱动升级 5.3.0 与 DFC 编译闭环踩坑，见 §5）
设备：Raspberry Pi 5 + Raspberry Pi AI HAT 2（Hailo-10H，8GB RAM，40 TOPS）

## 1. 目标

调研如何利用 AI HAT 2 扩展板资源对 actpred 动作预测模型做推理加速，
验证板载软件栈可用性，并给出部署路径。

## 2. 环境实测结果

### 2.1 软件栈（2026-08-13 已升级 hailort 5.3.0）

| 组件 | 版本 | 说明 |
|---|---|---|
| hailort（运行时 + PCIe 驱动） | 5.3.0 | apt 包 h10-hailort* 5.1.1 → 升级 5.3.0（含固件） |
| 固件 | 5.3.0 | `hailortcli fw-control identify` 确认 |
| Python API | 5.3.0 | actpred venv 内 pip 安装 `hailort-5.3.0-cp313`（模块名 `hailo_platform`） |
| DFC（Dataflow Compiler） | 5.3 | 仅 x86 Linux，运行于 `venv_dfc` |

> 注意：Python 模块名是 `hailo_platform`，**不是** `hailort`
> （`import hailort` 会 ModuleNotFoundError）。

### 2.2 硬件识别与基准

```bash
hailortcli scan                    # Device: 0001:01:00.0（正常识别）
hailortcli benchmark /usr/share/hailo-models/resnet_v1_50_h10.hef
# → resnet_v1_50: FPS: 307.70（驱动 5.3.0 复测，温度均值 46.7°C）
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
4. **vstream 报告顺序不可信**：实测 5 输入 HEF 的报告顺序为
   `input_layer1,5,4,3,2`，按报告顺序绑定会 frame1/frame2 错位；
   必须按 `input_layerN` 数字后缀排序（`hailo_infer.py` 已修复）。
5. 摄像头实时推理走 `rpicam-hello/rpicam-vid --post-process-file ...`
   （rpicam-apps-hailo-postprocess 提供）。
6. `hailo_platform.genai` 提供 LLM / VLM / Speech2Text 接口——
   AI HAT 2（Hailo-10H + 8GB RAM）支持本地大模型，是与
   AI HAT+（Hailo-8L）相比的最大增量。

## 4. actpred 部署路径

Hailo 板**不能直接运行 ONNX/PyTorch**，必须编译为 HEF（含量化）：

```
actpred checkpoint (pretrain_best_inference.pt, 224×224)
  → export_deploy.py（DFC 友好改造：按帧展开 CNN、手工注意力、
    (1,1,S,d) 布局）→ actpred_deploy.onnx + actpred_deploy_pred_token.npy
  → gen_calibration.py --deploy（合成帧 + .mt 动作历史 + pred_token
    抖动校准）→ calibration_deploy.npz
  → dfc_compile.py（parser → optimize → compiler，含输入连通性检查）
  → model.hef（拷回树莓派）
  → hailo_infer.py（HailoPredictor，--pred-token 喂学习值）
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

## 5. DFC 编译与实机验证踩坑（2026-08-13）

### 5.1 首版 HEF：输出对输入完全不敏感

首版 HEF（64×64 checkpoint 产物）在 NPU 上首跑即发现**输出对全部
5 个输入均不敏感**（全 0 / 全 255 / 真实数据输出完全一致，对照
resnet HEF 同 API 验证输入通道有效，排除封装问题）。

### 5.2 驱动升级复测：依旧不敏感

将 hailort 5.1.1 升级到 5.3.0（与编译用 SDK 5.3 对齐）后复测：
设备识别、resnet 基准（307.7 FPS）、actpred HEF 执行（6.3ms/次）
全部正常，但 4 角输入敏感性测试（真实/全零帧 × 真实/全零历史）
输出仍完全一致 → **判定为 DFC 编译侧问题（输入在编译图中被断开/
常量化），驱动版本不是根因**。

### 5.3 排查发现与修复（待重编译验证）

| 发现 | 证据 | 修复 |
|---|---|---|
| pred_token 常量校准量化退化 | HEF 边缘 `zp=123, scale=18571.7`，任何输入量化成 0 | `gen_calibration.py` 改为学习值 ±30% 抖动校准；推理端喂学习值（`export_deploy.py --pred-token-out` + `hailo_infer.py --pred-token`） |
| vstream 报告顺序乱序 | 5 输入 HEF 报告顺序 1,5,4,3,2 | `hailo_infer.py` 按 `input_layerN` 排序绑定 + 乱序单测 |
| 输入断开无任何报错 | 编译全流程通过但 HEF 恒定输出 | `dfc_compile.py` parser/optimize 后自动检查输入层下游连通性，断开即报错 |
| 编译前无数值门槛 | deploy ONNX 数值正确性未独立验证 | `_onnx_validate.py`：输入敏感性 + PyTorch 一致性（本次 PASS，差异 1.5e-5） |

### 5.4 待办

1. x86 主机重编译（`dfc_compile.py`，产物含 `_optimized.har`）；
2. 拷回 HEF + pred_token.npy，跑 `_npu_test.py` 4 角敏感性复验；
3. 若仍不敏感：检查 `_optimized.har` 输入层下游连通性，定位
   optimize 断连的具体算子。

## 6. 结论与产出

1. AI HAT 2 软件栈完整可用，新 API 推理链路实测打通（resnet50 307 FPS）。
2. actpred HEF 编译链路已全流程打通（export_deploy → gen_calibration →
   dfc_compile），但首版 HEF 因 DFC 编译侧输入断开不可用，修复方案
   已落地，待重编译复验。
3. actpred HEF 推理封装：`actpred/hailo_infer.py`（`HailoPredictor`），
   接口对齐 `predict.ActionPredictorInference`，单测含 fake hailo_platform
   的 5 输入与乱序顺序用例。
4. CPU 侧性能基线（224×224，Pi 5）：ONNX Runtime 11.4ms / PyTorch
   21ms，见
   [测速报告](actpred_inference_speed_benchmark_20260813.md) §6。

## 参考

- [Raspberry-Pi-AI-HAT+ 使用教程](https://spotpear.cn/index.php/wiki/Raspberry-Pi-5-AI-HAT-Plus-Hailo-8L-13-26-Tops-PCIe-M.2.html)
- [树莓派5 Raspberry Pi AI HAT+2 官方 Hailo-10H 8GBRAM 40TOPS](https://spotpear.cn/index.php/shop/Raspberry-Pi-5-AI-HAT-Plus-2-Hailo-10H-40-Tops.html)
- [AI HAT+ 文档](https://shumeipai.nxez.com/documentation/accessories/ai-hat-plus.html)
- [Raspberry Pi AI Kit](https://www.waveshare.net/wiki/Raspberry_Pi_AI_Kit)
- [onnx 转为 hef 用树莓派 hailo 使用](https://wenku.csdn.net/answer/1e0n2rmysd)
