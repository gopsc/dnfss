# libsqlite3 模块安全审计报告

**审计日期**: 2026-08-06  
**审计范围**: `libsqlite3/inc/us/Sqlite3.hpp`、`Makefile`、`_set.sh`、`README.md`  
**代码版本**: main 分支  

---

## 修复进度

| 优先级 | 总数 | 已修复 | 未修复 |
|--------|------|--------|--------|
| P0 | 3 | 2 | 1 |
| P1 | 4 | 1 | 3 |
| P2 | 5 | 2 | 3 |
| **合计** | **12** | **5** | **7** |

---

## 模块现状

`libsqlite3` 当前实际是一个**单头文件 C++ RAII 封装库**，只提供 `qing::Sqlite3`（数据库连接）和 `qing::Sqlite3_stmt`（预编译语句）。

- 整个模块**没有任何单元测试或示例**
- `Makefile` 已经**完全注释掉编译规则**，只保留 `install/uninstall`
- `_set.sh` 只调用 `make install`，不编译任何产物
- 全项目**唯一引用方**是 `gateway/src/main.cpp`（`#include <us/Sqlite3.hpp>`），但对应的使用代码已被注释掉
- `db2` 直接使用原生 `sqlite3.h` API，**没有使用本封装**

结论：**该模块目前处于事实上的废弃/未使用状态**，但头文件仍被 `_inst.sh` 安装到系统目录，存在被误用的风险。

---

## P0 — 严重问题

### P0-1: 未定义拷贝/移动语义，存在 double free / use-after-free

- **位置**: `Sqlite3.hpp` L7-60（`Sqlite3_stmt`）、L62-97（`Sqlite3`）
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: 为 `Sqlite3` 和 `Sqlite3_stmt` 显式 `= delete` 拷贝构造和拷贝赋值，并实现 `noexcept` 移动构造/移动赋值，移动后把源对象指针置 `nullptr`。析构函数先判空再释放，彻底消除 double free 和 use-after-free 风险。

  修复前代码问题：

  ```cpp
  Sqlite3_stmt stmt(std::string& sql) {            // L91
      return Sqlite3_stmt(db, sql.c_str());        // L92
  }
  ```

  `return` 依赖 NRVO/copy elision。若编译器未成功省略，会调用拷贝构造，触发 double free。即使现代编译器通常能省略，代码仍**不是良构且安全的**。

  同理，`Sqlite3` 类也持有 `sqlite3* db` 原始指针，未禁用拷贝/移动，也存在 double free 风险。

---

### P0-2: `Sqlite3::~Sqlite3()` 忽略 `sqlite3_close` 返回值

- **位置**: `Sqlite3.hpp` L74-L76
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: `~Sqlite3()` 改用 `sqlite3_close_v2(db)`，它会在所有 prepared statements 释放后才真正关闭数据库，避免 `SQLITE_BUSY` 导致的数据库未关闭问题。同时析构前判空，防止重复关闭。

---

### P0-3: `Sqlite3::exec` 使用字符串 SQL，无参数化绑定接口

- **位置**: `Sqlite3.hpp` L78-L89
- **当前状态**: ❌ 未修复
- **详情**:

  `bool exec(const std::string& sql)` 接收完整 SQL 字符串。虽然函数本身不拼接，但调用者容易把外部输入拼进 SQL，且该函数**没有提供参数化绑定版本**，迫使调用者自行处理转义或只能用于硬编码 SQL。

- **修复建议**: 至少提供文档警告“禁止拼接用户输入”；更推荐提供参数化版本或强制仅用于常量 SQL。

---

## P1 — 高危问题

### P1-1: `stmt()` 参数应为 `const std::string&`

- **位置**: `Sqlite3.hpp` L91
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: `stmt()` 参数改为 `const std::string& sql`，现在可以传入字符串字面量和临时对象：

  ```cpp
  auto s = db.stmt("SELECT 1"); // 现在可以编译
  ```

---

### P1-2: `bind_int` 只支持 32 位 int

- **位置**: `Sqlite3.hpp` L21-L27
- **当前状态**: ❌ 未修复
- **详情**:

  `bind_int` 调用 `sqlite3_bind_int`，只能绑定 32 位整数。SQLite 的 `INTEGER` 可以存 64 位，如果用户要绑定 `int64_t`，会静默截断。

- **修复建议**: 增加 `bind_int64(int idx, int64_t data)` 使用 `sqlite3_bind_int64`。

---

### P1-3: 缺少 `bind_null`、`bind_double`、`bind_blob` 等接口

- **位置**: `Sqlite3.hpp` L21-L35
- **当前状态**: ❌ 未修复
- **详情**:

  当前只提供 `bind_int` 和 `bind_text`，缺少 `bind_null`、`bind_blob`、`bind_double`、`bind_int64`。功能不完整，无法满足通用数据库操作。

- **修复建议**: 补充上述接口。

---

### P1-4: 异常信息丢失 SQLite 错误详情

- **位置**: `Sqlite3.hpp` L9-L15、L21-L35、L65-L70
- **当前状态**: ❌ 未修复
- **详情**:

  所有异常都使用固定字符串，例如：

  ```cpp
  throw std::runtime_error("Failed to prepare sql stmt");
  ```

  没有把 `sqlite3_errmsg(db)` 或错误码带入异常。调用者无法区分“语法错误”“无此表”“只读数据库”等不同错误。

- **修复建议**: 将 `sqlite3_errmsg(db)` 和错误码拼入异常信息。

---

## P2 — 中危问题

### P2-1: `Sqlite3_stmt` 成员 `stmt` 未显式初始化

- **位置**: `Sqlite3.hpp` L58-L59
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: `Sqlite3` 和 `Sqlite3_stmt` 的成员指针均显式初始化为 `nullptr`，配合移动语义中的置空操作，避免野指针和重复释放。

---

### P2-2: `read_text` 返回指针生命周期未文档化

- **位置**: `Sqlite3.hpp` L54-L56
- **当前状态**: ❌ 未修复
- **详情**:

  `read_text` 返回 `const unsigned char*`，生命周期依赖于 statement。statement finalize 后指针失效，头文件中没有任何文档说明。

- **修复建议**: 在注释中明确说明返回指针的生命周期；或提供返回 `std::string` 的安全版本。

---

### P2-3: 缺少类型安全读取接口

- **位置**: `Sqlite3.hpp` L49-L56
- **当前状态**: ❌ 未修复
- **详情**:

  只有 `read_int` 和 `read_text`，缺少 `read_int64`、`read_double`、`read_blob`、`column_type`、`column_count` 等常用接口。

- **修复建议**: 补充上述接口。

---

### P2-4: `exec` 中 `errMsg` 未初始化且存在危险注释

- **位置**: `Sqlite3.hpp` L78-L85
- **当前状态**: ✅ 已修复（2026-08-06）
- **修复**: `errMsg` 显式初始化为 `nullptr`，并删除危险注释 `//sqlite3_close(db)`，避免未来被误恢复导致 SQL 执行错误时关闭数据库连接。

---

### P2-5: `Makefile` install 目标文案错误

- **位置**: `libsqlite3/Makefile` L27
- **当前状态**: ❌ 未修复
- **详情**:

  ```makefile
  install:
      @echo "install UniServe_libth..."
  ```

  文案是 `libth`，明显是从 `libth/Makefile` 复制过来的，应改为 `UniServe_libsqlite3`。

- **修复建议**: 修正 echo 文本。

---

### P2-6: `Makefile` 与 `_set.sh` 完全无构建/测试步骤

- **位置**: `libsqlite3/Makefile`、`libsqlite3/_set.sh`
- **当前状态**: ❌ 未修复
- **详情**:

  - 全部编译规则被注释
  - `_set.sh` 只执行 `make install`
  - 头文件库不编译 `.so` 尚可理解，但连编译时语法检查都没有

- **修复建议**: 至少保留一个 `check` 或 `test` 目标，编译一个最小测试文件验证头文件能正常包含和实例化。

---

## 已有安全机制（正确实现）

1. **RAII 思路正确** — 用析构函数自动 `sqlite3_finalize` / `sqlite3_close`
2. **`SQLITE_TRANSIENT` 使用正确** — `bind_text` 传 `-1` 和 `SQLITE_TRANSIENT`，让 SQLite 复制数据，避免悬垂指针
3. **`step()` 返回值语义清晰** — 返回 `bool` 表示是否还有数据行
4. **异常路径不吞异常** — 构造/绑定失败直接抛异常，不返回错误码让调用者忽略
5. **头文件仅暴露最小接口** — 没有暴露原始 `sqlite3*`/`sqlite3_stmt*`，封装完整

---

## 修复路线图

### 立即（24小时）
- [x] **P0-1**: 为 `Sqlite3` 和 `Sqlite3_stmt` 显式 delete 拷贝、实现移动构造/赋值
- [x] **P0-2**: `~Sqlite3()` 改用 `sqlite3_close_v2` 或检查返回值
- [ ] **P0-3**: 为 `exec` 增加文档警告，限制仅用于硬编码 SQL

### 短期（72小时）
- [x] **P1-1**: `stmt()` 参数改为 `const std::string&`
- [ ] **P1-2**: 增加 `bind_int64`
- [ ] **P1-3**: 补充 `bind_null`、`bind_double`、`bind_blob`
- [ ] **P1-4**: 异常信息携带 `sqlite3_errmsg` 和错误码

### 中期
- [x] **P2-1**: 成员指针初始化为 `nullptr`
- [ ] **P2-2**: `read_text` 生命周期文档化，或提供 `std::string` 版本
- [ ] **P2-3**: 补充读取接口（int64/double/blob/column_type/column_count）
- [x] **P2-4**: 初始化 `errMsg` 并删除危险注释
- [ ] **P2-5**: 修正 `Makefile` echo 文案
- [ ] **P2-6**: 增加最小编译检查目标

---

## 审计方法

1. 通读 `libsqlite3/inc/us/Sqlite3.hpp` 全部 98 行实现
2. 审查 `libsqlite3/Makefile`、`_set.sh`、`README.md` 的安装与构建流程
3. 使用 `grep` 全项目查找 `Sqlite3.hpp`、`Sqlite3`、`Sqlite3_stmt`、`libus_sqlite3` 的引用情况
4. 对照 C++ 资源管理规则检查拷贝/移动/析构语义
5. 对照 SQLite3 C API 文档检查 API 使用正确性（绑定、关闭、finalize）
6. 评估接口完整性（参数化绑定、类型绑定、读取接口）
