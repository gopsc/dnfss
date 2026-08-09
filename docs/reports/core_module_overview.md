# core 模块架构总览（进程托管服务 PMC / Nidus）

> 适用版本：core v0.1.0（与 confs 内嵌合并版）
> 阅读对象：新接手 blackbird 项目的开发者

## 1. 模块定位

`core`（可执行 `nidus.out`）是整个 UniServe Framework 的**进程编排中枢**，负责：

1. 读取 `init.txt` 自启动列表，顺序拉起并托管全部业务子进程（fsys / dnfs2 / db2 / ca / gateway / waf / captcha2 / smtp / ai_review / aibox / redirect80）。
2. 提供本地 HTTP 管理接口（`127.0.0.1:5200`），支持 list / exec / kill 受管子进程。
3. 内嵌统一配置服务 `ConfsTask`（pymsg RPC，`:8010`），平替独立 confs2 进程。

崩溃守护由 systemd（`Nidus.service`）负责，core 自身**不含**自动重启子进程逻辑。

## 2. 入口与运行模式（`src/main.cpp`）

命令参数（boost::program_options）：

| 参数 | 作用 |
|------|------|
| `-i <file>` | 解析自启动列表 `init.txt` |
| `--serve <port>` | 启动 HTTP 管理接口（默认 5200，仅 loopback） |
| `--confs <port>` | 启用内嵌 confs 配置服务（默认 8010） |
| `--send/--exec/--list/--kill` | CLI 子系统调用模式（一次性，非服务模式） |
| `-h/-v` | 帮助 / 版本 |

生命周期：注册 `SIGINT`/`SIGTERM` → `signalHandler` 唤醒条件变量 `cv` → 主线程 `cv.Wait()` 返回后遍历 `taskPool` 调用 `stop()` 优雅退出。

实际启动命令见 `_run.sh`：

```bash
./nidus.out -i init.txt --serve 5200 --confs 8010
```

## 3. 任务池模型（`taskPool` + `ITask`）

core 把所有常驻能力抽象为 `ITask`，用 `libth` 的 `Thread`（带 `stop/wake/loop/clear` 回调状态机）封装：

- **`Tttask`**（`tt/` 消息队列通信服务）：以进程 pid 为名创建 boost.interprocess 消息队列，循环 `recv` 指令，回调 `parse_data_from_mq`。
- **`HttpTask`**（`HttpTask.cpp`）：在 `127.0.0.1:port` 起一个 `pmc::net::HttpServer`（纯 HTTP，无 TLS，因为 WAF 已终止 TLS），注册 `/list` `/exec` `/kill`。
- **`ConfsTask`**（`ConfsTask.cpp`）：起 `pmc::net::RpcServer`（pymsg），提供配置分发。

三者并列加入 `taskPool`，统一由主线程启停。

## 4. 进程托管核心（`PPool` / `ProcessTask` / `pmc_mtd`）

- `PPool`：`std::list<std::shared_ptr<ProcessTask>>`，接口 `crtp()`（create process）/ `clr()`（清理已退出）/ `kill(idx)` / `operator[]`。**注意 kill/list 用容器内下标而非 pid，代码内有 FIXME 标注不自然。**
- `ProcessTask`：`boost::process::child` 封装，`start()` 直接 `make_unique<child>(cmd)`，`stop()` 调 `terminate()`。当前编译走非 `__NEW_VER`（boost.process v1）分支。
- `pmc_mtd` 命名空间（`pmc_mtd.cpp`）：
  - `list()` → 序列化所有子进程 `{index, pid, running, command}`。
  - `kill(pid)` → 按 pid 遍历 `ppool` 匹配后 `kill(i)`。
  - `exec(cmd)` → `ppool.crtp(cmd)`。
  - `parse_self_init_list(path)` → 逐行读 `init.txt`，去掉 `#` 注释与空行，`ppool.clr()` 后 `crtp(row)`。**启动顺序很重要**（redirect80 → waf → ca → gateway …）。

## 5. 进程间通信层（`include/tt/`）

跨进程 / 跨机的进程管理采用「消息队列发令 + 命名管道回传」：

- **`Mq`**（`tt/Mq.h`）：`boost::interprocess::message_queue`，名字 `/<name>`，容量 2048×100。发送端 `send(json)`；接收端 `try_receive`。
- **`Pipe`**（`tt/Pipe.h`）：FIFO 命名管道，路径 `/tmp/pst-<pid>`，`CREATOR` 负责 `mkfifo`/`unlink`。`openForRead(nonblock)` + `select` 超时（`waitForRead`）+ `readPipe`。

指令 JSON 格式：`{"pipe": "<回传管道名>", "type": "list"|"kill"|"exec", ...}`。

- 服务端：`parse_data_from_mq()` 收到 JSON → 按 `type` 调 `pmc_mtd::list/kill/exec` → 经 `Pipe` 写回结果。
- 客户端：`subsys_call` 命名空间（`subsys_call.cpp`）：`send_*_and_recv_pipe(target, ...)` 先建 `Mq(target)` 发令，再开 `Pipe` 等回传（1 秒超时，失败返回 `"ERR"`）。

`HttpTask` 的 `listProcesses/execCommand/killProcess` 在 `target` 非空时走 `subsys_call` 转发到目标机器的 pmc，为空则本机 `pmc_mtd`。

## 6. HTTP 管理接口（`HttpTask` + `process_http_server`）

- 仅监听 `127.0.0.1:5200`，纯 HTTP（WAF 已终止 TLS，无证书）。
- 三个 POST 路由：`/list` `/exec` `/kill`，返回 `{"success", "message", "data"}`。
- **管理员鉴权**（`verify_admin_token`）：每个操作都先调 `gateway /admin/verify`（gateway 是 Token 签发/验签唯一权威，RSA 验签 + is_admin + 过期检查），Authorization 头优先、回退 `q4_token` Cookie；gateway 地址由 `GATEWAY_HOST`/`GATEWAY_PORT` 配置（默认 `127.0.0.1:8443`）。gateway 不可用或异常一律 `fail-closed` 返回 401。
- 命令执行（`exec`）与 pid 解析同时支持 query 参数与 JSON body。

## 7. 内嵌配置服务（`ConfsTask` + `ConfigStore`）

`ConfsTask.cpp` 由 confs2 移植而来，pymsg RPC，协议与 Python confs 字节级兼容，消费方零改动。

- `ConfigStore`：线程安全（`mutex`），内存缓存 + `mtime` 变化自动重载；`get()` 在锁内 canonical 解析 + 前缀校验 + 存在性检查（无 TOCTOU）；扩展名白名单（`.ini/.conf/.cfg/.json/.yaml/.yml/.toml/.txt`）+ 1 MiB 上限防路径遍历/DoS。
- 路由（与 confs2 逐项对齐）：
  - `get`：取全文；带 `key` 时 INI 用 `Section.option` 取值（Section 大小写敏感、Option 不敏感）、JSON 用点路径取值。
  - `list`：列目录全部配置元信息（name/size/mtime/cached）。
  - `reload`：指定 name 重载，或省略 name 清空全部缓存。
  - `health`：status/config_dir/cached/uptime。
- `ConfsLogC`（`ConfsLogC.hpp`）：基于 `libmsg::AsyncQueue`，50 条/2 秒攒批异步写 `db:8005` 的 `Logs` 表，`DB_HOST`/`DB_PORT` 可配（默认 127.0.0.1:8005），fail-safe 静默丢弃；含日志注入防护（`sanitize_for_log` 过滤控制字符）。

## 8. 支撑组件

- `Logger`（`logs/Logger.hpp`）：单例，控制台输出，支持 DEBUG/INFO/WARNING/ERROR/CRITICAL 级别（注释标注为 DeepSeek 创作，TODO 改为文件存储）。
- `include/cn/`：中文化编程宏（`中文化.hpp` 等），提供 C++ 关键字中文别名。
- 依赖库（Makefile）：`libth` `libhttp`/`libhttpclient` `libmsg` `libcurl` + boost（`program_options`/`json`/`thread`/`filesystem`）+ `ssl`/`crypto`/`pthread`。

## 9. 已知待办与风险（源码内 FIXME / 设计取舍）

1. **无子进程自动重启**：core 崩溃守护靠 systemd；单个业务进程退出后 core 不会拉起它（仅保留在 `ppool` 中标记非运行）。
2. **`PPool` 用下标而非 pid 索引**：`kill(idx)` 语义不自然，FIXME 仍在。
3. **内存泄漏 TODO**：`HttpTask`/`ConfsTask` 的 `start()` 获取资源后未确认释放（代码注释标注）。
4. **`/tmp/pst-*` FIFO 残留**：`subsys_call` 异常路径可能留下管道文件。
5. **管理接口强依赖 gateway**：`/admin/verify` 不可用时所有管理操作 fail-closed 拒绝（安全优先，但运维注意 gateway 必须在线）。
6. **`exec` 命令注入面**：`pmc_mtd::exec` 直接 `boost::process::child(cmd)` 以 shell 执行，仅在已通过管理员鉴权后可用——务必保证鉴权不被绕过。

## 10. 上手要点

- 调整自启动服务/顺序：编辑 `init.txt`（注释用 `#`）。
- 管理接口调用：必须携带 admin token（经 gateway 签发），例如带 `Authorization: Bearer <token>` 或 `q4_token` Cookie 访问 `:5200`。
- 构建：`core/_set.sh`（会先创建 `configs → ../confs/configs` 符号链接，保证内嵌 confs 零迁移）；`make clear && make` 后 `make install` 装入 systemd。
