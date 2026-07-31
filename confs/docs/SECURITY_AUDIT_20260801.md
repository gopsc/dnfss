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

---

## 三、遗留问题

### 🔴 P0 — Critical
**无遗留 P0 问题。**

---

### 🟠 P1 — High（建议尽快修复）

#### P1-1 `_handle_reload` 空字符串 name 意外清空全部缓存

**文件**: `confs/confs.py` 第 295-303 行

**问题**:
```python
def _handle_reload(self, req):
    name = req.get("name")
    names = self.store.reload(str(name).strip() if name else None)
```
当调用方传入 `"name": ""`（空字符串）或 `"name": 0`/`false`（falsy 值）时，会被当作 `None` 处理，导致 `reload(None)` **清空全部配置缓存**，造成瞬间磁盘 IO 尖峰和缓存穿透。对比 `_handle_get` 对空 name 的处理是正确报错：
```python
name = str(req.get("name", "")).strip()
if not name:
    raise ConfigError("缺少 name 字段")
```

**风险**: 内部服务误传空 name 会意外清空所有配置缓存。

**修复建议**:
```python
def _handle_reload(self, req):
    name = req.get("name")
    if name is None:
        names = self.store.reload(None)
        logs.info("confs", f"清空配置缓存（{len(names)} 项）")
        return {"reloaded": names}
    name = str(name).strip()
    if not name:
        raise ConfigError("name 字段不能为空")
    names = self.store.reload(name)
    logs.info("confs", f"重载配置 {names[0]}")
    return {"reloaded": names}
```

---

#### P1-2 `ConfigStore.get()` 返回缓存 dict 的可变引用，调用方可污染缓存

**文件**: `confs/confs.py` 第 133-149 行

**问题**: `get()` 直接返回缓存中 dict 对象的引用。若调用方意外修改返回值，会直接污染缓存，在文件 mtime 变化前所有后续 get 都拿到被篡改的内容。

```python
entry = self._cache.get(name)
if entry is not None and entry["mtime"] == mtime:
    return entry  # 返回缓存对象的直接引用
```

**风险**: 配置缓存被意外修改，影响所有依赖该配置的服务。

**修复建议**: 返回浅拷贝（content/size/mtime 都是不可变类型，浅拷贝足够）：
```python
if entry is not None and entry["mtime"] == mtime:
    return dict(entry)
# 以及新构建 entry 后存入缓存前也做一次拷贝或直接存入后返回拷贝
return dict(entry)
```

---

#### P1-3 `list()` 不对符号链接做 realpath 校验，可探测外部文件元信息

**文件**: `confs/confs.py` 第 151-168 行

**问题**: `list()` 使用 `os.path.isfile()` 和 `os.stat()`（均跟随符号链接），未做 realpath 前缀校验。若 `configs/` 目录下存在指向目录外的符号链接（如 `probe.ini -> /etc/passwd`），`list()` 会将其列入返回结果，泄露目标文件的大小和 mtime 元信息。虽然 `get()` 已通过 TOCTOU 修复阻止内容读取，但元信息泄露仍属信息泄露。

**风险**: 本地攻击者可通过符号链接探测系统文件存在性和修改时间。

**修复建议**:
```python
for name in sorted(os.listdir(self.root)):
    if os.path.splitext(name)[1].lower() not in ALLOWED_EXT:
        continue
    path = os.path.realpath(os.path.join(self.root, name))
    if not path.startswith(self.root + os.sep):
        continue  # 符号链接逃逸，跳过
    if not os.path.isfile(path):
        continue
    ...
```

---

#### P1-4 `_handle_get` 中 `key` 参数类型未严格校验

**文件**: `confs/confs.py` 第 280-289 行

**问题**:
```python
key = req.get("key")
if key is None:
    ...
key = str(key).strip()
```
当 key 为 `0`、`False`、`[]`、`{}` 等非字符串非 None 值时，`str()` 会将其转为 `"0"`/`"False"`/`"[]"`/`"{'a': 1}"` 等字符串，进入按键查询逻辑，返回误导性错误而非参数类型错误。

**风险**: 调用方传参错误时得到误导性的"键不存在"而非"参数类型错误"，增加排查难度。

**修复建议**:
```python
key = req.get("key")
if key is not None and not isinstance(key, str):
    raise ConfigError("key 字段必须是字符串")
if key is None:
    ...
key = key.strip()
```

---

### 🟡 P2 — Medium（建议修复）

#### P2-1 ConfigParser 未禁用插值，含 `%` 的配置值可能导致解析错误

**文件**: `confs/confs.py` 第 207 行

`configparser.ConfigParser()` 默认启用基本插值（`%(key)s` 语法），配置值中包含裸 `%` 字符可能触发 `InterpolationError`。应使用 `ConfigParser(interpolation=None)` 禁用插值，因为 confs 仅做配置分发，不需要插值功能。

---

#### P2-2 argparse 描述字符串未与文档同步更新

**文件**: `confs/confs.py` 第 319 行
顶部文档字符串已更新为 `pymsg RpcServer/TCP`，但 argparse description 仍为旧的 `pymsg/TCP`。

---

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
| 异常隔离 | ✅ | handler 异常由 RpcServer 包装为 `success=false`，不崩溃服务 |
| 仅监听本机 | ✅ | 默认 `127.0.0.1` |
| 配置缓存防污染 | ❌ | 返回缓存可变引用（P1-2） |
| list() 符号链接防护 | ❌ | 未做 realpath 校验（P1-3） |
| 参数类型校验 | ❌ | key/name 参数校验不完善（P1-1/P1-4） |
| 鉴权 | ❌ | 无身份校验，依赖"仅本机"信任边界 |

---

## 五、修复优先级建议

1. **立即修复（P1）**: P1-1（reload空字符串全清缓存）、P1-2（缓存可变引用）、P1-3（list符号链接）、P1-4（key类型校验）——均为几行代码的修复，成本低、收益明确
2. **近期修复（P2）**: P2-1（ConfigParser插值）、P2-2（文档同步）、P2-3（日志注入）
3. **可接受（P2-4/P3）**: 实际风险极低或属于运维/部署优化项
