# actpred HEF 输出不敏感问题根因分析报告

日期：2026-08-13
设备：Raspberry Pi 5 + AI HAT 2（Hailo-10H）；编译端 x86 + Hailo DFC 5.3
前情：[actpred_hailo_hat2_inference_research_20260813.md](actpred_hailo_hat2_inference_research_20260813.md) §5

## 1. 背景

首版 HEF 在 NPU 上输出对全部 5 个输入不敏感（全 0 / 全 255 / 真实数据
输出完全一致）。修复连通性检查后 x86 重编译，新 HEF（224×224，含
`_optimized.har` 连通性 PASS）拷回树莓派复验，**输出仍对输入不敏感**。
本报告记录复验实验与最终根因定位。

## 2. 复验实验

### 2.1 新 HEF 扩展敏感性测试（`_npu_test.py` + 变体）

对 6 种输入变体逐组测试，与基线输出对比（`max_diff`）：

| 变体 | max_diff | 结论 |
|---|---|---|
| 全零帧 | 0.0000 | 帧路径无效 |
| 全 255 帧 | 0.0000 | 帧路径无效（极端值同样无效） |
| 全零历史 | 0.0000 | 历史路径无效 |
| 全 500 历史 | 0.0000 | 历史路径无效 |
| pred_token=0 | 0.0000 | pred_token 无效 |
| pred_token=±0.09 | 0.0000 | pred_token 无效 |

输出为恒定向量 `[11.814, -1.575, 9.451, 4.725, 11.814, 3.15, ...]`
（uint8 q=`[17,0,14,8,17,6]`，zp=2，scale=0.7876）。

### 2.2 新 HEF vstream 量化参数

| 输入 | 形状 | zp | scale | 判定 |
|---|---|---|---|---|
| input_layer1~3（帧） | (224,224,3) | 114 | 0.0187 | 正常（≈归一化像素范围/255） |
| input_layer4（历史） | (1,5,18) | 0 | 1.961 | 正常（=500/255，与数据范围 0~500 一致） |
| input_layer5（pred_token） | (1,1,128) | 129 | **1965.92** | 退化（数据 ±0.09，合理 scale 应 ~0.0007） |

与 x86 备份 tgz 中旧 HEF（64×64）对比：pred_token scale
`18571.7 → 1965.92`。**scale 随校准数据变化，说明抖动校准确实生效**——
推翻了"抖动校准未生效"的初始假设。

### 2.3 控制组（排除环境因素）

resnet_v1_18.hef 同 API、同驱动实测：真实图 top1=984 vs 全零图
top1=111，输出随输入变化。**硬件、驱动 5.3.0、hailo_platform 封装
全部正常，问题在 HEF 本身。**

## 3. 根因：BN 统计退化 → 特征量级 ±1e6 → 量化压死全部信号

### 3.1 证据链

1. `pretrain_best_inference.pt` 中全部 4 个 BatchNorm 的 `running_var`
   均为 `3.7e-4`（每通道逐位相同）。预训练用全零图像占位，BN 从未
   见到真实数据，running_var 经 ~75 次零方差更新指数衰减至此。
2. 推理（eval 模式）下每级 BN 放大 `1/sqrt(3.7e-4 + 1e-5) ≈ 51` 倍，
   4 级串联放大 ~7e6 倍。实测（torch 复算部署版图）：
   - CNN 末级输出：**±1.4e6 ~ 2.5e6**（真实帧 / 全零帧均是）
   - `img_tokens`：±2.2e5 ~ 9.6e5
   - `act_tokens`：±537 ~ 694（历史 0~500 × act_proj 权重，量级正常）
3. torch 浮点推理为何不受影响：pre-LN 架构的 LayerNorm 在注意力前
   归一化 token 幅度，±1e6 的残差流在 float32 下不损失精度。但 uint8
   量化无法表达这种动态范围。

### 3.2 失效机理（量化视角）

DFC 在 token merge 点（`concat(img, act, pred) + pos_embed` 的
ew_add）按**最大幅度分配共享量化 scale**：

```
merge scale ≈ 250k / 127.5 ≈ 1965.92   ← 与 HEF 边缘实测一致
```

该 scale 下各支路命运：

| 支路 | 幅度 | 量化结果 |
|---|---|---|
| img_tokens | ±235k | 勉强存活（~120 级），但中间激活 ±1e6 超出 uint8 校准范围 → 饱和，任何输入差异被抹平 |
| act_tokens | ±537 | 537/1965.92 ≈ 0.27 → 全部量化到 zp（**信号归零**） |
| pred_token | ±0.09 | 全部量化到 zp（**信号归零**） |
| pos_embed | ±0.06 | 全部量化到 zp（**信号归零**） |

图像路径经 CNN 内各级退化激活的饱和 + 其余支路归零，最终网络退化为
对任何输入输出同一常量——与 2.1 的实测完全吻合。

### 3.3 为什么连通性检查通过仍然失败

`dfc_compile.py` 的检查验证的是**图拓扑**（输入层 BFS 可达输出层），
拓扑确实连通。问题是**数值量级**：量化把信号压成 0，等价于数值上的
断连。两者不矛盾，但都必须在编译前拦截。

## 4. 结论

- 当前 pretrain checkpoint 的图像支路数学上**无法 uint8 量化**
  （特征 ±1e6 远超表达范围），无论重编译多少次都不可用。
- 必须先用真实/随机图像数据微调，恢复 BN 统计（特征回到 O(1)），
  再重走导出→编译链路。
- pred_token 抖动校准本身没有 bug：merge 点 scale 由 ±235k 的图像
  token 主导，pred_token 无论怎么抖都会被压死。

## 5. 修复与后续

### 5.1 已落地：导出前量级前置检查

`actpred/export_deploy.py` 新增 `check_token_ranges()`：导出前用随机
噪声帧 + 0~500 均匀历史实测 token 量级，`|img| > 100` 直接中止导出
并提示微调；`act/img` 比例 > 32 时警告 merge 会粗量化图像路径。

实测：退化 checkpoint `|img|=2.46e5` → 正确拦截；模拟健康 BN
（var=1）`|img|=0.117` → 放行。全量单测 76/76 通过。

### 5.2 建议路径

1. x86 微调：随机图像 + 混入真实帧（本机 6 episode × 30 帧可拷贝
   过去），动作目标继续用 .mt 动力学；微调后 BN 统计恢复。
2. 重走链路：`export_deploy.py`（自带量级检查）→ `gen_calibration.py
   --deploy` → `dfc_compile.py`（连通性检查）→ 拷回 HEF。
3. `_npu_test.py` 4 角敏感性复验作为验收门槛。

### 5.3 遗留风险

- 健康场景下 `act_tokens`（±700）仍远大于 `img_tokens`（O(1)）：
  merge 共享 scale 会把图像路径粗量化到 ~2 级。后续可在部署图把
  `action_history` 输入除以 255 并将 `act_w` 放大 255 倍（线性等价，
  数学不变），把 act_tokens 压到 O(1) 量级。
- 随机图像微调后 BN 统计匹配噪声分布，部署端真实摄像头帧的统计
  略有差异，特征量级可能偏大——导出时的新检查会兜底拦截，混入
  真实帧可进一步降低风险。

### 5.4 验证链（防再犯）

| 阶段 | 检查 | 脚本 |
|---|---|---|
| 导出时 | token 量级 | `export_deploy.py` `check_token_ranges` |
| 编译前 | ONNX 数值正确性 | `_onnx_validate.py` |
| 编译时 | 输入连通性 | `dfc_compile.py` `check_input_connectivity` |
| 部署后 | 4 角输入敏感性 | `_npu_test.py` |
