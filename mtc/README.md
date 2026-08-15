# mtc（.mt 脚本编译器）

命令式动作脚本（Python DSL）→ `.mt` 矩阵脚本编译器。零第三方依赖（纯标准库），产物可直接由 [pybot](../pybot/) / [bot4cj](../bot4cj/) 解析执行。

## 定位与管线

手写 `.mt` 矩阵脚本需要心算 10ms 栅格上的全通道快照，冗长且易错。mtc 让动作用命令式 Python 描述（`Action("s1", 10, 100)`），编译器负责展开为栅格矩阵：

```
script.py ──exec(注入命名空间)──► 事件收集 ──► 时间轴 IR ──► 10ms 栅格化 ──► out.mt
                                            （相对→绝对、重叠截断）
```

## 用法

```bash
cd mtc                                # 须在本目录运行（外层目录会被识别为 namespace package）
python -m mtc walk.py                 # 编译到 stdout（可管道：python -m mtc walk.py | python -m pybot --dry-run /dev/stdin）
python -m mtc walk.py -o walk.mt      # 写文件
```

也可设 `PYTHONPATH=mtc` 后在任意目录运行。诊断（编译告警/错误）走 stderr，stdout 保持纯净 .mt 文本。

## DSL 参考

脚本即 `.py`，`exec` 注入两类函数。

### 硬件声明（函数名 = .mt 块名，位置参数 = 表头列，严格一一对应）

```python
I2C("i2c-1", "/dev/i2c-1")
PCA9685("pca9685-0", "i2c-1", "0x40")
Servo("s1", 180, 0, 0, "pca9685-0", 0)      # name,max,min,offset,controller,channel
Speaker("spk", 2, 48000, 0.6, 5000, 20)     # name,channels,sample_rate,volumn,max_f,min_f
Camera("cam0", 640, 480, 30, 8)
MPU6050_BIND("imu0", "i2c-1", "0x68")
Alias("A", "angle")
register_block("ADS1115", ["name", "I2C", "addr"])   # 开放扩展（首列恒为 name）
```

### 时间轴指令（编译期求值，不产生 .mt 行）

| 函数 | 语义 |
|------|------|
| `Action(dev, delta, dur)` | 相对运动：当前游标处注册，dur ms 内走 delta |
| `SetTo(dev, angle, dur)` | 绝对运动（糖：delta = angle − 已注册目标值） |
| `Beep(freq, dur, dev="spk")` | 扬声器频率段：dur ms 内恒为 freq（阶跃段） |
| `Delay(ms)` | 游标推进固定量 |
| `Wait()` | 汇合：游标推进到所有在途运动的最晚完成时刻 |
| `Pose(dev, angle)` | 声明编译期初始姿态（不产生运动段，须在 Action 之前） |

`dur` / `Delay` 必须是正的 10 的倍数（.mt 栅格与 pybot 微步节拍均为 10ms）。

## 语义规则

- **非阻塞时间轴**：`Action`/`SetTo`/`Beep` 在当前游标处注册运动段并立即返回，`Delay`/`Wait` 才推进游标。不同设备段在同一时间窗重叠即并发运动。
- **重叠截断**：同通道新段与未完结末尾段重叠时，前段截断至新段起点，新段基准取前段**目标值**（编译期确定，不依赖运行时实际姿态），并产出告警（stderr）。
- **静止值（rest）**：姿态通道（舵机等）段外保持前段目标值；`spk.frequency` 通道段前/段间空隙/段后统一回落 0 = 静音（对齐 pybot speaker 静音语义：`freq<=0` 不出声仅保节拍）。
- **节拍列**：`spk.delay` 由编译器管理（恒 10），脚本不能直接写；无 Speaker 块则无节拍列（pybot 执行时行间隔不等待，编译器给出告警）。`spk.frequency` 无段时自动补恒 0 列（pybot 契约：frequency/delay 两列成对出现）。
- **栅格行语义**：格点 t=10,20,…,T，每行代表该 10ms 结束时刻的绝对姿态；pybot `run()` 对 delay=10 的行分解为 1 个微步且插值系数 1.0，即直接到位——与栅格语义严格等价。

## 示例

```python
Servo("s1", 180, 0, 0, "pca9685-0", 0)
Servo("s2", 180, 0, 0, "pca9685-0", 1)
Speaker("spk", 2, 48000, 0.6, 5000, 20)
Action("s1", 10, 100)     # s1 在 [0,100ms) 走 10°
Delay(50)                 # 游标推进到 50ms
Action("s2", 20, 50)      # s2 在 [50,100ms) 走 20°（第 6 行起步）
```

编译产物：

```
@Servo
name max min offset controller channel;
s1 180 0 0 pca9685-0 0;
s2 180 0 0 pca9685-0 1;
@

@Speaker
name channels sample_rate volumn max_f min_f;
spk 2 48000 0.6 5000 20;
@

@Action
s1.angle s2.angle spk.frequency spk.delay;
1 0 0 10;
2 0 0 10;
3 0 0 10;
4 0 0 10;
5 0 0 10;
6 4 0 10;
7 8 0 10;
8 12 0 10;
9 16 0 10;
10 20 0 10;
@
```

## 输出契约（与 pybot parser 对齐）

- `@块名` / `@` 定界；行 0 表头；行尾 `;`；列间空格分隔；相邻同名块合并为一个块多行。
- `@Action` 表头为 `设备.参数` 长名（恰含一个 `.`，Q05 校验），末列 `<speaker>.delay` 恒 10。
- 数值格式化：整数无小数点，小数至多 3 位。

## 测试

```bash
cd mtc && python -m pytest tests/ -q
```

含 e2e 闭环验证：编译产物经 pybot `MatrixParser` 零修改解析（列数一致、Q05 表头契约、栅格行数值逐一比对、roundtrip 自检）。pybot 与 mtc 的 `tests/` 目录同名，两套测试须分进程运行。

## 目录结构

```
mtc/
├── mtc/
│   ├── __init__.py      # 包入口（compile_script / CompileError）
│   ├── __main__.py      # CLI：python -m mtc script.py [-o out.mt]
│   ├── dsl.py           # DSL 命名空间 + 事件收集（Collector）
│   ├── timeline.py      # 时间轴 IR：Segment/Channel、重叠截断、rest 静止值
│   └── emit.py          # .mt 序列化：硬件块合并 + 10ms 栅格矩阵
└── tests/               # pytest（含 pybot parser e2e 闭环）
```
