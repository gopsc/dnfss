# bot4cj 模块 Python 迁移开发方案

> 文档类型：功能开发计划
> 模块：bot4cj → pybot（Python 迁移）
> 作者：自动生成
> 日期：2026-08-09

---

## 1. 概述

### 1.1 背景与动机

`bot4cj` 是基于仓颉（Cangjie）编程语言和 Linux I2C 接口的机器人硬件脚本解析器，通过 `.mt` 矩阵脚本描述机器人硬件构成与动作序列，驱动 PCA9685 舵机控制板、MPU6050 传感器等真实硬件（宿主机为树莓派 aarch64）。

将其迁移到 Python 的动机：

| 收益 | 说明 |
|------|------|
| 工具链门槛消除 | 仓颉编译器（cjc 1.1.3）+ cjpm + 4 个仓颉扩展包（ctype4cj/pthread4cj/spi4cj/lua4cj）→ 树莓派系统自带 Python3，零编译 |
| 与 actpred 生态打通 | actpred（动作预测，PyTorch）已有 Python 版 `.mt` 解析器（`mt_parser.py`），迁移后"大脑（actpred）↔ 身体（bot4cj）"共享同一语言与数据结构，DRL 闭环开发成本大幅降低 |
| Lua 扩展层可消除 | 仓颉版引入 Lua 是为了运行时可扩展硬件驱动；Python 本身即脚本语言，硬件驱动直接写成 Python 插件模块，砍掉 lua4cj + liblua5.4 + C 函数注册整层 |
| C/C++ 共享库大部分可消除 | libi2c4cj.so 的功能可用 `fcntl.ioctl` + `os.read/write` 纯 Python 实现；矩阵/FFT/CNN 由 numpy/PyTorch（actpred 已有）承担 |
| 审计遗留一并修复 | [bot4cj 代码审计](../reviews/bot4cj_CODE_AUDIT_20260808.md) 15 项发现（3 P0 + 5 P1 + 7 P2）全部待修复，迁移时逐项闭环 |
| 与项目主流技术栈对齐 | micro_servers 业务服务以 Python/C++20 为主，仓颉是唯一的孤立技术栈，维护人手受限 |

**性能评估**：bot4cj 的执行节奏由硬件决定（舵机 50Hz PWM、I2C 100kHz~400kHz、动作行间 20~500ms 延时），解析与调度不是瓶颈，Python 的性能完全够用；且 Python 插件替代 Lua 桥接后，单次硬件调用的框架开销反而下降约两个数量级（详见附录 A 的量化分析）。这与 db2/captcha2 等"Python→C++ 求性能"的方向相反，是"仓颉→Python 求生态与可维护性"。

### 1.2 设计目标

1. **`.mt` 脚本零改动**：现有 `scripts/*.mt` 全部原样可用，块语法（`@Alias`/`@I2C`/`@PCA9685`/`@MPU6050_BIND`/`@Speaker`/`@Servo`/`@Action`）、`-` 占位符语义、`#` 注释、`;` 行结束符完全兼容。
2. **CLI 兼容**：保持 `cat scripts/std.mt | ./bot` 的 stdin 管道用法与 `--help`/`--test` 分支。
3. **零第三方依赖的核心**：解析器 + 硬件层仅用标准库（`fcntl`/`os`/`struct`/`threading`/`json`/`argparse`），树莓派上无需 venv 即可运行；numpy 仅测试/工具链可选。
4. **硬件插件化（Python 替代 Lua）**：未知 `@Block` 路由到 `modules/<Name>.py` 插件，保留 build/call/destroy 三段生命周期协议。
5. **审计 15 项发现全部修复**：见 §5 修复映射表。
6. **可测试**：pytest 单元测试覆盖解析器、占位符语义、Servo 校准、插件加载；硬件层可用 Mock I2C 在无硬件的 x86 上跑通全流程。

### 1.3 不做什么

- **不迁移 math/NN/CNN/FFT/CUDA 层**（`src/math/*.cj`、`src/cpp/cnn/`、`src/cpp/fft*`、`src/cuda/`）：这部分是为 DRL 预留的自研神经网络库，功能已被 actpred（PyTorch）完全覆盖且质量更高。DRL 闭环未来直接调 actpred，不重复造轮子。相应地 `--test nn/cnn/cnn1` 分支不迁移。
- **不迁移 `.bot` 旧版脚本支持**：`scripts/*.bot` 是历史遗留，仓颉版本身也未解析它。
- **不改 `.mt` 语法**：`@Action_Auto` 等语法演进（旧分析 D11）留待后续版本。
- **不引入 smbus2/RPi.GPIO 等三方库**：I2C 用 `fcntl.ioctl(fd, I2C_SLAVE=0x0703, addr)` 直接实现，与仓颉版 `I2C.cj`/`I2C.c` 行为逐字节对齐。
- **不删除 bot4cj 目录**：遵循项目"原版与重写版并存 → 验证通过后切换"的渐进策略（同 db→db2、captcha→captcha2、smtp→smtp3）。

---

## 2. 现状分析

### 2.1 仓颉版架构分层

```
.mt 矩阵脚本（硬件定义 + 动作序列）
    ↓
MatrixParser（P.cj，FSM 逐字符解析）→ MatrixObject 链表
    ↓
RobotStruct（r.cj）createHardVars()：按 @块名分发到硬件变量工厂
    MatrixVars_Hardware 基类 + 三闭包：rise/things/free
    ↓
硬件实现 hd/*.cj：I2C（20 槽锁数组）、Servo、PCA9685、MPU6050、Speaker
    +  Lua 扩展 modules/*.lua（未知 @Block 的通用驱动，经 C 函数注册调 I2C/SPI）
    ↓
系统库 .so：libi2c4cj（I2C glibc 封装）、libsnd4cj（音频）、libmt4cj/libfft4cj/libcnn4cj（数学）
```

### 2.2 迁移范围源码清单

| 仓颉源文件 | 行数 | 职责 | 迁移去向 |
|-----------|------|------|---------|
| `src/main.cj` | 178 | CLI 入口、stdin 管道、--test 分支 | `pybot/main.py` |
| `src/P.cj` | 264 | MatrixParser（FSM）+ MatrixObject + MatrixSaver + 解析异常 | `pybot/parser.py` |
| `src/r.cj` | 1072 | RobotStruct + 硬件变量基类 + 6 个工厂 + Lua 状态机桥接 | `pybot/robot.py` + `pybot/hardvars.py` |
| `src/hd/I2C.cj` + `src/cpp/I2C.c` | 340+ | I2C glibc 封装 + 每 fd 互斥锁 | `pybot/hd/i2c.py` |
| `src/hd/Servo.cj` | 31 | max/min/offset 校准 + 负 max 角度翻转 | `pybot/hd/servo.py` |
| `src/hd/MPU6050.cj` | 244 | 6 轴传感器 + 后台采样线程 + 卡尔曼 | `pybot/hd/mpu6050.py` |
| `src/hd/snd.cj` + `src/cpp/snd.cpp` | 23+ | 扬声器（仓颉侧仅 sleep 占位） | `pybot/hd/speaker.py` |
| `src/hd/PCA9685.cj` | — | 已标记"由 Lua 取代" | 不迁移（走插件） |
| `modules/*.lua`（PCA9685/ADS1115/ADS1256/SPI/TEST） | — | Lua 硬件驱动 | `pybot/modules/*.py` 插件 |
| `src/math/*.cj`、`src/cpp/cnn|fft|Matrix|kalman`、`src/cuda/` | ~2000 | 自研 NN/数学库 | **不迁移**（actpred 承担）；卡尔曼滤波例外，MPU6050 需要，~40 行纯 Python |

### 2.3 关键语义（必须逐项对齐）

1. **占位符 `-`**：递归沿用上一行同列值；首行为 `-` 时，`parseMt` 解析为 `0`（数值列），`getMt` 返回字面 `-`。
   actpred `mt_parser.py` 曾与此不同（首行 `-` 用 `@Servo` max 列填充），2026-08 已统一为本语义（填 0）：运行时 Servo 有 max/min 钳位保护，且 max 列是钳位上限而非初始位姿（反装舵机 max 为负值，会填出指令空间 0~180 之外的非法值）。
2. **@Action 列名 `dev.param`**：点号拆分为硬件名 + 参数名；参数名经 `@Alias` 表转换（`A`→`angle`、`F`→`frequency`）后传入硬件。
3. **每行执行**：同一行按硬件聚合参数字典，逐硬件调用一次 `play()`；行间时延由 `spk.delay` 参数承担。
4. **硬件生命周期**：`active()` 按创建顺序 build，`deactive()` 倒序 destroy，且必须在 `try/finally` 中保证执行。
5. **Servo 角度算法**：`a = angle + offset; a = clamp(a, |min|, |max|); if max < 0: a = 180 - a`。
6. **PCA9685 脉宽映射**：50Hz、0°→0.5ms、180°→2.5ms、`offval = width * 4096 / 20`、寄存器 `0x06 + 4*channel` 写 4 字节。
7. **MPU6050**：±500°/s、±8g、10ms 采样周期、卡尔曼滤波（Q=20, R=200）、roll/pitch 由加速度计反正切求得。
8. **I2C 互斥**：同一 fd 上"设地址 + 读/写寄存器"必须是原子序列（多设备共享总线）。

### 2.4 审计遗留（迁移时修复的输入清单）

来自 [bot4cj_CODE_AUDIT_20260808.md](../reviews/bot4cj_CODE_AUDIT_20260808.md)：3 项 P0（S01 列数校验恒假 / S02 调试 print 污染 stdout / S03 扬声器无实声）、5 项 P1（S04 转义不一致 / S05 MPU6050 地址重复 / S06 有符号读寄存器 / S07 Lua 路径硬编码 / S08 文档滞后）、7 项 P2（无测试、注释代码、锁扫描 O(n)、无编译告警、消费端报错不可读、线程退出、GC 析构不确定）。

---

## 3. 目标架构

### 3.1 目录结构

```
pybot/
├── pybot/
│   ├── __init__.py
│   ├── main.py           # CLI 入口（argparse：--help / --test / -i / -o 预留）
│   ├── parser.py         # MatrixParser + MatrixObject + MatrixSaver + 解析异常
│   ├── robot.py          # RobotStruct（createHardVars / active / run / deactive / save）
│   ├── hardvars.py       # HardwareVar 基类 + Alias/I2C/Servo/Speaker/MPU6050_BIND 工厂
│   ├── plugin.py         # Python 插件加载器（替代 Lua 状态机桥接）
│   └── hd/
│       ├── __init__.py
│       ├── i2c.py        # fcntl.ioctl I2C + 每 fd 锁（dict + threading.Lock）
│       ├── servo.py      # 舵机校准与翻转
│       ├── mpu6050.py    # 传感器 + threading.Thread 采样 + kalman
│       ├── kalman.py     # 一维卡尔曼滤波（~40 行）
│       └── speaker.py    # 正弦波发声（可选 alsaaudio，默认 aplay 子进程 / 静默降级）
├── modules/              # 硬件驱动插件（Python 替代 Lua）
│   ├── PCA9685.py
│   ├── ADS1115.py
│   ├── ADS1256.py
│   └── SPI.py
├── scripts -> ../bot4cj/scripts   # 软链或复制，.mt 脚本共享
├── tests/
│   ├── test_parser.py
│   ├── test_hardvars.py
│   ├── test_servo.py
│   └── mock_i2c.py       # Mock I2C 总线（记录寄存器写序列，供断言）
├── _run.sh               # cat x.mt | bash _run.sh（exec python3 -m pybot）
├── _set.sh               # 无编译；可选建 venv 装 pytest
├── requirements.txt      # 核心为空；[dev] pytest
└── README.md
```

### 3.2 数据流（与仓颉版一致）

```
stdin → MatrixParser.parse()（逐块产出 MatrixObject）
      → RobotStruct.create_hardvars()（按块名分发工厂；未知块 → plugin.load("modules/<Name>.py")）
      → robot.active()（按序 build）
      → robot.run()（逐行 @Action：聚合参数 → 逐硬件 play(dict)）
      → robot.deactive()（倒序 destroy，try/finally 保证）
```

### 3.3 关键映射决策

| 仓颉版机制 | Python 版方案 | 理由 |
|-----------|--------------|------|
| `JsonValue` 统一通信协议 | 原生 `dict`（进程内直接传递） | 无跨语言边界后无需序列化；插件协议文档化 dict 键约定 |
| 三闭包 rise/things/free | `HardwareVar` 基类三方法 `build()/play(params)/destroy()`，子类覆写 | Python 惯用法；闭包捕获改为实例属性 |
| Lua 状态机 + C 函数注册（i2c_open 等 11 个） | `importlib` 动态加载 `modules/<Name>.py`，插件直接 `from pybot.hd import i2c` | 消除整个 FFI 层；插件与核心同语言 |
| 20 槽 I2C 锁数组（O(n) 线性扫描） | `dict[fd → threading.Lock]` + 全局注册锁 | 修复审计 Q03；容量不再受限 |
| pthread_create/pthread_cancel（MPU6050 采样） | `threading.Thread(daemon=True)` + `threading.Event` 停止标志 | 修复审计 Q06/D3，安全退出 |
| 仓颉 GC 终结器不确定 | 上下文管理器 `with RobotStruct(...) as robot:` + `atexit` 兜底 | 修复审计 Q07/D1，确定性释放 fd |
| cjpm build + Makefile .so | 无构建步骤 | 树莓派上 `git pull` 即部署 |

### 3.4 插件协议（modules/*.py，替代 Lua build/call/destroy）

```python
# modules/PCA9685.py —— 与 PCA9685.lua 逐语义对齐
def build(ctx: dict) -> dict:
    """ctx: 元数据（fd/address/... 来自 @PCA9685 块行 + 引用硬件的 play() 展开）
    初始化：重启 → SLEEP → 设 prescale(50Hz) → 唤醒"""
def call(ctx: dict) -> dict:
    """ctx 额外含实时参数（channel/angle）；写 4 个 PWM 寄存器"""
def destroy(ctx: dict) -> dict:
    """复位 PCA9685"""
```

- 插件查找顺序（修复审计 S07）：`$PYBOT_MODULES_PATH`（冒号分隔多路径）→ 包内置 `modules/` → 当前工作目录 `./modules/`。
- 加载失败抛出含插件名与搜索路径的 `PluginNotFound` 异常。

---

## 4. 详细设计

### 4.1 parser.py

- **MatrixObject**：`name: str` + `rows: list[list[str]]`；提供 `__getitem__((row, col))`、`index_of_col(row, key)`、`save(writer)`。越界访问抛 `MatrixIndexError(块名, 行, 列)`（修复审计 Q05 的报错可读性）。
- **MatrixParser**：保持 FSM 逐字符解析（非正则），状态与仓颉版一致（nl/cm/nm 三标志），原因：`@` 块嵌套注释、行内注释、任意空白分隔的兼容性已被现有脚本验证。
  - 修复 S01：`submit_line` 用 `len(grp) != len(rows[-1])` 校验列数，不一致抛 `DoesNotSync(行号, 期望列数, 实际列数)`。
  - 修复 S02：删除逐字符 debug 打印；提供 `--verbose` 时输出到 **stderr**（stdout 保留给未来管道通信）。
  - 修复 S04：Python 无 Rune/String 混淆问题，换行统一 `"\n"` 字符比较；补充单测覆盖"漏写分号跨行"场景。
- **占位符语义**（§2.3-1）：`get_mt(obj, row, col)` 递归向上；`parse_mt(obj, row, col, type)` 首行 `-` 返回 `type("0")`。
- **MatrixSaver**：保持与仓颉版相同的输出格式（`@Name` / 空格分隔 / `;` 结尾 / `@` 收尾），保证 `--test matrix_o/matrix_2` 的解析↔序列化往返可用。

### 4.2 hd/i2c.py

```python
I2C_SLAVE = 0x0703
class I2CBus:
    def __init__(self, path): self.fd = os.open(path, os.O_RDWR); _register_lock(self.fd)
    def set_addr(self, addr): fcntl.ioctl(self.fd, I2C_SLAVE, addr)
    def write_reg(self, reg, val): os.write(self.fd, bytes([reg, val]))
    def read_reg(self, reg) -> int:  # 返回 0..255 无符号（修复 S06）
        os.write(self.fd, bytes([reg])); return os.read(self.fd, 1)[0]
    def transaction(self):  # with bus.transaction(): 原子序列（设地址+读写）
```

- 锁注册表：`_LOCKS: dict[int, threading.Lock]` + `_MAIN_LOCK`；`transaction()` 上下文管理器持锁，替代仓颉版 `I2cLock_lock/unlock` 手动配对。
- `read_reg` 天然返回无符号 int（`bytes[0]` ∈ 0..255），16 位拼接 `(hh << 8) | ll` 后用 `struct.unpack('>h')` 转有符号——修复 S06 的符号扩展错误且保留传感器数据的正确符号。

### 4.3 hd/servo.py

与 `Servo.cj` 逐行对齐（§2.3-5），构造参数 `max/min/offset/set_angle_callback`。补单测：正装（max=90）、反装（max=-180 翻转）、offset 漂移、越界钳位。

### 4.4 hd/mpu6050.py

- 寄存器常量与 `MPU6050.cj` 一致；**修复 S05**：`ADDR_0 = 0x68`、`ADDR_1 = 0x69`。
- 采样线程：`threading.Thread` + `self._stop = threading.Event()`，循环 `while not self._stop.wait(0.01)`（10ms 周期且可即时打断）——修复 Q06。
- 读传感器整体包在 `bus.transaction()` 中（设地址 + 14 字节连读），比仓颉版逐寄存器 12 次 I2C 往返更高效且原子。
- 卡尔曼滤波参数保持 Q=20、R=200；roll/pitch 公式不变。
- 数据出口：`latest()` 返回带 `threading.Lock` 保护的最近一帧滤波值（仓颉版 mutex 字段从未真正使用，此处补全）。

### 4.5 hd/speaker.py（决策 S03）

仓颉版 `play()` 仅 sleep（实声 C++ 函数被注释）。Python 版决策：**实现真实发声，带静默降级**。

- 首选：标准库 `wave` + `subprocess.run(["aplay", "-q", tmp.wav])`——树莓派自带 alsa-utils，零 pip 依赖；正弦波生成用 `math.sin`（每次 ≤ 数秒 @48kHz，纯 Python 可接受），保留 5ms 淡入淡出。
- `aplay` 不存在或播放失败：log 一次警告后仅 sleep(delay)，行为与仓颉版一致（延时职责不丢失）。
- `delay` 语义不变：`play()` 阻塞 `delay` 毫秒（发声与延时并行时取延时为准）。

### 4.6 robot.py / hardvars.py

- `RobotStruct` 保持仓颉版结构：`C: list[MatrixObject]` + `M: dict[str, HardwareVar]`（Python dict 有序，天然满足"倒序 destroy"需求）。
- `create_hardvars()` 分发表：`{"I2C": I2CVar, "Alias": AliasVar, "Servo": ServoVar, "Speaker": SpeakerVar, "MPU6050_BIND": MPU6050Var}`；`Action` 跳过；未知块 → `plugin.PluginVar`。
- 重名键检查（仓颉版 FIXME r.cj:911）：同名硬件变量重复定义时，先 `destroy()` 旧值再覆盖，并输出 stderr 警告。
- `run()` 逐行执行 `@Action`；列名合法性（必须含一个 `.`）在**执行前**整表校验一次，报错含列号与列名（配合 Q05 修复）。
- 上下文管理器：`__enter__`→`active()`，`__exit__`→`deactive()`；`main.py` 用 `with` 替代仓颉版 try/finally。

### 4.7 main.py

| 仓颉版 CLI | Python 版 | 说明 |
|-----------|-----------|------|
| （无参）stdin 管道 | 同 | `cat std.mt | ./bot` 不变；另支持 `pybot path/to/x.mt` 位置参数（增强，非破坏） |
| `--help` | argparse 自动生成 | 文案更新 |
| `--test nn/cnn/cnn1` | **移除**（见 §1.3） | NN 由 actpred 承担 |
| `--test matrix_o/matrix_2` | `--test roundtrip <in.mt> <out.mt>` | 解析↔序列化往返测试，路径改为参数（仓颉版硬编码 `ss/test.mt`） |
| `-i` / `-o`（DRL 预留） | 保留参数位，标注 reserved | 未来对接 actpred |
| （新增）`--dry-run` | 解析 + 构建硬件变量但不开 I2C，Mock 总线打印动作序列 | 无硬件调试 .mt 脚本 |
| （新增）`--verbose` | FSM 与硬件调用日志到 stderr | 替代 S02 删掉的调试打印 |

### 4.8 日志规范

- 所有诊断输出走 `logging`（stderr），stdout 保留纯净——为未来 actpred 管道集成预留（审计 §六 的阻塞项 S02 根治）。

---

## 5. 审计发现修复映射表

| 审计 ID | 级别 | 仓颉版问题 | Python 版修复方式 | 落点 |
|---------|------|-----------|------------------|------|
| S01 | P0 | submitLine 列数校验恒假 | `len(grp) != len(rows[-1])` 显式校验 + DoesNotSync 带期望/实际列数 | parser.py |
| S02 | P0 | parse() 逐字符 print 污染 stdout | 移除；--verbose 走 stderr logging | parser.py / main.py |
| S03 | P0 | 扬声器仅 sleep 无实声 | wave + aplay 实现发声，缺 aplay 静默降级为 sleep | hd/speaker.py |
| S04 | P1 | r"\n" 与 r'\n' 转义不一致 | Python 无此坑；补"漏分号跨行"单测防回归 | tests/test_parser.py |
| S05 | P1 | MPU6050 ADDR_1 应为 0x69 | `ADDR_1 = 0x69` | hd/mpu6050.py |
| S06 | P1 | readI2cReg 返回有符号 Int8 | read_reg 返回 0..255；16 位拼接后 struct 显式转有符号 | hd/i2c.py |
| S07 | P1 | Lua 路径硬编码 ./modules/ | `$PYBOT_MODULES_PATH` → 包内置 → cwd 三级查找 | plugin.py |
| S08 | P1 | README 已知问题表滞后 | pybot README 重写；bot4cj README 标注维护状态 | README.md |
| Q01 | P2 | 无单元测试 | pytest：解析器/占位符/Servo/插件/Mock I2C 全流程 | tests/ |
| Q02 | P2 | 大量注释旧代码 | 新代码库不携带 | — |
| Q03 | P2 | I2C 锁 O(n) 线性扫描 | dict[fd → Lock] | hd/i2c.py |
| Q04 | P2 | Makefile 告警关闭 | 无 C 编译；CI 跑 `python -m pyflakes`（可选） | _set.sh |
| Q05 | P2 | 列数不匹配报错不可读 | MatrixIndexError 带块名/行/列 + run() 前整表校验 | parser.py / robot.py |
| Q06 | P2 | pthread_cancel 非安全点退出 | threading.Event 优雅停止 | hd/mpu6050.py |
| Q07 | P2 | GC 析构不确定，fd 泄漏 | with 上下文管理器 + atexit 兜底 | robot.py / main.py |

---

## 6. 测试计划

### 6.1 单元测试（无硬件，x86 可跑）

| 用例组 | 覆盖 |
|--------|------|
| test_parser | 全部 `scripts/*.mt` 解析成功；列数不一致抛 DoesNotSync；漏分号跨行；注释/空行/CRLF；roundtrip（解析→save→再解析等价） |
| test_placeholder | `-` 递归沿用；首行 `-` 数值列=0；actpred mt_parser 已统一为同一语义（2026-08），两侧测试互为语义锚点 |
| test_servo | 正装/反装翻转/offset/钳位边界 |
| test_hardvars | @Alias 转换；@Action 列名拆分；同行参数聚合；重名覆盖警告 |
| test_plugin | 插件三级路径查找；PluginNotFound 报错内容；PCA9685.py 用 Mock I2C 断言寄存器写序列（prescale=121、reg=0x06+4*ch、offval 字节序） |
| test_mpu6050 | Mock 总线注入 14 字节样本 → 有符号拼接正确（高字节 ≥ 0x80 场景，回归 S06）；Event 停止 1s 内退出 |

### 6.2 实机验证（树莓派 + Dashun 蜘蛛机器人）

1. `cat scripts/std.mt | bash _run.sh`：站立姿态与仓颉版目测一致（10 舵机角度逐一对照 --verbose 日志）。
2. `jump.mt` / `lie-down.mt` / `tilt-*.mt` 逐个回放，动作时序无可感知差异。
3. 双 MPU6050（0x68 + 0x69）同总线并行采样（回归 S05），roll/pitch 数值与仓颉版单设备输出同量级。
4. 扬声器 `spk.F` 发声可闻（回归 S03），无爆音（淡入淡出生效）。
5. Ctrl-C 中断：舵机停止、I2C fd 全部释放（`ls /proc/<pid>/fd` 验证）、MPU6050 线程退出。

### 6.3 交叉验证

- `--dry-run` 模式下用 Mock 总线记录全部 I2C 写序列，与仓颉版（打补丁记录）同脚本输出逐字节 diff——作为迁移等价性的最强证据（可参考 captcha2 迁移的像素级验证经验）。

---

## 7. 里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 解析器 | parser.py + 全量单测 + roundtrip | 全部 scripts/*.mt 解析通过 |
| M2 硬件抽象 | hardvars.py + robot.py + plugin.py + Mock I2C | --dry-run 全流程可跑（x86） |
| M3 硬件落地 | i2c.py / servo.py / mpu6050.py / speaker.py + modules/*.py | 树莓派实机 std.mt 站立 |
| M4 等价性验证 | §6.2 实机回放 + §6.3 写序列 diff | 验证报告 |
| M5 切换 | README/INDEX 更新、bot4cj 标注 legacy、aibox/BotOps 若有引用改指 pybot | 并存期结束评估 |

---

## 8. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| Python GIL + 解释器抖动影响动作时序 | 舵机动作节奏轻微不均 | 动作行间延时 ≥ 20ms，抖动 <1ms 不可感知；MPU6050 采样用 Event.wait 定时，不忙等；量化依据见附录 A |
| aplay 子进程首次启动延迟 | 首个音符滞后 | build() 时预热（播放 0 长度静音）；或后续可选接 alsaaudio |
| 仓颉版行为的未记录细节 | 等价性缺口 | §6.3 写序列逐字节 diff 兜底 |
| .mt 脚本方言分裂（pybot vs actpred 解析器） | 语义漂移 | 占位符语义已统一（2026-08，首行 `-` 均填 0）；长期可评估 actpred 复用 pybot parser 的只读模式 |
| 树莓派 Python 版本参差 | 语法兼容 | 目标 Python ≥ 3.9（树莓派 OS Bullseye 起自带 3.9） |

---

## 9. 与 actpred 的集成（已实现：进程内闭环）

原规划的"大脑-身体"闭环已在 `run_loop()` 中落地，采用进程内调用替代 FIFO 序列化：

```
camera 帧 → actpred.predict(F帧, H历史) → 动作向量(18) → pybot 下发执行
    ↑                                                        ↓
history deque ←────────── 回写实际动作 ←────────────────────┘
```

**用法**：`python3 -m pybot --loop CKPT --hz 10 scripts/std.mt`

- checkpoint 后缀 `.hef` → Hailo NPU 后端，否则 → PyTorch 后端
- `@Action` 表头列数 ≤ `action_dim`（默认 18），列索引即动作向量索引
- 闭环中剔除 `spk.delay` 阻塞（发声但不延时），节拍由 `hz` 主循环掌控
- 冷启动：F 帧重复同帧 + H 步零动作历史
- `--loop-steps N` 限步数调试；`-i/-o` FIFO 保留用于未来跨进程隔离

**与录制的衔接**：`--record DIR` 产出 actpred 格式 episode → actpred 训练 → 推理 checkpoint → `--loop` 闭环执行，形成完整数据飞轮。

---

## 附录 A：性能评估依据

迁移评审中最常见的两个疑虑是"Python 做驱动会不会慢"与"Python 替代 Lua 会不会引入开销"。本附录给出量化分析，结论：**两者均不构成风险，后者反而是性能收益**。

### A.1 Python 做硬件驱动的时间尺度分析

bot4cj 架构中所有微秒级时序都已下沉到专用硬件芯片（PCA9685 自主生成 PWM 波形），主机侧只发送设定值，该路径的瓶颈是 I2C 总线物理速率，与宿主语言无关：

| 环节 | 耗时量级 | 由谁承担 |
|------|---------|---------|
| 舵机 PWM 波形（50Hz、0.5~2.5ms 脉宽） | µs 级精度 | PCA9685 硬件芯片，主机不参与 |
| I2C 写一个寄存器（100kHz 标准模式） | ~200–300 µs/次 | 总线物理速率，语言无关 |
| Python 一次 `os.write`/`ioctl` 调用开销 | ~2–5 µs | 解释器 + 系统调用 |
| 设一个舵机角度（4 次寄存器写） | ~1 ms | 总线 ≈ 99%，Python ≈ 1% |
| 舵机机械转动 60° | ~100–200 ms | 物理惯性 |
| `.mt` 动作行间延时 | 20–500 ms | 脚本显式指定 |

Python 解释开销比总线时间低约两个数量级，比机械动作低四五个数量级。逐项检查潜在慢点：

- **MPU6050 10ms 采样循环**：14 字节连读 ≈ 1.5–2ms 总线时间 + 卡尔曼滤波 6 次浮点（纯 Python 数 µs），周期内富余充足；且连读比仓颉版逐寄存器 12 次往返（每次重发从机地址）更快。
- **正弦波生成**：48kHz 下每秒音频约 4.8 万次 `math.sin`，纯 Python 10–30ms 算完，发声前一次性生成，可接受。
- **GIL/GC**：仅主线程 + 1 个采样线程，GIL 无竞争；GC 停顿亚毫秒级，低于 `sleep()` 本身的 ms 级精度。

Python 不适用的边界（bot4cj 均不涉及）：主机 GPIO bit-bang 生成 PWM/WS2812 单线协议、>1kHz 闭环控制、硬实时保证（Linux 非 RTOS，仓颉版同样做不到）。若未来 DRL 需要高频控制环，正确做法是下沉到协处理器（如 RP2040），属架构问题而非语言问题。

### A.2 Python 插件替代 Lua 桥接的开销对比

单论解释器速度 Lua 5.4 快于 CPython，但现有 Lua 路径的开销主要在跨语言桥接而非驱动代码本身。仓颉版每次硬件调用（[r.cj MtVars_Lua.things](../../bot4cj/src/r.cj#L311-L331)）的实际成本：

| 步骤 | 实现 | 开销量级 |
|------|------|---------|
| 元数据深拷贝 | `JsonValue.fromStr(metadata.toJsonString())`——JSON 序列化+反序列化一整轮 | ~10–50 µs |
| 参数推入 Lua 表 | 每键 `mallocCString` + `Lua_pushstring/setfield` + `free`，跨仓颉→C→Lua 三层 FFI | ~几 µs/键 |
| `Lua_pcallk` + 返回表遍历 | `Lua_next` 循环 + 每键值 `Lua_tostring` 转仓颉 String | ~几 µs |
| 强制调试打印 | 仓颉侧 2 次 `println` + Lua 侧 `print_table` 全表打印 | ~几十 µs 至 ms 级（终端 I/O） |
| Lua 内 4 次 `i2c_write_reg` | 每次跨 Lua→C→仓颉 @C 三层 | 桥接 ~1 µs + 总线 ~250 µs |

合计框架开销 ~0.1–1 ms/次，与 I2C 总线时间同量级。Python 插件路径的对应成本：

| 步骤 | 实现 | 开销量级 |
|------|------|---------|
| 上下文构造 | `{**metadata, **params}` dict 合并（同进程传引用，无序列化） | ~0.5 µs |
| 插件调用 | 同解释器直接函数调用 | ~0.1 µs |
| I2C 写 | 插件直接 `i2c.write_reg()`，一层 `os.write` 到底 | 系统调用 ~2 µs + 总线 ~250 µs |
| 诊断输出 | 默认关闭，`--verbose` 时走 stderr logging（即审计 S02 修复） | 0 |

框架开销 ~1–2 µs/次，比 Lua 桥接低约两个数量级。插件内部计算（脉宽换算、移位掩码）在两种解释器上的差距为百纳秒级，相对 1ms 总线时间可忽略。

**结论**：Lua 层在仓颉版存在的唯一理由是"编译型宿主做不了运行时扩展"，为此付出 JSON 往返 + 五层 FFI（仓颉→C→Lua→C→仓颉→glibc）的税；宿主换成 Python 后调用链缩短为 Python→glibc 一层，此项迁移是删除一整层间接性，而非语言互换。

---

## 参考文档

- [bot4cj 代码审计报告](../reviews/bot4cj_CODE_AUDIT_20260808.md)（15 项发现）
- [bot4cj 深度分析报告](bot4cj_analysis_20260525_00.md)（D1-D29）
- [bot4cj README.robot.md](../../bot4cj/README.robot.md)
- [actpred mt_parser.py](../../actpred/mt_parser.py)（Python 解析器先例）

---

*本文档为开发计划，实施前需结合实际硬件环境评估。*
