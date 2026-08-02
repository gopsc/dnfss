# 进程机器（PMC）
这是一个可以创建、管理子进程以及模块的程序。

## pmc是一个什么项目？

这是一个可扩展的模块，可以在文件夹下建立单独的模块，pmc核心模块可以托管运行这些模块。


## 为什么要做一个这样的项目?

2024年的时候，我想做一个树莓派机器人，准备用python来写控制程序，可是当时python的多线程能力较弱，所以想到使用子系统，就自己写了一个进程托管程序


## 项目的文件结构是怎样的？

users用户可以在目录下创建子模块，extra下面还有一些额外的模块。

请留意模块的README文件


## LCI 帧缓冲图形界面

LCI（Linux Chinese Interface）是基于 Linux 帧缓冲设备（`/dev/fb0`）的图形界面系统，无需 X11 或 Wayland，直接操作显存，专为树莓派等嵌入式环境设计。

### 图形架构

```
FrameBuf          -- 帧缓冲设备封装，支持旋转(0~3)和字号设置
  └─ PoolArea     -- 容器区域，管理子区域树，支持 add() 嵌套和 flush() 刷新
       ├─ ShapeArea   -- 形状区域，支持单个字符绘制
       │    └─ CharShape -- 字符形状，指定宽高和位置，Draw() 绘制到 ShapeArea
       └─ PoolArea    -- 递归嵌套，形成布局树
```

### 布局体系

界面采用三层布局：

```
┌─────────────────────────────────────┐
│  topArea         标题栏              │  ← 绘制 "CPU" 红色大字
│    └─ ShapeArea: CharShape 'C','P','U' │
├─────────────────────────────────────┤
│  mainArea        主体区域            │  ← 绿色坐标轴 + 实时折线图
│    └─ tableArea                     │
│       蓝色折线: 内存使用率            │
│       红色折线: CPU 使用率            │
├─────────────────────────────────────┤
│  bottomArea      底部状态栏（预留）    │
└─────────────────────────────────────┘
```

### 绘制原语

| 操作 | 用途 |
|------|------|
| `rectangle_fill(x, y, w, h, color)` | 矩形填充（清屏/背景） |
| `line(x0, y0, x1, y1, color)` | 画线（折线图、坐标轴） |
| `CharShape(w, h, x, y, ch).Draw(area, color)` | 字符绘制 |
| `flush(area, bool)` | 刷新区域到屏幕 |

### 实时数据可视化

每秒采集一次系统数据并绘制折线图：
- 通过 `/proc/stat` 读取 CPU 使用率（红色折线）
- 通过 `sysinfo()` 读取内存使用率（蓝色折线）
- 保留最近 40 个数据点，平滑滚动

### 构建

```bash
make lci
```

构建依赖 `-ljpeg -lpng`，需要帧缓冲设备和 I2C/摄像头等硬件支持。

### 运行

```bash
./lci --lci --rotate=0 --fontsize=18
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--rotate` | 屏幕旋转角度 (0~3) | 0 |
| `--fontsize` | 字号大小 | 18 |

> **注意**: `include/lci/` 和 `src/lci/` 目录下的图形核心文件（FrameBuf, Drawable, PoolArea, ShapeArea, CharShape, Color 等）当前处于开发中，尚未提交到仓库。

## 如何托管外部模块

编辑启动列表程序，常为init.txt


## PMC的安装方式
``` bash   
# 1.安装依赖
sudo apt update
sudo apt install git g++ make
#sudo apt install python3 python3-pip python3-venv
#sudo dnf install python3-virtualenv
sudo apt install libboost-all-dev
#sudo apt install libssl-dev
#sudo apt install cimg-dev
#sudo apt install libcurl4-openssl-dev
#sudo apt install libcurl4-gnutls-dev
#sudo apt install libargs-dev
#sudo apt install libcpp-httplib-dev

2. 创建项目
git clone https://github.com/gopsc/pmc
cd pmc  # 这是pmc模块
make pmc
#sudo make install
```

## 加密通信机制（废弃）
PMC在启动时生成一个AES密钥，然后用一个RSA公钥将其加密发送给访问者。

然后访问者与客户端之间的交流全部用此AES密钥加密


## 如何使用并发机器运行程序
首先应该在模块中创建一个启动脚本，脚本中应该完成工作目录的切换、虚拟环境的激活，以及程序的启动。

## 需要创建启动脚本（废弃）
```bash
# _run.sh
# 启动脚本 - 示例
# 需要执行权限

#!/bin/bash

# 切换工作目录
cd "$(dirname "$0")" || exit

# 准备工作（安装依赖）
#bash ./_set.sh

# 准备工作（比如激活虚拟环境）
#source ./.env/bin/activate

# 执行程序前一定要加exec 否则杀不死
exec python main.py

```


