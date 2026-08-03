# confs 模块安全审计报告

**审计日期**: 2026-08-01
**审计范围**: `confs/confs.py`、`confs/log_client.py`、`confs/_run.sh`、`confs/_set.sh`、`pymsg/pymsg/rpc.py`（RPC 框架依赖层）
**审计人**: AI Security Audit

---

## 一、模块概述

`confs` 是微服务架构中的统一配置分发服务，基于 `pymsg RpcServer` 在 `127.0.0.1:8010` 提供 TCP RPC 接口，集中管理各微服务的配置文件，通过内存缓存 + 文件 mtime 自动重载机制向内部服务分发配置。

**核心组件**:
- `ConfigStore`：配置文件仓库（磁盘目录 + 内存缓存 + 自动重载）
- `ConfsServer`：RPC 服务入口，路由分发
- `_extract_key()`：INI/JSON 单键提取功能
- `log_client.py`：异步日志推送客户端（副本，与其他服务同步）

---

## 二、已修复问题（本次审计周期内）

| # | 问题 | 级别 | 修复提交 | 状态 |
|---|------|------|---------|------|
| 1 | pymsg `recv()` 无超时导致主线程永久阻塞（Slowloris DoS） | **P0** | `9708fbc` | ✅ 已修复 |
| 2 | 配置文件读取无大小限制，超大文件导致 OOM | **P1** | `da7f11d` | ✅ 已修复 |
| 3 | log_client 后台线程异常无保护，异常可杀死线程导致内存无限增长 | **P1** | `1b9c727` | ✅ 已修复（4份副本同步） |
| 4 | `example_log_client.py` 仍使用旧 `MsgClient` 接口与当前 `RpcServer` 不匹配 | **P1** | `bdbea6a` | ✅ 已修复 |
| 5 | `ConfigStore.get()` TOCTOU 竞态：锁外 `_resolve()` 后锁内 `open()` 存在符号链接替换窗口 | **P1** | `9efb96e` | ✅ 已修复 |
| 6 | log_client 日志时间戳在 `_send()` 时生成而非入队时，队列积压时时间不准确 | **P2** | `1b9c727` | ✅ 已修复 |
| 7 | log_client `flush()` 固定 `sleep(0.6)` 不可靠 | **P2** | `1b9c727` | ✅ 已修复 |
| 8 | log_client 队列无上限，线程异常时内存无限增长 | **P2** | `1b9c727` | ✅ 已修复 |
| 9 | `_handle_reload` 空字符串/非字符串 name（falsy 值）意外清空全部缓存 | **P1** | `b53d499` | ✅ 已修复 |
| 10 | `ConfigStore.get()` 返回缓存 dict 可变引用，调用方可污染缓存 | **P1** | `b53d499` | ✅ 已修复 |
| 11 | `list()` 不对符号链接做 realpath 校验，可探测外部文件元信息 | **P1** | `b53d499` | ✅ 已修复 |
| 12 | `_handle_get` 中 `key` 参数未做类型校验，非字符串值被 `str()` 转换后产生误导行为 | **P1** | `b53d499` | ✅ 已修复 |
| 13 | `ConfigParser` 未禁用插值（`%(key)s`），含裸 `%` 的配置值触发 `InterpolationError` | **P2** | `b53d499` | ✅ 已修复 |
| 14 | argparse 描述字符串未与顶部文档同步更新（`pymsg/TCP` → `pymsg RpcServer/TCP`） | **P2** | `b53d499` | ✅ 已修复 |

---

## 三、遗留问题

### 🔴 P0 — Critical
**无遗留 P0 问题。**

---

### 🟠 P1 — High
**无遗留 P1 问题。**

---

### 🟡 P2 — Medium（建议修复）

#### P2-1 ~~ConfigParser 未禁用插值~~ ✅ 已修复

#### P2-2 ~~argparse 描述字符串未与文档同步更新~~ ✅ 已修复

#### P2-3 日志注入风险

**文件**: `confs/confs.py` 第 148/282/288 行
配置名和 key 直接拼入日志消息，若包含 `\n` 或 ANSI 转义序列可伪造日志条目。防御纵深角度建议过滤控制字符。

---

#### P2-4 `list()` 在持锁期间遍历目录并 stat 每个文件

配置文件数量预计极少（<100），实际影响可忽略，但若未来扩展到大量文件需考虑优化。

---

### 🔵 P3 — Low（改进建议）

| # | 问题 | 文件 | 说明 |
|---|------|------|------|
| P3-1 | `_run.sh` 无意义 `sleep 2` | `confs/_run.sh` L5 | 启动前固定 sleep 是 hack，应通过 core 的 init.txt 依赖顺序或健康检查保证 |
| P3-2 | `_set.sh` 硬编码清华 pip 镜像源 | `confs/_set.sh` L5 | 非中国大陆网络环境下安装可能失败 |
| P3-3 | `_set.sh` 缺少 `set -e` | `confs/_set.sh` | pip install 失败后脚本继续执行，可能导致运行时 ImportError |
| P3-4 | `requirements.txt` 包含运行时不需要的 nuitka | `confs/requirements.txt` | nuitka 是编译工具（且在 `_set.sh` 中被注释掉），不应作为运行时依赖 |
| P3-5 | ConfigParser section 大小写敏感/option 大小写不敏感的隐式行为 | `confs/confs.py` L206-217 | Python 标准库行为，建议文档中说明或统一转为小写处理 |
| P3-6 | 错误消息暴露底层 IO 异常信息 | `confs/confs.py` L144 | `Permission denied` 等底层错误信息被原样返回给调用方 |

---

## 四、安全机制总结

| 机制 | 状态 | 说明 |
|------|------|------|
| 扩展名白名单 | ✅ | 仅允许 `.ini/.conf/.cfg/.json/.yaml/.yml/.toml/.txt` |
| 路径遍历防护 | ✅ | basename 校验 + realpath 前缀校验 |
| TOCTOU 防护 | ✅ | 文件系统操作（realpath→stat→open）全部在锁内原子完成 |
| 文件大小限制 | ✅ | 单文件上限 1 MiB（`MAX_CONFIG_SIZE`） |
| Slowloris 防护 | ✅ | pymsg MsgServer 默认 10 秒 recv 超时 |
| 缓存一致性 | ✅ | 所有公开方法持锁操作 |
| 缓存防污染 | ✅ | `get()` 返回浅拷贝，调用方修改不影响缓存 |
| list() 符号链接防护 | ✅ | realpath 解析后校验前缀，逃逸链接跳过 |
| 参数类型校验 | ✅ | name/key 非字符串或空值明确报错，不产生误行为 |
| 异常隔离 | ✅ | handler 异常由 RpcServer 包装为 `success=false`，不崩溃服务 |
| ConfigParser 安全 | ✅ | `interpolation=None` 禁用 `%(key)s` 插值，裸 `%` 不报错 |
| 仅监听本机 | ✅ | 默认 `127.0.0.1` |
| 日志注入防护 | ❌ | 配置名/key 未过滤控制字符（P2-3） |
| 鉴权 | ❌ | 无身份校验，依赖"仅本机"信任边界 |

---

## 五、修复优先级建议

1. **全部 P0/P1 已修复**：共修复 1 个 P0 + 7 个 P1 问题（含此前修复的 pymsg/log_client/TOCTOU/OOM 等）
2. **近期修复（P2）**: P2-3（日志注入，过滤控制字符）
3. **可接受（P2-4/P3）**: 实际风险极低或属于运维/部署优化项
