# dnfs 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `dnfs.py`、`security_middleware.py`、`log_client.py`  
**代码版本**: dev_qing 分支

---

## 代码验证记录

**验证日期**: 2026-08-03  
**验证方式**: 逐条对照审计报告与当前代码（`dnfs.py`、`security_middleware.py`、`log_client.py`）

### 验证结果汇总

| 等级 | 总数 | 已修复 | 部分修复 | 未修复 | 修复率 |
|------|------|--------|----------|--------|--------|
| P0 | 1 | 1 | 0 | 0 | 100% |
| P1 | 2 | 2 | 0 | 0 | 100% |
| P2 | 2 | 0 | 0 | 2 | 0% |
| **合计** | **5** | **3** | **0** | **2** | **60%** |

### 逐条验证

| 编号 | 状态 | 代码位置 | 验证说明 |
|------|------|----------|----------|
| P0-1 | ✅ 已修复 | `dnfs.py` L312-351 | `decode_token` 完全重写：不再本地解码，改为调用 gateway `/whoami` 接口，由 gateway 完成 RSA 签名验证和过期检查；网络异常 fail-closed（返回 503） |
| P1-1 | ✅ 已修复 | `dnfs.py` L312-351 | Token 过期校验由 gateway `/whoami` 统一处理，dnfs 不再自行校验 |
| P1-2 | ✅ 已修复 | `dnfs.py` L348-351 | `is_admin` 由 gateway 服务端判定（基于 Admin 白名单），dnfs 信任 gateway 响应，不再从用户提交的 Token 中直接读取 |
| P2-1 | ❌ 未修复 | `dnfs.py` L156-202 | `review_content` 已实现 fail-closed（所有异常路径均拒绝请求），但返回类型不一致（bool vs dict），需代码清理 |
| P2-2 | ❌ 未修复 | `dnfs.py` L468-562 | `delete_note` 无确认机制，无速率限制 |

---

## 修复进度

| 优先级 | 总数 | 已修复 | 待修复 |
|--------|------|--------|--------|
| P0 | 1 | 1 | 0 |
| P1 | 2 | 2 | 0 |
| P2 | 2 | 0 | 2 |

---

## 🔴 P0 — 严重问题

### P0-1: Token 无 RSA 签名验证，可伪造任意用户身份（含管理员） ✅ 已修复
- **位置**: `dnfs.py:312-351`（完全重写）
- **原问题**: `decode_token` 本地 base64 解码 Token，代码明确标注 `FIXME: 验证数字签名`，但完全跳过了 RSA 签名验证，攻击者可伪造任意用户身份（含管理员）
- **修复方案**: `decode_token` 完全重写，改为调用 gateway `/whoami` 接口：
  1. 将 Token 通过 `Authorization: Bearer` 头发送给 gateway
  2. Gateway 服务端完成 RSA 签名验证 + 过期检查
  3. 返回 `{"login": true/false, "user": "...", "is_admin": true/false, ...}`
  4. 网络异常（gateway 不可达）→ fail-closed，返回 503
  5. 验签失败（`login=false`）→ 返回 None，调用方返回 401
- **影响范围**: 已消除，所有需要认证的端点（add_note、delete_note、update_note）均通过 gateway 验证

---

## 🟠 P1 — 高危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P1-1 | Token 过期校验不严谨 | ✅ 已修复 | Token 过期校验由 gateway `/whoami` 统一处理（基于 `created_at` + `keep_time`），dnfs 不再自行校验 |
| P1-2 | is_admin 权限完全依赖 Token 字段，无二次验证 | ✅ 已修复 | `is_admin` 由 gateway 服务端基于 Admin 白名单判定，dnfs 从 gateway 响应中读取（`info.get('is_admin')`），不再从用户提交的 Token 直接读取 |

---

## 🟡 P2 — 中危问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| P2-1 | AI 审核 fail-closed 实现需验证 | ❌ 未修复 | `review_content` 已实现 fail-closed（HTTPError/URLError/JSONDecodeError 均导致请求被拒），但函数返回类型不一致（bool vs dict），建议代码清理 |
| P2-2 | 节点删除无确认机制，批量删除无速率限制 | ❌ 未修复 | `delete_note` 无二次确认，无速率限制 |

---

## ✅ 已有安全机制

1. ✅ **特殊节点校验**: link/html/image/file 各类型有格式与大小限制（URL 前缀、大小上限）
2. ✅ **AI 内容审核集成**: 新增节点前调用 ai_review 服务审核
3. ✅ **security_middleware.py**: 安全响应头（CSP、X-Frame-Options、X-XSS-Protection 等）
4. ✅ **CORS 配置**: 跨域控制
5. ✅ **通过 pymsg 调 db 服务**: 不直连数据库，规避 SQL 注入

## 新增安全机制（审计后添加，审计报告未覆盖）

| 机制 | 代码位置 | 说明 |
|------|----------|------|
| Token 验签委托 gateway | `dnfs.py` L312-351 | `decode_token` 调用 gateway `/whoami` 完成 RSA 签名验证 + 过期检查，dnfs 不再持有/管理密钥 |
| Token 验签 fail-closed | `dnfs.py` L336-339 | gateway 不可达时返回 503，拒绝所有认证请求 |
| 配置白名单合并 | `dnfs.py` L55-63 | `_merge_ini` 仅覆盖已知 section/key，防止意外字段注入 |
| 配置完整性校验 | `dnfs.py` L100-112 | 启动时检查所有必需配置项，缺失则退出 |
| 特殊节点审核脱敏 | `dnfs.py` L273-286 | `_sp_review_text` 剔除 base64 负载（体量大且无审核意义），仅保留文本字段送审 |
| 统一异常处理 | `dnfs.py` L294-305 | `_handle_exp` 装饰器统一捕获 DnfsExp 和通用异常，返回结构化错误响应 |
| 异步日志 fail-safe | `log_client.py` L66-72, L147-148 | AsyncRpcQueue 后台异步推送，db 不可用时静默丢弃，不阻塞业务 |

---

## 修复路线图

### 立即（24小时）
- [x] 实现 Token RSA 签名验证 ✅ 已完成（委托 gateway）

### 短期（72小时）
- [x] Token 过期严格校验 ✅ 已完成（gateway 统一处理）
- [x] is_admin 二次验证 ✅ 已完成（gateway 服务端判定）

### 中期
- [ ] 清理 `review_content` 返回类型不一致（bool vs dict）
- [ ] 节点操作速率限制
- [ ] 节点删除二次确认机制
