# db2 模块安全审计报告

**初次审计**: 2026-08-06  
**最近复审**: 2026-08-07（P2-3 + P3-8 修复完成，编译验证通过）  
**审计范围**: `db2/src/db2.cpp`、`Makefile`、`docker-entrypoint.sh`  
**对照基准**: Python 版 `db/dbv2.py`（接口兼容性对比）  
**代码版本**: main 分支 761cfd4 + P2-3/P3-8 修复

---

## 修复进度

| 优先级 | 总数 | 已修复 | 未修复 |
|--------|------|--------|--------|
| P0 | 2 | 2 | 0 |
| P1 | 1 | 1 | 0 |
| P2 | 3 | 3 | 0 |
| P3 | 11 | 7 | 4 |
| **合计** | **17** | **13** | **4** |

---

## P0 — 严重问题

### P0-1: 列名 SQL 注入（列名未校验直接拼接 SQL）

- **位置**: 历史版本 L399-455 等位置
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: 新增 `_validate_col` 静态方法 [db2.cpp L443-449](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L443-L449)，对所有列名（where keys、columns、order_by、INSERT/UPDATE values keys）校验其存在于表 schema 的 `col_map` 中，不存在则抛 `"unknown column"` 错误。5 个注入点全部修复。

  `sqlite3_prepare_v2` 只编译单条语句（`; DROP TABLE` 不会执行），但**单语句内注入**在修复前仍然可行。修复后列名必须是真实存在的列名，注入不可能。

- **注入点覆盖**:
  - `where` 的 key → `_build_where` [L473-475](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L473-L475)
  - `columns` 数组元素 → `_do_select` [L588-590](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L588-L590)
  - `order_by[].column` → `_do_select` [L611-612](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L611-L612)
  - INSERT `values` 的 key → `_do_insert` [L691-693](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L691-L693)
  - UPDATE SET `values` 的 key → `_do_update` [L734-736](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L734-L736)

---

### P0-2: 空 `in` 数组 → 无 WHERE 子句 → 全表删除/更新

- **位置**: `_build_where` `in` 运算符分支
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: [L503-506](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L503-L506) 空 `in` 数组现在生成恒假条件 `parts.push_back("0")`，使 WHERE 子句匹配零行，与 Python 版 SQLAlchemy `column.in_([])` 行为一致。

---

## P1 — 高危问题

### P1-1: 所有值绑定为 TEXT，类型信息丢失

- **位置**: 历史版本 `_coerce_value` / `_bind_value`
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: 
  - `_coerce_value` [L414-420](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L414-L420) 现在返回 `boost::json::value`，仅对字符串做 `$now` 替换，其他类型保持原样。
  - 新增 `_bind_value` [L422-440](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L422-L440)，按 JSON 类型分别调用 `sqlite3_bind_int64` / `sqlite3_bind_double` / `sqlite3_bind_text` / `sqlite3_bind_null` / `sqlite3_bind_int` (bool)。
  - `WhereClause::params` 改为 `std::vector<json::value>`，保留原始类型。

  修复后 INTEGER/REAL 等数值类型以原生 SQLite 存储类写入，避免 TEXT 列上的字符串比较问题，行为与 Python 版一致。

---

## P2 — 中危问题

### P2-1: 清理时间戳格式不一致（T vs 空格）导致保留期偏移约 1 天

- **位置**: 历史版本 `utc_now` + `_do_cleanup`
- **当前状态**: ✅ 已修复（2026-08-07）
- **修复**: [L863-865](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L863-L865) 清理 SQL 改用 `strftime('%Y-%m-%dT%H:%M:%S','now','-N days')`，与 `utc_now()` 的 `T` 分隔格式完全对齐。

---

### P2-2: 缺少配置完整性校验

- **位置**: 历史版本 `load_config`
- **当前状态**: ✅ 已修复（2026-08-07）
- **修复**: [L159-175](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L159-L175) 在 INI 解析后，对 7 个必需字段做必填校验，缺失任一则 `fprintf` 明确错误信息后 `exit(1)`。

---

### P2-3: `cleanup_interval` 无下限校验，配置为 0 或负数时导致清理线程 busy loop 占满 CPU

- **位置**: `_cleanup_loop`、`load_config`
- **当前状态**: ✅ 已修复（2026-08-07）
- **修复**: [db2.cpp L207-L227](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L207-L227) 在 `load_config` 返回前（所有配置来源——INI、环境变量、命令行参数——都加载完毕后）统一增加数值范围校验，非法值立即 `fprintf` 明确错误信息后 `exit(1)`。
- **校验项**:
  - `listen_port`: 1-65535
  - `workers`: ≥ 1
  - `cleanup_interval`: ≥ 1（busy loop 问题根源）
  - `retention_days`: ≥ 0
  - `max_records`: ≥ 1

---

## P3 — 低危问题

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| P3-1 | schema 缓存永不失效 | 历史版本 | ✅ 已修复：缓存项携带 `PRAGMA schema_version` [L270-273](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L270-L273)，每次取 schema 前比对版本号；DDL 变更后自动失效并重新反射 |
| P3-2 | `_coerce_value` 对 null 返回 `""` | 历史版本 | ✅ 已修复：`_coerce_value` 改为返回 `json::value`，null 保持为 null |
| P3-3 | `limit`/`offset` 未校验负值 | 历史版本 | ✅ 已修复：[L622-632](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L622-L632) 非负校验 |
| P3-4 | WHERE 条件数 / IN 数组大小无上限 | 历史版本 | ✅ 已修复：[L463-467](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L463-L467) 64 条件上限、[L507-511](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L507-L511) IN 元素 1000 上限 |
| P3-5 | 空 `values` 对象导致 INSERT/UPDATE 语法错误 | 历史版本 | ✅ 已修复：[L680-682](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L680-L682)、[L724-726](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L724-L726) 前置校验 |
| P3-6 | 未知 WHERE 运算符被静默跳过 | 历史版本 | ✅ 已修复：[L519-521](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L519-L521) 未知运算符抛错 |
| **P3-7**（新） | **`utc_now()` 使用非线程安全的 `gmtime()`** | [L62](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L62) | ❌ 未修复：POSIX `gmtime()` 返回指向静态缓冲区的指针，多线程并发调用（worker线程+cleanup线程同时写日志时）存在数据竞争。虽然时间精度只到秒，实际数据损坏概率极低，但应使用线程安全版本 `gmtime_r(&t, &tm)` |
| **P3-8**（新） | **其他数值配置无范围校验** | [L186-L191](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L186-L191) | ✅ 已修复（2026-08-07，与 P2-3 一同）：[L207-L227](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L207-L227) 所有数值配置项范围校验覆盖：port 1-65535、workers ≥1、cleanup_interval ≥1、retention_days ≥0、max_records ≥1 |
| **P3-9**（新） | **`columns` 数组中非字符串元素未检查类型** | [L588-589](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L588-L589) | ❌ 未修复：`columns` 数组包含非字符串元素（数字、对象、null）时，直接调用 `c.as_string()` 抛出 boost::json 原始异常，错误信息不友好（对比 `order_by` 分支有 `if (!ob.is_object()) continue;` 防御）。建议增加：`if (!c.is_string()) continue;` 或显式校验类型抛清晰错误 |
| **P3-10**（新） | **`DbConn` 析构使用 `sqlite3_close` 而非 `sqlite3_close_v2`** | [L231](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L231) | ❌ 未修复：若异常路径存在未 finalize 的 prepared statement，`sqlite3_close` 返回 `SQLITE_BUSY` 导致连接泄漏；`sqlite3_close_v2` 标记连接为 zombie，等所有 stmt 释放后自动关闭，更鲁棒（与 libsqlite3 审计 P0-2 修复建议一致）。当前所有正常路径都正确 finalize 了 stmt，但改用 close_v2 是防御性最佳实践 |
| **P3-11**（新） | **`std::stoi/stoll` 解析配置失败无友好错误提示** | [L140](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L140)、[L186-L191](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L186-L191)、[L195-L197](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L195-L197)、[L203-L204](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L203-L204) | ❌ 未修复：配置项（端口、workers、数值日志参数）为非法数字时，`std::stoi` 抛出 `std::invalid_argument`，异常 `what()` 仅显示 "stoi"，无法定位哪个配置项出错，排查困难。建议 try/catch 后包装为带字段名的明确错误信息 |

---

## 已有安全机制（正确实现）

1. **表名校验** — [L558-562](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L558-L562)：仅允许 `[A-Za-z0-9_]`，防止表名级 SQL 注入
2. **列名白名单校验** — `_validate_col` [L443-449](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L443-L449)：5 个拼接点全部覆盖，列名必须存在于表 schema
3. **值参数化绑定** — `_bind_value` [L422-440](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L422-L440)：按 JSON 类型分别 `sqlite3_bind_*`，无值级 SQL 注入
4. **per-thread 连接** — [L337-341](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L337-L341)：`thread_local` 独占连接，避免跨线程共享 prepared statement
5. **异常脱敏** — `RpcServer::_sanitize_error`（libmsg）：正则替换绝对路径为 `<redacted>`
6. **仅操作已有表** — `PRAGMA table_info` 反射，无法建表/删表/执行 DDL
7. **空 `in` 恒假条件** — [L503-506](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L503-L506)：空数组不忽略 WHERE，匹配零行
8. **SQLite PRAGMA 优化** — [L223-228](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L223-L228)：WAL + synchronous=NORMAL + foreign_keys=ON + busy_timeout=5000 + mmap_size=256MB
9. **schema 缓存加锁 + 版本感知失效** — `_schema_mtx` [L326](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L326) 保护缓存；`PRAGMA schema_version` 自动感知 DDL 变更
10. **配置完整性校验** — [L159-175](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L159-L175)：7 个必需字段缺失则 `exit(1)`
11. **confs 引导重试** — `retry_on_refused=true`，不要求服务启动顺序
12. **日志内部直写** — `_log_internal` [L814-843](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L814-L843) 绕过 RPC 防递归，异常静默不阻塞业务
13. **后台清理线程** — 条件变量优雅停止 [L847-856](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L847-L856)，时间+条数双策略清理，时间戳格式 T 对齐
14. **空 values 前置校验** — INSERT/UPDATE 空 values 立即报错
15. **WHERE 运算符白名单** — 未知运算符立即抛错，不静默跳过
16. **`limit`/`offset` 非负校验** — 负值直接报错
17. **WHERE / IN 输入规模上限** — 64 条件 / 1000 IN 元素，防资源耗尽
18. **日志截断** — `kMaxMessage=8192`、`kMaxExtra=4096`，防止超长日志耗尽内存
19. **不可拷贝连接** — `DbConn` 显式 `= delete` 拷贝构造/赋值，防止 double free
20. **数值配置范围校验** — [L207-L227](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L207-L227)：listen_port 1-65535、workers ≥1、cleanup_interval ≥1、retention_days ≥0、max_records ≥1，非法值立即 `exit(1)`，防止误配置导致 busy loop/绑定失败等异常行为

---

## 修复路线图

### 已完成
- [x] **P0-1**: 列名白名单校验
- [x] **P0-2**: 空 `in` 生成恒假条件
- [x] **P1-1**: 按 JSON 类型分别参数绑定
- [x] **P2-1**: 清理时间戳格式统一为 T 分隔
- [x] **P2-2**: 配置完整性必填校验
- [x] **P2-3 + P3-8**: 所有数值配置项范围校验（port 1-65535、workers ≥1、cleanup_interval ≥1、retention_days ≥0、max_records ≥1）
- [x] **P3-1**: schema 缓存基于 `schema_version` 自动失效
- [x] **P3-2**: `_coerce_value` null 正确处理
- [x] **P3-3**: limit/offset 非负校验
- [x] **P3-4**: WHERE/IN 输入规模上限
- [x] **P3-5**: 空 values 前置校验
- [x] **P3-6**: 未知 WHERE 运算符报错
- [x] **P3-8**: 数值配置范围校验（与 P2-3 合并完成）

### 待修复（建议中期内完成）
- [ ] **P3-7**: `utc_now()` 改用 `gmtime_r` 保证线程安全
- [ ] **P3-9**: `columns` 数组元素类型检查（非字符串跳过或报错）
- [ ] **P3-10**: `DbConn` 析构改用 `sqlite3_close_v2`
- [ ] **P3-11**: `std::stoi` 解析配置时 try/catch 包装为带字段名的友好错误

---

## 审计方法

### 初次审计（2026-08-06）
1. 通读 `db2/src/db2.cpp` 全部 851 行实现
2. 逐函数对照 Python 版 `db/dbv2.py` 验证接口兼容性
3. 审查 libmsg RpcServer 异常脱敏机制
4. 逐注入点构造 PoC 验证 SQL 注入可行性

### 第一次复审（2026-08-07）
1. 通读修复后代码 875 行
2. 验证 P2-1/P2-2 修复
3. 全量扫描发现 P3-5/P3-6

### 第二次复审（2026-08-07）
1. 通读当前代码（main 分支 761cfd4）
2. 逐函数验证所有 11 个已修复问题无回退
3. 检查所有 `std::stoi`/数值转换路径，发现配置无范围校验问题（P2-3、P3-8、P3-11）
4. 检查所有系统调用线程安全性，发现 `gmtime()` 问题（P3-7）
5. 检查所有 JSON 访问路径的类型安全，发现 `columns` 缺类型检查（P3-9）
6. 对照 libsqlite3 审计最佳实践，发现 `sqlite3_close` 可改进（P3-10）
7. 更新行号引用至当前代码

### P2-3 / P3-8 修复验证（2026-08-07，本次）
1. 在 `load_config` 末尾（命令行参数解析之后）新增 5 项数值范围校验：port 1-65535、workers ≥1、cleanup_interval ≥1、retention_days ≥0、max_records ≥1
2. 非法配置立即 `fprintf` 带字段名的明确错误信息后 `exit(1)`，与已有配置必填校验风格一致
3. 修复 error handler lambda 未使用的 `this` 捕获，消除 `-Wunused-lambda-capture` 警告
4. `g++ -std=c++20 -O3 -Wall -Werror` 编译通过，零警告零错误
5. 更新审计文档：修复进度、问题状态、安全机制清单、修复路线图、复审结论

---

## 并发性分析

### 并发模型

```
RpcServer (N workers, 默认 4)
    ├── thread 1 → thread_local DbConn (sqlite3*)
    ├── thread 2 → thread_local DbConn (sqlite3*)
    ├── thread 3 → thread_local DbConn (sqlite3*)
    └── thread 4 → thread_local DbConn (sqlite3*)

Cleanup thread → thread_local DbConn (sqlite3*)
```

每个 worker 和清理线程各自持有独立的 `sqlite3*` 连接（`thread_local`），通过 WAL 模式实现并发读写。

### 优势

| 机制 | 位置 | 效果 |
|------|------|------|
| **WAL 模式** | [L223](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L223) | 读不阻塞写、写不阻塞读 |
| **per-thread 连接** | [L337-341](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L337-L341) | 无跨线程共享，零连接池锁 |
| **busy_timeout=5000** | [L226](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L226) | 写-写冲突自动等锁最多 5 秒 |
| **无 GIL** | — | C++ 线程真并行 |

### 瓶颈与风险
1. **写-写串行化** — SQLite 同一时刻只允许一个写事务，典型写入 300-800 TPS，对当前规模足够
2. **无批量事务** — 每条请求独立 auto-commit，无原子批量操作（Python 版同）
3. **清理与业务写竞争** — 大批量 DELETE 持锁数百毫秒可能阻塞业务写入
4. **`_schema_mtx` 全局锁** — 仅首次反射表结构时持锁，缓存命中无锁，影响极小

### 与 Python 版对比

| 维度 | Python db (dbv2.py) | C++ db2 |
|------|---------------------|---------|
| 并发模型 | N worker + 共享 engine | N worker + per-thread 连接 |
| GIL 限制 | 有 | 无，真并行 |
| 连接管理 | SQLAlchemy 连接池 | thread_local 独占 |
| 写冲突处理 | SQLAlchemy 自动重试 | busy_timeout=5000 |

---

## 复审结论

**当前状态**：P0/P1/P2 级问题已全部修复（6/6），仅剩 P3 级 4 个低危健壮性改进项。SQL 注入防护机制完整，配置误操作风险已全部消除，核心安全有保障。剩余 P3 问题均不直接导致安全漏洞：
- P3-7 `gmtime()` 线程安全：实际数据损坏概率极低（只有多线程同时写日志时竞争静态缓冲区，时间精度只到秒）
- P3-9 columns 类型检查：仅影响错误信息友好度，不会注入或崩溃
- P3-10 `sqlite3_close_v2`：异常路径防御性改进，当前正常路径全部正确 finalize
- P3-11 友好错误信息：仅影响配置排错效率

**与 Python 版对比**：安全基线已超越 Python 版——C++ 版新增了 schema 版本感知自动失效、空 values 校验、未知运算符报错、limit/offset 非负校验、WHERE/IN 输入规模上限、表名格式校验、日志截断、配置范围校验等防护，Python 版尚未实现这些。

**生产可用性**：✅ **可用于生产**。当前版本在配置正确（默认配置全部合法）的前提下运行稳定。剩余 P3 问题可按需排期修复。
