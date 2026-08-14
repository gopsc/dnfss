# pybot & actpred 审计报告（第三版 —— v2 修复批次落地后）

> 审查日期：2026-08-14 (Asia/Shanghai)
> 审查人：AI Assistant（SOLO Coder）
> 审查范围：`pybot/`（机器人运行时）与 `actpred/`（动作预测）两模块
> 代码基线：v2 审计修复批次后（commit 85bf68f，工作区干净）
> 测试状态：pybot 88 passed；actpred 148 passed / 18 skipped（Windows 开发机，全绿；
> ONNX 类 18 项因本机无 onnxruntime/onnxscript 跳过，在 Pi 上运行）
>
> 本报告对照 v2 报告逐项核对修复状态，并给出本轮新发现。
>
> **2026-08-14 更新：P0-2（方案 B）、两个新 P1、P2-a 残余、P2-a3 已修复；
> 3 项新 P2（验收脚本 fixed_range / diff 断言 / overrun 节流）与 5 项遗留 P2
> （P2-a1/P2-c 残余/P2-d/P2-e/P2-f/R2-4）亦全部闭环，附回归测试（pybot +2 / actpred +7）。
> P0/P1/P2 全部清零，仅剩 2 项 P3（边缘/环境便利）。**

---

## 一、审计结论摘要

| 级别 | 新发现 | 历史遗留（v2 未修复） | 说明 |
|------|--------|----------------------|------|
| ~~P0（崩溃/数据损坏）~~ | 0 | ~~1~~ **0（P0-2 已按方案 B 修复）** | 见 §三 |
| ~~P1（功能风险）~~ | ~~2~~ **0（均已修复）** | ~~1~~ **0（P2-a 已闭环）** | 见 §四 |
| ~~P2（代码质量）~~ | ~~3~~ **0（均已修复）** | ~~5~~ **0（全部闭环）** | 见 §五/§六 |
| P3（边缘/环境） | 2 | 0 | 见 §五 |

**重点：P0-2 曾随 85bf68f 未修复提交，本轮已按方案 B 闭环；两个新 P1、P2-a 残余与全部 P2 已于本轮回合修复。**

---

## 二、v2 修复项回归核对（13 项）

| # | v2 级别 | 状态 | 验证 |
|---|---------|------|------|
| P0-1 HailoPredictor 缺 normalize_mode | P0 | ✅ 已修复 | [main.py:163-193](../../pybot/pybot/main.py#L163-L193) 从 companion checkpoint 读取并传入 |
| R2-1 NaN val_loss 阻断 best | P1 | ✅ 已修复 | [train.py:270-274](../../actpred/train.py#L270-L274)、[pretrain_mt.py:361-364](../../actpred/pretrain_mt.py#L361-L364) isfinite 守卫，NaN 走 patience 早停 |
| P2-a 后端检测大小写敏感 | P1 | ✅ 已修复 | [main.py:144-146](../../pybot/pybot/main.py#L144-L146) 已 `.lower()`；v3 补齐 `.onnx → OnnxPredictor` 分支（归一化模式经元数据自动识别，zscore 缺 stats 时告警） |
| P1-3 mt_parser BOM | P1 | ✅ 已修复 | [mt_parser.py:33](../../actpred/mt_parser.py#L33) `utf-8-sig` |
| R1-1 pretrain data_dir 覆盖 | P1 | ✅ 已修复 | pretrain_mt.py 不再赋值 `cfg.data.data_dir` |
| R2-7 _run.sh 静默回退 | P1 | ✅ 已修复 | [_run.sh:17-20](../../actpred/_run.sh#L17-L20) 未知子命令报错退出 |
| P2-a2 init-from 误报警告 | P2 | ✅ 已修复 | [config.py:232](../../actpred/config.py#L232) 仅在双方归一化开启时比较 normalize_mode |
| R2-9 predict.py --action-dim 陷阱 | P2 | ✅ 已修复 | predict.py 已移除该参数，统一用 `predictor.action_dim` |
| R1-2 parse_action_history 三处重复 | P2 | ✅ 已修复 | 三推理文件均从 [utils.py](../../actpred/utils.py) 导入 |
| R2-3 空验证集无 best.pt | P2 | ✅ 已修复 | [train.py:309-324](../../actpred/train.py#L309-L324) 保底保存最终权重 |
| P2-c 闭环测试覆盖 | P2 | ✅ 已修复 | test_loop.py 含 recorder 集成 + 异常清理路径用例（test_predictor_exception_still_flushes_recorder） |
| P2-b numpy 三处 lazy import | P2 | ✅ 已修复 | 顶层导入曾破坏零依赖（v3-P1-1），已恢复函数级 lazy import 并加守卫测试 |
| P0-2 normalize_actions 默认值 | P0 | ✅ 已修复 | [README.md:170](../../actpred/README.md#L170) 更正为 `True` 并注明行为变更（方案 B，见 §三） |

---

## 三、P0 遗留

### P0-2 DataConfig normalize_actions 默认值与 README 不一致 —— ✅ 已修复（2026-08-14，方案 B）

**修复**：[README.md:170](../../actpred/README.md#L170) 默认值更正为 `True`，
并注明"2026-08-14 起默认开启——复现旧行为需显式 `--no-normalize-actions`"。
代码（config.py:14 `True`）、测试、训练实践此前均已按 `True` 落地，文档对齐即闭环。

---

## 四、P1 新发现

### v3-P1-1 pybot 零第三方依赖承诺被打破（85bf68f 修 P2-b 的副作用）—— ✅ 已修复（2026-08-14）

**修复**：robot.py 顶层 `import numpy` 移除，7 个 numpy 使用函数（run_loop /
_loop_cold_start / _execute_action_vec / _read_imus / _pad_imu /
_build_action_vec / _grab_frame）恢复函数内 lazy import，并在导入块处
注明约定禁止恢复顶层导入。新增回归测试
`test_loop.py::test_robot_importable_without_numpy`（子进程屏蔽 numpy
验证模块可导入）。纯脚本路径零依赖承诺恢复，README 无需变更。

**原问题**：~~main.py 无条件 `from .robot import RobotStruct`，numpy 成为硬依赖。~~

### v3-P1-2 OnnxPredictor 不支持 predict_delta，ONNX 无训练配置元数据 —— ✅ 已修复（2026-08-14）

**修复**：
- [export_onnx.py:46-63](../../actpred/export_onnx.py#L46-L63) 导出后写入
  `metadata_props`（normalize_mode / predict_delta / imu_dim / action_dim；
  未归一化训练记 `normalize_mode="none"`）；
- [onnx_infer.py:75-87](../../actpred/onnx_infer.py#L75-L87) 构造时从元数据
  自动识别两参数（显式传参优先），[predict():174-176](../../actpred/onnx_infer.py#L174-L176)
  在归一化空间加回 history 末步（与 predict.py/hailo_infer 同语义）；
- CLI `--normalize-mode` 缺省自动识别、新增 `--predict-delta`；
- README 部署注意已更正（含旧版无元数据 ONNX 的回退说明）；
- 新增 TestOnnxMetadata 7 用例（元数据写入/回读/覆盖/残差加回数值），
  本机无 onnxruntime 跳过，随 Pi 环境运行。

**原问题**：~~残差训练模型经 export_onnx.py 导出后，ONNX 推理输出被当作绝对动作——静默错误。~~

---

## 五、P2/P3 新发现

### v3-P2-1 验收脚本链未适配 fixed_range —— ✅ 已修复（2026-08-14）

[_npu_test.py:65-76](../../actpred/_npu_test.py#L65-L76)、[_onnx_validate.py:28-42](../../actpred/_onnx_validate.py#L28-L42)
均改为按 checkpoint config 的 `normalize_mode` 分支：zscore 照旧加载
action_stats.npy；fixed_range 走 action_norm min-max 且免 stats 文件；
_npu_test 另向 HailoPredictor 透传 normalize_mode、torch 参考输出按同
模式反归一化后再对比。

### v3-P2-2 导出一致性校验只打印不拦截 —— ✅ 已修复（2026-08-14）

[export_deploy.py:288-292,303-307](../../actpred/export_deploy.py#L288-L292)
与 [export_onnx.py:82-85](../../actpred/export_onnx.py#L82-L85) 新增
`MAX_TOLERATED_DIFF = 1e-4` 阈值断言，超限 `sys.exit` 拦截（P2-f 的
ManualAttnBlock 布局约定风险一并闭环）。

### v3-P2-3 run_loop overrun 逐步告警日志噪音 + 建议阈值偏离约束 —— ✅ 已修复（2026-08-14）

[robot.py:310-317](../../pybot/pybot/robot.py#L310-L317) 逐步告警节流为
第 1 次 + 每 100 次汇总；[robot.py:324-332](../../pybot/pybot/robot.py#L324-L332)
最终建议仅在 overrun 比例 >10% 时输出（否则 info 级汇总），对齐工程约束。

### v3-P3-1 dfc_compile.py --output 无 .hef 后缀时产物互相覆盖

**位置**：[dfc_compile.py:172-173](../../actpred/dfc_compile.py#L172-L173)

`args.output.replace(".hef", "_optimized.har")` 在无后缀时返回原路径，`shutil.copy(har_opt, ...)` 把 HAR 写到刚复制的 HEF 同路径，静默损坏产物。建议校验 `--output` 必须以 `.hef` 结尾。

### v3-P3-2 pybot Windows 测试依赖命令行 fcntl mock

**位置**：[pybot/hd/i2c.py:14](../../pybot/pybot/hd/i2c.py#L14)

顶层 `import fcntl`（Unix-only），Windows 下 conftest 自身 import i2c 即失败，需命令行注入 `sys.modules['fcntl']` mock 才能跑测试（87 passed 即以此方式验证）。建议在 conftest.py 内置同等 mock（一行，先于 i2c import），消除外部依赖。

---

## 六、v2 遗留 P2（已全部修复，2026-08-14）

| # | 位置 | 问题 | 处理 |
|---|------|------|------|
| P2-a1 | [action_norm.py:55-66](../../actpred/action_norm.py#L55-L66) | ~~`default_column_weights` 对 action_dim>18 的超出列权重恒 0~~ | ✅ 扩展列默认 1.0（新执行器须参与训练），补 20 列断言 |
| P2-a3 | [README.md:172](../../actpred/README.md#L172) | ~~column_weights 始终生效（含不归一化训练）未在文档注明~~ | ✅ 已随 P0-2 一并修复 |
| P2-c 残余 | test_loop.py | ~~predictor 异常清理路径无用例~~ | ✅ 新增 test_predictor_exception_still_flushes_recorder（异常传播 + finally 落盘 1 步） |
| P2-d | [dataset.py:46-49](../../actpred/dataset.py#L46-L49) | ~~Resize 不保持宽高比~~ | ✅ 注明有意为之（训练/推理同口径，变更须同步重训），不改行为 |
| P2-e | [train.py:248-251](../../actpred/train.py#L248-L251) | ~~resume 时 scheduler T_max 恢复旧值~~ | ✅ 语义已在代码注释 + README --resume 行明确 |
| P2-f | export_deploy.py | ~~ManualAttnBlock 依赖 PyTorch in_proj 内部布局~~ | ✅ 随 v3-P2-2 阈值断言闭环（布局失效即拦截退出） |
| R2-4 | [_set.sh:19-28](../../actpred/_set.sh#L19-L28) | ~~CUDA 版 torch 误装风险~~ | ✅ 安装后断言 `torch.version.cuda is None`，误装即拦截并给出修复命令 |

---

## 七、修复优先级

| 优先级 | 项 | 状态 |
|--------|-----|------|
| ~~P0~~ | P0-2 README 与默认值对齐（方案 B） | ✅ 已修复 |
| ~~P1~~ | v3-P1-1 robot.py numpy lazy import | ✅ 已修复（附子进程回归测试） |
| ~~P1~~ | v3-P1-2 ONNX predict_delta + 元数据 | ✅ 已修复（全链路 + 7 用例） |
| ~~P2~~ | v3-P2-1 验收脚本 fixed_range 适配 | ✅ 已修复 |
| ~~P2~~ | v3-P2-2 导出 diff 阈值断言 | ✅ 已修复（P2-f 一并闭环） |
| ~~P2~~ | v3-P2-3 overrun 告警节流 + 10% 阈值 | ✅ 已修复 |
| ~~P2~~ | P2-a1/P2-c 残余/P2-d/P2-e/R2-4 | ✅ 全部闭环（见 §六） |
| P3 | v3-P3-1 dfc_compile --output 后缀校验 / v3-P3-2 conftest 内置 fcntl mock | 未修复（边缘/环境便利） |

---

## 八、本轮复核确认无问题的关键路径

- **训练链**：weighted MSE 量级归一、scheduled sampling detach 截断、空验证集兜底、stats 训练集-only 计算（含 history∪target 并集防零方差爆炸）、文件级划分防泄漏——均正确。
- **推理链三后端**：normalize/denormalize 对称、predict_delta 加回在归一化空间、imu_dim=None 鸭子类型兼容旧 checkpoint、NaN/Inf 输出兜底——均正确。
- **部署链**：HAR 输入连通性双阶段检查、pred_token 抖动校准、按 input_layerN 排序绑定、量化网格 dequant(0) 判定帧归一化、token 量级导出前拦截——均正确。
- **pybot 闭环**：守护线程队列帧源、冷启动零历史、finally 落盘 recorder、执行前 isfinite 守卫、Speaker.delay 剔除防阻塞——均正确。

---

*本报告为 v2 修复批次后的回归+新发现审计。建议优先闭环 P0-2（文档一行）与两个新 P1。*
