# gateway 模块安全审计（合并文档）

> 本文档合并了 gateway 自 v0.8.0 以来的三轮安全审计记录：
> - **2026-08-01** 初版审计（gateway v0.8.0，2500+ 行，认证内嵌）
> - **2026-08-07** 复审（P0 全部修复、进程退出 bug 修复）
> - **2026-08-09** 增量复审（8 项新发现：优雅关闭崩溃、头名大小写、XFF 伪造绕过修复、黑盒测试重写落地）
>
> **架构变更（v0.9.0）**：gateway 已退化为**纯转发服务**（仅 loopback 监听，不验签），认证职责（Token 签发/验签、登录/注册/重置、限流、WS 代理鉴权、PBKDF2/RSA）整体迁移至 [ca](../ca/) 模块。
>
> 所有 ✅ 已修复项已作为代码注释合入 `gateway/src/main.cpp`（以“审计 A*”“P1-1”等标记内联于对应代码）。本文档仅保留**开放项、历史追溯与上线检查清单**，避免陈旧信息。

---

## 1. 审计轮次概览

| 轮次 | 日期 | 基线 / 范围 | 关键结论 |
|------|------|-------------|----------|
| 初版 | 2026-08-01 | v0.8.0 `src/main.cpp`、`src/shared/`（libth/libhttp/libcrypt/libsqlite3） | 建立 P0/P1/P2 分级；P0-1 RSA 持久化定为设计接受；发现认证内嵌带来的 Token 吊销缺失等 |
| 复审 | 2026-08-07 | v0.9.0 前 `src/main.cpp`、`inc/`、配置 | P0 三项全部修复；进程退出 bug 修复；Token 吊销（P1-1）实现并随认证独立化迁 ca |
| 增量复审 | 2026-08-09 | v0.9.0 纯转发 `src/main.cpp` | 8 项新发现（A1/A2/A5/A8 等代理层修复、黑盒测试重写落地）；认证类问题转入 ca 审计 |

---

## 2. 已完成修复（已合入代码，供追溯）

代码位置：`gateway/src/main.cpp`（注释以“审计 A*”/“P1-1”等标记）。

- **A1** 优雅关闭 `double-join` 崩溃（删除 else 分支重复 `join`）
- **A2** 白名单外 Target-Url 返回 **403**（改抛 `HttpException 2303`，不再 500）
- **A5** `is_special_header` 头名大小写归一（统一转小写比较）
- **A8** 错误消息 `[Http][Error NNNN]` 前缀剥离（仅 `e.message()` 外泄，前缀留服务端日志）
- **P1-1** 路径遍历防护（`is_safe_target`）+ XFF 剥离与可信注入（`is_special_header` / `handleProxy`）
- **libhttp P1-2** 反向代理响应体 10MB 上限
- 信号安全重构（async-signal-safe 标志 + SIGPIPE 忽略 + 关闭 8s / 日志 3s 超时保护）
- 启动 loopback 强制自检 + `X-Auth-User` 存在性不变量（v0.9.0 新增）

> 认证侧脱敏（P0-2 / P0-3 错误信息脱敏）已在 `wrapperHttp` / `ExpBase.hpp` 闭环。

---

## 3. 已随认证迁移至 ca（v0.9.0，不再属 gateway 待办）

以下历史问题在 v0.9.0 认证独立化后于 ca 模块闭环，gateway 侧无需再处理；ca 的独立审计见 `ca_SECURITY_AUDIT.md`（其 §2 为 ca 相关开放项）。

- P0-1 RSA 密钥持久化（设计接受：重启即密钥轮换）
- P0-2 / P0-3 错误信息脱敏
- P1-1 Token 吊销机制（jti 黑名单 + 用户版本号双轨，已实现于 ca）
- P1-2 账户级登录失败锁定（ca 侧）
- P2-5 JTI 黑名单内存有界（ca 侧）
- P2-6 用户名 / 邮箱长度限制（ca 侧）
- A3 缺字段返回 400（ca 侧 handler）
- A6 / A7 验证码双渠道统一 / 废弃内置验证码（ca + captcha2 侧）

---

## 4. 当前开放项（gateway v0.9.0 纯转发）

| 编号 | 等级 | 问题 | 状态 | 说明 / 修复建议 |
|------|------|------|------|------|
| P1-3 | 🟠 | 上游代理 `HttpClient` 未显式设置超时 | ⚠️ 部分修复 | 下游服务（captcha/smtp）超时已配；代理转发 `HttpClient` 未设连接/读取超时。建议连接 5s、读取 30s（`handleProxy` 出站调用处） |
| P1-5 | 🟠 | 请求体大小无显式限制 | ❌ 未修复 | `req.body()` 直接读取，gateway 层未检查 `Content-Length`；依赖 libhttp 默认 10MB。建议超 10MB 返回 413 |
| P1-7 | 🟠 | CSRF Token 缺失 | ❌ 未修复（低优先级） | gateway 纯转发、无认证表单；前端 + ca 会话模型覆盖。如启用独立 HTTPS 模式（`tls = on`）需评估 |
| P1-8 | 🟠 | 日志注入 | ⚠️ 部分修复 | 用户输入经 ca 透传后直接拼接日志；内部 pymsg→db，外部不可见，风险低。建议对 username/email 等字段做换行符转义 |
| P2-2 | 🟡 | TLS 版本 / 套件未限定 | ❌ 未修复（低风险） | `tls = off`，TLS 由 WAF 终止；如启用 `tls = on` 需增加 TLS 版本与 CipherSuites 配置项 |
| P2-3 | 🟡 | CORS 反射任意 Origin | ❌ 未修复 | `_CORS_ORIGINS` 白名单已加载但未用于响应头；OPTIONS 预检由 libhttp 直接反射 Origin。需 libhttp 层接入白名单或由 gateway 注册显式 OPTIONS 路由 |
| P2-4 | 🟡 | HTTP 服务器无 Read/Write/Idle 超时 | ⚠️ 部分修复 | `HttpServer(addr,port,4)` 的 `4` 为连接超时，无请求级超时；存在 Slowloris / 连接耗尽风险 |
| A4 | 🟡 | 模式 B 独立部署限流可被 XFF 伪造绕过 | 📝 已文档化 | README 模式 B 已加安全降级提示（伪造 XFF 可绕过限流，生产必须用模式 A / WAF 前置）。根因需 libhttp 向 handler 透传 TCP 对端地址（跨模块 API 变更，见 `../reports/libhttp_remote_addr_dev_doc.md`） |

> **不在 gateway 范围（libhttp 层，gateway 侧跳过）**：P1-4 C++ HTTP 解析器 fuzz（属 libhttp）；P1-6 反向代理响应大小已由 libhttp 默认 10MB 保证（视为已闭环）。

---

## 5. 配置安全审计（历史结论，仍适用）

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 监听地址 | ✅ 安全 | `address = 127.0.0.1` |
| TLS 模式 | ✅ 符合部署 | `tls = off`，WAF 终止 |
| 管理员白名单 | ✅ 安全 | `users` 显式配置，无默认管理员 |
| CORS 白名单 | ⚠️ 配置未生效 | `allowed_origins` 已配但未用于响应头（见 P2-3） |
| Token 有效期 | ✅ 合理 | `keep_time = 3600`，`max_refresh = 10` |
| WS 代理路由 | ✅ 安全 | 仅 `/ws/aibox`，需管理员权限 |
| 敏感凭据 | ✅ 无泄露 | 配置文件无硬编码密码 / 密钥 |

---

## 6. 黑盒测试待办（需测试钩子）

- 完整旅程（captcha → send_code → register → login → whoami → logout）、Cookie 链路（Set-Cookie 属性、代理注入 Authorization）、jti / 版本号吊销、refresh_token、白名单 403（A2 已转绿）、防枚举时序
- `test_captcha.py` / `test_smtp.py` 重写为 pymsg 直连（captcha2:8009、smtp:8008）
- 钩子方案（二选一）：captcha2 / smtp 提供测试模式（固定验证码答案）；或 `gateway.ini` 增加 `[Security] captcha_required / email_code_required` 开关

---

## 7. 上线前检查清单

- [ ] 确认 WAF 规则已启用
- [ ] 确认所有服务绑定 127.0.0.1
- [ ] 确认云安全组 / ufw 仅放行 TCP 443
- [ ] 确认无默认管理员账户
- [ ] 确认 `.env` 权限 0600，SMTP 密码未硬编码
- [ ] 确认 gateway 配置 `tls = off`、`address = 127.0.0.1`
- [ ] 回归测试：登录 / 注册 / 代理 / Token 刷新 / 吊销全链路正常

---

**维护说明**：新增 / 复测开放项请直接编辑本文件；修复后请将对应行移至“已完成”（§2）或合入 `gateway/src/main.cpp` 代码注释后删除本行。

**审计人**: AI Security Auditor
**合并于**: 2026-08-10（三轮审计合并；源文件 `gateway_SECURITY_AUDIT_20260801.md` / `_20260807.md` / `_20260809.md` 已归档删除）
