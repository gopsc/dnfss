# db2 模块安全审计报告

**审计日期**: 2026-08-06  
**审计范围**: `db2/src/db2.cpp`（全部实现）、`Makefile`、`docker-entrypoint.sh`  
**对照基准**: Python 版 `db/dbv2.py`（接口兼容性对比）  
**代码版本**: main 分支 fad749f

---

## 修复进度

| 优先级 | 总数 | 已修复 | 未修复 |
|--------|------|--------|--------|
| P0 | 2 | 2 | 0 |
| P1 | 1 | 1 | 0 |
| P2 | 2 | 2 | 0 |
| P3 | 4 | 1 | 3 |
| **合计** | **9** | **6** | **3** |

---

## P0 — 严重问题

### P0-1: 列名 SQL 注入（列名未校验直接拼接 SQL）

- **位置**: `db2.cpp` L399-455（where keys）、L518-523（columns）、L536-544（order_by）、L609-619（INSERT col_list）、L654-663（UPDATE set_clause）
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: 新增 `_validate_col` 静态方法，对所有列名（where keys、columns、order_by、INSERT/UPDATE values keys）校验其存在于表 schema 的 `col_map` 中，不存在则抛 `"unknown column"` 错误。5 个注入点全部修复。

  `sqlite3_prepare_v2` 只编译单条语句（`; DROP TABLE` 不会执行），但**单语句内注入**仍然可行：

  ```json
  // 攻击 1：DELETE 全表
  {"action": "delete", "table": "Users", "where": {"1=1 OR 1": 1}}
  // → DELETE FROM Users WHERE 1=1 OR 1 = '1'   ← 删除全部行

  // 攻击 2：SELECT 数据泄露（UNION 注入）
  {"action": "select", "table": "Users",
   "columns": ["password_hash FROM Users UNION SELECT secret FROM Secrets--"]}
  // → SELECT password_hash FROM Users UNION SELECT secret FROM Secrets-- FROM Users

  // 攻击 3：ORDER BY 子查询泄露
  {"action": "select", "table": "Users",
   "order_by": [{"column": "(SELECT password_hash FROM Users LIMIT 1)"}]}
  // → SELECT * FROM Users ORDER BY (SELECT password_hash FROM Users LIMIT 1)
  ```

  各注入点汇总：

  | 注入点 | 代码位置 | 拼接方式 |
  |--------|----------|----------|
  | `where` 的 key | L399-452 | `parts.push_back(col + " = ?")` |
  | `columns` 数组元素 | L518-523 | `col_expr += std::string(c.as_string())` |
  | `order_by[].column` | L536-544 | `order += std::string(o.at("column").as_string())` |
  | `values` 的 key (INSERT) | L609-619 | `col_list += std::string(k)` |
  | `values` 的 key (UPDATE) | L654-663 | `set_clause += std::string(k) + " = ?"` |

- **与 Python 版差异**: Python 版通过 SQLAlchemy 的 `table.columns[col]` 解析列名，列不存在时抛 `KeyError`，不可能注入。C++ 版缺失此校验，是**安全回归**。
- **修复建议**: 对所有列名做白名单校验——要么与表名同样校验 `[A-Za-z0-9_]`，要么严格检查列名存在于 `col_map` 中（不存在则抛错）。最稳妥的做法：两者都做。

---

### P0-2: 空 `in` 数组 → 无 WHERE 子句 → 全表删除/更新

- **位置**: `db2.cpp` L439
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: 空 `in` 数组现在生成恒假条件 `parts.push_back("0")`，使 WHERE 子句匹配零行，与 Python 版 SQLAlchemy `column.in_([])` 行为一致。

---

## P1 — 高危问题

### P1-1: 所有值绑定为 TEXT，类型信息丢失

- **位置**: `db2.cpp` L368-392（`_coerce_value` / `_bind_value`）、L476-480（`_bind_params`）
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: 
  - `_coerce_value` 现在返回 `boost::json::value`，仅对字符串做 `$now` 替换，其他类型保持原样。
  - 新增 `_bind_value`，按 JSON 类型分别调用 `sqlite3_bind_int64` / `sqlite3_bind_double` / `sqlite3_bind_text` / `sqlite3_bind_null`。
  - `WhereClause::params` 改为 `std::vector<json::value>`，删除原来的 `is_null` 向量；`_bind_params` 统一调用 `_bind_value`。

  修复后 INTEGER/REAL 等数值类型以原生 SQLite 存储类写入，避免 TEXT 列上的字符串比较问题，行为与 Python 版一致。

---

## P2 — 中危问题

### P2-1: 清理时间戳格式不一致（T vs 空格）导致保留期偏移约 1 天

- **位置**: `db2.cpp` L54-60（`utc_now`）、L787-789（`_do_cleanup`）
- **当前状态**: ✅ 已修复（2026-08-07）
- **修复**: 清理 SQL 改用 `strftime('%Y-%m-%dT%H:%M:%S','now','-N days')`，与 `utc_now()` 的 `T` 分隔格式完全对齐，截止时间字符串比较正确，保留期精确匹配配置天数。

- **原始问题**:

  存储的时间戳用 `utc_now()`，格式 `%Y-%m-%dT%H:%M:%S`（T 分隔）。清理 SQL 用 SQLite 内置 `datetime('now', '-N days')`，格式 `%Y-%m-%d %H:%M:%S`（空格分隔）：

  ```cpp
  "DELETE FROM Logs WHERE ts < datetime('now', '-"
  + std::to_string(_cfg.retention_days) + " days')"
  ```

  SQLite 字符串比较时 `T`(0x54) > 空格(0x20)，导致与截止日同日期的记录 `ts < cutoff` 为 false，**不会被删除**。实际保留期比配置多约 1 天。

- **与 Python 版差异**: Python 版两端都用 `datetime.isoformat()`（T 分隔），一致。
- **修复建议**: 用 `strftime('%Y-%m-%dT%H:%M:%S','now',-N days)` 或在 C++ 端算截止时间后参数绑定。

---

### P2-2: 缺少配置完整性校验

- **位置**: `db2.cpp` L131-185（`load_config`）
- **当前状态**: ✅ 已修复（2026-08-07）
- **修复**: 在 INI 解析后、环境变量覆盖前，对 7 个必需字段（`[server] host/port/workers`、`[database] url`、`[logs] retention_days/max_records/cleanup_interval`）做必填校验，缺失任一则 `fprintf` 明确错误信息后 `exit(1)`，不再用错误默认值静默运行。

- **原始问题**:

  `load_config` 对缺失配置项直接用内置默认值兜底（`get(sec, key, cfg.xxx)` 模式）。Python 版（`dbv2.py` L118-129）校验所有必需配置项，缺失则退出。

  后果：confs 提供的 `db.ini` 拥缺字段时 db2 不会报错，可能用错误的默认值静默运行（如 `db_url` 缺失则用 `sqlite:///database.db`）。

- **与 Python 版差异**: Python 版严格校验，C++ 版宽松兜底。
- **修复建议**: 对 `[server] host/port/workers`、`[database] url`、`[logs]` 三项做必填校验，缺失则 `exit(1)`。

---

## P3 — 低危问题

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| P3-1 | schema 缓存永不失效 | L316-351 | 运行时 `ALTER TABLE ADD COLUMN` 后缓存过期，可能导致查询/写入用错误列信息。两版均存在，实际影响低（运行时改表罕见） |
| P3-2 | `_coerce_value` 对 null 返回 `""`（注释误导） | L368-380 | ✅ 已修复（2026-08-06）：`_coerce_value` 改为返回 `json::value`，null 保持为 null，不再返回空字符串；`is_null` 向量已删除 |
| P3-3 | `limit`/`offset` 未校验负值 | L548-551 | `as_int64()` 可为负，SQLite 对负 LIMIT 返回全表。非安全问题但行为可能意外 |
| P3-4 | WHERE 条件数 / IN 数组大小无上限 | — | 恶意客户端可发送数千 WHERE 条件或百万 IN 元素耗尽资源。Python 版同样存在（既有问题） |

---

## 已有安全机制（正确实现）

1. **表名校验** — `db2.cpp` L493-496：仅允许 `[A-Za-z0-9_]`，防止表名级 SQL 注入
2. **值参数化绑定** — `_build_where`/`_do_insert`/`_do_update` 中所有 value 通过 `_bind_value` 按类型绑定，无值级 SQL 注入
3. **per-thread 连接** — L308-312：`thread_local` 连接，避免跨线程共享 prepared statement
4. **异常脱敏** — `RpcServer::_sanitize_error`（libmsg）：正则替换绝对路径为 `<redacted>`
5. **仅操作已有表** — `PRAGMA table_info` 反射，无法建表/删表
6. **SQLite PRAGMA 优化** — WAL + synchronous=NORMAL + foreign_keys=ON + busy_timeout=5000 + mmap_size=256MB，与 Python 版一致
7. **schema 缓存加锁** — `_schema_mtx` 保护 `_schema_cache`，线程安全
8. **confs 引导重试** — `retry_on_refused=true`，不要求启动顺序
9. **日志内部直写** — `_log_internal` 绕过 RPC 防递归，异常静默不阻塞业务
10. **后台清理线程** — 条件变量优雅停止，按时间+条数双策略清理

---

## 修复路线图

### 立即（24小时）
- [x] **P0-1**: 列名白名单校验（校验列名在 `col_map` 中存在 + `[A-Za-z0-9_]` 格式校验）
- [x] **P0-2**: 空 `in` 生成恒假条件

### 短期（72小时）
- [x] **P1-1**: 按 JSON 类型分别使用 `sqlite3_bind_int64` / `sqlite3_bind_double` / `sqlite3_bind_text`
- [x] **P2-2**: 配置完整性校验（必需字段缺失则退出）

### 中期
- [x] **P2-1**: 清理时间戳格式统一为 T 分隔
- [ ] **P3-3**: limit/offset 负值校验
- [ ] **P3-4**: WHERE 条件数 / IN 数组大小上限

---

## 审计方法

1. 通读 `db2/src/db2.cpp` 全部 851 行实现
2. 逐函数对照 Python 版 `db/dbv2.py` 验证接口兼容性与行为差异
3. 审查 `libmsg/inc/us/RpcServer.hpp`/`RpcClient.hpp` 确认框架层异常脱敏机制
4. 审查 `confs/configs/db.ini` 确认配置链
5. 审查 `docker-entrypoint.sh` 确认 schema 初始化
6. 逐注入点构造 PoC 请求验证 SQL 注入可行性（DELETE 全表、UNION 数据泄露、ORDER BY 子查询）
7. 追踪空 `in` 数组代码路径确认全表删除行为
