# actpred 多帧注意力架构改造测试报告

| 项目 | 值 |
|------|-----|
| 日期 | 2026-08-09 |
| 模块 | actpred |
| 测试人 | qing |
| 环境 | Intel Xeon E3-1200 v2 (Ivy Bridge), 4 核, Linux 24.04, Python 3.x, PyTorch (CPU-only) |
| 关联文件 | `config.py`, `model.py`, `dataset.py`, `train.py`, `pretrain_mt.py`, `predict.py`, `export_onnx.py`, `test_actpred.py` |
| 前置报告 | [actpred_pretrain_test_20260808.md](actpred_pretrain_test_20260808.md) |

---

## 一、改造背景与目标

原架构为"单帧 CNN + 历史动作 MLP/GRU + concat 融合"，仅支持单帧图像输入。
本次改造目标：

1. 引入注意力机制，支持**多帧图像 + 历史动作序列**联合建模，预测下一时间步动作
2. 保持 checkpoint 格式（`model_state_dict` + `config`）、CLI 工作流与 ONNX 导出链路不变
3. 保持除 batch 维外全静态 shape，面向树莓派 AI 扩展板（NPU）部署友好
4. 直接替换旧架构（用户决策）：删除 MLP/GRU 编码器与 concat 路径，旧 checkpoint 作废

## 二、新架构

```
F 帧图像 (F×3×64×64) ─► 共享 CNN 编码器 ─► F 个 token ─► proj → d_model ┐
                                                                      ├─► [img×F, act×H, PRED] + 位置编码
动作历史 (H×D)      ─► Linear proj → d_model ─► H 个 token ──────────┤        │
可学习 [PRED] token ─────────────────────────────────────────────────┘        ▼
                                   TransformerEncoder (pre-LN, 2 层, 4 头, d_model=128)
                                                                              │
                                          取 PRED 位置输出 ─► MLP 头 ─► 预测动作 (action_dim)
```

| 设计点 | 取值/说明 |
|--------|----------|
| token 序列 | `[img×F, act×H, PRED]`，长度固定 F+H+1（默认 3+5+1=9） |
| 帧序约定 | 旧→新，末帧为当前帧；冷启动可重复传同一帧 |
| 注意力 | `nn.TransformerEncoder`，pre-LN（norm_first），dim_feedforward=2×d_model |
| 位置编码 | 可学习 `pos_embed (1, F+H+1, d)`，trunc_normal 初始化 |
| 输出 | 取末位 PRED token 过 MLP 头（d → 128 → 64 → action_dim） |
| 静态性 | forward 中 reshape 仅 batch 维动态，其余编译期固定 |
| 参数量 | 409,042（旧架构 142,034，约 2.9 倍） |

### 新增配置参数

| 参数 | 默认值 | 校验范围 |
|------|--------|---------|
| `--frame-len` | 3 | [1, 1024] |
| `--attn-dim` | 128 | [1, 4096]，且须能被 attn-heads 整除 |
| `--attn-heads` | 4 | [1, 64] |
| `--attn-layers` | 2 | [1, 64] |

删除参数：`--history-encoder`、`--action-hidden-dim`。
旧 checkpoint 因 config 含未知键（`history_encoder`/`action_hidden_dim`）在加载时被
`parse_checkpoint_config` 以明确 ValueError 拒载，符合"作废"预期（有专项测试覆盖）。

## 三、单元测试

```
$ python test_actpred.py
Ran 56 tests in 5.959s
OK
```

**56 项测试全部通过**（安装 onnxscript/onnxruntime 后无跳过项）。本次新增/改写用例：

| 用例 | 验证点 |
|------|--------|
| `test_forward_multi_frame` | (2,3,3,64,64) + (2,5,18) → (2,18) |
| `test_forward_single_frame_degenerate` | frame_len=1 退化单帧仍正常前向 |
| `test_forward_dynamic_batch` | batch 1/4/8 动态（frame_len=2） |
| `test_custom_attn_config` | attn_dim=64/heads=2/layers=1 可构建前向 |
| `test_attn_dim_not_divisible_by_heads_raises` | 130 % 4 ≠ 0 → ValueError |
| `test_dataset_loading` | 多帧样本 shape (3,3,32,32)，batch key 改为 `images` |
| `test_frame_len_gt_history_shifts_start` | 采样起点 max(history_len, frame_len-1) 正确 |
| `test_legacy_checkpoint_config_rejected` | 旧架构 checkpoint 拒载 |
| `test_wrong_frame_count_raises` | 推理帧数不符 → ValueError |
| `test_end_to_end_predict` 等推理用例 | 多帧输入端到端、归一化等价性、确定性 |

## 四、预训练测试

### 4.1 训练配置

| 参数 | 值 |
|------|-----|
| 数据来源 | `../bot4cj/scripts/*.mt`（13 文件） |
| train/val 划分 | **文件级**（11 训练文件 / 2 验证文件，杜绝滑动窗口泄漏） |
| 输入 | 全零多帧图像 (B, 3, 3, 64, 64) + action_history (B, 5, 18) |
| 优化器/调度 | AdamW (lr=0.001, wd=1e-4) + CosineAnnealingLR |
| 数据增强 | Gaussian noise (std=1.0) |
| batch_size / epochs / patience | 8 / 200 / 30 |
| 随机种子 | 42 |

### 4.2 训练结果

| 指标 | 值 |
|------|-----|
| 最终轮数 | 65（早停，patience=30） |
| 最优 val_loss | **2476.75** |
| 产出 | `checkpoints/pretrain_best.pt`（含 optimizer）、`checkpoints/pretrain_best_inference.pt` |

> **关于 val_loss 数值的说明**：本次 2476.75 与前次报告的 190.58 不可直接对比——
> 前次数值系样本级滑动窗口划分导致的数据泄漏虚低值（已在 0808 审计中定性）；
> 本次为文件级划分（验证集是训练中完全未见过的动作脚本），且模型更大（409K vs 142K）
> 而数据仅 13 个脚本，属"诚实的困难任务"数值。注意力架构的真实收益需在真实图像数据
> 微调后评估。

## 五、推理链路验证

### 5.1 CLI 全链路

```
$ bash _run.sh --model checkpoints/pretrain_best_inference.pt \
    --images /tmp/f0.jpg /tmp/f1.jpg /tmp/f2.jpg \
    --action-history "45,0,...（5 步 × 18 值）"
18.3430 10.1614 17.3654 ... （18 维输出，数值有限）
```

✅ 3 帧 + 5 步历史全链路输出正常。

### 5.2 错误场景

```
$ ... --images /tmp/f0.jpg ...
--images 需传入 3 个路径（旧→新），实际为 1 个。冷启动时可重复传同一帧路径。
```

✅ 帧数不符时报错信息明确，含冷启动指引。

## 六、ONNX 导出验证

```
$ python export_onnx.py --model checkpoints/pretrain_best_inference.pt --output /tmp/actpred_attn.onnx
ONNX model exported to: /tmp/actpred_attn.onnx
ONNX 一致性验证通过（最大差异: 7.629395e-06）
```

| 项目 | 结果 |
|------|------|
| 导出 | ✅ 成功（opset 17，输入 `images (batch,3,3,64,64)` + `action_history (batch,5,18)`） |
| onnxruntime 一致性 | ✅ 最大差异 7.63e-06（< 1e-4 阈值） |
| 动态维 | 仅 batch，NPU 编译友好 |

注：本次为环境补装了 `onnxscript` 与 `onnxruntime`（torch ≥ 2.12 ONNX 导出依赖）。

## 七、CPU 推理性能基准

测试方法：warmup 20 次后测 200 次取平均，输入随机 3 帧 (64,64,3) uint8 + (5,18) float32。

| 环节 | 本次（注意力，3 帧） | 前次（concat，单帧） |
|------|---------------------|---------------------|
| 参数量 | 409,042 | 142,034 |
| 端到端推理（含预处理） | **4.25 ms (235 FPS)** | 1.61 ms (622 FPS) |
| 纯前向 | 2.77 ms (360 FPS) | 1.09 ms (916 FPS) |
| image_encoder 占比 | 1.30 ms（47%，3 帧） | 0.82 ms（75%，1 帧） |

评估：

- 235 FPS 对 bot4cj 30 FPS 控制循环仍有 **7.8 倍余量**，实时性满足
- 延迟增长主因：CNN 由 1 帧变 3 帧（约 3×0.43ms）+ Transformer 编码器（约 1.5ms）
- image_encoder 占比由 75% 降至 47%，注意力部分成为新的次要热点；
  部署到树莓派 AI 扩展板后两者均可被 NPU 加速（静态 shape 已保证）

## 八、结论与下一步

### 8.1 测试结论

| 测试项 | 结果 |
|--------|------|
| 多帧注意力模型前向 | ✅ 通过（多帧/单帧退化/动态 batch/自定义 attn 配置） |
| 配置校验 | ✅ 通过（frame_len/attn_* 值域 + 整除校验 + 旧 checkpoint 拒载） |
| 多帧数据集采样 | ✅ 通过（采样起点与样本数断言） |
| 单元测试 | ✅ 56/56 通过 |
| 预训练 | ✅ 通过（65 epoch 早停，文件级划分无泄漏） |
| 推理 CLI | ✅ 通过（正常 + 错误场景） |
| ONNX 导出 | ✅ 通过（一致性 7.63e-06） |
| CPU 性能 | ✅ 通过（235 FPS，7.8 倍实时余量） |

### 8.2 已知局限

1. **预训练图像分支未习得**：全零多帧占位下 image_encoder 与图像 token 的注意力权重
   无有效梯度信号，真实收益依赖后续真实图像微调
2. **数据量极少**：13 个 .mt 脚本对 409K 参数模型明显不足，val_loss 绝对值偏高属预期
3. **旧 checkpoint 全部作废**：已删除并重训预训练 checkpoint；如有外部持有旧
   `best_inference.pt` 需同步告知重训

### 8.3 下一步建议

1. **采集真实多帧数据**：用 `DataRecorder` 录制 bot4cj 摄像头 episode，以本次预训练
   checkpoint 为起点微调（勿开 `--normalize-actions`）
2. **树莓派 AI 扩展板部署验证**：将 ONNX 模型走扩展板厂商工具链编译，实测 NPU 延迟
3. **帧数消融**：真实数据到位后对比 frame_len=1/3/5 的验证误差，确认多帧收益
4. **扩充 .mt 脚本**：增加动作脚本数量，缓解预训练数据不足

---

## 附录：文件变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `actpred/config.py` | 修改 | 新增 frame_len/attn_*；删除 history_encoder/action_hidden_dim；整除校验 |
| `actpred/model.py` | 重写 | 多帧 Transformer 注意力架构；删除 MLP/GRU 编码器与 concat 融合 |
| `actpred/dataset.py` | 修改 | 多帧窗口采样；batch key `image` → `images` |
| `actpred/train.py` | 修改 | 多帧适配（frame_len 透传、batch key） |
| `actpred/pretrain_mt.py` | 修改 | 全零多帧占位；打印信息更新 |
| `actpred/predict.py` | 修改 | `predict(images, history)` 多帧 API；CLI `--image` → `--images` |
| `actpred/export_onnx.py` | 修改 | 多帧 dummy 输入；input_names 更新 |
| `actpred/test_actpred.py` | 修改 | 56 项用例（新增多帧/校验/拒载/帧数错误等） |
| `actpred/README.md` | 修改 | 架构图、CLI 示例、配置表、API 文档同步 |
| `actpred/checkpoints/pretrain_best*.pt` | 重新生成 | 旧架构 checkpoint 已删除并重训 |
