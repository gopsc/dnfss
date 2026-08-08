# 研究报告：actpred 与 bot4cj 项目

> 研究时间：2026-08-09
> 仓库：`git.cancerai.top/qing/blackbird`（micro_servers 单体仓库）
> 结论先行：**两个项目是同一套「具身智能 / 机器人」技术栈的两个互补半环**——
> `actpred` 是「大脑」（学出来的动作预测器），`bot4cj` 是「身体」（把动作落到真实硬件的执行引擎）。

---

## 1. 一句话定位

| 项目 | 语言/框架 | 角色 | 输入 → 输出 |
|------|-----------|------|-------------|
| **actpred** | Python / PyTorch | 动作预测「大脑」 | 摄像头图像 + 历史动作序列 → 下一步连续动作（回归） |
| **bot4cj** | 仓颉 Cangjie + C/C++/Lua | 硬件执行「身体」 | 矩阵式脚本(.mt) → 驱动舵机/IMU/扬声器 |

---

## 2. actpred 详解

### 2.1 它是什么
纯神经网络实现的**动作预测模块**：给定一帧摄像头图像（3×64×64）和前 `history_len` 步动作，回归预测当前步的连续动作向量。默认参数量约 140K，最大可到 ~2.3M（mobilenet + gru）。

### 2.2 架构
```
图像(3×64×64) ──► CNN编码器(simple/mobilenet) ──► img_feat(128)
                                                      │ concat
动作历史(H×D)  ──► MLP/GRU编码器            ──► act_feat(64) ──► 融合头 ──► 动作(action_dim)
```
- **图像编码器**：`SimpleCNN`（4 层卷积+自适应池化）或 `MobileNetEncoder`（ImageNet 预训练，带离线权重容错）。
- **历史编码器**：`MLPHistoryEncoder`（默认）或 `GRUHistoryEncoder`。
- **损失**：纯 MSE 回归；优化器 AdamW + CosineAnnealingLR；早停 + 梯度裁剪。

### 2.3 工程质量（亮点）
- 全部超参通过 `config.py` 的 dataclass + CLI 覆盖，序列化进 checkpoint，推理时自动还原。
- 数据层 `dataset.py` 设计扎实：自然排序键、按 episode 切分训练/验证、动作归一化统计量**只用训练集计算**（避免 val loss 失真）、`DataRecorder` 采集器。
- `export_onnx.py` 导出 ONNX 并**自动用 onnxruntime 验证一致性**（最大差异打印）。
- `test_actpred.py` 单元测试覆盖：排序、解析、前向、动态 batch、录制器、数据集、配置——质量明显高于一般研究脚本。

### 2.4 默认配置
`image_size=64, action_dim=8, history_len=5, backbone=simple, history_encoder=mlp, epochs=100, lr=1e-3`。

---

## 3. bot4cj 详解

### 3.1 它是什么
基于**仓颉（Cangjie）**编程语言 + Linux I2C 接口的**机器人硬件脚本解析器**。用一种「行列式表格」脚本（`.mt`）描述机器人的硬件构成与动作序列，解析后驱动真实硬件执行。目标平台是树莓派（aarch64），适配 Dashun 蜘蛛机器人（10 舵机 + 2 云台 + 扬声器）。

### 3.2 架构分层
```
.mt 矩阵脚本
   ↓  MatrixParser (P.cj) —— FSM 逐字符解析 → MatrixObject 链表
   ↓  RobotStruct (r.cj) —— createHardVars() 构建硬件变量（工厂模式）
   ↓  硬件实现 hd/*.cj：PCA9685 / Servo / MPU6050 / Speaker / Lua扩展
   ↓  系统库 cpp/ + cuda/：I2C / 矩阵 / FFT / CNN / 音频
   ↓  数学/NN 库 math/*.cj：Matrix1 / Tensor / Af / Nn1 / Adam / Kalman
```

### 3.3 关键设计
- **MatrixVars_Hardware 基类**：用三个闭包回调统一硬件接口——`build()`（rise，拿资源）、`play()`（things，执行，输入输出均为 JsonValue）、`free()`（destroy，放资源）。硬件与脚本通过 `JsonValue` 解耦。
- **硬件变量工厂**：`MtVars_I2C / Servo / Speaker / MPU6050_BIND / Alias / Lua`，从 `@I2C/@PCA9685/@Servo/@Speaker/@MPU6050` 块按名构建。
- **Lua 扩展口**：任意未知 `@Block` 会路由到 `modules/<Name>.lua`（已接 ADS1115/ADS1256/PCA9685/SPI），C 层注册了 i2c_*/spi_*/milli_sleep 给 Lua 调用——这是「通用机器人硬件描述语言」野心的落点。
- **I2C 总线保护**：20 槽锁数组，多硬件共享总线时互斥。
- **资源释放**：`main.cj` 用 `try/finally` 显式 `deactive()`，规避仓颉 GC 析构时机不确定的风险。

### 3.4 自带 NN 库（有意思的工程副产物）
不依赖 PyTorch，纯仓颉实现了 `Nn1`（全连接，含前向/反向/权重更新）、`Matrix1`/`Tensor`、`Af`（6 种激活）、`Ini`（Xavier/Kaiming）、`Adam`、`cnn`、`fft`、`kalman`。配套 C/C++ 加速库（可切 CUDA）。这说明作者**试图把训练与推理都搬进仓颉生态**，为后续 DRL 端到端闭环做准备。

### 3.5 已支持的硬件
- **PCA9685**（0x40，50Hz PWM，角度→脉宽 0.5~2.5ms→12 位 PWM）。
- **Servo**（max/min/offset 校准，max<0 自动翻转反向安装舵机）。
- **MPU6050**（±500°/s，±8g，后台 10ms 采样线程 + 卡尔曼融合）。
- **Speaker**（正弦波发声，5ms 淡入淡出）。

---

## 4. 两个项目的关系（核心结论）

1. **闭环互补**：`actpred` 感知+记忆→预测动作（连续向量）；`bot4cj` 把动作变成舵机角度执行。
   这在代码里已有明确意图：
   - `bot4cj/src/main.cj` 预留了 DRL 分支（`--test`、注释掉的 `run_action_drl`、`-i/-o` 模型加载/保存）。
   - `bot4cj/src/r.cj:838` 注释：`TODO(20260518-qing): 使用神经网络RNN直接根据动作+传感器脚本生成下一步动作`。
2. **尚未真正打通**：目前两者**没有共享数据格式**。`actpred` 默认 `action_dim=8`，`bot4cj` 是 10 舵机 + 云台；`actpred` 输出 numpy 向量，`bot4cj` 消费 `.mt` 矩阵。集成是明确的「未来工作」。
3. **同作者、同仓库**：均署名 `qing`，同属 `blackbird` 单体仓库，是同一具身智能蓝图的「预测端」与「执行端」。

---

## 5. 发现的问题与不一致（值得跟进）

### 5.1 在代码中已修复、但 README 仍列为「已知问题」的旧项（README 滞后）
- **D6（Softmax 雅可比错误）**：当前 `math/Af.cj:softmax_jacobian` 已实现正确——对角线 `y_i(1-y_i)`、非对角 `-y_i*y_j`，逻辑无误。
- **D8（Tanh 导数错误）**：当前 `math/Af.cj:tanh_dot` 用 `1 - y_val*y_val`（`y` 即 tanh 输出），**这是正确的**。代码里 `NOTE(20260525-qing): 修复命名（x => y）` 表明作者已意识到此前是按输出 y 计算的。README 描述的旧 bug 已修。
- → 建议：更新 `README.robot.md` 的「已知问题」表，删除/标注 D6、D8 已修复，避免误导。

### 5.2 真实存在的代码瑕疵（未在 README 列出）
- **`src/P.cj:147` 调试残留**：`parse()` 主循环里有 `print(s)`，**逐字符把脚本内容打到 stdout**。运行脚本时会污染标准输出（若将来用 stdout 与上层通信会很致命）。应删除或改为 debug 开关。
- **`src/P.cj:93` 运算符优先级 bug**：`if (!values.size == 0 && !grp.size == values[...].size)`。`!values.size` 实际是对 size 整数取反（非零→false，零→true），**并非** `!= 0`。正确写法应为 `values.size != 0 && grp.size != values[...].size`。当前该行恒为假分支，导致「行未对齐」检测 `DoesNotSync` 实际**从不触发**，矩阵列数不一致时不会报错而可能静默错位。
- **`src/P.cj:202` 转义不一致**：同文件里换行判断混用 `r'\n'` 与 `r"\n"`（后者是字面两字符，非换行符），属复制粘贴隐患。
- **PCA9685 双实现**：`hd/PCA9685.cj` 仍保留主路径，但已标注「由 Lua 取代」（`modules/PCA9685.lua`），存在代码冗余（D4）。

### 5.3 设计层面的待办（来自代码 TODO）
- DRL 闭环未实现（`main.cj` 中 `run_action_drl`、`run_action` 均注释）。
- `RobotStruct` 的 `@Action` 位置指示器（增量行动）未做，脚本路径写死 `./modules/`，未从环境变量/相对路径查找（r.cj:996, :998）。
- `Tensor` 乘法仅支持 2D（D7），限制模型复杂度。

---

## 6. 总体评价

| 维度 | actpred | bot4cj |
|------|---------|--------|
| 代码成熟度 | 高（测试完备、可导出 ONNX） | 中（功能可用，有调试残留与 TODO） |
| 实用性 | 可直接训练/推理/集成 | 强硬件依赖（树莓派+I2C），需真机验证 |
| 创新性 | 常规回归网络 | 「矩阵式机器人硬件 DSL」+ 纯仓颉 NN 库，有想法 |
| 集成度 | 独立 | 与 actpred 的物理闭环尚未打通 |

**建议下一步**：
1. 修掉 `P.cj` 的 `print(s)` 与 `!size == 0` 两个硬 bug（影响解析正确性）。
2. 更新 `README.robot.md`，把 D6/D8 标记为已修复。
3. 定义 `actpred` 动作向量 ↔ `bot4cj` `.mt` 舵机角度的映射协议，打通预测—执行闭环（可先用 `action_dim=10` 对齐 10 舵机，或保留舵机+云台=12 维）。

---
*本报告基于源码静态阅读（actpred 全部 .py、bot4cj 的 README/main/P/r/nn/Af + 构建脚本 + 示例脚本），未做真机/真模型运行验证。*
