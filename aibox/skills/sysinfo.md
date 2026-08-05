# sysinfo - 获取系统运行时间、CPU、内存、磁盘、网络、负载、温度、电池等状态信息的技能

## 功能概述
本技能用于查询 Linux 服务器/本机的系统状态信息。它通过 `psutil` 库和系统命令采集数据，支持按需获取单项或组合信息，支持文本和 JSON 两种输出格式。

**依赖要求**：需要 Python3 环境，并安装 `psutil` 库（`pip install psutil`）。若未安装，可改用 `/proc`、`free`、`df`、`uptime`、`top` 等系统命令替代（见下文备选方案）。

## 信息类型及对应获取方法

### 1. 系统基本信息 (system)
- 系统名称、主机名、内核版本、硬件架构、处理器型号、Python 版本
- 命令：`uname -a`、`hostname`、`python3 --version`

### 2. 运行时间 (uptime)
- 开机时间、已运行时长（天/时/分/秒）
- 命令：`uptime -s`（开机时间）、`uptime`（运行时长）

### 3. CPU 信息 (cpu)
- 物理核心数、逻辑核心数、实时使用率、当前频率
- 命令：`lscpu`、`nproc`、`top -bn1 | head -5`、`cat /proc/cpuinfo`

### 4. 内存信息 (memory)
- 总内存、可用内存、已用内存、内存使用率、交换分区大小及使用率
- 命令：`free -h`

### 5. 指定磁盘信息 (disk)
- 指定路径（默认 `/`）的总空间、已用、可用、使用率
- 命令：`df -h /路径`

### 6. 所有磁盘分区 (disks)
- 所有挂载点的文件系统、总空间、已用、可用、使用率
- 命令：`df -hT`

### 7. 进程信息 (process)
- 进程总数、运行中进程数、睡眠进程数
- 命令：`ps -e | wc -l`、`ps -eo stat | grep -c '^R'`、`ps -eo stat | grep -c '^S'`

### 8. 网络信息 (network)
- 累计发送/接收的数据量（MB）、发送/接收包数
- 命令：`cat /proc/net/dev`、`ip -s link`

### 9. 系统负载 (load)
- 1/5/15 分钟负载及每核心负载
- 命令：`cat /proc/loadavg`、`uptime`

### 10. 温度信息 (temperature)
- CPU/主板等传感器温度（仅支持部分硬件）
- 命令：`sensors`（需安装 lm-sensors）、`cat /sys/class/thermal/thermal_zone*/temp`

### 11. 电池信息 (battery)
- 电量百分比、是否充电中、剩余时间（仅笔记本）
- 命令：`cat /sys/class/power_supply/BAT0/capacity`、`cat /sys/class/power_supply/BAT0/status`

## 使用方式

### 方式一：直接运行原版 Python 脚本（若旧版技能仍可用）
```bash
# 获取系统基本信息
python3 /opt/micro_servers/aibox/skills/info.py --info system

# 获取 CPU + 内存 + 磁盘信息
python3 /opt/micro_servers/aibox/skills/info.py --info cpu --info memory --info disk

# 指定磁盘路径
python3 /opt/micro_servers/aibox/skills/info.py --info disk --disk-path /home

# 获取所有信息（JSON 格式，便于程序解析）
python3 /opt/micro_servers/aibox/skills/info.py --info all --format json

# 获取负载 + 温度 + 电池
python3 /opt/micro_servers/aibox/skills/info.py --info load --info temperature --info battery
```

### 方式二：使用系统命令（无 psutil 时的备选方案）
```bash
# 综合系统信息
uname -a && hostname && uptime && free -h && df -hT && nproc
```

## 参数说明（原版脚本）
| 参数 | 简写 | 说明 |
|------|------|------|
| `--info` / `-i` | 可多次 | 信息类型：system/uptime/cpu/memory/disk/disks/process/network/load/temperature/battery/all |
| `--disk-path` / `-d` | 默认 `/` | 磁盘检查路径 |
| `--format` / `-f` | 默认 text | 输出格式：text 或 json |
| `--help` / `-h` | - | 显示帮助与示例 |

## 输出示例（文本格式）
```
系统信息:
   系统: Linux
   主机名: my-server
   架构: x86_64
CPU信息:
   物理核心数: 4
   逻辑核心数: 8
   使用率: 12.5%
内存信息:
   总内存: 15.58 GB
   可用内存: 8.21 GB
   内存使用率: 45.2%
```

## 典型使用场景
1. **运维巡检**：配合定时任务，每日采集系统资源快照，监控负载与内存趋势
2. **故障排查**：磁盘空间告警时，快速定位使用率最高的挂载点
3. **性能评估**：获取 CPU 核心数、内存大小、网络吞吐等硬件基线数据
4. **上报集成**：使用 `--format json` 输出结构化数据，便于脚本解析和上报

## 注意事项
- 温度/电池信息仅在支持对应硬件的系统上可用，服务器通常无电池
- CPU 使用率为瞬时采样（间隔 1 秒），如需平均值建议多次采样
- 网络数据为累计值，对比时需记录时间差
