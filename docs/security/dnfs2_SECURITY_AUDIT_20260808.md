# dnfs2 安全审计报告

**审计日期**: 2026-08-08  
**审计范围**: dnfs2/src/dnfs2.cpp (1180 行)  
**对比基准**: dnfs/dnfs.py (723 行)  
**审计轮次**: 第 1 轮  
**安全评级**: A-

---

## 1. 审计概述

dnfs2 是 Python dnfs 的 C++20 重实现，基于 libhttp + libmsg + boost::json。本轮审计逐行对比 dnfs2 与 dnfs 的实现，验证计划中的 P0/P1 修复有效性，检查新引入的安全问题，并评估接口兼容性。

### 审计结论

| 类别 | 数量 | 状态 |
|------|------|------|
| 计划修复的 P0/P1（继承自 dnfs） | 6 | 全部有效 ✓ |
| 新发现问题 | 4 | 全部修复 ✓ |
| 已知限制（架构性/继承性） | 6 | 记录 |

---

## 2. 计划修复验证（P0/P1 继承自 dnfs）

### P0-4: 统一封装 _call_db() 检查 success — ✓ 已修复

**dnfs 问题**: 多处直接访问 `notes['data']` 而不检查 `success`，db 返回错误时引发 KeyError。  
**dnfs2 修复**: 统一封装 `_call_db()` 返回 `DbResult{success, data, error}`，所有调用方先检查 `success` 再访问 `data`。

验证点：
- `_call_db()` 捕获 `RpcError` 返回 `{false, nullptr, err}`
- `_handle_get_notes`: `if (!r.success)` → 返回 500
- `_handle_add_note`: `if (!r.success)` → 抛 DnfsExp
- `_handle_delete_note`: 子节点检查和所有权检查均先判断 `success`
- `_handle_update_note`: 同上

### P1-6: 请求体大小限制 — ✓ 已修复

**dnfs 问题**: Flask/Waitress 无请求体大小限制。  
**dnfs2 修复**: `server.setMaxBodySize(kMaxBodySize)` (10MB)，对齐 WAF。

### P1-7: 日志客户端配置一致性 — ✓ 已修复

**dnfs 问题**: log_client 使用独立配置，可能与主配置不一致。  
**dnfs2 修复**: `_log_rpc` 直接使用 `_cfg.db_host` / `_cfg.db_port`，与主 `_rpc` 同源配置。

### P1-8: update_note 改 pid 校验父节点存在 — ✓ 已修复

**dnfs 问题**: update_note 改 pid 时不校验新父节点是否存在。  
**dnfs2 修复**: `if (pid != 0 && !_note_exists(pid))` → 抛 DnfsExp("父节点不存在", 400)。

### P1-9: RpcClient 启用 retry_on_refused — ✓ 已修复

**dnfs 问题**: db 服务未启动时 dnfs 直接失败退出。  
**dnfs2 修复**: `RpcClient(..., retry_on_refused=true)`，等待 db 就绪。

### P1-11: note_id 强制类型转换 — ✓ 已修复

**dnfs 问题**: `note_id = data.get('id')` 无类型校验，字符串/None 引发后续错误。  
**dnfs2 修复**: `get_int()` 使用 `is_int64()` 严格校验，非整数直接返回 400。

---

## 3. 新发现问题

### P1-N1: stop_watcher 分离线程 use-after-free 竞态 — ✓ 已修复

**位置**: `run()` 方法，原 L522-528  
**问题**: `stop_watcher` 线程被 `detach()`，`server.run()` 返回后局部变量 `server` 被析构。stop_watcher 在下一次 200ms 轮询时调用 `_server->stop()` → 访问已析构对象（CWE-416）。

时序分析：
1. 信号 → `_should_stop = true`
2. stop_watcher: 调用 `_server->stop()`
3. 主线程: `server.run()` 返回 → 局部 `server` 析构
4. stop_watcher: 200ms 后循环 → `_server->stop()` → **use-after-free**

**修复**: 改为 `join()`，`server.run()` 返回后设置 `_should_stop=true` 并 `join()` stop_watcher 线程，确保线程在 `server` 析构前退出。

```cpp
// 修复后
server.run();
_should_stop.store(true);
if (stop_watcher.joinable()) stop_watcher.join();
```

### P1-N2: curl_global_init 未调用，多线程首次 curl_easy_init 竞态 — ✓ 已修复

**位置**: `main()` / `decode_token()` / `review_content()`  
**问题**: HttpClient 构造函数调用 `curl_easy_init()` 但从未调用 `curl_global_init()`。libcurl 文档明确指出：首次 `curl_easy_init()` 会隐式调用 `curl_global_init()`，但此隐式调用**非线程安全**。dnfs2 使用多线程 HttpServer，多个 worker 线程可能同时首次创建 HttpClient → 竞态条件（CWE-362）。

**修复**: 在 `main()` 最开始显式调用 `curl_global_init(CURL_GLOBAL_DEFAULT)`，在多线程启动前完成全局初始化。

```cpp
int main(int argc, char* argv[]) {
    curl_global_init(CURL_GLOBAL_DEFAULT);
    // ...
}
```

### P2-N3: delete_note 子节点查询失败时错误消息误导 — ✓ 已修复

**位置**: `_handle_delete_note()`，原 L968-972  
**问题**: 原代码将 db 查询失败和"存在子节点"合并为同一判断，db 故障时返回 `"this node has a child"` (400)，客户端可能误判为永久性条件。

```cpp
// 修复前
if (!child_r.success || !child_r.data.is_array() || !child_r.data.as_array().empty()) {
    throw DnfsExp("this node has a child", 400);
}

// 修复后：分离 db 错误和子节点检查
if (!child_r.success || !child_r.data.is_array()) {
    throw DnfsExp("查询子节点失败", 500);  // db 故障返回 500
}
if (!child_r.data.as_array().empty()) {
    throw DnfsExp("this node has a child", 400);  // 有子节点返回 400
}
```

### P2-N4: threads 无上限校验 — ✓ 已修复

**位置**: `load_config()` L257-260  
**问题**: 仅校验 `threads >= 1`，无上限。用户可设置 `--threads 10000` 创建过多线程。项目约定 workers 范围 [0, 256]。

**修复**: 添加上限 256，与项目约定一致。

```cpp
if (cfg.threads < 1 || cfg.threads > 256) { ... exit(1); }
```

---

## 4. 已知限制（不修复，架构性/继承性）

### L-1: update_note 允许创建循环（P1，继承自 dnfs）

update_note 允许将节点的 pid 改为任意已存在的节点，包括自身或自身后代，可创建循环。这会导致树结构损坏。修复需要图遍历检测后代关系，超出本次范围。Python dnfs 有相同问题。

### L-2: delete_note TOCTOU 竞态（P0 级残余，继承自 dnfs）

delete_note 在"检查无子节点"和"执行删除"之间存在 TOCTOU 窗口，并发请求可在检查后添加子节点。修复需要 db2 支持事务或原子条件删除。dnfs2 已通过 `_call_db()` 统一检查 success 修复了"未检查 success"部分，但 TOCTOU 窗口仍存在。

### L-3: 配置的超时时间未使用（P2）

`review_timeout` 和 `gateway_timeout` 已加载并校验范围，但 HttpClient 硬编码 30s 超时（libhttp 限制，未提供 `set_timeout()` 接口）。实际超时为 30s 而非配置值。

### L-4: 无速率限制（P2，继承自 dnfs）

无请求频率限制，恶意用户可高频调用 add_note 填充数据库。应由 gateway/WAF 层防护。

### L-5: title/content 无最大长度校验（P2）

仅校验非空，无长度上限（受 body size 10MB 间接限制）。超长 title 会导致 AI 审核和数据库存储性能问题。Python dnfs 有相同问题。

### L-6: AI 审核服务不可用时阻断所有写入（P2，设计决策）

`review_content()` 采用 fail-closed 策略：ai_review 不可达时返回 false，所有 add_note/update_note 被拒绝。这是安全优先的设计决策，但可能导致服务不可用。

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

## 6. 安全评级

### 评级: A-

**理由**:
- 计划修复的 6 项 P0/P1 全部有效 ✓
- 新发现 4 项问题全部修复 ✓ (2 P1 + 2 P2)
- IP/用户名泄露问题作为重写副效果已修复 ✓
- 残余 6 项已知限制均为架构性/继承性问题，有明确修复路径
- 相比 Python dnfs（评级 B，多个 P0/P1 未修复），安全基线显著提升

**扣分项**:
- L-1/L-2 循环创建和 TOCTOU 未修复（需 db2 事务支持）
- L-3 超时配置未生效（需 libhttp 增强）

---

## 7. 修复文件清单

| 文件 | 修改内容 |
|------|---------|
| dnfs2/src/dnfs2.cpp | P1-N1: stop_watcher join 替代 detach |
| dnfs2/src/dnfs2.cpp | P1-N2: main() 添加 curl_global_init |
| dnfs2/src/dnfs2.cpp | P2-N3: delete_note 分离 db 错误和子节点检查 |
| dnfs2/src/dnfs2.cpp | P2-N4: threads 上限 256 |
| dnfs2/src/dnfs2.cpp | 新增 `#include <curl/curl.h>` |

编译验证: `g++ -std=c++20 -O3 -Wall -Werror` 通过，零错误零警告。
