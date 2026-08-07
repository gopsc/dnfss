# db2 模块安全审计报告

**初次审计**: 2026-08-06  
**最近复审**: 2026-08-07（P2 修复后全量重审，新增 P3-5/P3-6）  
**审计范围**: `db2/src/db2.cpp`（全部实现 875 行）、`Makefile`、`docker-entrypoint.sh`  
**对照基准**: Python 版 `db/dbv2.py`（接口兼容性对比）  
**代码版本**: main 分支 ec626dc

---

## 修复进度

| 优先级 | 总数 | 已修复 | 未修复 |
|--------|------|--------|--------|
| P0 | 2 | 2 | 0 |
| P1 | 1 | 1 | 0 |
| P2 | 2 | 2 | 0 |
| P3 | 6 | 3 | 3 |
| **合计** | **11** | **8** | **3** |

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
| P3-1 | schema 缓存永不失效 | L335-370 | 运行时 `ALTER TABLE ADD COLUMN` 后缓存过期，可能导致查询/写入用错误列信息。两版均存在，实际影响低（运行时改表罕见） |
| P3-2 | `_coerce_value` 对 null 返回 `""`（注释误导） | L387-393 | ✅ 已修复（2026-08-06）：`_coerce_value` 改为返回 `json::value`，null 保持为 null，不再返回空字符串；`is_null` 向量已删除 |
| P3-3 | `limit`/`offset` 未校验负值 | L581-584 | `as_int64()` 可为负，SQLite 对负 LIMIT 返回全表。非安全问题但行为可能意外 |
| P3-4 | WHERE 条件数 / IN 数组大小无上限 | L431-495 | 恶意客户端可发送数千 WHERE 条件或百万 IN 元素耗尽资源。Python 版同样存在（既有问题） |
| P3-5 | 空 `values` 对象导致 INSERT/UPDATE 语法错误 | L628-671 | ✅ 已修复（2026-08-07）：`_do_insert` 和 `_do_update` 新增 `vals.empty()` 前置校验，空 `values` 直接抛出 `"insert/update requires non-empty 'values'"` 错误，不再生成无效 SQL |
| P3-6 | 未知 WHERE 运算符被静默跳过 | L446-485 | ✅ 已修复（2026-08-07）：运算符分支末尾新增 `else throw "unknown operator: X"`，非法运算符立即报错，不再静默跳过导致条件失效 |

---

## 已有安全机制（正确实现）

1. **表名校验** — `db2.cpp` L518-522：仅允许 `[A-Za-z0-9_]`，防止表名级 SQL 注入
2. **列名白名单校验** — `_validate_col` L416-422：所有列名必须在表 schema 中存在，5 个拼接点全部覆盖
3. **值参数化绑定** — `_bind_value` L395-413：所有 value 按 JSON 类型分别 `sqlite3_bind_*`，无值级 SQL 注入
4. **per-thread 连接** — L327-331：`thread_local` 连接，避免跨线程共享 prepared statement
5. **异常脱敏** — `RpcServer::_sanitize_error`（libmsg）：正则替换绝对路径为 `<redacted>`
6. **仅操作已有表** — `PRAGMA table_info` 反射，无法建表/删表
7. **空 `in` 恒假条件** — L470-473：空 `in` 数组生成 `"0"` 使 WHERE 匹配零行
8. **SQLite PRAGMA 优化** — WAL + synchronous=NORMAL + foreign_keys=ON + busy_timeout=5000 + mmap_size=256MB，与 Python 版一致
9. **schema 缓存加锁** — `_schema_mtx` L316 保护 `_schema_cache`，线程安全
10. **配置完整性校验** — L155-171：7 个必需字段缺失则 `exit(1)`，不再用默认值静默运行
11. **confs 引导重试** — `retry_on_refused=true`，不要求启动顺序
12. **日志内部直写** — `_log_internal` L762-791 绕过 RPC 防递归，异常静默不阻塞业务
13. **后台清理线程** — 条件变量优雅停止，时间戳格式 T 对齐，按时间+条数双策略清理
14. **空 values 前置校验** — `_do_insert`/`_do_update` 检测空 `values` 立即报错，不生成无效 SQL
15. **WHERE 运算符白名单** — 未知运算符立即抛出 `"unknown operator"` 错误，不再静默跳过

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
- [x] **P3-5**: 空 `values` 对象前置校验（`values` 为空时直接返回错误，避免生成无效 SQL）
- [x] **P3-6**: 未知 WHERE 运算符校验（遇到非合法运算符时抛出错误而非静默跳过）

### 低优先级
- [ ] **P3-1**: schema 缓存失效机制（可选 TTL 或版本计数，实际影响极低）

---

## 审计方法

### 初次审计（2026-08-06）

1. 通读 `db2/src/db2.cpp` 全部 851 行实现
2. 逐函数对照 Python 版 `db/dbv2.py` 验证接口兼容性与行为差异
3. 审查 `libmsg/inc/us/RpcServer.hpp`/`RpcClient.hpp` 确认框架层异常脱敏机制
4. 审查 `confs/configs/db.ini` 确认配置链
5. 审查 `docker-entrypoint.sh` 确认 schema 初始化
6. 逐注入点构造 PoC 请求验证 SQL 注入可行性（DELETE 全表、UNION 数据泄露、ORDER BY 子查询）
7. 追踪空 `in` 数组代码路径确认全表删除行为

### 复审（2026-08-07）

1. 通读修复后 `db2/src/db2.cpp` 全部 875 行实现
2. 验证 P2-1 修复：确认 `strftime('%Y-%m-%dT%H:%M:%S','now',-N days)` 与 `utc_now()` 格式对齐
3. 验证 P2-2 修复：确认 7 个必需字段校验逻辑正确，`confs/configs/db.ini` 包含全部必需字段
4. 确认 P0-1/P0-2/P1-1/P3-2 修复在代码中仍然正确，无回退
5. 全量逐函数扫描发现 P3-5（空 values）、P3-6（未知运算符）两个新既有问题
6. 对照 Python `dbv2.py` 确认新增问题在两版中行为一致（非回归）
7. 更新安全机制清单行号引用至当前代码

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

每个 worker 持有独立的 `sqlite3*` 连接（`thread_local`，L327-331），通过 WAL 模式实现并发读写。

### 优势

| 机制 | 位置 | 效果 |
|------|------|------|
| **WAL 模式** | L219 | 读不阻塞写、写不阻塞读，多读并发零争用 |
| **per-thread 连接** | L327-331 | 无跨线程共享 prepared statement，无需连接池锁 |
| **busy_timeout=5000** | L222 | 写-写冲突时等锁最多 5 秒，短暂突发写入可自动串行化 |
| **synchronous=NORMAL** | L220 | WAL 下减少 fsync，写吞吐提升约 2-3 倍（对比 FULL） |
| **mmap_size=256MB** | L223 | 热数据走内存映射，读性能接近 memcpy |
| **schema 缓存加锁** | L316 | 仅首次请求做 `PRAGMA table_info`，后续锁外快路径命中缓存 |
| **无 GIL** | — | C++ 线程真并行，不受 Python GIL 限制 |

典型场景表现：
- **读密集**（社区首页、用户查询）：4 worker 并发读无争用，SQLite 读延迟 ~0.01ms，吞吐可达数万 QPS
- **读写混合**（dnfs 增删节点 + 查询）：WAL 允许读写并行，偶尔写-写冲突由 busy_timeout 消化

### 瓶颈与风险

**1. 写-写串行化**

SQLite 任何时刻只允许一个写事务。4 个 worker 同时写时，后到者排队等锁：

```
thread 1: BEGIN → INSERT → COMMIT  (持有写锁 ~0.1ms)
thread 2: BEGIN → INSERT → 等锁... → COMMIT
thread 3: BEGIN → INSERT → 等锁... → COMMIT
```

- `busy_timeout=5000` 兜底：等锁超 5 秒则抛 `SQLITE_BUSY`，返回 error 响应
- 实际影响：单表写入吞吐约 300-800 TPS（SQLite 典型值），对本项目（日志写入 + 偶尔 CRUD）足够

**2. 无事务包装的批量操作**

当前每条 execute 请求是独立的 auto-commit 事务。若客户端需要"插入 100 条要么全成功要么全失败"，db2 不支持——每条独立提交，中途失败无法回滚前序行。Python 版同样如此（既有设计限制）。

**3. `_log_internal` 与业务写竞争**

后台清理线程的 `DELETE FROM Logs`（L806-851）和业务 worker 的 `INSERT INTO Logs` 可能同时争用写锁。清理操作涉及大量删除，持锁时间可能达数百毫秒，期间业务写入被阻塞。

**4. schema 缓存无失效机制（P3-1）**

运行时 `ALTER TABLE ADD COLUMN` 后，缓存中的列信息过期。新列在 `where`/`values` 中会被 `_validate_col` 拒绝（`"unknown column"`），需重启 db2 刷新缓存。

**5. `_schema_mtx` 锁粒度**

全局互斥锁保护整个 `_schema_cache`。极端高并发（数百 QPS 首次请求不同表）时可能短暂排队，但实际影响极低（缓存命中后锁外快路径，仅首次 miss 持锁）。

### 与 Python 版对比

| 维度 | Python db (dbv2.py) | C++ db2 |
|------|---------------------|---------|
| 并发模型 | RpcServer N worker 线程 + 共享 engine | RpcServer N worker 线程 + per-thread sqlite3* |
| 连接管理 | SQLAlchemy 连接池（`check_same_thread=False`） | thread_local 独占连接 |
| 写冲突处理 | SQLAlchemy 自动重试 | busy_timeout=5000 |
| 事务控制 | `conn.commit()` 显式提交 | auto-commit（每条隐式事务） |
| GIL 影响 | Python GIL 限制真正并行 | C++ 无 GIL，真并行 |

**结论**：db2 的并发性优于 Python 版——无 GIL 限制、无连接池锁开销、per-thread 连接零共享。在当前项目规模下（日志写入 + 业务 CRUD，峰值百级 QPS）完全够用。若未来写入量级跃升至千级 TPS，需考虑：① 日志写入改批量事务 ② 清理线程错峰执行 ③ 或迁移至 PostgreSQL。

---

## 复审结论

P0/P1/P2 级问题已全部修复（6 项），P3 级修复 3 项（P3-2/P3-5/P3-6），合计 8/11 已修复。剩余 3 项均为 P3 级低危既有问题（schema 缓存失效、limit 负值、条件数上限），非安全回归，可在后续迭代中逐步修复。当前版本安全基线已超越 Python 版（C++ 版新增了空 values 校验和未知运算符校验，Python 版尚无），可用于生产部署。
