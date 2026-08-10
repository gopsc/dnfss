# 进程机器（PMC · core）

> 进程托管服务 / 微服务编排中枢 · 可执行 `nidus.out` · systemd 服务 `Nidus`
> 版本：core v0.1.0（内嵌 confs 合并版）

## pmc 是一个什么项目？

PMC（Process Machine，进程机器）是一个可以创建、管理子进程以及模块的进程托管程序。你可以在它的托管下运行独立的业务模块，由 PMC 负责拉起与守护。它是整个 UniServe Framework 的**进程编排中枢**——读取 `services.yaml` 声明式配置，顺序拉起并托管全部业务子进程（fsys / dnfs2 / db2 / ca / gateway / waf / captcha2 / smtp / ai_review / aibox / redirect80），并提供本地 HTTP 管理接口与内嵌配置服务。

崩溃守护由 systemd（`Nidus.service`）负责，PMC 自身**不含**自动重启子进程逻辑。

### 为什么要做这个项目？

2024 年，作者想做一个树莓派机器人，准备用 Python 写控制程序，但当时 Python 的多线程能力较弱，于是想到使用子进程（子系统）来分担工作，自己动手写了一个进程托管程序。后来演进为整套轻量级微服务架构的底座。

## 模块文件结构

```
core/
├── Makefile            # 构建 / 安装（VERSION=0.1.0, APP_NAME=nidus）
├── services.yaml      # 声明式服务启动配置（name/cmd/args/env/depends_on/sleep_before）
├── Nidus.service       # systemd 单元（User=root, ExecStart=_run.sh）
├── _run.sh             # 启动：nidus.out -i . --serve 5200 --confs 8010
├── _set.sh             # 构建 + 创建 configs 符号链接 + make install
├── src/
│   ├── main.cpp        # 入口：双模式 + 信号 + taskPool 生命周期
│   ├── main/
│   │   ├── PPool.cpp           # 子进程池
│   │   ├── ProcessTask.cpp     # boost.process 子进程封装
│   │   ├── pmc_mtd.cpp         # list / kill / exec / parse_self_init_list
│   │   ├── subsys_call.cpp     # 跨机子系统调用（MQ 发令 + Pipe 回传）
│   │   ├── Tttask.cpp          # 消息队列通信任务
│   │   ├── HttpTask.cpp        # 管理 HTTP 接口（:5200）
│   │   └── ConfsTask.cpp       # 内嵌配置服务（:8010）
│   └── registry/
│       └── InitParser.cpp     # services.yaml 解析器
└── include/
    ├── main/*.hh       # PPool / ProcessTask / pmc_mtd / subsys_call / Tttask / HttpTask / ConfsTask
    ├── registry/InitParser.hh  # services.yaml 解析器
    ├── tt/Pipe.h       # IPC：命名管道（FIFO）
    ├── tt/Mq.h         # IPC：消息队列
    ├── logs/Logger.hpp # 单例日志
    └── cn/*.hpp        # 中文化编程宏
```

> 注：早期 README 提到的 `users/`、`extra/` 子模块目录已不再使用，模块托管统一改为通过 `services.yaml` 配置拉起各业务服务。

## 如何托管外部模块

编辑 `services.yaml`，在 `services` 列表中声明每个服务的 `name`、`cmd`、`args`、`env`、`depends_on`、`sleep_before` 等字段。PMC 启动时按声明顺序逐个拉起（先 `clr()` 清理已退出进程，再 `crtp()` 创建）。**启动顺序很重要**（redirect80 → waf → ca → gateway …）。

## 运行模式与命令行

PMC 支持两种工作模式：

1. **命令行子系统调用**（一次性，非服务模式）：
   - `--send <mq> --exec <cmd>`：向目标 PMC 的消息队列发启动命令并取回结果
   - `--send <mq> --list`：列出目标 PMC 运行的进程
   - `--send <mq> --kill <pid>`：杀死目标 PMC 的某个进程
2. **HTTP 服务模式**：`--serve <port>` 启动本地管理接口（默认 `127.0.0.1:5200`，纯 HTTP，因 WAF 已终止 TLS）。

此外：

- `-i <dir/file>` / `--init`：指定自启动配置（目录路径自动查找 `services.yaml`，或直接指定 `.yaml` 文件）
- `--confs <port>`：启用内嵌 confs 配置服务（默认 `127.0.0.1:8010`）
- `-h` / `--help`：打印帮助与 API MAP（`/list` `/exec` `/kill` 路由）
- `-v` / `--version`：打印版本号

实际启动命令（`_run.sh`）：

```bash
./nidus.out -i . --serve 5200 --confs 8010
```

**生命周期**：注册 `SIGINT`/`SIGTERM` → `signalHandler` 唤醒条件变量 `cv` → 主线程 `cv.Wait()` 返回后遍历 `taskPool` 调用 `stop()` 优雅退出。

## 架构：任务池模型

PMC 把所有常驻能力抽象为 `ITask`，用 `libth` 的 `Thread`（带 `stop/wake/loop/clear` 回调状态机）封装，统一加入 `taskPool`：

- **`Tttask`**（`tt/` 消息队列通信服务）：以进程 pid 为名创建 boost.interprocess 消息队列，循环 `recv` 指令，回调 `parse_data_from_mq`。
- **`HttpTask`**（`HttpTask.cpp`）：在 `127.0.0.1:port` 起 `pmc::net::HttpServer`（纯 HTTP，无 TLS），注册 `/list` `/exec` `/kill`。
- **`ConfsTask`**（`ConfsTask.cpp`）：起 `pmc::net::RpcServer`（pymsg），提供配置分发。

三者并列由主线程启停。

## 架构：进程托管核心

- **`PPool`**（`PPool.hh/.cpp`）：`std::list<std::shared_ptr<ProcessTask>>`，接口 `crtp()`（create process）/ `clr()`（清理已退出）/ `kill(idx)` / `operator[]`。**注意 kill/list 用容器内下标而非 pid，代码内有 FIXME 标注不自然。**
- **`ProcessTask`**（`ProcessTask.hh/.cpp`）：`boost::process::child` 封装，`start()` 直接 `make_unique<child>(cmd)`，`stop()` 调 `terminate()`。当前编译走非 `__NEW_VER`（boost.process v1）分支。
- **`pmc_mtd`** 命名空间（`pmc_mtd.cpp`）：
  - `list()` → 序列化所有子进程 `{index, pid, running, command}`。
  - `kill(pid)` → 按 pid 遍历 `ppool` 匹配后 `kill(i)`。
  - `exec(cmd)` → `ppool.crtp(cmd)`。
  - `parse_self_init_list(path)` → 经 `InitParser` 解析 `services.yaml`，按声明顺序 `clr()` + `crtp()` 拉起，支持 `sleep_before` 延时与 `env` 环境变量注入。

## 架构：进程间通信层（`include/tt/`）

跨进程 / 跨机的进程管理采用「消息队列发令 + 命名管道回传」：

- **`Mq`**（`tt/Mq.h`）：`boost::interprocess::message_queue`，名字 `/<name>`，容量 2048×100。发送端 `send(json)`，接收端 `try_receive`。
- **`Pipe`**（`tt/Pipe.h`）：FIFO 命名管道，路径 `/tmp/pst-<pid>`，`CREATOR` 负责 `mkfifo`/`unlink`。`openForRead(nonblock)` + `select` 超时 + `readPipe`。

指令 JSON：`{"pipe": "<回传管道名>", "type": "list"|"kill"|"exec", ...}`。

- 服务端：`parse_data_from_mq()` 收到 JSON → 按 `type` 调 `pmc_mtd::list/kill/exec` → 经 `Pipe` 写回结果。
- 客户端：`subsys_call`（subsys_call.cpp）：`send_*_and_recv_pipe(target, ...)` 先建 `Mq(target)` 发令，再开 `Pipe` 等回传（1 秒超时，失败返回 `"ERR"`）。
- `HttpTask` 的 `listProcesses/execCommand/killProcess` 在 `target` 非空时走 `subsys_call` 转发到目标机器 PMC，为空则本机 `pmc_mtd`。

## 架构：HTTP 管理接口

- 仅监听 `127.0.0.1:5200`，纯 HTTP（WAF 已终止 TLS，无证书）。
- 三个 POST 路由：`/list` `/exec` `/kill`，返回 `{"success", "message", "data"}`。
- **管理员鉴权**（`verify_admin_token`）：每个操作都先调 `ca /admin/verify`（认证 v0.9.0 起由 gateway 迁至 ca，RSA 验签 + is_admin + 过期检查），`Authorization` 头优先、回退 `q4_token` Cookie；ca 地址由 `GATEWAY_HOST`/`GATEWAY_PORT` 环境变量配置（沿用旧名，默认 `127.0.0.1:8443`）。ca 不可用或异常一律 `fail-closed` 返回 401。
- 命令执行（`exec`）与 pid 解析同时支持 query 参数与 JSON body。

## 内嵌 confs 配置服务（v0.1.0 起）

core 内置了统一配置服务（由 confs2 合并而来，pymsg RPC 协议，与 Python confs 完全兼容）：

```bash
./nidus.out -i . --serve 5200 --confs 8010
```

- `--confs <port>`：启用内嵌 confs 配置服务（get/list/reload/health 四个路由）
- 不传 `--confs` 时行为与原版完全一致
- 配置文件目录默认为 `./configs`（`_set.sh` 会创建指向 `../confs/configs` 的符号链接）
- 环境变量：`CONFS_ADDR` / `CONFS_PORT` / `CONFS_WORKERS` / `CONFS_DIR`

`ConfsTask`（`ConfsTask.cpp`）由 confs2 移植，业务逻辑逐项对齐：

- **`ConfigStore`**：线程安全（`mutex`），内存缓存 + `mtime` 变化自动重载；`get()` 在锁内 canonical 解析 + 前缀校验 + 存在性检查（无 TOCTOU）；扩展名白名单（`.ini/.conf/.cfg/.json/.yaml/.yml/.toml/.txt`）+ 1 MiB 上限防路径遍历/DoS。
- **路由**：
  - `get`：取全文；带 `key` 时 INI 用 `Section.option` 取值（Section 大小写敏感、Option 不敏感）、JSON 用点路径取值。
  - `list`：列目录全部配置元信息（name/size/mtime/cached）。
  - `reload`：指定 name 重载，或省略 name 清空全部缓存。
  - `health`：status/config_dir/cached/uptime。
- **`ConfsLogC`**（`ConfsLogC.hpp`）：基于 `libmsg::AsyncQueue`，50 条/2 秒攒批异步写 `db:8005` 的 `Logs` 表，`DB_HOST`/`DB_PORT` 可配（默认 127.0.0.1:8005），fail-safe 静默丢弃；含日志注入防护（`sanitize_for_log` 过滤控制字符）。

## 支撑组件

- **`Logger`**（`logs/Logger.hpp`）：单例，控制台输出，支持 DEBUG/INFO/WARNING/ERROR/CRITICAL 级别（注释标注为 DeepSeek 创作，TODO 改为文件存储）。
- **`include/cn/`**：中文化编程宏（`中文化.hpp` 等），提供 C++ 关键字中文别名。
- **依赖库**（Makefile）：`libth` `libhttp`/`libhttpclient` `libmsg` `liblogc` `libcurl` + boost（`program_options`/`json`/`thread`/`filesystem`）+ `yaml-cpp` + `ssl`/`crypto`/`pthread`。

## PMC 的安装方式

```bash
# 1. 安装依赖
# 安装 UniServe 的 libth 包
git clone https://github.com/gopsc/UniServe_libth
cd UniServe_libth
sudo bash _set.sh

# 安装别的依赖
sudo apt update
sudo apt install git g++ make
sudo apt install libboost-all-dev
sudo apt install libyaml-cpp-dev
#sudo apt install libssl-dev
#sudo apt install libcurl4-openssl-dev
#sudo apt install libcurl4-gnutls-dev

# 2. 构建并安装 core
git clone https://github.com/gopsc/UniServe_core
cd UniServe_core
make               # 等价于 make clear && make
sudo make install  # 装入 /usr/local/bin/nidus + systemd
```

> `make install` 会把 `services.yaml` 拷到 `/etc/Nidus/`、`Nidus.service` 拷到 `/etc/systemd/system`，并 `daemon-reload` + `enable` + `restart`。

本仓库内的快捷方式：

```bash
bash core/_set.sh   # make clear && make + 建 configs 符号链接 + make install
bash core/_run.sh   # 直接 ./nidus.out -i . --serve 5200 --confs 8010
```

## 加密通信机制（废弃）

~~PMC 在启动时生成一个 AES 密钥，然后用一个 RSA 公钥将其加密发送给访问者，之后访问者与客户端之间的交流全部用此 AES 密钥加密。~~ 该机制已废弃，当前管理接口与子系统调用改用 `gateway` 的 Token 验签（见「HTTP 管理接口」与「进程间通信层」章节）。

## 如何使用并发机器运行程序

首先应该在模块中创建一个启动脚本，脚本中应该完成工作目录的切换、虚拟环境的激活，以及程序的启动。在 `services.yaml` 中声明该脚本后，PMC 会按声明顺序执行这些启动脚本，从而把多个业务模块拉起并纳入托管。

## 已知待办与风险

1. **无子进程自动重启**：PMC 崩溃守护靠 systemd；单个业务进程退出后 PMC 不会拉起它（仅保留在 `ppool` 中标记非运行）。
2. **`PPool` 用下标而非 pid 索引**：`kill(idx)` 语义不自然，FIXME 仍在。
3. **内存泄漏 TODO**：`HttpTask`/`ConfsTask` 的 `start()` 获取资源后未确认释放（代码注释标注）。
4. **`/tmp/pst-*` FIFO 残留**：`subsys_call` 异常路径可能留下管道文件。
5. **管理接口强依赖 gateway**：`/admin/verify` 不可用时所有管理操作 fail-closed 拒绝（安全优先，但运维注意 gateway 必须在线）。
6. **`exec` 命令注入面**：`pmc_mtd::exec` 直接 `boost::process::child(cmd)` 以 shell 执行，仅在已通过管理员鉴权后可用——务必保证鉴权不被绕过。

## 上手要点

- 调整自启动服务/顺序：编辑 `services.yaml`（在 `services` 列表中增删条目，调整声明顺序）。
- 管理接口调用：必须携带 admin token（经 gateway 签发），例如带 `Authorization: Bearer <token>` 或 `q4_token` Cookie 访问 `:5200`。
- 构建：`core/_set.sh`（会先创建 `configs → ../confs/configs` 符号链接，保证内嵌 confs 零迁移）；`make clear && make` 后 `make install` 装入 systemd。
