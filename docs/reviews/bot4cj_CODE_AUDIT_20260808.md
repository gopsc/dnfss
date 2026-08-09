# bot4cj 模块代码审计报告

> 审查日期：2026-08-08 (Asia/Shanghai)
> 审查人：Automated Review
> 审查范围：`bot4cj/` 全模块（仓颉 Cangjie 机器人硬件脚本解析器）
> 参考文档：[深度分析报告](../reports/bot4cj_analysis_20260525_00.md)（D1-D29）、[actpred 与 bot4cj 研究报告](../reports/actpred_bot4cj_research_20260809.md)

---

## 一、模块概述

| 属性 | 值 |
|------|-----|
| 定位 | 机器人硬件脚本解析器（.mt 矩阵脚本 → 驱动舵机/IMU/扬声器） |
| 语言 | 仓颉 Cangjie + C/C++（系统库）+ Lua（硬件扩展） |
| 架构 | MatrixParser(FSM) → MatrixObject → RobotStruct → 硬件变量工厂 → hd/*.cj + modules/*.lua |
| 目标平台 | Linux aarch64（树莓派）/ x86_64 |
| 入口 | `main.cj`（stdin 管道 / --test 分支 / -i/-o DRL 预留） |
| 构建 | Makefile（C/C++ .so）+ cjpm.toml（仓颉包管理） |

### 依赖项

| 库 | 用途 |
|----|------|
| libc / libpthread | I2C glibc 封装、线程 |
| liblua-5.4 | Lua 硬件模块扩展（可选） |
| ctype4cj / pthread4cj / spi4cj / lua4cj | 仓颉官方扩展包 |

### 目录结构

```
bot4cj/
├── src/
│   ├── main.cj           # 入口：CLI 参数解析 + stdin 管道执行
│   ├── P.cj              # 矩阵脚本解析器（FSM 逐字符解析）+ MatrixObject
│   ├── r.cj              # RobotStruct + MatrixVars_Hardware 基类 + 硬件变量工厂
│   ├── hd/
│   │   ├── I2C.cj        # I2C 封装 + 20 槽锁数组
│   │   ├── PCA9685.cj    # PCA9685 舵机控制板（已由 Lua 替代）
│   │   ├── Servo.cj      # 舵机：max/min/offset 校准
│   │   ├── MPU6050.cj    # 6 轴传感器 + 卡尔曼滤波 + 后台线程
│   │   ├── snd.cj        # 扬声器（仅 sleep 占位）
│   │   └── fb.cj         # 外部硬件扩展
│   ├── math/
│   │   ├── Matrix.cj     # 二维矩阵（+运算符重载）
│   │   ├── Af.cj        # 6 种激活函数 + 雅可比
│   │   ├── nn.cj         # 全连接层 Nn1（前向/反向/权重更新）
│   │   ├── Adam.cj       # Adam 优化器
│   │   ├── Ini.cj        # Xavier 初始化
│   │   ├── kalman.cj     # 卡尔曼滤波
│   │   └── ...           # Tensor / cnn / fft / pooling / flatten
│   ├── cpp/              # C/C++ 系统库源
│   └── cuda/             # CUDA GPU 加速
├── modules/              # Lua 硬件模块
├── scripts/              # .mt 动作脚本 + .bot 旧版脚本
├── Makefile             # C/C++ 共享库构建
├── cjpm.toml            # 仓颉包配置
├── README.md            # 旧版说明
└── README.robot.md      # 技术文档（含已知问题表 D1-D11）
```

---

## 二、审查发现

### 🔴 严重问题 (P0)

#### [BOT-S01] submitLine 列数校验恒假，DoesNotSync 从不触发

- **文件**: `src/P.cj:93`
- **描述**: `MatrixObject.submitLine()` 中的行列数一致性检查使用了错误的运算符写法。仓颉中 `!`（逻辑非）优先级高于 `==`，表达式实际解析为 `(!values.size) == 0 && (!grp.size) == values[...].size`。

```cangjie
// 当前代码（第 93 行）：
if (!values.size == 0 && !grp.size == values[values.size - 1].size)
    { throw DoesNotSync(count) }

// 实际解析为：
if ((!values.size) == 0 && (!grp.size) == values[values.size - 1].size)
    { throw DoesNotSync(count) }
```

`!grp.size` 对整数取逻辑非（C 语义：`!0=1, !非零=0`），结果为 0 或 1。对于 12 列的 `@Action` 矩阵，`0 == 12` 恒为 false，整个条件恒假，`DoesNotSync` 异常**从不触发**。

| `grp.size` | `!grp.size` | `values[...].size` | `==` 结果 |
|------------|------------|---------------------|----------|
| 12（正常） | 0 | 12 | `0 == 12` → false |
| 10（少 2 列） | 0 | 12 | `0 == 12` → false |
| 14（多 2 列） | 0 | 12 | `0 == 12` → false |

- **触发条件**: 任何 `.mt` 脚本的矩阵块中某行列数与表头不一致
- **影响**: 列数不一致时不报错，静默存入 `MatrixObject`。消费端 `run_drl_action()` 用表头列数遍历，数据行列数不足时抛出通用 `IndexOutOfBoundsException`，报错信息不可读
- **修复建议**: 改用 `!=` 运算符：
```cangjie
if (values.size != 0 && grp.size != values[values.size - 1].size)
    { throw DoesNotSync(count) }
```
- **关联**: 旧分析报告 D11 仅指出"错误信息不清晰"，未发现根因是运算符优先级导致检查完全失效

#### [BOT-S02] parse() 主循环 print(s) 调试残留

- **文件**: `src/P.cj:147`
- **描述**: `MatrixParser.parse()` 主循环中有一行 `print(s)`，逐字符将整个脚本内容打到 stdout。

```cangjie
while (!flag && hasValue(so)) {
    try {
        let s = so.getOrThrow()
        print(s)        // ← debug：逐字符打印整个脚本
        count = _next(s, count)
        so  = input.read()
    }
    ...
}
```

- **影响**: 每次运行脚本时 stdout 被解析器输出污染。若未来用 stdout 与上层通信（如 actpred 的管道集成），会造成数据混淆
- **修复建议**: 删除 `print(s)` 行，或改为可选的 debug 开关
- **关联**: 旧分析报告 D25 已记录

#### [BOT-S03] 扬声器 play() 仅 sleep，实际发声未接入

- **文件**: `src/hd/snd.cj:17-21`
- **描述**: `Spk.play()` 的实际发声 C++ 函数 `play_frequency()` 被注释掉，方法体仅执行 `sleep()`，扬声器无实际声音输出。

```cangjie
public func play(frequency: Float32, volume: Float64, durations_ms: Int32): Bool {
    //unsafe {  play_frequency(frequency, durations_ms, volume, channels, sample_rate)  }
    sleep(Duration.millisecond * Int64(durations_ms))  // ← 仅延时
    return true
}
```

- **影响**: `.mt` 脚本中 `spk.F`（频率）参数被接受但无实际效果。机器人不会发声
- **修复建议**: 取消注释并确保 C++ `libsnd4cj.so` 正确链接；或在 README 明确标注"扬声器仅用于延时占位"
- **关联**: 旧分析报告 D2

---

### 🟡 中等问题 (P1)

#### [BOT-S04] r"\n" 与 r'\n' 转义不一致

- **文件**: `src/P.cj:200, 202`
- **描述**: 同一文件中换行判断混用 `r'\n'`（Rune，换行符 U+000A）与 `r"\n"`（String，两字符 `\` + `n`）。Rune 与 String 比较永远为 false。

```cangjie
// 第 195 行 — 正确：r'\n' 是 Rune
else if (!nl && nm && (c == r' ' || c == r'\t' || c == r'\n')) {

// 第 200 行 — 错误：r"\n" 是 String（两字符）
else if (!nl && !nm && (c == r' ' || c == r'\t' || c == r"\n")) {
    obj.submitItem()
    if (c == r"\n") { count += 1 }   // 第 202 行：永远为 false
}
```

- **影响**: 当 `!nl && !nm` 状态下遇到换行符，不触发 `submitItem()` 也不递增行号，而是落入第 209 行 `obj.addRune(c)` 将换行符吞入当前 item 文本。现有脚本每行都以 `;` 结尾所以不触发，但漏写 `;` 时两行内容会被合并为一行
- **修复建议**: 将第 200、202 行的 `r"\n"` 统一改为 `r'\n'`

#### [BOT-S05] MPU6050 ADDR_0 与 ADDR_1 均为 0x68

- **文件**: `src/hd/MPU6050.cj:14-15`
- **描述**: MPU6050 的两个地址常量都是 `0x68`，第二个应为 `0x69`（AD0 引脚拉高时的地址）。

```cangjie
const ADDR_0: UInt8 = 0x68  /* 默认地址 */
const ADDR_1: UInt8 = 0x68  /* 地址2 — 应为 0x69 */
```

- **影响**: 无法同时使用两个 MPU6050（I2C 地址冲突）。当前脚本中 MPU6050_BIND 块全部被注释，不触发
- **修复建议**: `const ADDR_1: UInt8 = 0x69`
- **关联**: 旧分析报告 D10

#### [BOT-S06] readI2cReg 返回 Int8，负值寄存器数据出错

- **文件**: `src/hd/I2C.cj:97`
- **描述**: `readI2cReg()` 返回 `Int8`（有符号），但 I2C 寄存器值为无符号字节。寄存器值 ≥ 128 时被解释为负数，后续计算（如 `read16BitReg` 的 `(hh << 8) | ll`）可能出错。

```cangjie
public func readI2cReg(fd: Int32, reg: UInt8): Int8 {  // ← 应为 UInt8
    ...
    var value: Int8 = 0
    ...
    return value
}
```

- **影响**: MPU6050 传感器数据的高字节 ≥ 128 时，符号扩展导致 16 位拼接结果错误
- **修复建议**: 返回类型改为 `UInt8`；或调用处做 `UInt8(Int16(value) & 0xFF)` 显式转换
- **关联**: 旧分析报告 D9

#### [BOT-S07] Lua 脚本路径硬编码

- **文件**: `src/r.cj:998`
- **描述**: 未知 `@Block` 路由到 Lua 扩展时，脚本路径写死为 `./modules/${MtName}.lua`，不支持相对路径或环境变量查找。

```cangjie
M[name] = MtVars_Lua.Factory("./modules/${MtName}.lua", obj)
```

- **影响**: 仅当工作目录为 `bot4cj/` 时可找到 Lua 模块。从其他目录运行时 Lua 脚本加载失败
- **修复建议**: 从可执行文件路径、环境变量（如 `BOT4CJ_MODULES_PATH`）或仓颉包资源中查找
- **关联**: 代码内 FIXME 注释（r.cj:996）

#### [BOT-S08] README.robot.md 已知问题表 D6/D8 已在代码中修复但文档未更新

- **文件**: `README.robot.md:367, 369`
- **描述**: README 已知问题表中：
  - **D6** "Softmax 雅可比矩阵实现错误" — 当前 `Af.cj:246-271` 的 `softmax_jacobian` 实现正确（对角线 `y_i(1-y_i)`、非对角 `-y_i*y_j`），且 `nn.cj:93-141` 的 `softmax_backward` 正确处理 n×n 雅可比矩阵
  - **D8** "Tanh 导数实现错误" — 当前 `Af.cj:194-206` 的 `tanh_dot` 使用 `1.0f32 - y_val * y_val`（`y` 即 tanh 输出），这是正确的。代码注释 `NOTE(20260525-qing): 修复命名（x => y）` 表明已修复

- **影响**: 文档误导用户认为激活函数有 bug，可能误用或绕过
- **修复建议**: 更新 README.robot.md 已知问题表，将 D6/D8 标注为"已修复"

---

### 🟢 轻微问题 (P2)

#### [BOT-Q01] 无单元测试

- **文件**: 全局
- **描述**: 模块无任何自动化测试。解析器 FSM、矩阵对象、激活函数导数、硬件变量工厂等关键路径完全依赖手工验证
- **修复建议**: 添加测试框架，至少覆盖：解析器（正常/异常输入）、激活函数（前向/反向数值验证）、Matrix1 运算
- **关联**: 旧分析报告 D20

#### [BOT-Q02] 大量注释掉的旧代码

- **文件**: `src/math/nn.cj:164-332`（~170 行）、`src/main.cj:39-49,89-95`（~15 行）
- **描述**: 旧版 `Nn` 类和 `ActivationFuncs` 枚举全部注释保留，`main.cj` 的 DRL 代码也是注释状态
- **修复建议**: 移入版本历史（git），保持源码整洁

#### [BOT-Q03] I2C 锁线性扫描 O(n)

- **文件**: `src/hd/I2C.cj:145, 175, 220, 246`
- **描述**: `I2cLock_create/destroy/lock/unlock` 每次遍历 20 个元素的锁数组，O(n) 复杂度
- **影响**: 20 个槽位时影响可忽略，但多 I2C 设备高频通信时累积开销
- **修复建议**: 用 `HashMap<Int32, I2C_Lock>` 替代线性扫描
- **关联**: 旧分析报告 D4

#### [BOT-Q04] Makefile -Wall -Werror 被注释

- **文件**: `Makefile:8`
- **描述**: `FLAGS := -O3 -pipe #-Wall -Werror`，编译警告被关闭，潜在类型不匹配和未使用变量无法在编译期发现
- **修复建议**: 开启 `-Wall`，逐步修复警告后开启 `-Werror`

#### [BOT-Q05] 消费端列数不匹配时报错不可读

- **文件**: `src/r.cj:1044, 1051`
- **描述**: `run_drl_action()` 用 `ActionDefs.col`（表头列数）遍历，若数据行列数不足，`values[row][col]` 抛出通用 `IndexOutOfBoundsException`，不报行号和期望列数
- **影响**: 与 S01 叠加——解析器不拦截不一致，消费端崩在不可读的越界错误
- **修复建议**: `parse_action_matrixObject` 入口增加列数校验，或在 `MatrixObject.[]` 中增加越界检查并抛出含行号列号的异常

#### [BOT-Q06] MPU6050 后台线程无法安全退出

- **文件**: `src/hd/MPU6050.cj:123-161`
- **描述**: `mpu6050_main()` 是无限 `while(true)` 循环，`stopDaemon()` 通过 `pthread_cancel` 终止，可能在非取消点中断
- **影响**: 传感器数据可能不一致；长时间运行后线程资源泄漏
- **修复建议**: 用原子标志位（`volatile bool running`）替代 `pthread_cancel`，循环条件改为 `while(running)`
- **关联**: 旧分析报告 D3

#### [BOT-Q07] 仓颉 GC 析构时机不确定，I2C fd 可能泄漏

- **文件**: `src/r.cj:28`（MatrixVars_Hardware 基类注释）
- **描述**: 仓颉 GC 不保证在确定性时机调用析构器，`MatrixVars_Hardware` 的 `free()` 回调依赖 `main.cj:82-87` 的 `try/finally` 显式调用 `deactive()` 来释放
- **现状**: 已有 `try/finally` 保底，但若执行路径异常跳出 `try` 块（如 `active()` 抛异常），`finally` 仍会执行，问题不严重
- **修复建议**: 确保所有异常路径都经过 `finally`；考虑 RAII wrapper
- **关联**: 旧分析报告 D1

---

## 三、统计汇总

| 等级 | 数量 | 问题 ID | 状态 |
|------|------|---------|------|
| 🔴 Critical | 3 | S01, S02, S03 | 待修复 |
| 🟡 Medium | 5 | S04-S08 | 待修复 |
| 🟢 Low | 7 | Q01-Q07 | 待修复 |
| **合计** | **15** | | **待修复** |

### 按类别统计

| 类别 | 问题数 | 占比 |
|------|--------|------|
| 解析器正确性（运算符 bug、转义不一致） | 3 | 20% |
| 硬件实现缺陷（扬声器、I2C、MPU6050） | 3 | 20% |
| 文档滞后（README 已知问题表） | 1 | 7% |
| 代码质量（调试残留、注释代码、无测试） | 3 | 20% |
| 设计限制（路径硬编码、锁扫描、线程退出） | 3 | 20% |
| 消费端健壮性 | 2 | 13% |

### 与已有文档的交叉引用

| 本报告 ID | 旧分析报告 ID | 关系 |
|-----------|--------------|------|
| S01 | D11（仅说"错误信息不清晰"） | **新发现根因**：运算符优先级导致检查完全失效 |
| S02 | D25 | 一致 |
| S03 | D2 | 一致 |
| S04 | — | **新发现** |
| S05 | D10 | 一致 |
| S06 | D9 | 一致 |
| S07 | — | **新发现** |
| S08 | D6, D8 | **新发现**：代码已修复，README 未更新 |
| Q01 | D20 | 一致 |
| Q02 | D24 | 一致 |
| Q03 | D4 | 一致 |
| Q05 | D3 | 一致 |
| Q07 | D1 | 一致 |

---

## 四、修复优先级建议

### 第一阶段（紧急）

1. **S01**: 修复 `submitLine` 运算符优先级 — `!=` 替代 `! ==`
2. **S02**: 删除 `parse()` 中的 `print(s)` 调试行
3. **S03**: 决策扬声器方案 — 接入 C++ 实现或明确标注占位

### 第二阶段（重要）

4. **S04**: 统一 `r'\n'` 转义
5. **S05**: 修正 MPU6050 `ADDR_1 = 0x69`
6. **S06**: `readI2cReg` 返回 `UInt8`
7. **S08**: 更新 README.robot.md，D6/D8 标注"已修复"

### 第三阶段（优化）

8. **S07**: Lua 脚本路径从环境变量查找
9. **Q01-Q07**: 添加测试、清理注释代码、优化锁扫描、开启编译警告

---

## 五、亮点

| 亮点 | 说明 |
|------|------|
| 矩阵式硬件 DSL | `.mt` 脚本统一描述硬件定义与动作序列，设计独特 |
| Lua 扩展口 | 未知 `@Block` 自动路由到 `modules/<Name>.lua`，C 层注册 I2C/SPI 函数给 Lua，实现通用硬件扩展 |
| 闭包回调架构 | `MatrixVars_Hardware` 基类用 build/play/free 三闭包统一硬件接口，JsonValue 解耦 |
| I2C 总线保护 | 20 槽锁数组，多硬件共享总线时互斥 |
| try/finally 资源释放 | 规避仓颉 GC 析构时机不确定的风险 |
| 纯仓颉 NN 库 | 不依赖 PyTorch，自实现 Nn1/Matrix1/Af/Adam/Kalman/CNN，为 DRL 闭环做准备 |
| CPU/CUDA 可切换 | Makefile `MODE=cpu/cuda` 切换矩阵运算后端 |

---

## 六、与 actpred 的集成现状

```
actpred（大脑）                    bot4cj（身体）
摄像头图像 + 历史动作               .mt 矩阵脚本
    ↓                                 ↓
神经网络预测动作向量              解析器 → 硬件变量
    ↓                                 ↓
action_dim=8 的连续向量           10 舵机 + 2 云台角度
    ↓                                 ↓
    └────── 尚未打通的闭环 ──────────┘
```

- `r.cj:838` 注释明确意图：`TODO: 使用神经网络RNN直接根据动作+传感器脚本生成下一步动作`
- `main.cj` 预留 DRL 分支（`-i/-o` 模型加载/保存，`run_action_drl` 注释状态）
- **目前没有共享数据格式**：actpred 输出 numpy 向量，bot4cj 消费 `.mt` 矩阵

### 集成前需修复的阻塞项

| 问题 ID | 阻塞原因 |
|---------|---------|
| S01 | 解析器不校验列数，集成时自定义脚本容易出错且无报错 |
| S02 | `print(s)` 污染 stdout，管道通信时数据混淆 |
| S07 | 路径硬编码，部署时找不到 Lua 模块 |

---

## 七、附录

### 审查工具

- 人工静态分析
- 代码路径追踪
- 运算符优先级推演
- 全部 `.mt` / `.bot` 脚本列数统计验证

### 参考文档

- [bot4cj 深度分析报告](../reports/bot4cj_analysis_20260525_00.md)（28 章，1407 行，D1-D29）
- [actpred 与 bot4cj 研究报告](../reports/actpred_bot4cj_research_20260809.md)
- [仓颉编程语言](https://cangjie-lang.org/)

### 审查人

- 自动化审计助手
- 审查时间：2026-08-08

---

*本报告由自动化代码审计生成，供开发团队参考。修复建议需结合实际硬件环境评估。*
