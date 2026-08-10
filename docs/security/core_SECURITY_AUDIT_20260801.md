# core（Nidus）模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `src/main/`、`src/shared/`  
**代码版本**: dev_qing 分支

**复审日期**: 2026-08-10  
**复审范围**: `src/main.cpp`、`src/main/`（含新增 `ConfsTask.cpp`）、`src/registry/InitParser.cpp`、`include/`、`Makefile`、`Nidus.service`、`_run.sh`、`services.yaml` 及运行现场（`/dev/shm`、`/etc/Nidus/`）  
**复审结论**: HTTP 管理面 P0 鉴权缺失已修复（ca `/admin/verify` fail-closed 链路）；但**部署面新发现 P1 提权路径**（`services.yaml` 组可写 + root 运行）、**PPool 无锁并发竞态**（P1），且 MQ 通道无认证、信号处理、进程池上限、core dump 等旧问题仍开放。详见下文「2026-08-10 复审」章节。

---

## 修复进度（截至 2026-08-10 复审）

**原有 8 项问题**

| 优先级 | 总数 | 已修复 | 部分修复 | 未修复 |
|--------|------|--------|----------|--------|
| P0 | 2 | 0 | 2 | 0 |
| P1 | 3 | 0 | 2 | 1 |
| P2 | 3 | 0 | 0 | 3 |
| **合计** | **8** | **0** | **4** | **4** |

**2026-08-10 复审新增 8 项**

| 优先级 | 总数 | 编号 |
|--------|------|------|
| P1 | 2 | N1、N2 |
| P2 | 4 | N3、N4、N5、N8 |
| P3 | 2 | N6、N7 |

---

## 🔴 P0 — 严重问题

### P0-1: PMC HTTP 管理接口无认证，可执行任意系统命令
- **位置**: `src/main/HttpTask.cpp` L58、L130-142
- **当前状态**: ⚠️ 大部分修复（2026-08-10 复审）
  - ✅ 管理接口已绑定 `127.0.0.1`（`HttpTask.cpp` L58），仅本地可访问
  - ✅ **HTTP 端点 `/list`、`/exec`、`/kill` 已全部接入管理员鉴权**（2026-08-10 确认）：`verify_admin_token()`（`HttpTask.cpp` L159-204）调用 ca 的 `/admin/verify`（默认 `127.0.0.1:8443`，`GATEWAY_HOST`/`GATEWAY_PORT` 可配；v0.9.0 拓扑下 8443 由 ca 接管，契约不变）做 RSA 验签 + is_admin 检查，Authorization 头优先、回退 `q4_token` Cookie，ca 不可达/响应异常一律 401（fail-closed）
  - ⚠️ MQ 通道的 `kill`/`exec` 操作**仍无认证**（`main.cpp` L320-334），但风险降级：生产部署下 MQ 文件（`/dev/shm/<pid>`）为 root 属主 0644，仅 root 可写入发送；残留风险为非 root 运行（开发模式）时同用户进程可注入，且 MQ 操作完全绕过审计 → 见 N1/N3
  - ❌ 无操作审计日志（HTTP 与 MQ 两个通道均未记录）
- **修复建议**:
  1. ~~确保管理接口仅监听 127.0.0.1~~ ✅ 已实现
  2. 添加强认证（预共享 Token 或 Basic Auth over localhost）
  3. 危险命令（exec、kill）要求二次确认
  4. 记录所有管理操作审计日志

### P0-2: 自启动配置文件无完整性校验，可注入恶意命令
- **位置**: 原 `src/main/pmc_mtd.cpp` `parse_self_init_list()`；现为 `src/registry/InitParser.cpp`（2026-08-10 复审时已重写）
- **当前状态**: ⚠️ 部分缓解（2026-08-10 复审）
  - ✅ init.txt 纯命令列表格式已废弃，改为结构化 `services.yaml`（yaml-cpp 解析，`InitParser::parseYaml()` L38-88），缺 `cmd` 的条目跳过，解析异常捕获不崩溃
  - ❌ 仍无数字签名/校验和验证，`cmd`/`args` 内容无任何校验，`toCommand()` 拼接后直接 `ppool.crtp()` 以 shell 执行
  - ❌ **文件权限未收紧反而恶化**：实际运行时加载的 `/opt/micro_servers/core/services.yaml`（`_run.sh` 以 `-i .` 指定）权限为 `0664 qing:qing` **组可写**，而 core 经 systemd 以 **root** 运行 → qing 组成员篡改后下次启动即以 root 执行任意命令（**提权路径，见 N2**）
  - ❌ 部署漂移：`make install` 将 services.yaml 复制到 `/etc/Nidus/`，但 `_run.sh` 实际读取工作目录文件；`/etc/Nidus/init.txt` 为已废弃格式的残留文件
- **修复建议**:
  1. ~~init.txt 添加数字签名/校验和验证~~（格式已废弃）→ 对 services.yaml 做校验和/签名
  2. 运行时监控 services.yaml 修改，变更需管理员确认
  3. 文件权限设为 0640 root:root，仅 root 可写；统一配置加载路径（`/etc/Nidus/services.yaml`）

---

## 🟠 P1 — 高危问题

| # | 问题 | 状态 | 位置 | 说明 |
|---|------|------|------|------|
| P1-1 | 子进程异常退出无次数限制 | ❌ 未修复（风险性质已变化） | `PPool.cpp`、`ProcessTask.hh` | 2026-08-10 复审：代码中**根本不存在自动重启逻辑**（`ProcessTask` 无重启循环，README 声称的"自动重启"与代码不符），故 crash-loop 风险不存在，但反向缺失守护能力——子进程退出即永久死亡，无重启计数/熔断/告警 |
| P1-2 | 日志路径穿越 | ⚠️ 部分缓解 | `pmc_mtd.cpp` L103-153、`InitParser.cpp` | 2026-08-10 复审：init.txt 解析路径已移除，命令行经 services.yaml 结构化声明后原样透传，core 不再直接解析日志路径参数；残留风险等价于 P0-2（取决于 services.yaml 文件权限，见 N2） |
| P1-3 | C++ HTTP 解析器需审计缓冲区溢出 | ⚠️ 部分覆盖 | libhttp 库 | 2026-08-10 复审：libhttp 已有专项审计（`docs/security/libhttp_SECURITY_AUDIT_20260803.md`），但 core 自身仍未对 HTTP 输入做 fuzz 测试 |

---

## 🟡 P2 — 中危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P2-1 | 信号处理函数中使用非异步安全函数 | ❌ 未修复 | `main.cpp` L86-91 `signalHandler` 调用 `LOG_INFO`（日志宏，底层为 printf）和 `cv.WakeCv()`（条件变量），均非异步信号安全（对比：gateway v0.8.0 已改为 `volatile sig_atomic_t` 标志 + `write()` 的正确姿势，可直接参照） |
| P2-2 | 线程池任务队列无上限 | ❌ 未修复 | 消息队列 `Mq` 有固定容量（`msgcnt=100`），但进程池 `PPool` 仍无大小上限，`crtp()`（`PPool.cpp` L7-17）可无限追加 |
| P2-3 | 崩溃 core dump 可能包含敏感信息 | ❌ 未修复 | `Nidus.service` 未设置 `LimitCORE=0`，且以 `User=root` 运行；崩溃时可能泄露内存中的 Token、环境变量等 |

---

## 🆕 2026-08-10 复审新增发现

### N1（P1）: PPool 进程池无锁，HTTP 与 MQ 双通道并发竞态
- **位置**: `pmc_mtd.cpp` L18 静态 `ppool` + `PPool.cpp` 全部方法
- **问题**: HTTP 服务 4 个 worker 线程（`HttpTask.cpp` L58）与 Tttask MQ 回调线程（`main.cpp` L256-263）可同时调用 `pmc_mtd::list/exec/kill`，并发读写 `std::list<shared_ptr<ProcessTask>>`，无任何互斥保护 → 数据竞争/UB，可导致 core（root 进程）崩溃或内存损坏
- **修复建议**: 为 `PPool` 内部加 `std::mutex`，或在 `pmc_mtd` 命名空间层统一加锁

### N2（P1）: services.yaml 组可写 + root 运行 = 本地提权路径
- **现场证据**: `/opt/micro_servers/core/services.yaml` 权限 `0664 qing:qing`；`Nidus.service` 以 `User=root` 运行；`_run.sh` 执行 `nidus.out -i .`（加载仓库目录文件）
- **问题**: qing 组任何成员可篡改 `cmd`/`args`/`env`，core 重启（systemd restart、主机重启或 kill）后以 **root** 执行注入命令
- **修复建议**: `chown root:root` + `chmod 0640`；或 core 启动时检查配置文件属主/权限，非 root 独占即拒绝加载（fail-closed）

### N3（P2）: `/tmp/pst-*` FIFO 可预测命名 + 0666 权限
- **位置**: `include/tt/Pipe.h` `mkfifo(fifo_path, 0666)`，路径 `/tmp/pst-<pid>`
- **问题**: 名称仅由 pid 构成且位于全局可写 `/tmp`：本地攻击者可预创建同名 FIFO 截获/阻断响应（如进程列表），或以大量写端挂起读方；异常路径下 FIFO 残留（subsys_call 注释已自认）
- **修复建议**: 改用 `mkstemp` 式随机名 + `fchmod 0600`，或迁移到匿名管道/socketpair

### N4（P2）: verify_admin_token 无超时，可被挂起型上游拖死管理面
- **位置**: `HttpTask.cpp` L187-190 `post_stream_sync`
- **问题**: ca 不可达时快速失败（fail-closed ✅），但 ca 建立连接后挂起时，4 个 worker 线程全部阻塞 → 管理接口拒绝服务（不影响安全边界，仅可用性）
- **修复建议**: HttpClient 设置连接/读取超时（如 2s）

### N5（P2）: setenv 环境变量跨服务泄漏
- **位置**: `pmc_mtd.cpp` L136-137 `parse_self_init_list()`
- **问题**: 每个服务的 `env` 条目通过 `setenv()` 永久写入 core 自身环境，前序服务的环境变量会被后续所有子进程继承（如 db2 的敏感 env 泄漏到 ai_review 子进程）
- **修复建议**: 改用 `boost::process::env` 或 fork 后子进程内 setenv，不污染父进程

### N6（P3）: PPool::clr() 空池未定义行为
- **位置**: `PPool.cpp` L20-32
- **问题**: 池为空时 `--pool.end()` 为 UB（std::list 实践上通常无害，但属未定义行为）；首次启动流程 `parse_self_init_list` 必经此路径

### N7（P3）: 部署配置漂移与残留文件
- **现场证据**: `Makefile install` 目标将 services.yaml 复制到 `/etc/Nidus/`，但 `_run.sh` 以 `-i .` 读仓库目录，`/etc/Nidus/services.yaml` 实际不存在，仅残留已废弃格式的 `/etc/Nidus/init.txt`（0644 root）
- **风险**: 低（init.txt 已不再被解析），但易误导运维，且两处配置不一致时行为不可预期

### N8（P2）: Nidus.service 无安全加固指令
- **位置**: `Nidus.service`
- **问题**: 仅 `User=root` + `ExecStart`，无 `NoNewPrivileges`、`ProtectSystem`、`ProtectHome`、`LimitCORE=0`、`PrivateTmp` 等加固项；root 运行放大任何本地漏洞的影响面（与 N2/N3 叠加）

### 复审正面确认（新增安全机制）

| 机制 | 位置 | 评价 |
|------|------|------|
| 管理员鉴权 fail-closed 链路 | `HttpTask.cpp` L159-204、L280/L315/L368 | ✅ 三路由全覆盖；响应 JSON 字段类型严格校验（`is_bool`）；异常/不可达一律拒绝 |
| Token 提取健壮性 | `HttpTask.cpp` L161-174 | ✅ Bearer 前缀校验，Cookie 回退仅读 `q4_token`，空值直接拒绝 |
| 内嵌 ConfsTask 路径安全 | `ConfsTask.cpp` L133-270 | ✅ 配置名白名单（禁 `/`、`\`）+ 扩展名白名单 + `weakly_canonical` 前缀校验 + 锁内 stat/read 无 TOCTOU + 1MiB 大小上限 + list 路由符号链接逃逸检测，与 confs2 审计水准一致 |
| MQ 消息长度限制 | `Mq.h` `send()` | ✅ 超长消息抛 `length_error`，缓冲区按 `msglen` 定长分配 |

---

## ✅ 已有安全机制

1. ⚠️ **进程生命周期管理**: 2026-08-10 复审更正——代码中并无自动重启逻辑（见 P1-1），实际仅提供创建/列举/终止能力，README 描述与实现不符
2. ✅ **线程池模型**: 固定线程数，防止无限线程创建
3. ✅ **日志集中管理**: 统一日志格式和输出
4. ✅ **C++ RAII 内存管理**: 使用智能指针减少内存泄漏风险
5. ✅ **依赖审计**: 使用经过审计的 libth 线程库、libhttp HTTP 库、libcrypt 加密库
6. ✅ **管理接口本地绑定**: HTTP 管理接口仅监听 `127.0.0.1`（`HttpTask.cpp` L58）
7. ✅ **HTTP 管理接口管理员鉴权**（2026-08-10 新增）: `/list`、`/exec`、`/kill` 均经 ca `/admin/verify` RSA 验签 + is_admin 检查，fail-closed
8. ⚠️ **MQ 通道危险操作仍无认证**: `kill`/`exec` 经消息队列的路径仍活跃（`main.cpp` L320-334）；生产部署下受 MQ 文件 0644 root 属主限制仅 root 可写，但绕过审计且非 root 运行时无防护

---

## 修复路线图（2026-08-10 复审后重排）

### 立即（24小时）
- [x] 确认 PMC HTTP 接口仅绑定 127.0.0.1 → ✅ 已确认
- [x] PMC HTTP 接口添加认证 → ✅ 已实现（ca `/admin/verify` fail-closed）
- [ ] **收紧 services.yaml 权限为 root 属主 0640**（N2，当前 0664 qing 组可写，为现存最高风险项）
- [ ] MQ 通道 kill/exec 添加认证或禁用（`main.cpp` L320-334）
- [ ] PPool 加锁（N1，并发竞态可崩 root 进程）

### 短期（72小时）
- [ ] 子进程守护：退出自动重启 + 计数熔断（P1-1，README 声称但代码缺失）
- [ ] `/tmp/pst-*` FIFO 改随机名 + 0600（N3）
- [ ] verify_admin_token 加 HTTP 超时（N4）
- [ ] Nidus.service 加固（LimitCORE=0 / NoNewPrivileges / ProtectSystem，P2-3 + N8）

### 中期
- [ ] services.yaml 完整性校验（校验和/签名）+ 统一加载路径到 /etc/Nidus/（P0-2 + N7）
- [ ] 管理操作审计日志（HTTP + MQ 双通道）
- [ ] 信号处理函数安全改造（参照 gateway v0.8.0 的 async-signal-safe 实现，P2-1）
- [ ] setenv 跨服务泄漏修复（N5）
- [ ] 进程池大小上限（P2-2）；PPool::clr() 空池 UB 修复（N6）
- [ ] core 对 HTTP 输入的 fuzz 测试（P1-3 残留）

---

## 代码验证记录

**验证日期**: 2026-08-03  
**验证方式**: 逐条对照审计报告与当前代码（`main.cpp`、`HttpTask.cpp`、`ProcessTask.cpp`、`PPool.cpp`、`pmc_mtd.cpp`、`Tttask.cpp`、`subsys_call.cpp`）

### 验证结果汇总

| 等级 | 总数 | 已修复 | 部分修复 | 未修复 | 修复率 |
|------|------|--------|----------|--------|--------|
| P0 | 2 | 0 | 1 | 1 | 25% (部分) |
| P1 | 3 | 0 | 0 | 3 | 0% |
| P2 | 3 | 0 | 1 | 2 | 17% (部分) |
| **合计** | **8** | **0** | **2** | **6** | **12.5%** |

### 逐条验证

| 编号 | 状态 | 代码位置 | 验证说明 |
|------|------|----------|----------|
| P0-1 | ⚠️ 部分 | `HttpTask.cpp` L58, L130-142 | 已绑定 127.0.0.1；但 MQ 通道 kill/exec **未禁用**（`main.cpp` L283-296 仍活跃）；HTTP 端点 `/exec`/`/kill` 仍无认证 |
| P0-2 | ❌ 未修复 | `pmc_mtd.cpp` L90-118 | `parse_self_init_list` 无完整性校验、无大小限制、无命令内容校验 |
| P1-1 | ❌ 未修复 | `PPool.cpp` | `crtp()` 无熔断/频率限制；`ProcessTask.cpp` 在 `#ifdef __NEW_VER` 下未启用 |
| P1-2 | ❌ 未修复 | `pmc_mtd.cpp` L90-118 | init.txt 解析未校验子进程日志路径参数 |
| P1-3 | ❌ 未修复 | `src/shared/` | libhttp 库未进行 fuzz 测试 |
| P2-1 | ❌ 未修复 | `main.cpp` L76-81 | `signalHandler` 使用 `LOG_INFO`(printf) + `cv.WakeCv()`，非异步安全 |
| P2-2 | ⚠️ 部分 | `Tttask.cpp` L43, `PPool.cpp` | Mq 有容量限制；但 PPool 进程池无上限 |
| P2-3 | ❌ 未修复 | - | 无 core dump 过滤/禁用机制 |

### 新增安全措施（审计报告未覆盖）

| 措施 | 代码位置 | 说明 |
|------|----------|------|
| MQ 通道危险操作**未禁用** | `main.cpp` L282-296 | `kill`/`exec` 通过消息队列的路径**仍活跃**，L282 注释标注"在身份验证之前禁止操作"但未实际注释代码 |
| HTTP 管理接口本地绑定 | `HttpTask.cpp` L58 | 显式绑定 `127.0.0.1`，非 `0.0.0.0` |
| init.txt 注释行支持 | `pmc_mtd.cpp` L105-107 | 支持 `#` 注释行跳过，可禁用特定启动项 |

---

## 代码验证记录（第二轮）

**验证日期**: 2026-08-10  
**验证方式**: 逐行审读全部源码（`main.cpp`、`src/main/` 8 个 .cpp、`src/registry/InitParser.cpp`、`include/` 全部头文件）+ 构建配置（`Makefile`、`_run.sh`、`Nidus.service`）+ 运行现场取证（`/dev/shm` MQ 文件权限、`/etc/Nidus/` 内容、services.yaml 属主权限）；交叉验证 ca `/admin/verify` 契约（`ca/src/main.cpp` L1203/L1585）

### 原有 8 项逐条复核结果

| 编号 | 状态变化 | 代码/现场依据 |
|------|----------|----------------|
| P0-1 | ⚠️ 部分 → ⚠️ 大部分修复 | HTTP 三路由全部前置 `verify_admin_token()`（`HttpTask.cpp` L280/L315/L368），fail-closed；MQ 通道（`main.cpp` L320-334）仍无认证但受 `/dev/shm` 文件 0644 root 属主限制（现场取证：`-rw-r--r-- root root`） |
| P0-2 | ❌ 未修复 → ⚠️ 部分缓解 | init.txt 格式废弃，改 `registry/InitParser.cpp` 结构化解析；但无完整性校验且 services.yaml 组可写（0664），提权风险反而具体化（N2） |
| P1-1 | ❌ 未修复（性质变化） | 全部源码检索确认无重启循环；crash-loop 风险不存在，改为守护缺失问题 |
| P1-2 | ❌ → ⚠️ 部分缓解 | init.txt 解析路径删除，命令行透传不经 core 解析；残面收敛到 services.yaml 权限 |
| P1-3 | ❌ → ⚠️ 部分覆盖 | libhttp 已有专项审计（20260803）；core 自身 fuzz 仍缺 |
| P2-1 | ❌ 未修复 | `main.cpp` L86-91 原样（行号因代码演进偏移，原报告 L76-81） |
| P2-2 | ⚠️ → ❌ 未修复 | `PPool::crtp`（`PPool.cpp` L7-17）仍无任何上限；原判"部分修复"仅指 Mq 容量，维持原判后 PPool 部分无任何改善 |
| P2-3 | ❌ 未修复 | `Nidus.service` 无 `LimitCORE` 等指令，且确认 root 运行 |

### 新增 8 项发现

| 编号 | 等级 | 状态 | 摘要 |
|------|------|------|------|
| N1 | P1 | ❌ 开放 | PPool 无锁并发竞态（HTTP 4 worker + MQ 线程） |
| N2 | P1 | ❌ 开放 | services.yaml 0664 组可写 + core root 运行 → 本地提权 |
| N3 | P2 | ❌ 开放 | `/tmp/pst-<pid>` FIFO 0666 可预测命名 |
| N4 | P2 | ❌ 开放 | verify_admin_token 无超时，挂起型 DoS |
| N5 | P2 | ❌ 开放 | setenv 污染 core 环境，env 跨服务泄漏 |
| N6 | P3 | ❌ 开放 | PPool::clr() 空池 `--end()` UB |
| N7 | P3 | ❌ 开放 | /etc/Nidus/ 配置漂移与 init.txt 残留 |
| N8 | P2 | ❌ 开放 | Nidus.service 无 systemd 安全加固 |

### 复审综合结论

- **修复率（原 8 项）**: 已修复 0 / 部分修复 4 / 未修复 4，较 2026-08-03 验证（部分 2 / 未修复 6）有实质进展，核心驱动力为 HTTP 管理面接入 ca 鉴权链路
- **新增风险面**: 8 项（P1×2、P2×4、P3×2），其中 **N2（services.yaml 提权路径）为当前最高优先级**，仅需一条 `chown/chmod` 即可消除
- **安全评级建议**: 网络面（远程攻击面）已收敛良好（loopback + fail-closed 鉴权）；本地提权面（同机普通用户 → root）为主要短板，建议修复 N1/N2/N3 后再评定级
