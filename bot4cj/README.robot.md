# bot4cj — 机器人硬件脚本解析器

基于**仓颉（Cangjie）** 编程语言和 Linux I2C 接口的机器人动作脚本解析器。用一种矩阵式脚本来描述机器人的硬件构成与动作序列，解析后驱动真实硬件执行。

## 架构分层

```
┌──────────────────────────────────────────────────────────────┐
│ .mt 矩阵脚本（硬件定义 + 动作序列）                           │
├──────────────────────────────────────────────────────────────┤
│ MatrixParser（P.cj） — FSM 逐字符解析 → MatrixObject 链表       │
├──────────────────────────────────────────────────────────────┤
│ RobotStruct（r.cj） — 从矩阵对象构建硬件变量（createHardVars）    │
│   MatrixVars_Hardware 基类 + 闭包回调：                        │
│     · build() → rise 回调（打开硬件、获取资源）                   │
│     · play()  → things 回调（执行动作，参数/返回值均为 JsonValue） │
│     · free()  → free 回调（释放资源）                           │
├──────────────────────────────────────────────────────────────┤
│ 硬件实现：hd/*.cj                                             │
│   PCA9685（I2C 0x40, 50Hz PWM, 角度→脉宽 0.5~2.5ms）            │
│   Servo（舵机：max/min/offset 校准 + 角度翻转）                  │
│   MPU6050（陀螺+加速度，±500°/s ±8g，后台 10ms 采样线程+卡尔曼）   │
│   Speaker（正弦波发声，5ms 淡入淡出）                             │
│   fb.cj（外部硬件扩展口）                                       │
├──────────────────────────────────────────────────────────────┤
│ 系统库：cpp/ + cuda/                                          │
│   libi2c4cj.so  — I2C glibc 封装 + 20 槽锁数组                   │
│   libmt4cj.so   — FP32 矩阵运算（CPU / CUDA 二选一）              │
│   libfft4cj.so  — 基-2 Cooley-Tukey FFT                         │
│   libcnn4cj.so  — Conv/Pool/GA-Pool/Flatten + Adam/Momentum      │
│   libsnd4cj.so  — WaveAPI / ALSA 音频播放                       │
├──────────────────────────────────────────────────────────────┤
│ 数学/NN 库：math/*.cj                                         │
│   Matrix1（二维矩阵）+ Tensor + 6 种激活函数 + Xavier 初始化       │
│   Nn1 全连接层（前向/反向/权重更新）                              │
│   Af（Sigmoid/Tanh/ReLU/Leaky_ReLU/Swish/Softmax）              │
└──────────────────────────────────────────────────────────────┘
```

## 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 应用层 | **仓颉 Cangjie** | 主程序、解析器、机器人结构、数学库、HTTP 服务 |
| 系统层 | C / C++ | I2C 驱动、矩阵运算、FFT、CNN、音频 |
| 加速层 | CUDA（可选） | `MODE=cpu/cuda` 切换 |
| 脚本层 | `.mt` 矩阵脚本 | 硬件定义 + 动作序列 |
| 依赖包 | ctype4cj / pthread4cj / spi4cj / lua4cj | 仓颉官方扩展包 |
| 构建 | Makefile（C/C++ 共享库）+ cjpm.toml（仓颉包管理） | — |

## 目录结构

```
bot4cj/
├── src/
│   ├── main.cj           # 入口：CLI 参数解析（-i/-o/--test）+ stdin 管道执行
│   ├── P.cj              # 矩阵脚本解析器（FSM 逐字符解析）
│   ├── r.cj              # RobotStruct + MatrixVars_Hardware 基类
│   ├── testCNN.cj        # CNN 测试入口
│   ├── testCNN1.cj       # 几何 CNN 测试
│   ├── hd/
│   │   ├── I2C.cj        # I2C 封装 + 20 槽锁数组
│   │   ├── PCA9685.cj    # PCA9685 舵机控制板（已标记由 Lua 替代）
│   │   ├── Servo.cj      # 舵机：校准 + 角度翻转
│   │   ├── MPU6050.cj    # MPU6050 陀螺+加速度+卡尔曼滤波
│   │   ├── snd.cj        # 扬声器
│   │   ├── fb.cj         # 外部硬件扩展
│   │   └── extern/
│   │       └── macro_ExternHardware.cj
│   ├── math/
│   │   ├── Matrix.cj     # 二维矩阵（+运算符重载）
│   │   ├── Tensor.cj     # 张量
│   │   ├── Af.cj         # 6 种激活函数
│   │   ├── Ini.cj        # Xavier/Kaiming 初始化
│   │   ├── nn.cj         # 全连接层 Nn1
│   │   ├── nns.cj        # 旧版神经网络（XNOR 门测试）
│   │   ├── cnn.cj        # CNN 仓颉绑定
│   │   ├── pooling.cj    # 池化
│   │   ├── flatten.cj    # Flatten
│   │   ├── fft.cj        # FFT 仓颉绑定
│   │   ├── kalman.cj     # 卡尔曼滤波
│   │   └── Adam.cj       # Adam 优化器
│   ├── cpp/              # C/C++ 系统库源
│   │   ├── I2C.c         # I2C glibc 封装
│   │   ├── Matrix.cpp    # CPU 矩阵运算
│   │   ├── fft1.c        # FFT 单实序列
│   │   ├── fft.cpp       # FFT 双实序列 + 基-2 Cooley-Tukey
│   │   ├── kalman.c      # 卡尔曼滤波
│   │   ├── snd.cpp       # 音频播放（WaveAPI/ALSA）
│   │   └── cnn/          # 纯 C++ CNN（Conv/Pool/GA-Pool/Flatten）
│   └── cuda/
│       ├── matrix.cu     # GPU 矩阵运算 kernel
│       └── error.cuh     # CUDA 错误检查宏
├── modules/             # Lua 硬件模块（ADS1115/ADS1256/PCA9685/SPI）
├── scripts/             # .mt 动作脚本（std/jump/lie-down/tilt-* 等）
├── test/                # C/Lua 测试脚本
├── docs/Reports/        # 深度分析报告
├── cjpm.toml            # 仓颉包配置
├── Makefile             # C/C++ 共享库构建
├── _set.sh              # 编译（cjpm update + build）
├── _run.sh              # 运行（stdin 管道）
├── compile.ps1          # Windows 编译脚本
├── run.ps1              # Windows 运行脚本
├── README.md            # 旧版说明
└── README.robot.md      # 本文件
```

## 快速开始

```bash
# 1. 编译 C/C++ 共享库（一次性）
make MODE=cpu     # CPU 模式（默认）
# 或
make MODE=cuda    # GPU 模式（需要 nvcc）

# 2. 编译仓颉程序
bash _set.sh

# 3. 运行动作脚本（stdin 管道）
cat scripts/std.mt | bash _run.sh
```

> **硬件要求**：树莓派（aarch64）或其他 Linux I2C 设备。x86_64 开发板需自行验证 I2C 参数。Windows 暂不支持（依赖 glibc）。

## CLI 参数

```
./bot                      # 从 stdin 读取 .mt 脚本执行
./bot --help               # 打印帮助
./bot --test nn            # 运行神经网络与门测试
./bot --test cnn           # 运行 CNN 测试
./bot --test cnn1          # 运行几何 CNN 测试
./bot --test matrix_o      # 解析 ss/test.mt 并保存为 ss/output.mt
./bot --test matrix_2      # 解析 ss/output.mt → 执行 → 再次保存
./bot -i model.json -o out.json  # 加载/保存神经网络模型（DRL 预留）
```

## .mt 脚本语法

行列式表格，每列以空格或制表符分隔，每行以分号 `;` 结尾。

### 块结构

```
@BlockName
column1  column2  column3;
value1   value2   value3;
@
```

每个 `@` 块由 `@Name` 行开始，`@` 行结束。支持的块类型：

| 块 | 说明 |
|----|------|
| `@Alias` | 列名别名（如 `A` → `angle`、`F` → `frequency`） |
| `@I2C` | 定义 I2C 设备（name + path） |
| `@PCA9685` | 定义 PCA9685 舵机控制板（name + I2C 引用 + 地址） |
| `@MPU6050` | 定义 MPU6050 传感器（name + I2C 引用 + 地址） |
| `@Speaker` | 定义扬声器（channels + sample_rate + volume + max_f + min_f） |
| `@Servo` | 定义舵机（name + max + min + offset + controller + channel） |
| `@Action` | **动作矩阵**：列头为硬件变量的属性，行为每帧的参数 |

### 占位符

`-` 表示"沿用上一行同列的值"，当前行该列不产生调用。

### 完整示例

```mt
@Alias
name      alias;
   A      angle;
   F  frequency;
@

@I2C
 name        path;
i2c-0  /dev/i2c-0;
i2c-1  /dev/i2c-1;
@

@PCA9685
     name    I2C   address;
pca9685-0  i2c-1   0x40;
@

@Speaker
name  channels  sample_rate  volumn  max_f  min_f;
 spk         2        48000     0.6   5000      -;
@

@Servo
 name   max  min  offset  controller  channel;
  s1    90     -       -   pca9685-0        0;
  s2   180     -       -           -        1;
  s3   -90     -       -           -        2;
  s4  -180     -       -           -        3;
@

@Action
s1.A  s2.A  s3.A  s4.A  spk.F  spk.delay;
  45     -    45     -     10       200;   # 站立
  90     -     -     -      -        20;   # 左转
@
```

## 执行流程

```
stdin.read()
    ↓
P.MatrixParser (FSM 逐字符解析)
    ↓
LinkedList<MatrixObject> 链表
    ↓
RobotStruct.createHardVars()   # 按 @I2C/@PCA9685/@Servo/@Speaker/@MPU6050 构建硬件变量
    ↓
robot.active()                 # build()：逐个硬件调用 rise 回调（打开 I2C、初始化 PWM 等）
    ↓
robot.run()                    # 逐行遍历 @Action 矩阵，每列调用对应硬件的 play() 回调
    ↓
robot.deactive()               # free()：倒序释放资源
```

`play()` 的输入输出均为 `JsonValue`，实现了硬件与脚本之间的统一通信协议。

## 已支持的硬件

### PCA9685（I2C 舵机控制板）
- 地址：默认 `0x40`
- PWM 频率：50Hz（舵机标准 20ms 周期）
- 舵机脉宽映射：0°→0.5ms，180°→2.5ms
- 角度映射到 12 位 PWM 值（0-4095）
- **注意**：PCA9685.cj 代码已标记"由 Lua 脚本取代"，保留作备用

### Servo（舵机）
- 支持 `max` / `min` 角度限制与 `offset` 漂移校准
- `max < 0` 时自动翻转角度（反向安装的舵机）
- 通过闭包回调与实际硬件（PCA9685/Lua 模块）解耦

### MPU6050（6 轴传感器）
- 陀螺仪：±500°/s 量程
- 加速度计：±8g 量程
- 后台 10ms 采样线程（pthread_create + pthread_cancel）
- 卡尔曼滤波融合数据
- **已知问题**：`ADDR_0` 与 `ADDR_1` 常量均为 `0x68`（[MPU6050.cj:14-15](src/hd/MPU6050.cj#L14-L15)），第二个地址应改为 `0x69`

### Speaker（扬声器）
- 支持正弦波发声（指定频率 `F` + 延时 `delay`）
- 5ms 淡入淡出，避免"咔哒"声
- 通道数/采样率/音量可在 `.mt` 中配置
- **已知问题**：`play()` 仅 sleep 指定延时，真实发声依赖底层 C++ 实现

## 机器人型号适配

### Dashun 蜘蛛机器人（默认）
10 舵机 + 2 云台 + 扬声器，建模文件见 [gopsc/Dashun](https://github.com/gopsc/Dashun)：

| 舵机 | 关节 | 舵机 | 关节 |
|------|------|------|------|
| s1 | 左下水平 (rb0) | s6 | 右上垂直 (lf1) |
| s2 | 左下垂直 (rb1) | s7 | 左上水平 (rf0) |
| s3 | 右下水平 (lb0) | s8 | 左上垂直 (rf1) |
| s4 | 右下垂直 (lb1) | s9 | 云台1 (cam0) |
| s5 | 右上水平 (lf0) | s10 | 云台2 (cam1) |

### 可用脚本

`scripts/` 目录下预写了多种动作脚本：
- `std.mt` — 站立姿态
- `jump.mt` — 跳跃
- `lie-down.mt` — 趴下
- `left-hand.mt` / `rght-hand.mt` — 单手动作
- `left-turn.mt` / `tilt-f.mt` / `tilt-b.mt` / `tilt-l.mt` / `tilt-r.mt` — 转向与倾斜

## 构建

### 依赖

```bash
# 仓颉 1.1.3
# 仓颉官方扩展包：ctype4cj / pthread4cj / spi4cj / lua4cj
# libc / libpthread
# liblua-5.4（可选，Lua 硬件模块支持）
sudo ln -s /usr/lib/aarch64-linux-gnu/liblua5.4.so /usr/lib/aarch64-linux-gnu/liblua.so
sudo ln -s /usr/lib/x86_64-linux-gnu/liblua5.4.so /usr/lib/x86_64-linux-gnu/liblua.so
```

### 构建步骤

```bash
# 1. 构建 C/C++ 共享库
make MODE=cpu
# 产出：libi2c4cj.so libmt4cj.so libfft4cj.so libsnd4cj.so

# 2. （可选）构建 CNN
make libcnn4cj.so

# 3. （可选）CUDA 模式需 NVIDIA CUDA Toolkit
make MODE=cuda

# 4. 仓颉主程序
bash _set.sh
```

[_set.sh](_set.sh) 执行 `cjpm update` + `cjpm build`，自动解析 cjpm.toml 依赖。

### 运行

```bash
bash _run.sh scripts/std.mt
# 或
cat scripts/std.mt | bash _run.sh
```

[_run.sh](_run.sh) 以 `cjStackSize=64MB` / `cjHeapSize=64MB` 启动。

## 神经网络支持

项目内置了全连接层 Nn1（[math/nn.cj](src/math/nn.cj)）与 CNN（[math/cnn.cj](src/math/cnn.cj) + [cuda/](src/cuda/)）：

- **Nn1**：多层感知机，支持前向传播、反向传播、权重更新
- **CNN**：Conv → ReLU → Pool → GA-Pool → Flatten → FC
- **优化器**：Adam / Momentum
- **激活函数**：Sigmoid / Tanh / ReLU / Leaky_ReLU / Swish / Softmax
- **初始化**：Xavier / Kaiming

### 测试

```bash
./bot --test nn      # 与门训练（2 输入 → 1 输出）
./bot --test cnn     # CNN 前向/反向测试
./bot --test cnn1    # 几何分类 CNN 测试
```

### DRL 预留（未完成）

main.cj 中预留了 DRL 流程（注释状态）：加载预训练权重 → 执行动作 → 保存微调模型。通过 `-i` / `-o` 参数指定输入/输出路径。

## 安全与限制

### I2C 总线保护
- [hd/I2C.cj](src/hd/I2C.cj) 内置 **20 槽锁数组**，多硬件共享 I2C 总线时自动互斥
- 每个 I2C fd 独立加锁，避免并发写入冲突

### 硬件关闭时机
- `MatrixVars_Hardware` 析构依赖仓颉 GC，**不保证**在确定性时机调用 `free()`
- 机器人使用 `try/finally` 在 [main.cj:80-87](src/main.cj#L80) 中显式调用 `deactive()`，确保硬件资源正确释放

### 线程安全
- MPU6050 采样通过 `pthread_create` 启动后台线程
- `pthread_cancel` 依赖系统定义的取消点（sleep / 系统调用），可能在非安全点中断

### 平台支持
- ✅ Linux aarch64（树莓派）/ x86_64
- ❌ Windows（依赖 glibc）

## 已知问题

| ID | 描述 | 影响 |
|----|------|------|
| D1 | 仓颉 GC 不保证析构器调用，I2C fd 可能泄漏 | 长时间运行后 I2C 资源耗尽 |
| D2 | Speaker `play()` 仅 sleep，真实发声未接入 C++ | 扬声器无实际声音 |
| D3 | MPU6050 `pthread_cancel` 可能在非安全点中断 | 传感器数据不一致 |
| D4 | PCA9685 初始化被标记为"由 Lua 取代"，但仍保留主路径 | 代码存在双实现 |
| D5 | `_set.sh` 会删除 `a.out` | 误删用户产物 |
| D6 | Softmax 雅可比矩阵实现错误（math/Af.cj） | NN 训练收敛异常 |
| D7 | Tensor 乘法仅支持 2D，3D+ 会 panic | 限制模型复杂度 |
| D8 | Tanh 导数实现错误（使用 `1 - y^2` 而非 `1 - tanh^2(x)`） | NN 训练收敛异常 |
| D9 | CNN `testGeometryCNN` 使用随机数据 | 测试无实际意义 |
| D10 | MPU6050 `ADDR_0` 与 `ADDR_1` 均为 `0x68` | 无法同时使用两个 MPU6050 |
| D11 | `@Action` 与 `@Action_Auto` 并存未合并 | 脚本语法冗余 |

## 相关资源

- [Dashun 蜘蛛机器人建模](https://github.com/gopsc/Dashun)
- [深度分析报告](../docs/reports/bot4cj_analysis_20260525_00.md)（28 章节，1280 行）
- [仓颉编程语言](https://cangjie-lang.org/)
- [PCA9685 数据手册](https://www.nxp.com/docs/en/data-sheet/PCA9685.pdf)
- [MPU6050 数据手册](https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Datasheet1.pdf)

## 许可证

MIT License
