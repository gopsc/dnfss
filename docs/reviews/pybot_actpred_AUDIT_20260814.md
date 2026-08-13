# pybot & actpred 闭环功能审计报告（含 P1-2 帧源语义研究）

> 审查日期：2026-08-14 (Asia/Shanghai)
> 审查人：AI Assistant（SOLO Coder）
> 审查范围：`pybot/`（机器人运行时）与 `actpred/`（动作预测）两模块，重点是当日新增的进程内实时预测闭环（`run_loop()` / `--loop`）
> 代码基线：pybot 新增 `run_loop`/`_execute_action_vec`/`--loop` CLI；actpred 为部署链稳定版
>
> **审计背景**：当日实现进程内闭环后做专项审计。pybot 此前已有 bot4cj 审计 15 项（S01~S08/Q01~Q07）
> 全部修复的基线；actpred 已有 `actpred_CODE_AUDIT_20260808.md`（27 项已修复）与
> `actpred_CODE_AUDIT_20260812.md`（架构重写后首轮）。本报告**仅记录新发现问题**，
> 焦点是闭环路径与两模块接口。

---

## 一、审计结论摘要

| 级别 | pybot | actpred | 说明 |
|------|-------|---------|------|
| P0（崩溃/数据损坏） | 0 | 0 | 无 |
| P1（功能风险） | 2 | 2 | 见 §二、§三 |
| P2（代码质量） | 3 | 3 | 见 §四 |

**无 P0**。两模块既有防护（pybot Mock 总线逐字节等价、actpred 部署链层层数值验证）覆盖了大部分高危路径。

最高优先：**P1-2 闭环帧源语义**——闭环的相机读取路径与 camera 守护线程完全解耦，导致 `framerate`/`capacity` 配置在闭环中失效，且闭环周期被同步曝光锁死在 ~33ms+。本报告 §五 给出专项研究与修复方案。

---

## 二、pybot P1 发现

### P1-1 闭环节拍在长执行下静默失效

**位置**：[pybot/robot.py:245-258](../../pybot/pybot/robot.py#L245-L258)

```python
remain = dt - (time.monotonic() - t0)
if remain > 0:
    time.sleep(remain)
```

**问题**：当某步"推理+执行"耗时超过 `1/hz`（NPU 抖动、首帧预热、系统调度抢占），`remain <= 0` 则不 sleep。连续多步超时会让实际控制频率**静默漂移**低于设定 `hz`，无任何累积补偿、无告警。闭环是硬实时假设，部署者无法察觉节拍已失守。

**影响**：`--hz 50` 设定下若实际只能跑 30Hz，动作节奏变慢但无人知晓，可能导致预测模型的"动作→结果"时序与训练分布失配。

**修复建议**：
- 超时步 `log.warning("loop overrun: step took %.1fms > %.1fms", elapsed*1000, dt*1000)`；
- 可选：统计 overrun 比例，超过阈值（如 10%）时提示降低 `hz`；
- 不做累积补偿（追赶会引入动作抖动，比降频更危险）。

### P1-2 闭环帧源与 camera 守护线程解耦（详见 §五 专项研究）

**位置**：[pybot/robot.py:359-371](../../pybot/pybot/robot.py#L359-L371) `_grab_frame` + [pybot/robot.py:247](../../pybot/pybot/robot.py#L247)

**问题**：闭环 `_grab_frame` 走 `camera.capture()`（同步曝光），而非 camera 守护线程维护的有界队列 `latest()`。后果：
1. `framerate`/`capacity` 配置在闭环中**完全不生效**（守护线程空转、队列白填）；
2. 闭环每步同步阻塞等相机曝光（30fps ≈ 33ms），`hz` 上限被锁死；
3. `hz` 与相机 `framerate` 两个参数实际耦合却未声明。

**修复建议**：闭环改从 `camera.snapshot()` 取最近 F 帧。详见 §五。

---

## 三、actpred P1 发现

### P1-3 mt_parser 对 UTF-8 BOM 与首块表头的边界

**位置**：[actpred/mt_parser.py:33-37](../../actpred/mt_parser.py#L33-L37)

**问题**：`read_text(encoding="utf-8")` 对 UTF-8 with BOM 文件，首字符是 `﻿`。`re.search(r"@Action\s+")` 仍能匹配（BOM 在 `@Action` 前不干扰定位），但**若 `@Action` 恰为文件第一个块**，`start_m.end()` 之后、`split("\n")` 之前的表头首列名可能粘附 BOM（如 `﻿s1.A`）。下游用表头列名做 `.mt` 列对齐时可能失配。

**影响**：低概率（BOM 文件少见，且 `@Action` 常非首块），但 pybot 的 FSM 解析器对 BOM 的处理需与 actpred 对齐验证，否则两侧对同一文件的列名解析可能不一致——这正是两侧"语义锚点"测试未覆盖的盲区。

**修复建议**：`read_text(encoding="utf-8-sig")` 显式剥离 BOM；在 `test_actpred.py::TestMtParser` 补一个 BOM 文件用例，并在 pybot `test_parser.py` 补对应锚点用例。

### P1-4 DataRecorder 缺 output_dir 入口校验

**位置**：[actpred/dataset.py:136-156](../../actpred/dataset.py#L136-L156)

**问题**：`self.output_dir = Path(output_dir)` 后直接 `glob` + `images_dir.mkdir(parents=True)`。父目录靠 `parents=True` 隐式创建，正常路径 OK。但若 `output_dir` 是**已存在的文件**（非目录），`glob` 抛 `NotADirectoryError` 或 `mkdir` 抛出不含路径上下文的底层错误，排查困难。对比 pybot `EpisodeRecorder.__init__` 有显式 `output.mkdir(parents=True, exist_ok=True)` 先行（[pybot/recorder.py:36](../../pybot/pybot/recorder.py#L36)）。

**修复建议**：`__init__` 开头加 `self.output_dir.mkdir(parents=True, exist_ok=True)`，并对 `output_dir` 已存在但非目录的情况抛出含路径的清晰错误。

---

## 四、P2 代码质量发现（汇总）

### pybot

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| P2-a | [main.py:137](../../pybot/pybot/main.py#L137) | `_load_predictor` 用 `endswith(".hef")` 区分后端，大小写敏感且无 ONNX 分支，误传 `.HEF`/`.onnx` 会走 PyTorch 后端抛出晦涩错误 | 按扩展名白名单（`.hef`/`.onnx`/`.pt`）显式分发，或加 `--backend` 参数 |
| P2-b | [robot.py:281](../../pybot/pybot/robot.py#L281) 等 3 处 | `numpy` 在闭环路径 3 个函数各自 `import`；闭环本就依赖 actpred（必装 numpy） | 闭环路径顶部统一 import；脚本模式保留函数级 import 以守零依赖承诺 |
| P2-c | [tests/test_loop.py](../../pybot/tests/test_loop.py) | 未覆盖闭环 + recorder 集成、predictor 抛异常时的资源清理 | 补 `run_loop`+recorder 落盘验证、predictor 异常时 `with robot` 清理验证 |

### actpred

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| P2-d | [dataset.py:32-36](../../actpred/dataset.py#L32-L36) | 图像 `Resize((H,W))` 不保持宽高比，16:9 相机配 4:3 尺寸会畸变；训练/推理一致故对精度无害，但与真实几何脱节 | 文档注明取舍；换相机/分辨率时需重评估 |
| P2-e | [train.py:135](../../actpred/train.py#L135) | resume 时改 `--epochs` 会按新 T_max 重算 lr，用户可能误以为冻结原调度 | 文档注明 resume 改 epochs 的语义 |
| P2-f | [export_deploy.py:31-81](../../actpred/export_deploy.py#L31-L81) | `ManualAttnBlock` 从 `in_proj_weight` 切 Q/K/V 依赖 PyTorch 内部布局约定（非 API 承诺）；已有导出时数值对比兜底 | 风险已缓解；保留导出数值验证作为强制门槛 |

---

## 五、P1-2 专项研究：闭环帧源语义

### 5.1 两条帧路径的真实差异

camera 提供两种帧访问（[hd/camera.py](../../pybot/pybot/hd/camera.py)）：

| 路径 | 实现 | 延迟特性 | 帧新旧 |
|------|------|---------|--------|
| `capture()` (L72-76) | 同步调 `picam.capture_array()` | **阻塞等一帧曝光**（30fps≈33ms） | "调用之后"的新帧 |
| `latest()` (L67-70) | 读守护线程有界队列队尾 | **无阻塞** | 最多落后一个抓帧周期 |

守护线程（`start_daemon`/`_loop`）按 `framerate` 持续抓帧填入 `deque(maxlen=capacity)`，满丢最旧。

### 5.2 闭环当前行为的问题

`run_loop` 每步 `frames.append(self._grab_frame(camera_var))`（L247），而 `_grab_frame`（L359-371）走 `camera.capture()`：

```
闭环步 → capture() → 同步阻塞 ~33ms（30fps 曝光）→ 拿到新帧
   ↓
camera 守护线程：空转（抓的帧填入队列，但无人消费）
```

**量化后果**（以相机 30fps、闭环目标 50Hz 为例）：
- 每步 `capture()` 阻塞 ~33ms → 闭环实际周期 ≥ 33ms → **实际 hz ≤ 30**，设定 50 完全达不到；
- `framerate=30`、`capacity=4` 的配置在闭环里无任何作用（守护线程抓的帧全部过期丢弃）；
- `hz` 与 `framerate` 必须手工对齐（hz ≤ framerate），但代码无任何校验或声明。

### 5.3 修复方案：闭环改走后台队列

闭环应消费守护线程的队列，而非同步抓帧。核心改动在 `run_loop` 取帧处：

```python
# 改进前：同步曝光，阻塞 ~33ms
frames.append(self._grab_frame(camera_var))

# 改进后：从守护线程队列取最近 F 帧，无阻塞
snap = camera_var.camera.snapshot()          # [(ts, frame), ...] 旧→新
recent = snap[-F:]                            # 取最近 F 帧
if len(recent) < F:                           # 队列未填满：重复最旧帧兜底
    recent = [snap[0]] * (F - len(recent)) + recent if snap else [self._grab_frame(camera_var)] * F
frames.clear(); frames.extend(f for _, f in recent)
```

**收益**：
- 闭环不再同步阻塞曝光，周期由 `hz` 主导（受限于推理+执行，~12ms）；
- `framerate`/`capacity` 配置真正生效：相机高频抓帧，闭环消费最新 F 帧，**帧延迟从 33ms 降到 1/framerate**；
- `hz` 与 `framerate` 解耦（hz 控制节奏、framerate 控制帧新鲜度），但需文档建议 `framerate ≥ 2×hz`。

**代价与对策**：
- 帧可能略旧（最多 1/framerate）：对 50Hz 控制，33ms 旧帧可接受（舵机机械惯性 ~100ms 量级）；
- `snapshot()` 返回带时间戳，可顺带校验帧新鲜度（队尾 ts 距今 > 阈值时 warning）；
- 冷启动 `_loop_cold_start` 同样改从队列取，队列空时回退 `_grab_frame` 合成帧。

### 5.4 与录制路径的一致性

脚本模式 `run()` 的录制（L180/190）也用 `_grab_frame`。若闭环改走队列，需同步评估：
- 录制用 `capture()`（同步新帧）反而合理——录制追求"动作↔帧"严格对齐，新帧更准确；
- 闭环用队列（最新可得帧）合理——实时控制追求低延迟，容忍轻微旧帧。

两者语义本可不同，不必强行统一。但应在文档明确"录制=同步新帧，闭环=队列最新帧"的取舍。

### 5.5 验证建议

修复后补测试（[tests/test_loop.py](../../pybot/tests/test_loop.py)）：
- 注入 FakeGrabber 的 Camera，验证闭环从队列取最近 F 帧（而非 capture）；
- 队列未满时重复最旧帧兜底；
- 断言闭环周期不再含 capture 阻塞（Mock 计时）。

---

## 六、修复优先级建议

| 优先级 | 项 | 理由 |
|--------|-----|------|
| 1 | P1-2 闭环帧源（§五） | 直接决定 `--loop` 真机延迟特性与 hz/framerate 语义，影响最大 |
| 2 | P1-1 节拍 overrun 告警 | 一行 warning + 统计，低成本高诊断价值 |
| 3 | P1-4 DataRecorder 入口校验 | 一行 mkdir + 校验，与 pybot 对齐 |
| 4 | P1-3 mt_parser BOM | 低概率但涉及两侧语义锚点一致性 |
| 5 | P2 各项 | 文档/测试补强，随下次迭代带入 |

---

*本报告为闭环功能专项审计，修复前建议在树莓派真机验证 P1-2 的实际延迟影响（capture vs 队列两种模式的周期对比）。*
