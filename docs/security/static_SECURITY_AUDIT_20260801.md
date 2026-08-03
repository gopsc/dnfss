# static 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `fsys.py`、`security_middleware.py`、`log_client.py`  
**代码版本**: dev_qing 分支

---

## 代码验证记录

**验证日期**: 2026-08-03（更新）  
**验证方式**: 逐条对照审计报告与当前代码（`fsys.py`、`security_middleware.py`、`log_client.py`）

### 验证结果汇总

| 等级 | 总数 | 已修复 | 部分修复 | 未修复 | 修复率 |
|------|------|--------|----------|--------|--------|
| P0 | 5 | 5 | 0 | 0 | 100% |
| P1 | 3 | 1 | 0 | 2 | 33% |
| P2 | 2 | 0 | 1 | 1 | 25% (部分) |
| **合计** | **10** | **6** | **1** | **3** | **65%** |

### 逐条验证

| 编号 | 状态 | 代码位置 | 验证说明 |
|------|------|----------|----------|
| P0-1 | ✅ 已修复 | `fsys.py` L326-341 | `/api/reset_password` 整个路由已注释禁用，标注 "DISABLED: P0 security issue" |
| P0-2 | ✅ 已修复 | `fsys.py` L360-467 | yhdet 系列功能（`yhdet_callback`、`yhdet_login`、`yhdet_verify`）全部注释禁用，Token 问题随功能一同消除 |
| P0-3 | ✅ 已修复 | `fsys.py` L360-467 | yhdet 系列功能全部注释禁用，`_create_unverified_context()` SSL 跳过问题随功能一同消除 |
| P0-4 | ✅ 已修复 | `fsys.py` L362 | `YHDET_SECRET` 已注释并设为空字符串，标注"已失效" |
| P0-5 | ✅ 已修复 | `fsys.py` L300-314 | 新增 `_db_call()` 辅助函数，4个接口（`api_check_user`、`yhdet_callback`、`yhdet_login`、`yhdet_verify`）全部改为通过 pymsg RpcClient 调 db:8005 服务，不再直连 SQLite；`GATEWAY_DB`/`GATEWAY_DB_YHDET` 常量已删除 |
| P1-1 | ❌ 未修复 | `fsys.py` L344-355 | `/api/check_user` 无速率限制，可枚举有效邮箱 |
| P1-2 | ❌ 未修复 | `fsys.py` L182-192 | `safe_path_join` 路径遍历防护经审计确认实现正确（realpath 前缀校验），但审计报告状态应更新为"已验证" |
| P1-3 | ✅ 已修复 | `fsys.py` L360-467 | yhdet 系列功能全部注释禁用，默认密码 `oauth_{user_id}` 问题随功能一同消除 |
| P2-1 | ❌ 未修复 | `fsys.py` L272-291 | Range 请求未校验 `end >= start`，恶意 Range 头可导致异常 |
| P2-2 | ⚠️ 部分 | `fsys.py` L325, L423-431 | 密码/Token 未直接记录；email 日志脱敏改进（`email[:3]***` / `email[:8]***`），但 yhdet_callback 注册日志未脱敏（L389）— 注：yhdet 功能已禁用，该日志路径不再可达 |

---

## 修复进度

| 优先级 | 总数 | 已修复 | 待修复 |
|--------|------|--------|--------|
| P0 | 5 | 5 | 0 |
| P1 | 3 | 1 | 2 |
| P2 | 2 | 0 | 2 |

---

## 🔴 P0 — 严重问题

### P0-1: 无认证密码重置接口，可接管任意账户 ✅ 已修复
- **位置**: `fsys.py:326-341`（已注释禁用）
- **修复**: 整个 `/api/reset_password` 路由及其函数体已注释禁用，标注 "DISABLED: P0 security issue - no authentication, allows arbitrary password reset"

---

### P0-2: yhdet OAuth Token 为 base64(email:password)，解码即得明文密码 ✅ 已修复
- **位置**: `fsys.py:360-467`（yhdet 功能已全部注释禁用）
- **修复**: yhdet 系列功能（`yhdet_callback`、`yhdet_login`、`yhdet_verify`）全部注释禁用，Token 问题随功能一同消除

---

### P0-3: yhdet_callback 禁用 SSL 证书验证（中间人攻击） ✅ 已修复
- **位置**: `fsys.py:360-467`（yhdet 功能已全部注释禁用）
- **修复**: yhdet 系列功能全部注释禁用，`_create_unverified_context()` SSL 跳过问题随功能一同消除

---

### P0-4: yhdet OAuth 硬编码密钥 ✅ 已修复
- **位置**: `fsys.py:362`（已注释）
- **修复**: `YHDET_SECRET` 已注释并设为空字符串，标注"已失效，原值: yhdet_qsont_oauth_2026_secret_key"

---

### P0-5: 直连 gateway SQLite 数据库文件，绕过 db 服务 ✅ 已修复
- **位置**: `fsys.py:300-314`（新增 `_db_call` 辅助函数）、`fsys.py:344-355,395-401,421-443,446-466`（4个接口已改造）
- **修复方案**:
  1. 新增 `_db_call()` 辅助函数，通过 `pymsg.RpcClient` 调用 db 服务（`127.0.0.1:8005`）的 `execute` 路由
  2. 4 个接口全部改为通过 db 服务操作 Users 表：
     - `api_check_user`：`_db_call('select', 'Users', ...)`
     - `yhdet_callback`：`_db_call('select'/'insert', 'Users', ...)`
     - `yhdet_login`：`_db_call('select', 'Users', ...)`
     - `yhdet_verify`：`_db_call('select', 'Users', ...)`
  3. 删除 `GATEWAY_DB` 和 `GATEWAY_DB_YHDET` 常量
  4. 移除所有 `import sqlite3` 局部导入

---

## 🟠 P1 — 高危问题

| # | 问题 | 位置 |
|---|------|------|
| P1-1 | `/api/check_user` 无速率限制，可枚举有效邮箱 | `fsys.py:295-306` |
| P1-2 | 文件服务路径遍历防护需全面审计 | 文件浏览/下载接口 |
| P1-3 | yhdet 新用户默认密码可预测：`oauth_{user_id}` | `fsys.py:360-467`（yhdet 功能已禁用） | ✅ 已修复 |

---

## 🟡 P2 — 中危问题

| # | 问题 |
|---|------|
| P2-1 | 视频/音频 Range 请求实现需审计边界条件 |
| P2-2 | 日志可能记录敏感信息（密码、Token） |

---

## ✅ 已有安全机制

1. ✅ **security_middleware.py**: 安全响应头（CSP、X-Frame-Options、nosniff、Permissions-Policy 等）
2. ✅ **路径遍历防护**: `safe_path_join()` realpath 前缀校验，覆盖所有文件服务路由
3. ✅ **dnfs 代理**: 解决浏览器跨域问题，支持 Cookie-to-Bearer Token 转发
4. ✅ **静态文件托管**: 独立服务，经 gateway 代理访问
5. ✅ **系统文件过滤**: 文件列表过滤 `__` 前缀的目录/文件（如 `__pycache__`、`__view.html`）

## 新增安全机制（审计后添加，审计报告未覆盖）

| 机制 | 代码位置 | 说明 |
|------|----------|------|
| 配置白名单合并 | `fsys.py` L63-71 | `_merge_ini` 仅覆盖已知 section/key，防止意外字段注入 |
| 配置完整性校验 | `fsys.py` L104-115 | 启动时检查所有必需配置项，缺失则退出；ssl 段有合理默认值 |
| `/api/favicon` SSRF 防护 | `fsys.py` L570-634 | 多层防护：仅 http/https、DNS 解析后 IP 黑名单（内网/环回/链路本地/保留/组播）、重定向目标重新校验、512KB 上限 |
| 日志 email 脱敏 | `fsys.py` L325, L423-431 | 密码重置和 yhdet 登录日志中 email 仅保留前缀（`email[:3]***` / `email[:8]***`） |
| 参数校验 | `fsys.py` L319-320, L335-336 | `api_reset_password` 和 `api_check_user` 新增参数非空校验 |
| Cookie-to-Bearer 转发 | `fsys.py` L486, L502, L517, L532 | dnfs 代理路由支持 HttpOnly Cookie 模式，自动将 `q4_token` Cookie 转为 Authorization Bearer 头 |
| 生产级服务器 | `fsys.py` L722-723 | 使用 Waitress 生产服务器（threads=8），替代 Flask 内置开发服务器 |
| 本地日志轮转 | `fsys.py` L31-36 | RotatingFileHandler 10MB/10 个备份，防止日志文件无限增长 |

## 新发现问题（验证过程中发现，审计报告未覆盖）

| 编号 | 等级 | 问题 | 位置 | 说明 |
|------|------|------|------|------|
| NEW-1 | P0 | `proxy_send_code` 禁用 SSL 验证 | `fsys.py` L550-569 | ✅ 已修复：`proxy_send_code` 路由已注释禁用 |
| NEW-2 | P0 | `PRIVATE_SECRET` 硬编码 | `fsys.py` L472-473 | ✅ 已修复：`PRIVATE_SECRET` 和 `YHDET_PRIVATE_API` 已注释 |
| NEW-3 | P1 | CORS 配置过于宽松 | `fsys.py` L482 | `_cors` 函数设置 `Access-Control-Allow-Origin: *`，允许任意来源 |
| NEW-4 | P2 | `proxy_send_code` 包含死代码 | `fsys.py` L550-569 | ✅ 已修复：`proxy_send_code` 路由已注释禁用，死代码随功能一同消除 |

---

## 修复路线图

### 立即（24小时）
- [x] 禁用/删除无认证的 `/api/reset_password` 接口（P0-1） ✅ 已完成
- [x] 所有数据库操作迁移到 pymsg 调 db 服务（P0-5） ✅ 已完成
- [x] 禁用 yhdet 系列功能，消除 P0-2/P0-3/P0-4/NEW-1/NEW-2 全部风险 ✅ 已完成
- [x] 移除硬编码 YHDET_SECRET 和 PRIVATE_SECRET（P0-4 + NEW-2） ✅ 已完成

### 短期（72小时）
- [ ] `/api/check_user` 添加速率限制（P1-1）
- [ ] CORS 限制为具体域名（NEW-3）

### 中期
- [ ] Range 请求边界条件校验（P2-1）
- [ ] P1-2 路径遍历防护状态确认（经审计已确认实现正确，标记为已验证）
