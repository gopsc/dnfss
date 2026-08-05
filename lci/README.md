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

### 整体架构

```
应用层    mainLci.cpp (LciTask)
          ├── 线程管理 (Fsm → CommonThread → Thread)
          ├── 终端管理 (Term: chvt + 隐藏光标)
          └── 图形界面 (三层布局)

图形层    PoolArea (树形容器)
          ├── ShapeArea → CharShape (矢量字符)
          ├── PoolArea    (递归嵌套)
          ├── CmdArea → InputBox (命令行交互)
          └── 图层区域     (CImg 图片渲染)

绘制层    Drawable (抽象基类)
          ├── FrameBuf  → /dev/fb0 (mmap 显存)
          └── Area      → 代理绘制 (坐标偏移)

辅助层    Color / Rectangle / 字体绘制器
          utf8↔ansi / 中文化.hpp / KeyboardEvent
```

### 核心类层次

#### 绘制层

| 类 | 头文件 | 职责 |
|---|--------|------|
| `Drawable` | `include/lci/Drawable.h` | 抽象基类，提供 point/line/rectangle/rectangle_fill 绘制原语，内置旋转支持(0/90/180/270) |
| `FrameBuf` | `include/lci/FrameBuf.h` | 帧缓冲实现，`mmap` 映射 `/dev/fb0`，支持 16-bit(RGB565) 和 32-bit(RGB888) 色深 |
| `Area` | `include/lci/Area.h` | 区域抽象，代理绘制操作时自动叠加偏移量 `(this->x, this->y)` |

`FrameBuf` 构造函数通过 `ioctl FBIOGET_VSCREENINFO/FSCREENINFO` 获取屏幕参数，区分虚拟分辨率和物理分辨率，计算 `line_jump`（行字节跨度）处理行缓冲过量问题。

#### 布局层

| 类 | 头文件 | 职责 |
|---|--------|------|
| `PoolArea` | `include/lci/PoolArea.h` | 容器区域，`unordered_map<string, shared_ptr<Area>>` 管理命名子区域 |
| `ShapeArea` | `include/lci/ShapeArea.h` | 形状集合，`vector<shared_ptr<Shape>>` 管理形状对象 |
| `Shape` | `include/lci/Shape.h` | 形状抽象基类，定义 `(w,h,x,y)` 和 `Draw(Drawable&, Color)` |
| `CharShape` | `include/lci/CharShape.h` | 字符形状，使用矢量字体绘制器渲染单个字符 |

#### 交互组件

| 类 | 头文件 | 职责 |
|---|--------|------|
| `CmdArea` | `include/lci/CmdArea.h` | 命令行区域，支持 ANSI 转义码解析（颜色、换行、制表符），文本自动换行和触底滚动 |
| `InputBox` | `include/lci/InputBox.h` | 输入框，渲染用户输入并绘制光标下划线 |
| `KeyboardEvent` | `include/lci/KeyboardEvent.h` | 键盘输入，扫描 `/dev/input/event*`，`select()` 非阻塞轮询 |

`CmdArea` 解析 ANSI 颜色码：`\033[32m`（绿色）、`\033[34m`（蓝色）、`\033[0m`（白色复位）。当文本超出可视区域时自动向上滚动。

#### 图片渲染

`图层区域` (`include/lci/图层区域.hpp`) 基于 CImg 库，支持 JPEG/PNG 图片渲染：
- 按区域长宽比自适应缩放（以长边或宽边为标准）
- 彩色图转灰度图后逐像素绘制
- 灰度值映射到当前画笔颜色，实现单色图片渲染

### 矢量字体系统

字体渲染器 (`include/lci/字体绘制器.h`) 使用内置矢量字库：

- `zzhi[]` / `zzhinumber`：字符索引表，存储 Unicode 码点
- `ttu[]` / `ttunumber`：矢量笔画数据，每对字节编码一个坐标点，最低位标记曲线/图像结束
- `drawvfont()` 在字库中查找字符索引，定位笔画数据，调用 `drawvect()` 将矢量坐标缩放后逐段绘制

字体数据位于 `src/lci/ku500.cpp`（约 20k 行），为 GB2312 编码的中文字库。

### 编码转换

| 类 | 头文件 | 职责 |
|---|--------|------|
| `utf8` | `include/lci/utf8.h` | UTF-8 字节长度检测 + `iconv` 转 GB18030 |
| `ansi` | `include/lci/ansi.h` | GB2312 字符宽度判断（单字节 ASCII / 双字节汉字） |

### 线程架构

| 组件 | 头文件 | 职责 |
|------|--------|------|
| `Fsm` | `include/lci/fsm.h` | 有限状态机：SSHUT → SSTOP → SSTART → SRUNNING，mutex + condition_variable 线程安全 |
| `ThreadInterface` | `include/lci/ThreadInterface.h` | 四事件接口：StopEvent / WakeEvent / LoopEvent / ClearEvent |
| `CommonThread` | `include/lci/CommonThread.h` | 托管线程，构造函数直接启动 `std::thread`，FSM 驱动生命周期 |
| `p_th` | `include/lci/p_th.h` | 打印线程，`select` 读取 PTY 输出，渲染到 CmdArea |

线程生命周期：`StopEvent`（挂起等待）→ `WakeEvent`（初始化资源）→ `LoopEvent`（循环执行）→ `ClearEvent`（清理资源）→ 回到 StopEvent 或退出。

### 中文化编程

`include/cn/中文化.hpp` 提供 C++ 关键字的中文宏定义，允许使用中文编写代码：

```cpp
使用 命名空间 qing;
类 图层区域: 公开的 Area {
    如果(长为标准) {
        auto fixed = scaleByWidth(this->img, this->w);
    } 否则 {
        auto fixed = scaleByHeight(this->img, this->h);
    }
};
```

支持的中文关键字包括：`类`/`公开的`/`私有的`/`如果`/`否则`/`返回`/`空类型`/`静态的`/`虚拟的` 等。

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
| `point(x, y, color)` | 绘制像素点 |
| `line(x0, y0, x1, y1, color)` | 画线（参数插值算法） |
| `rectangle(x, y, w, h, color)` | 矩形边框 |
| `rectangle_fill(x, y, w, h, color)` | 矩形填充（清屏/背景） |
| `drawvfont(x, y, unicode, color)` | 矢量字体绘制字符 |
| `flush(area, bool)` | 刷新区域到屏幕 |

### 实时数据可视化

每秒采集一次系统数据并绘制折线图：
- 通过 `CpuMonitor` 读取 CPU 使用率（红色折线）
- 通过 `getMemoryUsageWithSysinfo()` 读取内存使用率（蓝色折线）
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

> **注意**: 运行需要 `/dev/fb0` 帧缓冲设备，通常在树莓派等嵌入式 Linux 环境下可用。程序启动时通过 `chvt` 切换到 tty8，退出时切回 tty1。

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


