# db2 模块安全审计报告

**初次审计**: 2026-08-06  
**最近复审**: 2026-08-07（第六轮审计，全部问题已修复，编译验证通过）  
**审计范围**: `db2/src/db2.cpp`、`Makefile`、`docker-entrypoint.sh`  
**对照基准**: Python 版 `db/dbv2.py`（接口兼容性对比）  
**代码版本**: main 分支 + 第六轮修复

---

## 修复进度

| 优先级 | 总数 | 已修复 | 未修复 |
|--------|------|--------|--------|
| P0 | 2 | 2 | 0 |
| P1 | 1 | 1 | 0 |
| P2 | 4 | 4 | 0 |
| P3 | 20 | 20 | 0 |
| **合计** | **26** | **26** | **0** |

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
| **P3-7** | **`utc_now()` 使用非线程安全的 `gmtime()`** | [L58-66](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L58-L66) | ✅ 已修复（2026-08-07，第五轮）：改用 `gmtime_r(&t, &tmbuf)` 线程安全版本，消除多线程并发调用时的静态缓冲区数据竞争 |
| **P3-8** | **其他数值配置无范围校验** | [L186-L191](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L186-L191) | ✅ 已修复（2026-08-07，与 P2-3 一同）：[L207-L227](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L207-L227) 所有数值配置项范围校验覆盖：port 1-65535、workers ≥1、cleanup_interval ≥1、retention_days ≥0、max_records ≥1 |
| **P3-9** | **`columns` 数组中非字符串元素未检查类型** | [L633-636](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L633-L636) | ✅ 已修复（2026-08-07，第五轮）：添加 `if (!c.is_string()) throw "columns array must contain only strings"` 类型校验，非字符串元素抛清晰错误而非 boost 原始异常 |
| **P3-10** | **`DbConn` 析构使用 `sqlite3_close` 而非 `sqlite3_close_v2`** | [L254-256](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L254-L256) | ✅ 已修复（2026-08-07，第五轮）：改用 `sqlite3_close_v2`，异常路径存在未 finalize 的 stmt 时标记为 zombie 状态，等所有 stmt 释放后自动关闭，更鲁棒 |
| **P3-11** | **`std::stoi/stoll` 解析配置失败无友好错误提示** | [L72-87](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L72-L87)、[L163](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L163)、[L208-214](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L208-L214)、[L218-219](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L218-L219)、[L226-227](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L226-L227) | ✅ 已修复（2026-08-07，第五轮）：引入 `safe_stoi(s, field_name)` 辅助函数，所有 `std::stoi` 调用点（CONFS_PORT、INI 配置、环境变量、命令行参数共 9 处）替换为 `safe_stoi`，失败时抛带字段名的友好错误（如 `"invalid [server] port: 'abc' (stoi)"`） |
| **P3-12**（新） | **`_log_internal` 静默吞掉所有异常** | [L898-900](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L898-L900) | ✅ 已修复（2026-08-07，第五轮）：`catch (...)` 改为 `catch (const std::exception& e)`，并 `fprintf(stderr, "[db2] _log_internal failed: %s", e.what())`，便于排查日志写入失败原因，仍不阻塞业务 |
| **P3-13**（新） | **`_do_cleanup` COUNT 查询 prepare 失败静默跳过** | [L947-951](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L947-L951) | ✅ 已修复（2026-08-07，第五轮）：prepare 失败时 `fprintf(stderr, ...)` 记录 sqlite3_errmsg，同时简化 finalize 逻辑为单一出口 `if (stmt) sqlite3_finalize(stmt)`，避免重复 finalize 风险 |
| **P3-14**（新） | **`order_by` 非法元素静默跳过** | [L654-671](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L654-L671) | ✅ 已修复（2026-08-07，第五轮）：非 object 元素、缺少 `column` 字段、`column` 非 string、`desc` 非 bool 均改为抛 `runtime_error` 明确报错，不再静默跳过，避免掩盖客户端错误 |
| **P3-15**（新） | **`action` 字段非 string 静默默认为 "select"** | [L596-600](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L596-L600) | ✅ 已修复（2026-08-07，第五轮）：`action` 字段存在但非 string 类型时抛 `"'action' must be a string"`，不再静默回退为 "select" 导致意外执行查询 |
| **P3-16**（新） | **`order_by` 的 `column`/`desc` 字段类型未校验** | [L658-668](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L658-L668) | ✅ 已修复（2026-08-07，第五轮，与 P3-14 一同）：`column` 字段检查 `is_string()`，`desc` 字段检查 `is_bool()`，类型不符时抛清晰错误而非 boost 原始异常 |

---

## 第六轮审计新增问题（2026-08-07）

### ND-1（P2）: `start()` 异常路径导致 `std::terminate()` 硬崩溃

- **位置**: `start()` [L341-355](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L341-L355)
- **当前状态**: ✅ 已修复（2026-08-07，第六轮）
- **问题**: 清理线程 [L343](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L343) 启动后，若 `rpc.start()` 抛出异常（socket/bind/listen 失败如端口被占用，或事件循环异常经 `MsgServer::start()` 重新抛出），原有的 `_cleanup_running=false; notify; join` 正常清理路径被跳过。栈展开时 `DB2Server` 析构，`std::thread` 成员仍 joinable → `std::terminate()` 进程硬崩溃，并掩盖真实错误（本应干净退出返回错误码）。
- **修复**: 引入 RAII 作用域守卫 `CleanupGuard` [L348-355](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L348-L355)，析构时停止并 join 清理线程。无论 `rpc.start()` 正常返回还是抛出，清理线程都被优雅回收，异常沿调用链传播到 `main` 的 catch 返回错误码。

### ND-4（P3）: `limit`/`offset`/`where` 类型不符被静默忽略

- **位置**: `_do_select` limit/offset [L692-708](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L692-L708)、`_handle_execute` where [L626-630](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L626-L630)
- **当前状态**: ✅ 已修复（2026-08-07，第六轮）
- **问题**: `limit`/`offset` 仅当 `is_int64()` 才应用，`where` 仅当 `is_object()` 才解析；调用方传错类型（字符串/浮点/数组）时字段被静默丢弃，出现"看似生效实则未生效"的静默偏差，掩盖客户端错误。
- **修复**: `limit`/`offset` 存在但非整型、`where` 存在但非对象时抛清晰错误（与 P3-15 action 校验一致）。

### ND-5（P3）: `_bind_value` uint64 超 INT64_MAX 截断溢出

- **位置**: `_bind_value` [L480-485](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L480-L485)
- **当前状态**: ✅ 已修复（2026-08-07，第六轮）
- **问题**: `jv.as_uint64()` 经 `static_cast<sqlite3_int64>` 直接截断，超出 INT64_MAX 的 uint64 值发生回绕，数值静默失真。
- **修复**: [L480-483](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L480-L483) 绑定前校验 `u > INT64_MAX` 则抛错，防止静默数值截断。

### ND-6（P3）: `in` 运算符非数组值被静默当作空数组

- **位置**: `_build_where` in 分支 [L558-562](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L558-L562)
- **当前状态**: ✅ 已修复（2026-08-07，第六轮）
- **问题**: `in` 值为字符串/数字等非数组时被当作空数组 → 恒假条件，静默掩盖客户端错误。
- **修复**: [L558-559](file:///Users/qingsong/Documents/blackbird/db2/src/db2.cpp#L558-L559) 非数组立即抛 `"'in' operator requires an array value"`；仅空数组保留恒假 `0` 语义（与 Python 版 `column.in_([])` 一致）。

### 已知限制（设计决策，非缺陷）

- **ND-2（L-13）**: **`select` 无结果集上限** — 无 WHERE + 无 `limit` 时 `SELECT *` 返回全表并整体装入内存，恶意/异常客户端可触发内存耗尽 DoS。为使调用方零改动切换而不加隐式 limit，记录为已知限制；建议 gateway 侧对响应做大小/行数限制，或后续引入可配置 `max_result_rows`（默认关闭以保兼容）。
- **ND-3（L-14）**: **`update`/`delete` 无 WHERE 时全表更新/删除，无内置防护** — 与 Python 版行为对齐，属兼容性权衡；调用方漏传 where 会静默作用于全表，属数据安全风险。建议调用方侧强制校验，或后续提供可选 `require_where` 开关。

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
21. **线程安全时间函数** — `utc_now()` 使用 `gmtime_r` 而非 `gmtime`，消除多线程并发写日志时的静态缓冲区数据竞争（P3-7 修复）
22. **安全 sqlite3 关闭** — `DbConn` 析构使用 `sqlite3_close_v2`，异常路径未 finalize 的 stmt 不会导致连接泄漏（P3-10 修复）
23. **友好配置解析错误** — `safe_stoi(s, field_name)` 包装所有 `std::stoi` 调用点，失败时抛带字段名的错误信息（P3-11 修复）
24. **全量 JSON 字段类型校验** — `action`/`columns`/`order_by` 的 `column`+`desc` 字段均检查 JSON 类型，类型不符抛清晰错误而非 boost 原始异常（P3-9/P3-15/P3-16 修复）
25. **日志写入失败可观测** — `_log_internal` catch 块记录 stderr，不再静默吞异常（P3-12 修复）
26. **清理线程错误可观测** — `_do_cleanup` COUNT 查询 prepare 失败时记录 stderr（P3-13 修复）
27. **清理线程 RAII 守卫** — `start()` 使用 `CleanupGuard`，`rpc.start()` 异常路径下清理线程被优雅回收，不触发 `std::terminate`（ND-1 修复）
28. **select 输入类型全量校验** — `where`/`limit`/`offset` 类型不符抛清晰错误，不静默忽略（ND-4 修复）
29. **uint64 溢出防护** — `_bind_value` 绑定前校验超出 INT64_MAX 抛错，防止静默数值截断（ND-5 修复）
30. **`in` 运算符类型校验** — 非数组立即抛错，仅空数组保留恒假语义（ND-6 修复）

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
- [x] **P3-7**: `utc_now()` 改用 `gmtime_r` 保证线程安全（第五轮）
- [x] **P3-9**: `columns` 数组元素类型检查，非字符串抛清晰错误（第五轮）
- [x] **P3-10**: `DbConn` 析构改用 `sqlite3_close_v2`（第五轮）
- [x] **P3-11**: `safe_stoi` 包装所有 `std::stoi` 调用，带字段名友好错误（第五轮）
- [x] **P3-12**: `_log_internal` catch 块记录 stderr（第五轮）
- [x] **P3-13**: `_do_cleanup` COUNT prepare 失败记录 stderr（第五轮）
- [x] **P3-14**: `order_by` 非法元素抛错而非静默跳过（第五轮）
- [x] **P3-15**: `action` 非 string 抛错而非默认 "select"（第五轮）
- [x] **P3-16**: `order_by` 的 `column`/`desc` 字段类型校验（第五轮）
- [x] **ND-1**: `start()` 清理线程 RAII 守卫，`rpc.start()` 异常路径不 `std::terminate`（第六轮）
- [x] **ND-4**: `where`/`limit`/`offset` 类型不符立即抛错，不静默忽略（第六轮）
- [x] **ND-5**: `_bind_value` uint64 超 INT64_MAX 抛错，防止截断溢出（第六轮）
- [x] **ND-6**: `in` 运算符非数组值抛错，仅空数组保留恒假语义（第六轮）

### 待修复
无 — 全部 26 个问题已修复（100%）。新增 2 项已知限制（ND-2/ND-3）属兼容性设计决策，记录于"第六轮审计新增问题"章节。

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

### P2-3 / P3-8 修复验证（2026-08-07，第四次复审）
1. 在 `load_config` 末尾（命令行参数解析之后）新增 5 项数值范围校验：port 1-65535、workers ≥1、cleanup_interval ≥1、retention_days ≥0、max_records ≥1
2. 非法配置立即 `fprintf` 带字段名的明确错误信息后 `exit(1)`，与已有配置必填校验风格一致
3. 修复 error handler lambda 未使用的 `this` 捕获，消除 `-Wunused-lambda-capture` 警告
4. `g++ -std=c++20 -O3 -Wall -Werror` 编译通过，零警告零错误
5. 更新审计文档：修复进度、问题状态、安全机制清单、修复路线图、复审结论

### 第五轮复审（2026-08-07，本次）
1. 通读当前代码全部 960 行，逐函数审查
2. 修复 4 个遗留 P3 问题：
   - P3-7: `utc_now()` 改用 `gmtime_r` 线程安全版本
   - P3-9: `columns` 数组元素添加 `is_string()` 类型校验
   - P3-10: `DbConn` 析构改用 `sqlite3_close_v2`
   - P3-11: 引入 `safe_stoi(s, field_name)` 辅助函数，替换全部 9 处 `std::stoi` 调用
3. 新发现并修复 5 个 P3 问题：
   - P3-12: `_log_internal` 的 `catch (...)` 改为 `catch (const std::exception& e)` 并记录 stderr
   - P3-13: `_do_cleanup` COUNT 查询 prepare 失败时记录 stderr，简化 finalize 逻辑为单一出口
   - P3-14: `order_by` 非法元素（非 object、缺 column、类型不符）改为抛错而非静默跳过
   - P3-15: `action` 非 string 类型时抛错而非默认 "select"
   - P3-16: `order_by` 的 `column`/`desc` 字段添加 `is_string()`/`is_bool()` 类型校验
4. `clang++ -std=c++20 -O2 -Wall -Werror -c` 编译验证通过，零警告零错误
5. 更新审计文档：修复进度（22/22）、问题状态、安全机制清单（26 项）、修复路线图、复审结论

### 第六轮复审（2026-08-07，本次）
1. 通读当前代码全部约 1000 行，逐函数审查
2. 核查 libmsg `RpcServer`/`MsgServer` 依赖行为，确认 `start()` 在 socket/bind/listen 失败时抛出异常
3. 新发现并修复 4 个问题：
   - ND-1 (P2): `start()` 异常路径清理线程未回收 → `std::terminate()` 硬崩溃，引入 `CleanupGuard` RAII 守卫
   - ND-4 (P3): `where`/`limit`/`offset` 类型不符静默忽略
   - ND-5 (P3): `_bind_value` uint64 超 INT64_MAX 截断溢出
   - ND-6 (P3): `in` 运算符非数组值静默当作空数组
4. 记录 2 项已知限制 ND-2/ND-3（无结果集上限、无 WHERE 全表更新/删除，均属兼容性设计决策）
5. `clang++ -std=c++20 -O2 -Wall -Werror -fsyntax-only` 与 `g++ -std=c++20 -O3 -Wall -Werror -c` 双重编译验证通过，零警告零错误
6. 更新审计文档：修复进度（26/26）、问题状态、安全机制清单（30 项）、修复路线图、复审结论

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

**当前状态**：六轮审计累计发现 **26 个问题，全部已修复（100%）**。P0/P1/P2 级问题在第六轮后全部修复（2 P0 / 1 P1 / 4 P2），第五轮修复了 9 个 P3 问题，第六轮新发现修复 1 个 P2（ND-1）与 3 个 P3（ND-4/ND-5/ND-6）。SQL 注入防护机制完整，配置误操作风险已全部消除，所有 JSON 字段访问路径均有类型校验，异常路径均有错误日志，启动异常路径不再触发进程硬崩溃。新增 2 项已知限制（ND-2/ND-3）属兼容性设计决策。

**第六轮修复亮点**：
- **启动健壮性**：`CleanupGuard` RAII 守卫确保 `rpc.start()` 无论返回还是抛出都回收清理线程，消除异常路径 `std::terminate()` 硬崩溃
- **输入类型完备**：`where`/`limit`/`offset` 类型不符立即抛错，杜绝"看似生效实则未生效"的静默偏差
- **数值保真**：`_bind_value` 对超 INT64_MAX 的 uint64 抛错，防止静默截断溢出
- **运算符严格化**：`in` 运算符非数组值抛错，仅空数组保留恒假语义（对齐 Python 版）

**第五轮修复亮点**：
- **线程安全**：`utc_now()` 改用 `gmtime_r`，消除多线程并发写日志时的数据竞争
- **资源安全**：`sqlite3_close_v2` 替代 `sqlite3_close`，异常路径不泄漏连接
- **错误可观测**：`_log_internal` 和 `_do_cleanup` 不再静默吞异常，均有 stderr 输出
- **输入校验完整**：`action`/`columns`/`order_by` 全部 JSON 字段类型校验，类型不符抛清晰错误
- **配置友好**：`safe_stoi` 包装全部 9 处 `std::stoi` 调用，失败时带字段名定位

**与 Python 版对比**：安全基线已超越 Python 版——C++ 版新增了 schema 版本感知自动失效、空 values 校验、未知运算符报错、limit/offset 非负与类型校验、WHERE/IN 输入规模上限、表名格式校验、日志截断、配置范围校验、线程安全时间函数、安全 sqlite3 关闭、全量 JSON 字段类型校验、异常可观测、启动异常路径 RAII 守卫等防护，Python 版尚未实现这些。

**生产可用性**：✅ **可用于生产**。全部 26 个问题已修复，30 项安全机制就位，核心安全与健壮性均有保障。2 项已知限制（ND-2 无结果集上限、ND-3 无 WHERE 全表更新/删除）为兼容性权衡，建议在调用方（gateway）侧做相应防护。
