# dnfs 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `dnfs.py`、`security_middleware.py`  
**代码版本**: dev_qing 分支

---

## 修复进度

| 优先级 | 总数 | 已修复 | 待修复 |
|--------|------|--------|--------|
| P0 | 1 | 0 | 1 |
| P1 | 2 | 0 | 2 |
| P2 | 2 | 0 | 2 |

---

## 🔴 P0 — 严重问题

### P0-1: Token 无 RSA 签名验证，可伪造任意用户身份（含管理员）
- **位置**: `dnfs.py:285-292`
- **问题**:
  ```python
  def decode_token(token: str):
      idx = token.find('_')
      if idx != -1:
          raw = token[:idx]
          # FIXME: 验证数字签名
          decoded = base64.b64decode(raw)
          jj = decoded.decode('utf-8')
          return json.loads(jj)
  ```
  代码明确标注 `FIXME: 验证数字签名`，但**完全跳过了 RSA 签名验证**：
  1. 攻击者可自行构造 base64 编码的 JSON payload（`{"user": "任意邮箱", "is_admin": true, ...}`）
  2. 下划线后拼接任意字符串作为"签名"
  3. 直接伪造管理员身份，绕过所有权限校验，可增删改任意节点
- **修复建议**:
  1. 立即实现 RSA 签名验证（与 gateway 验签逻辑一致）
  2. dnfs 需要持有 gateway 的 RSA 公钥（启动时从配置加载，或内置公钥）
  3. 添加 Token 过期校验（`created_at` + `keep_time`）
  4. 校验失败返回 401

---

## 🟠 P1 — 高危问题

| # | 问题 | 说明 |
|---|------|------|
| P1-1 | Token 过期校验不严谨 | 需严格验证 `created_at` + `keep_time`，防止过期 Token 被复用 |
| P1-2 | is_admin 权限完全依赖 Token 字段，无二次验证 | 管理员权限应在服务端再次校验，而非仅信任 Token 声明 |

---

## 🟡 P2 — 中危问题

| # | 问题 |
|---|------|
| P2-1 | AI 审核 fail-closed 实现需验证：ai_review 不可用时是否确实拒绝写入 |
| P2-2 | 节点删除无确认机制，批量删除无速率限制 |

---

## ✅ 已有安全机制

1. ✅ **特殊节点校验**: link/html/image/file 各类型有格式与大小限制（URL 前缀、大小上限）
2. ✅ **AI 内容审核集成**: 新增节点前调用 ai_review 服务审核
3. ✅ **security_middleware.py**: 安全响应头
4. ✅ **CORS 配置**: 跨域控制
5. ✅ **通过 pymsg 调 db 服务**: 不直连数据库，规避 SQL 注入

---

## 修复路线图

### 立即（24小时）
- [ ] 实现 Token RSA 签名验证（最高优先级）

### 短期（72小时）
- [ ] Token 过期严格校验
- [ ] is_admin 二次验证

### 中期
- [ ] AI 审核 fail-closed 验证测试
- [ ] 节点操作速率限制
