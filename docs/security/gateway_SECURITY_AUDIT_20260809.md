# gateway 模块安全审计报告（2026-08-09 增量复审）

**审计日期**: 2026-08-09
**审计范围**: `src/main.cpp`（v0.8.0，2526 行全量）、`inc/DbC.hpp`、`inc/LogC.hpp`、`inc/Conf.hpp`、`inc/ExpBase.hpp`
**基线文档**: [gateway_SECURITY_AUDIT_20260807.md](gateway_SECURITY_AUDIT_20260807.md)（P0 3/3 已修复、P1 8 项 56%、P2 6 项 33%）
**审计方式**: 全量代码走查 + 实机黑盒实证（重建二进制 → 启动 8443 → curl 探针 12 组 → 对照基线做增量发现）
**配套交付**: `tests/blackbox/test_gateway.py` 重写 + `helpers.py` 增强（见 §六）

---

## 代码修复记录

**修复日期**: 2026-08-09（审计当日）

| 编号 | 状态 | 代码位置 | 修复说明 |
|------|------|----------|----------|
| A1 | ✅ 已修复 | `src/main.cpp` 关闭流程 | 删除 else 分支重复的 `log_flush_thread.join()`（ready 时 async 已完成 join） |
| A2 | ✅ 已修复 | `handleProxy` 白名单检查 | `throw std::runtime_error("403")` → `THROW_EX_CUSTOM(HttpException, 2303, "Forbidden: target not allowed", 403, "*")` |
| A3 | ✅ 已修复 | `handleLogin`/`handleRegister`/`handleResetPassword` | 字段提取 `obj.at()` 包 try/catch，缺字段返回 400 |
| A5 | ✅ 已修复 | `is_special_header()` | 比较前统一转小写，小写 `x-forwarded-for` 等变体不再绕过剥离 |
| A6 | ✅ 已修复 | `handleRegister` CAPTCHA 校验 | `verify_captcha()`（内置存储）→ `verify_captcha_remote()`（captcha2），与 `/captcha/image` 渠道互通 |
| A7 | ✅ 已修复 | `src/main.cpp` 内置验证码渠道 | 整体移除：`POST /captcha` 路由、`handleCaptcha`、`generate_captcha_text`/`verify_captcha`、`_CAPTCHA_STORE`/`_CAPTCHA_MTX`（净 -79 行）；未注册路径按既有通配代理语义处理 |
| A8 | ✅ 已修复 | `wrapperHttp` HttpException 分支 | 客户端消息从 `e.what()` 改为 `e.message()`，`[Http][Error NNNN]` 前缀仅保留在服务端 stderr/集中日志 |
| A4 | 📝 文档化 | `README.md` 部署模式 B | 根因需 libhttp 向 handler 透传 TCP 对端地址（跨模块 API 变更），本轮以文档化闭环：README 模式 B 增加安全降级提示（伪造 XFF 可绕过限流，生产必须用模式 A） |

### 修复验证

| 验证项 | 结果 |
|--------|------|
| 编译（make，g++ -std=c++20） | ✓ 通过（仅 LogC.hpp 既有 snprintf 警告） |
| 黑盒全量（test_gateway.py 34 断言） | ✓ **32 passed / 0 failed / 2 skipped**，锁定 A3 的两个红测试转绿 |
| A1 关闭路径 | ✓ `kill` 后 "已退出" 正常打印，进程 Done 退出，无 core dump（两轮构建均复核） |
| A2 | ✓ 代码路径确认（HttpException 经 wrapperHttp 映射 403；黑盒触达需有效 Token，待测试钩子） |
| A8 实证 | ✓ `POST /login {}` → `{"success":false,"message":"Bad_Request"}`，无 `[Http][Error 2002]` 前缀 |
| A7 实证 | ✓ `POST /captcha` 不再返回答案（落入通配代理语义，与其他未注册路径行为一致） |
| 黑盒全量回归（修复 A6/A7/A8 后） | ✓ 32 passed / 0 failed / 2 skipped，无回归 |

---

## 一、审计结论摘要

本次为增量复审，基线已确认项不重复列出（复测见 §五）。新发现 8 项：

| 等级 | 数量 | 编号 |
|------|------|------|
| 🔴 P0 | 0 | — |
| 🟠 P1 | 2 | A1（优雅关闭必崩溃）、A2（白名单拒绝返回 500 而非 403） |
| 🟡 P2 | 4 | A3（缺字段 500）、A4（XFF 伪造绕过限流）、A5（头名大小写架空 P1-1 修复）、A6（双 CAPTCHA 渠道不互通） |
| ⚪ P3 | 2 | A7（内置验证码接口明文给答案）、A8（内部错误码前缀外泄） |

**未发现新的 P0**。Token 验签链（RSA + 过期 + jti 黑名单 + 版本号）、PBKDF2（10 万轮 + 常数时间比较）、fail-closed 下游语义、防用户枚举 dummy 校验等核心机制实现正确。

---

## 二、P1 详细发现

### A1 [P1] 优雅关闭以崩溃收场：`log_flush_thread` double-join

**位置**: `src/main.cpp` L1066-1077

```cpp
std::thread log_flush_thread([]() { logs().shutdown(); });
auto future = std::async(std::launch::async, &std::thread::join, &log_flush_thread);
if (future.wait_for(std::chrono::seconds(3)) == std::future_status::timeout) {
    log_flush_thread.detach();
} else {
    log_flush_thread.join();  // ← async 已完成 join，此处二次 join
}
```

**根因**: `std::async(std::launch::async, &std::thread::join, &t)` 已在独立线程中执行完 `join()`；`wait_for` 返回 ready 后 else 分支再次 `t.join()`，此时 `joinable()==false` → 抛 `std::system_error`（EINVAL）→ 无人捕获 → `std::terminate` → SIGABRT。

**实证**:
```
$ kill <pid>
terminate called after throwing an instance of 'std::system_error'
  what():  Invalid argument
[1]+  Aborted (core dumped)  nohup ./build/gateway.out
```
"已退出" 收尾日志不打印，进程以 core dump 结束。基线报告修复的"进程退出 bug"（0807 版）实际只修了一半：main 函数体能走完，但关闭路径最后一步仍崩溃。

**影响**: 每次正常关闭（SIGINT/SIGTERM）都产生 core dump；systemd 下触发不必要告警；关闭期日志是否落库无从保证。

**修复建议**: 删除 else 分支中重复的 `log_flush_thread.join()`（ready 即已 join 完毕）。

### A2 [P1] 白名单外 Target-Url 返回 500 而非文档宣称 403

**位置**: `src/main.cpp` L2401-2404（`handleProxy`）

```cpp
if (!is_safe_target(target)) {
    throw std::runtime_error("403");   // 意图返回 403
}
```

**根因**: `std::runtime_error` 不是 `HttpException`，被 `wrapperHttp()`（L1288-1303）的 `std::exception` 兜底分支统一包装为 500 "Internal Server Error"。README 接口契约明确写"目标不在白名单 → 403"。

**实证局限**: 白名单检查在 Token 验签之后，无有效 Token 时只能走到 401，黑盒无法触达；但代码路径确凿（`runtime_error` 必经 500 兜底）。待测试钩子落地后补黑盒断言。

**影响**: 客户端无法区分"目标被拒"与"服务器故障"；监控告警误报 500。

**修复建议**: 改为 `THROW_EX_CUSTOM(HttpException, 2303, "Forbidden", 403, "*")`，与 L2370 验签失败的风格一致。

---

## 三、P2 详细发现

### A3 [P2] 缺失 JSON 字段 → 500 而非 400

**位置**: `src/main.cpp` L1373-1374（`handleLogin`，同型问题遍布 `handleRegister`/`handleResetPassword`/`handleSendEmailCode` 等）

```cpp
auto& obj = json.as_object();
std::string username = obj.at("username").as_string().c_str();  // ← 在 try 块外
std::string password = obj.at("password").as_string().c_str();
```

**根因**: JSON 语法解析有 try/catch（解析失败 → 400），但随后的字段提取 `obj.at()` 在 try 之外，`std::out_of_range` 直达 `wrapperHttp` 兜底 → 500。

**实证**: `POST /login` body `{}` → **500**（应为 400 Bad Request）。已由新版黑盒测试 A5/A8 两个用例锁定（当前红，修复后转绿）。

**修复建议**: 各 handler 的字段提取统一包进 try/catch，缺字段返回 400；或提供 `get_or_throw_400(obj, key)` 辅助函数。

### A4 [P2] 独立部署模式 B 下限流可被 XFF 伪造绕过

**位置**: `src/main.cpp` L1353-1359（`handleLogin`，`handleRegister`/`handleCaptchaImage` 同型）

```cpp
std::string client_ip = req.base()["X-Forwarded-For"];  // 直连模式下即客户端伪造值
if (client_ip.empty()) client_ip = req.remote_ip();
```

**根因**: 模式 A（WAF 前置）中 WAF 会覆写 XFF，此逻辑安全；模式 B（独立部署）中 XFF 完全由客户端控制，每个伪造 IP 都是独立限流桶 → 限流失效。基线报告 P1-1 修复的是"向下游注入可信 XFF"，限流侧未覆盖。

**实证**:
```
XFF=10.1.1.1 连续 6 次 POST /login → 第 6 次 429
XFF=10.1.1.2 立即再发          → 400（未限流，新桶）
```

**影响**: 登录爆破/注册滥用防护在独立部署下可绕过（每次请求换一个 XFF 即无上限）。

**修复建议**: 二选一——(a) 配置项声明部署模式，模式 B 下限流 key 只用 `remote_ip()`；(b) README 明确"独立部署时安全降级：限流不可用"并建议仅在 WAF 后使用。

### A5 [P2] `is_special_header` 大小写敏感，伪造 XFF 绕过剥离，架空 P1-1 修复

**位置**: `src/main.cpp` L2464-2476

```cpp
static bool is_special_header(const std::string& name) {
    return name == "Host" || name == "connection" || name == "Accept-Encoding" ||
           name == "X-Forwarded-For" || name == "X-Real-IP" || name == "Forwarded";
}
```

**根因**: Boost.Beast 的 `field.name_string()` 保留线上原始大小写。客户端发送小写 `x-forwarded-for: 6.6.6.6` 时不匹配 `"X-Forwarded-For"`，不被剥离；gateway 随后注入可信 XFF，下游收到**两个** XFF 头——若下游取第一个（常见实现），攻击者伪造值生效，基线 P1-1"注入可信 XFF"的修复被架空。

**实证**: 代码级确认（`name_string()` 行为见 Beast 文档；代理剥离逻辑 L2410-2420 逐头比对）。

**修复建议**: 比较前统一转小写（`boost::beast::iequals` 或手动 lowercase 后查集合），并将 `connection`/`keep-alive`/`transfer-encoding` 等逐跳头一并规范化。

### A6 [P2] 注册/登录 CAPTCHA 双渠道不互通，README 与实现不符

**位置**: `src/main.cpp` L1954（`handleRegister`）vs L1845-1887（`handleCaptchaImage`）

**根因**: 注册接口用 `verify_captcha()` 校验**内置** `_CAPTCHA_STORE`（内置 `GET/POST /captcha` 接口签发的 32 位 token）；而前端实际通过 `/captcha/image` 代理拿的是 **captcha2** 签发的 64 位 token。两个存储互不通信——前端走完图形验证码流程后，注册提交 captcha_token 永远 invalid。`/login` 走的是远端 `verify_captcha_remote()`（captcha2），与 `/captcha/image` 配套，正常。

**实证**: 不带 captcha 字段 `POST /login` → 400 Bad_Request（README 称"可选"，代码实为强制）。

**影响**: 注册流程的 CAPTCHA 环节对真实前端不可用（目前靠"不提交则跳过"绕过——即注册 CAPTCHA 形同虚设）；文档误导。

**修复建议**: 统一走 `verify_captcha_remote()`；废弃内置验证码接口（见 A7）；README 更新为"登录强制 CAPTCHA"。

---

## 四、P3 详细发现

### A7 [P3] 内置 `POST /captcha` 明文返回答案，人机验证无效化

**位置**: `src/main.cpp` 内置验证码接口（`_CAPTCHA_STORE` 配套路由）

**实证**:
```
$ curl -X POST :8443/captcha
{"token":"...","text":"AmM2U"}     ← 答案明文随 token 一起返回
```

**叠加问题**: 该接口无速率限制、`_CAPTCHA_STORE` 无容量上限（内存 DoS 面）。

**修复建议**: 直接废弃内置验证码接口与 `_CAPTCHA_STORE`（captcha2 已全面接管），删除路由。

### A8 [P3] 错误消息携带内部错误码前缀外泄

**实证**: 错误响应体形如 `[Http][Error 2002] Bad_Request` —— 内部模块名与错误码编号随 4xx 返回客户端。

**评估**: 不构成路径/类型泄露（P0-2/P0-3 的脱敏基线未违反，16 条错误样本回归测试通过），但为攻击者提供内部结构测绘素材，且污染前端可直接展示的消息。

**修复建议**: `wrapperHttp` 返回客户端前剥离 `[模块][Error NNNN]` 前缀（完整信息保留在服务端日志）。

---

## 五、基线遗留项复测

| 编号 | 项 | 复测结果 |
|------|----|----------|
| P1-1 | 伪造 XFF 注入下游 | 修复已生效（标准大小写头被剥离），但**被 A5 架空** |
| P2-3 | CORS 反射任意 Origin | ❌ 仍存在：`Origin: https://evil.example.com` 被原样反射进 ACAO |
| — | 路径遍历防护 | ✅ `..`、`%2e`、`%2f`、`%5c` 四种变体全部 400 |
| — | `/whoami` 无/伪造 Token | ✅ 恒 `{login:false}`，不泄露验签细节 |
| — | `/admin/verify` fail-closed | ✅ 无/伪造 Token 均 401 |
| — | `/captcha/image` 代理 | ✅ 200 + image/png + 64 位 hex token + `no-store` + 限流 10/min/IP |
| — | 默认代理下游宕机语义 | ⚠️ static 宕机时默认路由 500（环境语义，非缺陷） |

---

## 六、黑盒测试补充（已落地）

### 6.1 测试套件脱节结论

- **test_gateway.py 旧版 10 场景已失效**：v0.8.0 后 /login 强制远端 CAPTCHA、/register 强制 email_code，无 OCR/收信能力的黑盒 happy path 全断；且无 db 清理，反复运行污染 Users 表
- **test_captcha.py / test_smtp.py 形同虚设**：用 HTTP 测 pymsg/TCP 服务（captcha2:8009、smtp:8008），captcha 用例期望 32 位 token（captcha2 实为 64 位）
- **可测试性根因**：认证 happy path 需要测试钩子——方案 a：captcha2/smtp 提供测试模式（固定验证码答案）；方案 b：`gateway.ini` 增加 `[Security] captcha_required/email_code_required` 开关

### 6.2 本轮交付（P0 部分，无需钩子）

**`tests/blackbox/helpers.py` 增强（+48 行）**:

| 工具 | 用途 |
|------|------|
| `random_xff()` | 随机 XFF 隔离限流桶（利用 A4 行为，避免限流用例互斥） |
| `assert_no_internal_leak()` | 错误响应脱敏回归（P0-2/P0-3 基线：无源码路径/C++ 类型名） |
| `db_execute()` | pymsg 调 db 做测试数据准备/清理 |

**`tests/blackbox/test_gateway.py` 重写（34 断言 6 段）**:

| 段 | 内容 | 断言数 |
|----|------|--------|
| A | 输入校验新契约（强制 captcha/email_code、fail-closed 语义） | 8 |
| B | `/whoami`、`/admin/verify`（含伪造 Token） | 4 |
| C | `/captcha/image` 代理（200/PNG/64 位 token/no-store） | 4 |
| D | 无 Token 401 + 路径遍历矩阵（原始请求行，`%2e/%2f/%5c` 变体） | 5 |
| E | 限流全谱：登录 6th→429、注册 4th→429、发码 6th→429、验证码 11th→429、换 XFF 隔离 | 9 |
| F | CORS 反射现状固化（P2-3 修复后翻转断言）+ 16 条错误样本脱敏回归 | 2 |
| 尾 | 测试用户清理（db `like` 删除，旧版 `test_%` 污染仅报告） | 2 |

**实测结果**（gateway 实跑）: `34 tests: 30 passed, 2 failed, 2 skipped`

- 2 个 FAIL 为刻意锁定 A3 的红测试（缺字段断言 400，当前实现 500），修复后自然转绿
- 2 个 SKIP 为环境性（static 未运行；本地 db 无 Users 表——**本环境下注册写库必 500**，跑 P1 认证链路测试前需先执行 `db/create_sqlite3.sql` 建表）

### 6.3 后续清单（需钩子，P1/P2）

- 完整旅程（captcha → send_code → register → login → whoami → logout）、Cookie 链路（Set-Cookie 属性、代理注入 Authorization）、jti/版本号吊销、refresh_token、白名单 403（A2 转绿）、防枚举时序
- test_captcha.py / test_smtp.py 重写为 pymsg 直连

---

## 七、修复建议优先级

| 优先级 | 项 | 说明 |
|--------|----|------|
| P1 | A1、A2 | double-join 删 3 行；A2 改抛 HttpException，均为小改动 |
| P2 | A3、A5、A6、A4 | A3 修复后两个红测试转绿；A5 头名归一化；A6 统一走 captcha2；A4 配置或文档化 |
| P3 | A7、A8 | 废弃内置验证码接口；错误消息剥离内部前缀 |
| 配套 | 测试钩子 | captcha2/smtp 测试模式或 gateway.ini 开关，解锁 P1 测试清单 |
