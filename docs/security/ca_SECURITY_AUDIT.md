# ca 模块安全审计索引（Central Auth）

> ca（Central Auth，C++20，v1.0.0）是 UniServe Framework 的中央认证服务，承接原 gateway v0.8.0 的全部认证职责。
> 本索引汇总：① 从 gateway v0.9.0 认证独立化**迁移接收**的审计项（已在 ca 闭环）；② **ca 相关开放项**（与 gateway 转发层/配置共享，需 ca 侧处理）；③ ca 原生安全态势基线。
> 关联文档：gateway 合并审计 [`gateway_SECURITY_AUDIT.md`](gateway_SECURITY_AUDIT.md)。

---

## 1. 从 gateway 迁移接收项（v0.9.0，已在 ca 闭环）

下列历史问题随 v0.9.0 认证独立化由 gateway 移交 ca，gateway 侧不再跟踪。ca 已闭环（实现见 `ca/src/main.cpp`）：

| 原编号 | 问题 | ca 侧状态 | 实现位置 / 说明 |
|--------|------|-----------|----------------|
| P0-1 | RSA 密钥持久化 | 设计接受（重启即密钥轮换，同进程验签） | ca 内存生成 RSA 密钥 |
| P0-2 / P0-3 | 错误信息脱敏 | ✅ 已闭环 | ca handler 错误不外泄内部原因 |
| P1-1 | Token 吊销机制 | ✅ 已实现 | jti 黑名单（5000 阈值）+ 用户版本号双轨，纯内存 |
| P1-2 | 账户级登录失败锁定 | ✅ 已实现 | 登录失败计数 + 锁定（ca 侧） |
| P2-5 | JTI 黑名单内存有界 | ✅ 已实现 | 5000 条阈值清理 |
| P2-6 | 用户名 / 邮箱长度限制 | ✅ 已实现 | ca 输入校验 |
| A3 | 缺字段返回 400 | ✅ 已实现 | ca handler 参数校验 |
| A6 / A7 | 验证码双渠道统一 / 废弃内置验证码 | ✅ 已闭环 | 统一走 captcha2 远端校验 |

---

## 2. ca 相关开放项（需 ca 侧处理）

下列 gateway 合并审计的开放项与 ca 直接相关，归 ca 跟踪：

| 编号 | 等级 | 问题 | 归属说明 | 状态 |
|------|------|------|----------|------|
| P2-3 | 🟡 | CORS 白名单未生效（OPTIONS 预检反射 Origin） | ca `CORS.allowed_origins` 已读取但未用于预检响应头（见 `ca/README.md` 配置段）；与 gateway 共享同一白名单配置 | ❌ 未修复 |
| P1-7 | 🟠 | CSRF Token 缺失 | gateway 纯转发无认证表单；CSRF 评估在 ca 层（会话模型 + `SameSite=Lax`）。如 ca 启用 `tls=on` 独立 HTTPS 需评估 | ❌ 未修复（低优先级） |
| A2 | 🟠 | 白名单 403 断言 | `Target-Url` 白名单由 gateway `Proxy.opened` 裁决 403，但白名单配置 / 断言随转发层迁入 ca；跨模块边界，gateway 与 ca 需保持一致 | ⚠️ 边界共识（gateway A2 已转绿，ca 侧需保持白名单同步） |

> 其余 gateway 开放项（P1-3 代理超时、P1-5 请求体大小、P1-8 日志注入、P2-2 TLS、P2-4 HTTP 超时、A4 XFF）属 gateway 纯转发层 / libhttp 层，不归 ca，见 [`gateway_SECURITY_AUDIT.md`](gateway_SECURITY_AUDIT.md) §4。

---

## 3. ca 原生安全态势基线

摘自 `ca/README.md` 安全特性（v1.0.0）：

| 特性 | 实现 |
|------|------|
| 密码哈希 | PBKDF2-HMAC-SHA256，100000 轮，16B 随机盐，32B 哈希 |
| 密码强度 | ≥8 字符，大写/小写/数字/特殊中至少 3 类 |
| Token 机制 | `Base64(payload) + "_" + RSA-Sign(payload)`，验签 + 过期（默认 1h） |
| Token 吊销 | 双轨纯内存：jti 黑名单（5000 阈值）+ 用户版本号 |
| 常数时间比较 | PBKDF2 验证 `diff |= hash[i] ^ expected[i]` 防 timing |
| 防枚举 | 不存在用户 dummy PBKDF2 拉平时序；重置密码恒成功 |
| CAPTCHA | 统一 captcha2 远端校验（一次性 Token）；`/login` 强制 |
| TLS 可选 | `Server.tls = off` 纯 HTTP（配合 WAF），`on` 用 `full_chain.crt` / `server.key` |
| 下游容错 | pymsg `retry_on_refused` 阻塞等待 db/confs；日志 fail-safe |

---

## 4. 维护说明

- 新增 / 复测 ca 相关开放项请编辑本文件 §2；修复后移至 §1 或合入 `ca/src/main.cpp` 注释。
- gateway 合并审计的“已迁 ca”清单（§1）以本文件为权威；如 gateway 文档与本文件不一致，以本文件为准。

**审计人**: AI Security Auditor
**建立于**: 2026-08-10（从 gateway 合并审计同步开放项与迁移接收项；此前 ca 无独立审计文档）
