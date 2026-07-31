# static 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `fsys.py`、`security_middleware.py`  
**代码版本**: dev_qing 分支

---

## 修复进度

| 优先级 | 总数 | 已修复 | 待修复 |
|--------|------|--------|--------|
| P0 | 5 | 0 | 5 |
| P1 | 3 | 0 | 3 |
| P2 | 2 | 0 | 2 |

---

## 🔴 P0 — 严重问题

### P0-1: 无认证密码重置接口，可接管任意账户
- **位置**: `fsys.py:278-292`
- **问题**:
  ```python
  @app.route('/api/reset_password', methods=['POST'])
  def api_reset_password():
      data = request.get_json(force=True)
      email = data.get('email', '').strip()
      new_hash = data.get('password_hash', '').strip()
      conn = _sql.connect(GATEWAY_DB)
      conn.execute("UPDATE Users SET password_hash=? WHERE email=?", (new_hash, email))
      conn.commit()
  ```
  `/api/reset_password` **无任何认证**，任何人只要知道邮箱即可修改任意用户密码（包括管理员），直接接管账户。
- **修复建议**:
  1. **立即禁用/删除此接口**，或仅允许本机访问 + 管理员 Token
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

1. ✅ **security_middleware.py**: 安全响应头（CSP、X-Frame-Options、nosniff 等）
2. ✅ **路径遍历防护**: `safe_path_join()` realpath 前缀校验
3. ✅ **dnfs 代理**: 解决浏览器跨域问题
4. ✅ **静态文件托管**: 独立服务，经 gateway 代理访问

---

## 修复路线图

### 立即（24小时）
- [ ] 禁用/删除无认证的 `/api/reset_password` 接口
- [ ] 移除 `_create_unverified_context()` 启用 SSL 验证
- [ ] 从代码中移除硬编码 YHDET_SECRET

### 短期（72小时）
- [ ] yhdet Token 改用 gateway 统一 RSA 签名 Token
- [ ] `/api/check_user` 添加速率限制
- [ ] yhdet 新用户密码改为随机生成

### 中期
- [ ] 所有数据库操作迁移到 pymsg 调 db 服务
- [ ] 文件服务路径遍历全面审计
