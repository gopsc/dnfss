# static 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `fsys.py`、`security_middleware.py`、`log_client.py`  
**代码版本**: dev_qing 分支

---

## 代码验证记录

**验证日期**: 2026-08-03  
**验证方式**: 逐条对照审计报告与当前代码（`fsys.py`、`security_middleware.py`、`log_client.py`）

### 验证结果汇总

| 等级 | 总数 | 已修复 | 部分修复 | 未修复 | 修复率 |
|------|------|--------|----------|--------|--------|
| P0 | 5 | 0 | 0 | 5 | 0% |
| P1 | 3 | 0 | 0 | 3 | 0% |
| P2 | 2 | 0 | 1 | 1 | 25% (部分) |
| **合计** | **10** | **0** | **1** | **9** | **5%** (部分) |

### 逐条验证

| 编号 | 状态 | 代码位置 | 验证说明 |
|------|------|----------|----------|
| P0-1 | ❌ 未修复 | `fsys.py` L312-326 | `/api/reset_password` 仍无任何认证；仅新增了参数校验（email/password_hash 非空检查）和日志脱敏 |
| P0-2 | ❌ 未修复 | `fsys.py` L432-433, L445-449 | yhdet Token 仍为 `base64(email:password)`，无签名无加密，`yhdet_verify` 直接解码查库 |
| P0-3 | ❌ 未修复 | `fsys.py` L395 | `yhdet_callback` 仍使用 `_create_unverified_context()` 禁用 SSL 证书验证 |
| P0-4 | ❌ 未修复 | `fsys.py` L346 | `YHDET_SECRET` 仍硬编码在源码中 |
| P0-5 | ❌ 未修复 | `fsys.py` L300, L321, L337, L347, L383, L419, L452 | 多处直接 `sqlite3.connect(GATEWAY_DB)` 绕过 db 服务 |
| P1-1 | ❌ 未修复 | `fsys.py` L329-340 | `/api/check_user` 无速率限制，可枚举有效邮箱 |
| P1-2 | ❌ 未修复 | `fsys.py` L182-192 | `safe_path_join` 路径遍历防护经审计确认实现正确（realpath 前缀校验），但审计报告状态应更新为"已验证" |
| P1-3 | ❌ 未修复 | `fsys.py` L382 | yhdet 新用户默认密码仍为 `oauth_{user_id}`，可预测 |
| P2-1 | ❌ 未修复 | `fsys.py` L272-291 | Range 请求未校验 `end >= start`，恶意 Range 头可导致异常 |
| P2-2 | ⚠️ 部分 | `fsys.py` L325, L423-431 | 密码/Token 未直接记录；email 日志脱敏改进（`email[:3]***` / `email[:8]***`），但 yhdet_callback 注册日志未脱敏（L389） |

---

## 修复进度

| 优先级 | 总数 | 已修复 | 待修复 |
|--------|------|--------|--------|
| P0 | 5 | 0 | 5 |
| P1 | 3 | 0 | 3 |
| P2 | 2 | 0 | 2 |

---

## 🔴 P0 — 严重问题

### P0-1: 无认证密码重置接口，可接管任意账户 ❌ 未修复
- **位置**: `fsys.py:312-326`
- **问题**:
  ```python
  @app.route('/api/reset_password', methods=['POST'])
  def api_reset_password():
      data = request.get_json(force=True)
      email = data.get('email', '').strip()
      new_hash = data.get('password_hash', '').strip()
      if not email or not new_hash:
          return jsonify({"success": False, "error": "missing params"}), 400
      conn = _sql.connect(GATEWAY_DB)
      conn.execute("UPDATE Users SET password_hash=? WHERE email=?", (new_hash, email))
      conn.commit()
  ```
  `/api/reset_password` **仍无任何认证**，任何人只要知道邮箱即可修改任意用户密码（包括管理员），直接接管账户。
- **改进**: 新增了参数非空校验和日志 email 脱敏，但核心安全问题未解决
- **修复建议**:
  1. **立即禁用/删除此接口**，或添加管理员 Token 认证
  2. 长期：密码重置应走邮件验证码流程（smtp + gateway 统一实现）

---

### P0-2: yhdet OAuth Token 为 base64(email:password)，解码即得明文密码
- **位置**: `fsys.py:398-400`
- **问题**:
  ```python
  token_data = f"{email}:{password}"
  token = base64.urlsafe_b64encode(token_data.encode()).decode()
  ```
  Token 仅仅是 `email:password` 的 base64 编码，**无签名无加密**：
  1. XSS/日志泄露/网络抓包即可解码得到明文密码
  2. 密码若复用其他站点，导致更大范围账户接管
  3. `/api/yhdet_verify` 直接解码后查库，等价于明文密码传输
- **修复建议**:
  1. 废弃此 Token 设计，改为调用 gateway 登录接口获取正规 RSA 签名 Token
  2. 现有 yhdet_* 用户强制密码重置

---

### P0-3: yhdet_callback 禁用 SSL 证书验证（中间人攻击）
- **位置**: `fsys.py:361`
- **问题**:
  ```python
  with _urlreq.urlopen(req, timeout=10, context=__import__('ssl')._create_unverified_context()) as resp:
  ```
  使用 `_create_unverified_context()` 禁用 SSL 证书验证，中间人可伪造 yhdet.top 响应，以任意用户身份登录。
- **修复建议**: 移除 `_create_unverified_context()`，使用默认 SSL 验证。

---

### P0-4: yhdet OAuth 硬编码密钥
- **位置**: `fsys.py:312`
- **问题**:
  ```python
  YHDET_SECRET = "yhdet_qsont_oauth_2026_secret_key"
  ```
  OAuth 密钥硬编码在源码中并提交到仓库。
- **修复建议**: 移至环境变量或 `.env` 文件，从代码中移除。

---

### P0-5: 直连 gateway SQLite 数据库文件，绕过 db 服务
- **位置**: `fsys.py:287,349` 等多处
- **问题**: static 直接通过 sqlite3 模块连接 gateway 的数据库文件：
  1. 多进程并发写可能导致数据库损坏
  2. 绕过 db 服务的访问控制和审计
  3. 密码重置等敏感操作无日志
- **修复建议**: 所有数据库操作统一通过 pymsg 调 `db:8005` 服务。

---

## 🟠 P1 — 高危问题

| # | 问题 | 位置 |
|---|------|------|
| P1-1 | `/api/check_user` 无速率限制，可枚举有效邮箱 | `fsys.py:295-306` |
| P1-2 | 文件服务路径遍历防护需全面审计 | 文件浏览/下载接口 |
| P1-3 | yhdet 新用户默认密码可预测：`oauth_{user_id}` | `fsys.py:348` |

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
| NEW-1 | P0 | `proxy_send_code` 禁用 SSL 验证 | `fsys.py` L550-552 | 与 P0-3 相同模式：`check_hostname=False, verify_mode=CERT_NONE` |
| NEW-2 | P0 | `PRIVATE_SECRET` 硬编码 | `fsys.py` L464 | `"qsont_me_yhdet_2026_private"` 硬编码在源码中 |
| NEW-3 | P1 | CORS 配置过于宽松 | `fsys.py` L474 | `_cors` 函数设置 `Access-Control-Allow-Origin: *`，允许任意来源 |
| NEW-4 | P2 | `proxy_send_code` 包含死代码 | `fsys.py` L555-561 | `urlopen` 返回后 `return` 已执行，后续代码永不执行，且重复禁用 SSL 验证 |

---

## 修复路线图

### 立即（24小时）
- [ ] 禁用/删除无认证的 `/api/reset_password` 接口（P0-1）
- [ ] 移除 `_create_unverified_context()` 启用 SSL 验证（P0-3 + NEW-1）
- [ ] 从代码中移除硬编码 YHDET_SECRET 和 PRIVATE_SECRET（P0-4 + NEW-2）

### 短期（72小时）
- [ ] yhdet Token 改用 gateway 统一 RSA 签名 Token（P0-2）
- [ ] `/api/check_user` 添加速率限制（P1-1）
- [ ] yhdet 新用户密码改为随机生成（P1-3）
- [ ] CORS 限制为具体域名（NEW-3）

### 中期
- [ ] 所有数据库操作迁移到 pymsg 调 db 服务（P0-5）
- [ ] Range 请求边界条件校验（P2-1）
- [ ] 清理 `proxy_send_code` 死代码（NEW-4）
- [ ] yhdet_callback 注册日志 email 脱敏（P2-2）
