# pybot 微动作分解与自监督数据集录制

> 文档类型：特性报告
> 模块：pybot → actpred
> 作者：自动生成
> 日期：2026-08-13

---

## 1. 概述

### 1.1 背景

pybot 执行 `.mt` 动作脚本驱动机器人，actpred 需要图像+动作的 episode 数据进行微调训练。两个模块此前是分离的——actpred 的 `DataRecorder` 可录制 episode，但没有与 pybot 运行时集成的路径。

同时，原始执行模型中每行 `@Action` 的舵机角度是瞬间跳变的：`play()` 写 I2C 寄存器后立即返回，由 `Speaker.play(freq, delay)` 的阻塞 sleep 提供行间时序。舵机在 delay 期间停在目标位置不动，而非平滑过渡。

### 1.2 目标

1. **自监督数据集录制**：pybot 运行 `.mt` 脚本时同步录制摄像头帧 + 动作向量，产出 actpred `ActionDataset` 可直接加载的 `episode_XXXX/` 格式数据集。标签（动作）来自脚本执行本身，无需人工标注。

2. **微动作分解**：将每行带时延的动作分解为 N = delay/10 个 10ms 微步，舵机角度从上一行目标值线性插值到当前行目标值，实现平滑运动控制。

3. **同步帧采集**：每个微步触发一次摄像头同步抓帧，确保帧与动作 1:1 对应，不从后台队列读取可能重复的旧帧。

---

## 2. 自监督数据集录制

### 2.1 数据格式

产出格式与 actpred `dataset.py` 的 `DataRecorder` 完全兼容，但零跨模块依赖：

```
output_dir/
  episode_XXXX/
    images/
      0000.jpg, 0001.jpg, ...     # PIL 可读的 JPG（quality=90）
    actions.npy                     # shape (T, action_dim) float32
```

`ActionDataset`（`actpred/dataset.py:17`）期望的样本结构：帧 `[t-frame_len+1, t]` + 动作历史 `[t-history_len, t)` → 预测动作 `t`。

### 2.2 动作向量映射

`@Action` 矩阵列按顺序映射到 `action_dim` 维向量（默认 18），与 `actpred/pretrain_mt.py` 的 `MTActionDataset` 语义一致：

- `.mt` 有 12 列（s1.A..s10.A, spk.F, spk.delay）→ 填入 action_vec[0:12]，末尾补零到 18 维
- 用 `parse_mt()` 解析占位符 `-`（递归取上一行），保证录制值与实际执行值一致
- 不足补零，超出截断

### 2.3 CLI 接口

```bash
# 树莓派上运行 .mt 脚本同时录制
python3 -m pybot --record ../actpred/data scripts/jump.mt

# dry-run 测试录制流程（合成随机噪声帧）
python3 -m pybot --dry-run --record /tmp/test_record scripts/jump.mt

# 自定义动作维度
python3 -m pybot --record ../actpred/data --action-dim 18 scripts/jump.mt
```

新增 CLI 选项：

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--record DIR` | 无 | 录制 actpred 格式 episode 到 DIR |
| `--action-dim N` | 18 | 动作向量维度（与 actpred 一致） |

### 2.4 EpisodeRecorder 实现

`pybot/pybot/recorder.py` 中的 `EpisodeRecorder` 类：

- 自动递增 episode 编号（扫描已有 `episode_*` 目录取最大值+1）
- 原子创建目录避免并发冲突（`mkdir(exist_ok=False)` 循环重试）
- `record(frame, action_vec)` 内 lazy import numpy + PIL，`Image.fromarray(frame).save(path, quality=90)`
- `flush()` 保存 `actions.npy`，返回 episode 路径
- numpy/Pillow 为可选依赖（lazy import），不影响 pybot 零依赖运行时承诺

---

## 3. 微动作分解

### 3.1 核心规则

| 条件 | 行为 |
|------|------|
| 首行（无前序状态） | 不分解，按原逻辑执行（`speaker.play(freq, delay)` 一次性阻塞全时长） |
| 后续行 + delay > 0 | 分解为 N = delay//10 个微步，舵机角度线性插值 |
| delay 不是 10 的倍数 | `ValueError` |
| 无 delay 或无 Speaker | 不分解，单步执行（现有行为不变） |

### 3.2 插值公式

对于微步 k（0-indexed, k=0..N-1）：

```
servo_angle[k] = prev_angle + (target_angle - prev_angle) × (k+1) / N
```

- `prev_angle`：上一行 `@Action` 的解析值（含占位符继承）
- `target_angle`：当前行的解析值
- 第一行无前序状态，不插值

### 3.3 插值范围

| 设备/参数 | 处理方式 |
|-----------|----------|
| ServoVar 的 angle 参数 | 线性插值 |
| SpeakerVar 的 frequency | 保持行值不变（不插值） |
| SpeakerVar 的 delay | 每步设为 10 |
| 其他设备参数 | 保持行值不变 |

### 3.4 时序

每步调 `speaker.play(freq, 10)` 提供声音 + 10ms 阻塞时序。dry_run 下 speaker 立即返回，补 `time.sleep(0.01)` 维持 10ms 节拍。

### 3.5 示例

`jump.mt` 的 `@Action` 矩阵：

```
s1.A  s2.A  ...  spk.F  spk.delay;
  45     -  ...     10        200;   # row 1: stand（首行不分解）
   -    10  ...      -         20;   # row 2: delay=20 → N=2
   -    20  ...      -         20;   # row 3: delay=20 → N=2
   -    30  ...      -         20;   # row 4: delay=20 → N=2
  45     0  ...     10        200;   # row 5: stand（N=20）
```

分解结果（27 步）：

| 步 | 来源行 | s1 | s2 | spk.delay |
|----|--------|----|----|-----------|
| 0 | row 1（首行） | 45 | 0 | 200 |
| 1-2 | row 2（0→10, N=2） | 45 | 5, 10 | 10, 10 |
| 3-4 | row 3（10→20, N=2） | 45 | 15, 20 | 10, 10 |
| 5-6 | row 4（20→30, N=2） | 45 | 25, 30 | 10, 10 |
| 7-26 | row 5（30→0, N=20） | 45 | 28.5→...→0 | 10×20 |

### 3.6 辅助方法

`RobotStruct` 新增 5 个辅助方法（`pybot/pybot/robot.py`）：

| 方法 | 职责 |
|------|------|
| `_resolve_row(action, row, n_cols)` | 解析一行的全部值（占位符继承） |
| `_resolve_param(param)` | 解析别名 → 真实参数名（A→angle） |
| `_find_delay_idx(columns)` | 找到 spk.delay 列索引；无 Speaker 返回 None |
| `_build_step_vals(prev, target, columns, k, n)` | 为微步 k 构建插值后的值列表 |
| `_execute_step(vals, columns)` | 从值列表构建 row_sets 并逐设备 play() |

---

## 4. 同步帧采集

### 4.1 问题

原始设计使用 `camera.latest()` 从后台帧队列读取最新帧。摄像头 30fps（33ms/帧），微步 10ms 间隔，连续 3 个微步可能取到同一帧——帧与动作不是 1:1 对应。

### 4.2 解决方案

Camera 类新增 `capture()` 方法（`pybot/pybot/hd/camera.py`）：

```python
def capture(self):
    """同步抓一帧（按需，不从队列读）；dry_run 或无 grabber 返回 None"""
    if self._grabber is None:
        return None
    return self._grabber()
```

`_grab_frame` 改用 `camera.capture()` 代替 `camera.play({})`（后者调用 `latest()` 读队列）。每次微步直接调 `_grabber()`（即 `picamera2.capture_array`）同步抓帧，确保帧与微步 1:1 对应。

dry_run 下 `_grabber` 为 None（setup 不打开 picamera2），`capture()` 返回 None，回退为随机噪声帧（`np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)`）。

### 4.3 性能分析

| 环节 | 耗时 | 是否阻塞 |
|------|------|---------|
| `capture_array()` 缓冲区拷贝 | <1ms | 是 |
| JPG 编码+写盘 | 2-5ms | 是 |
| I2C 寄存器写 | <1ms | 是 |
| `speaker.play(freq,10)` / `sleep` | 10ms | 是 |
| **合计** | **~13-17ms** | |

当前 JPG 落盘是同步的，每步实际 ~13-17ms 而非严格 10ms。若需严格 10ms 精度，可将 JPG 落盘移至后台线程异步执行（待后续优化）。

帧新鲜度取决于摄像头帧率：30fps 下每 33ms 产生一帧新画面，10ms 间隔的连续调用中仍有帧重复可能。若需每步独立帧，需将摄像头配到 ≥100fps 或降低分辨率。

---

## 5. 对 actpred 训练的影响

### 5.1 正面影响

- **数据量增加**：delay=200ms 的行从 1 个样本变为 20 个，jump.mt 从 5 帧变为 27 帧
- **动作轨迹平滑**：action vector 从粗粒度跳变（0→10→20→30）变为平滑过渡（0→5→10→15→20→25→30→28.5→...→0），模型更容易学习连续动作
- **帧-动作对齐更精确**：10ms 粒度的帧-动作配对比行级配比更精细
- **模型学到的动作更适用于实时控制**：预测微步而非大跳变

### 5.2 注意事项

- **spk.delay 列方差极低**：微步中 delay 全为 10（首行除外），模型基本忽略此列
- **预训练→微调分布偏移**：pretrain_mt.py 直接解析 `.mt` 拿到行级粗粒度值，微调数据是微步级细粒度值。分布不同但方向一致，微调阶段自然适应
- **格式完全兼容**：`images/*.jpg + actions.npy` 格式不变，`ActionDataset` 可直接加载

---

## 6. 测试

### 6.1 测试覆盖

`pybot/tests/test_recorder.py`（9 个测试）和 `pybot/tests/test_camera.py`（新增 2 个测试）：

| 测试 | 验证内容 |
|------|----------|
| `test_episode_format` | 目录结构正确（images/*.jpg + actions.npy） |
| `test_action_vector_padding` | 4 列 .mt → 18 维向量，末尾 14 维为零；微步展开后 19 步 |
| `test_placeholder_resolution` | `-` 占位符递归解析 + 微步插值 |
| `test_dry_run_synthetic_frame` | dry_run 下随机噪声帧，尺寸与 camera 配置一致 |
| `test_no_camera_error` | 非 dry_run 无 @Camera 时报错 |
| `test_episode_auto_increment` | 多次录制自动递增 episode 编号 |
| `test_micro_action_interpolation` | s1 从 0→30 分 3 步 → 10, 20, 30 |
| `test_delay_not_multiple_of_10` | delay 非 10 倍数时报错 |
| `test_actpred_compatibility` | 用 actpred ActionDataset 加载录制的 episode |
| `test_capture_on_demand` | capture() 每次返回新帧，不复用队列 |
| `test_capture_no_grabber` | dry_run 时 capture() 返回 None |

### 6.2 全量测试

```
62 passed in 4.37s
```

---

## 7. 关键文件

| 文件 | 说明 |
|------|------|
| `pybot/pybot/recorder.py` | EpisodeRecorder 类 |
| `pybot/pybot/robot.py` | run() 微动作分解 + 辅助方法 + _grab_frame |
| `pybot/pybot/main.py` | --record / --action-dim CLI 选项 |
| `pybot/pybot/hd/camera.py` | capture() 同步按需抓帧方法 |
| `pybot/tests/test_recorder.py` | 9 个录制+微动作测试 |
| `pybot/tests/test_camera.py` | 2 个 capture() 测试 |

---

## 8. 提交记录

| 提交 | 说明 |
|------|------|
| `b9f8abd` | feat(pybot): 运行 .mt 脚本时录制 actpred 格式自监督数据集 |
| `3fc213e` | feat(pybot): 微动作分解——带时延的 @Action 行按 10ms 粒度插值执行 |
| `bfa6d8d` | fix(pybot): 录制时改用同步 capture() 按需抓帧，避免帧重复 |
