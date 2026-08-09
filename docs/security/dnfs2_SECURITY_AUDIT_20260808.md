# dnfs2 安全审计报告

**审计日期**: 2026-08-08  
**审计范围**: dnfs2/src/dnfs2.cpp (1200 行)  
**对比基准**: dnfs/dnfs.py (723 行)  
**审计轮次**: 第 2 轮（第 1 轮发现 5 项已全部修复）  
**安全评级**: A

> 📌 **本服务为 dnfs 的 C++20 生产实现**，`core/init.txt` 实际启动的是 `dnfs2`（Python dnfs 已废弃，历史审计见 [`dnfs_SECURITY_AUDIT_20260801.md`](dnfs_SECURITY_AUDIT_20260801.md)，2026-08-10 已复核为全量 16 项发现）。本审计覆盖生产代码，结论有效。

---

## 1. 审计概述

dnfs2 是 Python dnfs 的 C++20 重实现，基于 libhttp + libmsg + boost::json。本轮审计在第 1 轮基础上逐行复审，验证第 1 轮修复有效性，深入审查 db 错误处理一致性、并发安全、资源管理及依赖库接口使用正确性。

### 审计结论

| 类别 | 数量 | 状态 |
|------|------|------|
| 第 1 轮计划修复的 P0/P1（继承自 dnfs） | 6 | 全部有效 ✓ |
| 第 1 轮新发现问题 | 5 | 全部有效 ✓ |
| 第 2 轮新发现问题 | 5 | 4 修复 ✓ / 1 记录 |
| 已知限制（架构性/继承性） | 6 | 记录 |

---

## 2. 第 1 轮修复验证

### 计划修复的 P0/P1（继承自 dnfs）— 全部有效 ✓

| 编号 | 问题 | 验证 |
|------|------|------|
| P0-4 | 统一封装 `_call_db()` 检查 success | ✓ `_call_db` 返回 `DbResult{success, data, error}`，所有调用方先检查 `success` |
| P1-6 | 请求体大小限制 | ✓ `server.setMaxBodySize(kMaxBodySize)` (10MB) |
| P1-7 | 日志客户端配置一致性 | ✓ `_log_rpc` 复用 `_cfg.db_host/db_port` |
| P1-8 | update_note 改 pid 校验父节点存在 | ✓ `pid != 0 && !_note_exists(pid)` → 抛 400 |
| P1-9 | RpcClient 启用 retry_on_refused | ✓ `_rpc` 构造 `retry_on_refused=true` |
| P1-11 | note_id 强制类型转换 | ✓ `get_int()` 使用 `is_int64()` 严格校验 |

### 第 1 轮新发现问题 — 全部有效 ✓

| 编号 | 问题 | 验证 |
|------|------|------|
| P1-N1 | stop_watcher use-after-free | ✓ `join()` 替代 `detach()`，`server.run()` 返回后 join |
| P1-N2 | curl_global_init 未调用 | ✓ `main()` 开头调用 `curl_global_init(CURL_GLOBAL_DEFAULT)` |
| P2-N3 | delete_note 子节点查询失败错误消息误导 | ✓ 分离 db 错误(500)和子节点存在(400) |
| P2-N4 | threads 无上限校验 | ✓ 范围 [1, 256] |
| P2-N5 | HttpClient 超时硬编码 | ✓ `set_timeout()`/`set_connect_timeout()` 接口，dnfs2 设置 gateway/review 超时 |

---

## 3. 第 2 轮新发现问题

### P2-N6: _note_exists db 业务错误误判为节点不存在 — ✓ 已修复

**位置**: `_note_exists()` 方法  
**问题**: 原代码 `if (!r.success) return false;` — 当 db2 对 select 返回 RpcError（业务错误，如表不存在、SQL 错误）时，`_note_exists` 返回 false，导致 add_note/update_note 误报"父节点不存在"(400)。这与 P2-N3 修复的 delete_note 子节点检查不一致：db 故障应返回 500，而非误导为业务条件。

**影响**: 
- add_note: db 业务故障时用户看到"父节点不存在"(400)，可能误删合法的父节点重试
- update_note: 同上

**修复**: db 业务错误时抛 500，仅查询成功且结果为空时返回 false。

```cpp
// 修复前
if (!r.success) return false;

// 修复后
if (!r.success) {
    _log("ERROR", "dnfs2", "查询节点存在性失败 id=" + std::to_string(id)
         + " err=" + r.error);
    throw DnfsExp("查询节点失败", 500);
}
```

### P2-N7: delete_note/update_note 所有权检查 db 错误误判为节点不存在 — ✓ 已修复

**位置**: `_handle_delete_note()` / `_handle_update_note()` 所有权检查段  
**问题**: 原代码 `if (!owner_r.success || !owner_r.data.is_array() || owner_r.data.as_array().empty())` 将 db 业务错误(`!owner_r.success`)与"节点不存在"合并为同一判断，db 故障时返回 404。与 P2-N3 修复的子节点检查不一致。

**影响**: db 业务故障时用户看到"节点不存在"(404)，可能误以为节点已被删除而重试创建。

**修复**: 分离 db 错误(500)和节点不存在(404)，与 P2-N3 的修复方式一致。

```cpp
// 修复前
if (!owner_r.success || !owner_r.data.is_array() || owner_r.data.as_array().empty()) {
    throw DnfsExp("节点不存在", 404);
}

// 修复后
if (!owner_r.success) {
    _log("ERROR", "dnfs2", "delete_note 查询节点失败 ...");
    throw DnfsExp("查询节点失败", 500);
}
if (!owner_r.data.is_array() || owner_r.data.as_array().empty()) {
    throw DnfsExp("节点不存在", 404);
}
```

### P3-N8: CONFS_PORT 无范围校验 — ✓ 已修复

**位置**: `load_config()` L176  
**问题**: 环境变量 `CONFS_PORT` 经 `safe_stoi` 解析后缺少 [1, 65535] 范围校验。其他端口（listen_port、db_port）均有校验，但 confs_port 遗漏。设置为 0 或负数时 RpcClient 构造不失败，但连接行为未定义。

**修复**: 添加范围校验，与 listen_port/db_port 一致。

```cpp
if (const char* p = std::getenv("CONFS_PORT")) {
    cfg.confs_port = safe_stoi(p, "CONFS_PORT");
    if (cfg.confs_port < 1 || cfg.confs_port > 65535) {
        std::fprintf(stderr, "[dnfs2] invalid CONFS_PORT: %d (must be 1-65535)\n", cfg.confs_port);
        std::exit(1);
    }
}
```

### P3-N9: curl_global_cleanup 未调用 — ✓ 已修复

**位置**: `main()`  
**问题**: P1-N2 修复添加了 `curl_global_init()`，但未在程序退出时调用 `curl_global_cleanup()`。虽然 OS 会回收资源，但不规范，且在某些 curl 版本下可能导致 SSL 状态泄漏到 fork 的子进程。

**修复**: 重构 main() 为 `init → try/catch → cleanup` 模式，确保所有退出路径都调用 `curl_global_cleanup()`。

```cpp
int main(int argc, char* argv[]) {
    curl_global_init(CURL_GLOBAL_DEFAULT);
    int ret = 0;
    try {
        // ...
        server.run();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[dnfs2] fatal: %s\n", e.what());
        ret = 1;
    }
    curl_global_cleanup();
    return ret;
}
```

### P3-N10: CSP 允许 'unsafe-inline' — 记录（设计决策）

**位置**: `make_response()` L437-440  
**问题**: Content-Security-Policy 中 `script-src 'self' 'unsafe-inline'` 和 `style-src 'self' 'unsafe-inline'` 包含 `'unsafe-inline'`，降低了 CSP 对 XSS 的防护效果。

**决策**: 前端 dnfs.html/dnfs.js 使用了内联脚本和样式（如 markdown-it 渲染、代码高亮），移除 `'unsafe-inline'` 会导致前端功能损坏。这是前端架构限制，需要前端重构（nonce-based CSP 或 hash-based CSP）才能修复。记录为已知限制，不在本次修复范围。

---

## 4. 已知限制（不修复，架构性/继承性）

### L-1: update_note 允许创建循环（P1，继承自 dnfs）

update_note 允许将节点的 pid 改为任意已存在的节点，包括自身或自身后代，可创建循环。这会导致树结构损坏。修复需要图遍历检测后代关系，超出本次范围。Python dnfs 有相同问题。

### L-2: delete_note TOCTOU 竞态（P0 级残余，继承自 dnfs）

delete_note 在"检查无子节点"和"执行删除"之间存在 TOCTOU 窗口，并发请求可在检查后添加子节点。修复需要 db2 支持事务或原子条件删除。dnfs2 已通过 `_call_db()` 统一检查 success 修复了"未检查 success"部分，但 TOCTOU 窗口仍存在。

### L-4: 无速率限制（P2，继承自 dnfs）

无请求频率限制，恶意用户可高频调用 add_note 填充数据库。应由 gateway/WAF 层防护。

### L-5: title/content 无最大长度校验（P2）

仅校验非空，无长度上限（受 body size 10MB 间接限制）。超长 title 会导致 AI 审核和数据库存储性能问题。Python dnfs 有相同问题。

### L-6: AI 审核服务不可用时阻断所有写入（P2，设计决策）

`review_content()` 采用 fail-closed 策略：ai_review 不可达时返回 false，所有 add_note/update_note 被拒绝。这是安全优先的设计决策，但可能导致服务不可用。

### L-7: CSP 允许 'unsafe-inline'（P3，前端架构限制）

见 P3-N10。前端使用内联脚本/样式，移除 `'unsafe-inline'` 需要前端重构。

---

## 5. 接口兼容性分析

### 响应格式兼容 — ✓

| 路由 | dnfs 响应 | dnfs2 响应 | 兼容 |
|------|-----------|------------|------|
| get_notes | `jsonify(result)` 200 | `json::serialize(result)` 200 | ✓ |
| add_note 成功 | `{'success': true}` 200 | `{"success": true}` 200 | ✓ |
| add_note 失败 | `{'error': msg}` 400 | `{"error": msg}` 400 | ✓ |
| delete_note 成功 | `{'success': true}` 200 | `{"success": true}` 200 | ✓ |
| delete_note 失败 | `{'success': false}` 或 `{'error': msg}` | 同上 | ✓ |
| update_note | 同 delete_note | 同上 | ✓ |

### 错误消息差异（P3，不影响功能）

dnfs2 的参数校验错误消息更具体（如 `"非法参数: pid 必须是整数"` vs dnfs 的 `"非法参数"`），但前端仅展示给用户，不影响逻辑。

### 安全改进（P2-12 部分修复）

dnfs 的错误消息泄露 IP 和用户名（如 `"未授权访问，从192.168.1.1"`），dnfs2 移除了这些泄露：

| 场景 | dnfs 消息 | dnfs2 消息 |
|------|-----------|------------|
| 未授权 | `未授权访问，从{ip}` | `未授权访问` |
| 特殊节点非管理员 | `特殊节点仅管理员可发布，用户{user}，从{ip}` | `特殊节点仅管理员可发布` |
| 审核不通过 | `内容审核不通过，用户{user}，从{ip}` | `内容审核不通过` |

---

## 6. db 错误处理一致性矩阵

第 2 轮审计核心关注点：所有 db 查询的 `!r.success`（业务错误）应返回 500，而非误判为业务条件。

| 位置 | 查询目的 | 修复前行为 | 修复后行为 | 状态 |
|------|---------|-----------|-----------|------|
| delete_note 子节点检查 | 检查有无子节点 | db错误→400(误导) | db错误→500 / 有子节点→400 | ✓ P2-N3 (第1轮) |
| _note_exists | 检查父节点存在 | db错误→false→400(误导) | db错误→500 / 不存在→false | ✓ P2-N6 (第2轮) |
| delete_note 所有权检查 | 查询节点 owner | db错误→404(误导) | db错误→500 / 不存在→404 | ✓ P2-N7 (第2轮) |
| update_note 所有权检查 | 查询节点 owner | db错误→404(误导) | db错误→500 / 不存在→404 | ✓ P2-N7 (第2轮) |
| get_notes 查询 | 查询子节点列表 | db错误→500("查询失败") | 无需修改 | ✓ 原本正确 |
| add_note 插入 | 插入节点 | db错误→400("插入失败") | 无需修改 | ✓ 原本正确 |
| delete_note 删除 | 删除节点 | db错误→400(success=false) | 无需修改 | ✓ 原本正确 |
| update_note 更新 | 更新节点 | db错误→400(success=false) | 无需修改 | ✓ 原本正确 |

---

## 7. 安全评级

### 评级: A

**理由**:
- 第 1 轮计划修复的 6 项 P0/P1 全部有效 ✓
- 第 1 轮新发现 5 项问题全部有效 ✓
- 第 2 轮新发现 5 项问题，4 项修复 ✓，1 项记录为已知限制
- db 错误处理一致性全面修正：所有 select 查询的 db 业务错误统一返回 500
- IP/用户名泄露问题已修复 ✓
- 残余 6 项已知限制均为架构性/继承性问题，有明确修复路径
- 相比 Python dnfs（评级 B，多个 P0/P1 未修复），安全基线显著提升

**扣分项**:
- L-1/L-2 循环创建和 TOCTOU 未修复（需 db2 事务支持）
- L-7 CSP 'unsafe-inline' 受限于前端架构

---

## 8. 修复文件清单

### 第 1 轮修复（已验证有效）

| 文件 | 修改内容 |
|------|---------|
| dnfs2/src/dnfs2.cpp | P1-N1: stop_watcher join 替代 detach |
| dnfs2/src/dnfs2.cpp | P1-N2: main() 添加 curl_global_init |
| dnfs2/src/dnfs2.cpp | P2-N3: delete_note 分离 db 错误和子节点检查 |
| dnfs2/src/dnfs2.cpp | P2-N4: threads 上限 256 |
| dnfs2/src/dnfs2.cpp | 新增 `#include <curl/curl.h>` |
| libhttp/inc/us/HttpClient.hpp | P2-N5: 新增 set_timeout/set_connect_timeout 接口 |
| libhttp/src/HttpClient.cpp | P2-N5: 新增实现，request_stream_sync 使用成员变量 |
| dnfs2/src/dnfs2.cpp | P2-N5: decode_token/review_content 设置超时 |

### 第 2 轮修复

| 文件 | 修改内容 |
|------|---------|
| dnfs2/src/dnfs2.cpp | P2-N6: _note_exists db 业务错误抛 500，不返回 false |
| dnfs2/src/dnfs2.cpp | P2-N7: delete_note 所有权检查分离 db 错误(500)和节点不存在(404) |
| dnfs2/src/dnfs2.cpp | P2-N7: update_note 所有权检查同上 |
| dnfs2/src/dnfs2.cpp | P3-N8: CONFS_PORT 添加 [1, 65535] 范围校验 |
| dnfs2/src/dnfs2.cpp | P3-N9: main() 添加 curl_global_cleanup，重构为 init→try/catch→cleanup |

编译验证: `g++ -std=c++20 -O3 -Wall -Werror` 通过，零错误零警告。
