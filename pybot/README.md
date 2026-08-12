# pybot

机器人 `.mt` 动作脚本解析器 —— [bot4cj](../bot4cj/)（仓颉版）的 Python 迁移版。

驱动大顺（Dashun）蜘蛛机器人（树莓派 aarch64，10 舵机 + 2 云台，
PCA9685 PWM 控制器 + MPU6050 姿态传感器）。

迁移方案与审计修复映射见
[docs/reports/bot4cj_python_migration_dev_doc.md](../docs/reports/bot4cj_python_migration_dev_doc.md)。

## 特性

- **零第三方依赖**：纯标准库（fcntl / struct / threading / wave / argparse），
  树莓派上 `git pull` 即部署，无构建步骤。
  （picamera2 为可选依赖，仅 `@Camera` 摄像头块需要，树莓派 OS 预装，勿 pip 安装）
- **与仓颉版逐语义对齐**：FSM 解析器、占位符递归、Servo 校准算法、
  PCA9685 寄存器序列均与 bot4cj 一致（Mock 总线 journal 可逐字节 diff）。
- **Python 插件替代 Lua**：`modules/<块名>.py` 定义
  `build(ctx) / call(ctx) / destroy(ctx)`，消除 JSON 深拷贝 + 五层 FFI 桥接。
- **审计修复**：bot4cj 审计 15 项发现（S01~S08 / Q01~Q07）全部修复，
  详见迁移文档 §5。

## 用法

```bash
cat scripts/std.mt | ./_run.sh              # 与 bot4cj 相同的管道用法
./_run.sh scripts/std.mt                    # 位置参数（增强）
./_run.sh --dry-run scripts/std.mt          # Mock 总线，动作序列到 stdout
./_run.sh --test roundtrip in.mt out.mt     # 解析↔序列化往返自检
./_run.sh --verbose scripts/std.mt          # DEBUG 日志（stderr）
```

`-i` / `-o` 参数位保留（reserved），未来对接 [actpred](../actpred/) 动作预测。

## 目录

```
pybot/              # 包：parser / robot / hardvars / plugin / main
pybot/hd/           # 硬件层：i2c / spi / servo / kalman / mpu6050 / speaker / camera
modules/            # 硬件驱动插件：PCA9685 / SPI / ADS1115 / ADS1256
scripts -> ../bot4cj/scripts    # .mt 脚本与仓颉版共享
tests/              # pytest 单元测试（Mock 总线，无需硬件）
```

插件查找顺序：`$PYBOT_MODULES_PATH`（冒号分隔）→ 包内置 `modules/` → `./modules/`。

## 开发

```bash
./_set.sh                       # venv + pytest 全量测试
venv/bin/python -m pytest tests/ -v
```

## .mt 脚本格式

见 [bot4cj/README.md](../bot4cj/README.md) 与 `scripts/std.mt`。
格式要点：`@块名` 开块、行 0 为表头、`;` 结行、`#` 注释、
`-` 占位符（非首行取上一行同列值；首行数值列解析为 0）、`@` 收块。

新增块（无仓颉版对应）：

```
@Camera
 name  width  height  framerate  capacity;
 cam0    1920     1080       30         4;
@
```

`build` 启动后台抓帧线程，维护容量为 `capacity` 的有界帧队列（满丢最旧帧）；
`play` 返回最新帧 `(时间戳, frame)`。
