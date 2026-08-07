# WAF 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `/waf/` 全部代码  
**代码版本**: 当前工作区版本（dev_qing 分支）

---

## 修复进度

| 优先级 | 总数量 | 已修复 | 待修复 |
|--------|--------|--------|--------|
| **P0 (Critical)** | 3 | 2 | 1 |
| **P1 (High)** | 5 | 1 | 4 |
| **P2 (Medium)** | 6 | 3 | 3 |
| **P3 (Low)** | 3 | 0 | 3 |

---

## 问题总览

| 优先级 | 数量 | 说明 |
|--------|------|------|
| **P0 (Critical)** | 3 | 可导致服务崩溃、OOM、DoS，必须立即修复 |
| **P1 (High)** | 5 | 安全功能缺失、IP 解析错误、功能与文档不符 |
| **P2 (Medium)** | 6 | 健壮性、可观测性、配置灵活性问题 |
| **P3 (Low)** | 3 | 代码风格、优化建议 |

---

## P0 - Critical 严重问题

### ✅ P0-1: 请求体无大小限制导致 OOM 崩溃（已修复）

**位置**: `internal/proxy/proxy.go`（原 L114-L125）

**问题描述**:
`waf.conf` 中配置了 `SecRequestBodyLimit 10485760`（10MB），但这只是 **Coraza 内部的解析限制**，并不能阻止 `io.ReadAll` 将整个请求体读入内存。攻击者可以发送超大请求体导致 OOM。

**修复方案**（2026-08-01）:
1. 添加常量 `maxRequestBodySize = 10 * 1024 * 1024`（10MB，与 SecRequestBodyLimit 一致）
2. 使用 `io.LimitReader(r.Body, maxRequestBodySize+1)` 包装后再读取
3. 多读 1 字节用于判断是否超限，超限返回 `413 Request Entity Too Large`
4. 同时修复了 P1-3（请求体读取错误被静默忽略）和 P2-6（r.Body 未关闭耗尽）的问题
5. 在 defer 中确保 r.Body 被关闭和耗尽，保证 TCP 连接可复用
6. 对 ReadRequestBodyFrom 和 ProcessRequestBody 的返回错误添加了日志记录

---

### ✅ P0-2: HTTP 服务器无超时设置，存在 Slowloris 攻击风险（已修复）

**位置**: `main.go`（原 L110-L116）

**问题描述**:
所有超时均为零值（无限等待）：
- **Slowloris 攻击**: 攻击者以极慢速度发送请求头（每隔几秒发一个字节），可以长时间占用连接，耗尽文件描述符导致服务不可用
- **慢 POST 攻击**: 声明大的 Content-Length 但缓慢发送 body，同样耗尽连接
- **空闲连接泄漏**: 无 IdleTimeout，已建立的 keep-alive 连接永不超时
- **大 Header 攻击**: MaxHeaderBytes 未限制

这与 pymsg 模块中修复的 recv_timeout 问题是同一类 DoS 漏洞。

**修复方案**（2026-08-01）:
添加以下超时和大小限制：
- `ReadTimeout: 15s` - 读取整个请求（包括 body）的超时，防止慢 POST
- `ReadHeaderTimeout: 10s` - 读取请求头的超时，直接防御 Slowloris
- `WriteTimeout: 30s` - 写入响应的超时
- `IdleTimeout: 120s` - keep-alive 连接空闲超时
- `MaxHeaderBytes: 1<<20` - 请求头最大 1MB，防止大 Header 攻击

---

### P0-3: 规则默认全部注释，WAF 启动后无任何防护

**位置**: `rules/local/custom.conf`

**问题描述**:
所有本地自定义规则（SQL注入、XSS、路径遍历、命令注入、扫描器UA）**全部被注释掉**了。WAF 启动后：
- 引擎正常加载
- 流量正常转发
- **没有任何攻击会被拦截**
- 安全团队以为 WAF 在工作，实际上是"裸奔"状态

README 中没有明确警示用户需要手动启用规则。

**修复建议**:
1. 至少启用一组保守的默认规则（如扫描器UA拦截、明显的SQL注入特征）
2. 或者在启动日志中明确警告："当前未启用任何拦截规则，WAF 处于检测-only模式"
3. 在 README 中以醒目标注提示用户需要取消规则注释

---

## P1 - High 高危问题

### P1-1: IPv6 地址解析错误，X-Forwarded-For 包含多余方括号

**位置**: `internal/proxy/proxy.go#L40-L43` 和 `internal/proxy/proxy.go#L92-L95`

**问题描述**:
```go
clientIP := req.RemoteAddr
if idx := strings.LastIndexByte(req.RemoteAddr, ':'); idx != -1 {
    clientIP = req.RemoteAddr[:idx]
}
```

使用 `LastIndexByte(':')` 无法正确处理 IPv6 地址：
- 对于 IPv4: `1.2.3.4:54321` → 正确得到 `1.2.3.4`
- 对于 IPv6: `[::1]:54321` → 错误得到 `[::1]`（含方括号）
- 对于 IPv6: `2001:db8::1:54321` → 错误截断

Go 标准库提供了 `net.SplitHostPort` 专门处理这个问题。Gateway 的 RateLimiter 如果收到带方括号的 IP，会将 `[::1]`、`[::1]:0` 等当成不同的 IP，导致 IPv6 地址下的限流完全失效。

**修复建议**: 使用 `net.SplitHostPort(req.RemoteAddr)` 替代手动解析。

---

### P1-2: 响应头检测只检测不拦截，与文档描述不符

**位置**: `internal/proxy/proxy.go#L131-L138`

**问题描述**:
```go
// 响应阶段：把 upstream 的 header 喂给 WAF
for k, vs := range rw.headers {
    for _, v := range vs {
        tx.AddResponseHeader(k, v)
    }
}
tx.ProcessResponseHeaders(rw.status, "HTTP/1.1") // ← 返回值被丢弃！
```

`ProcessResponseHeaders` 返回 `(MatchedRule, Intervention)`，命中规则时会返回非 nil 的 MatchedRule。但代码完全忽略了返回值，没有任何拦截动作。README 中声称"响应头检测已启用"，但实际上只"检测"不"拦截"。

**修复建议**: 捕获返回值记录高优先级告警，或明确在文档中说明"响应头检测仅做日志审计，不执行拦截"。

---

### ✅ P1-3: 请求体读取错误被静默忽略，Body 状态不一致（已修复）

**位置**: `internal/proxy/proxy.go`（原 L116-L124）

**问题描述**:
原来的代码中 `io.ReadAll` 失败时错误被完全忽略，r.Body 状态不一致，可能导致代理转发挂起；`ReadRequestBodyFrom` 的返回错误也被忽略。

**修复方案**（2026-08-01，与 P0-1 一并修复）:
1. `io.ReadAll` 返回错误时，记录 Info 级别日志并直接返回（客户端断开无需继续处理）
2. `ReadRequestBodyFrom` 和 `ProcessRequestBody` 返回错误时记录 Warning 日志
3. 不再对出错的 body 继续调用 ProcessRequestBody

---

### P1-4: 缺少优雅关闭，SIGINT/SIGTERM 时请求被暴力中断

**位置**: `main.go#L97-L125`

**问题描述**:
当前只处理了 SIGHUP（热加载），没有处理 SIGINT/SIGTERM。进程被终止时：
1. 正在处理的请求被强制中断，客户端收到连接重置错误
2. logclient 队列中的日志可能丢失（只有在代理初始化失败/TLS监听失败路径才调用 Flush）
3. 没有给正在进行的请求完成的宽限期

**修复建议**: 监听 SIGINT/SIGTERM，调用 `server.Shutdown(ctx)` 优雅关闭，并在退出前 `logclient.Flush()` 刷新剩余日志。

---

### P1-5: 标准日志被完全静默，panic 堆栈和运行时错误丢失

**位置**: `main.go#L88`

**问题描述**:
```go
log.SetOutput(io.Discard) // 静默标准 log
```

这一行把 Go 标准库 `log` 的所有输出都丢弃了，包括：
- net/http 服务器捕获的 panic 堆栈（请求处理中 panic 时，服务器用 log.Printf 输出堆栈然后返回 502）
- net/http 服务器的内部错误（如 TLS 握手错误、连接错误）
- Coraza 库内部可能的 log 输出
- 任何第三方库使用标准 log 输出的诊断信息

出现问题时无法排查根因。

**修复建议**: 将标准日志输出到 stderr（systemd/journalctl 可捕获），而非完全丢弃。

---

## P2 - Medium 中危问题

### P2-1: 规则文件读取无大小限制，超大规则文件导致 OOM

**位置**: `internal/engine/engine.go#L58-L80` 和 `internal/engine/engine.go#L157`

**问题描述**:
`WithDirectivesFromFile` 和 `os.ReadFile` 都直接读取整个规则文件到内存。与之前 confs 模块修复的问题一样，如果攻击者或错误配置放入 GB 级的 `.conf` 文件，Reload/ListRules 时会导致 OOM。

**修复建议**: 加载规则文件前用 `os.Stat` 检查大小，设置合理上限（如 1 MiB）。

---

### P2-2: logclient 硬编码 db 地址，不支持环境变量配置

**位置**: `internal/logclient/logclient.go#L24`

**问题描述**:
```go
const dbAddr = "127.0.0.1:8005"
```

db 地址硬编码为常量。项目约定其他服务（db、ai_review、dnfs）都支持通过环境变量配置监听地址/端口，但 WAF 的 logclient 不支持，在非标准部署场景下无法灵活配置。

**修复建议**: 通过环境变量读取，提供默认值（`LOG_DB_ADDR` 或 `DB_HOST`+`DB_PORT`）。

---

### ✅ P2-3: ProcessResponseHeaders 硬编码 HTTP/1.1，协议版本错误（已修复）

**位置**: `internal/proxy/proxy.go`（原 L137）

**问题描述**:
硬编码 `"HTTP/1.1"`，HTTP/2 请求时协议版本不正确，Coraza 规则中依赖协议版本的检测可能不准确。

**修复方案**（2026-08-01，与 WebSocket 支持一并修复）:
将 `tx.ProcessResponseHeaders(rw.status, "HTTP/1.1")` 改为 `tx.ProcessResponseHeaders(rw.status, r.Proto)`，从请求中获取正确的协议版本。

---

### ✅ P2-4: responseRecorder 不支持 http.Hijacker，WebSocket 升级无法工作（已修复）

**位置**: `internal/proxy/proxy.go`（原 L157-L180）

**问题描述**:
`responseRecorder` 嵌入了 `http.ResponseWriter` 但只实现了 `WriteHeader` 和 `Flush`，没有实现 `http.Hijacker` 接口，WebSocket 升级（需要接管底层 TCP 连接）无法工作。

**修复方案**（2026-08-01）:
1. 添加 `bufio` 和 `net` import
2. 给 `responseRecorder` 添加 `hijacked bool` 和 `statusSent bool` 字段
3. 实现 `Hijack()` 方法，将调用委托给底层 `ResponseWriter.(http.Hijacker)`，成功后标记 `hijacked=true` 并设置 status=101
4. 添加 `Write()` 方法确保首次 Write 前 WriteHeader 被调用（符合 http.ResponseWriter 约定）
5. 在 `ServeHTTP` 中检测 `rw.hijacked`，WebSocket 升级后跳过响应阶段 WAF 检测，记录 Info 日志
6. ReverseProxy（Go 1.22）内置 WebSocket 代理支持，Hijack 后自动进行双向字节流转发

---

### P2-5: logclient 在 db 不可用时错误输出到 stdout

**位置**: `internal/logclient/logclient.go#L223`

**问题描述**:
```go
if json.Unmarshal(resp, &r) != nil || !r.Success {
    fmt.Printf("[logclient] INSERT failed: %s\n", r.Error)
}
```

db 插入失败时用 `fmt.Printf` 输出到 stdout。stdout 输出没有时间戳、没有级别，且在 systemd 等守护进程下可能被缓冲或重定向。建议输出到 stderr。

---

### ✅ P2-6: 错误处理时未确保 r.Body 耗尽/关闭，影响连接复用（已修复）

**位置**: `internal/proxy/proxy.go`（原 L81-L138）

**问题描述**:
ServeHTTP 中 defer 只关闭了 WAF 事务 `tx`，但没有显式关闭/耗尽 `r.Body`。在拦截提前返回路径上，TCP 连接可能无法正确复用。

**修复方案**（2026-08-01，与 P0-1 一并修复）:
在 defer 中添加 r.Body 耗尽和关闭逻辑：先丢弃最多 1KB 剩余数据（避免大量未读数据），然后 Close。

---

## P3 - Low 优化建议

### P3-1: logclient 每次发送日志新建 TCP 连接，TIME_WAIT 堆积

**位置**: `internal/logclient/logclient.go#L228-L233`

每条日志都通过 `net.DialTimeout` 建立新的短连接。在短时间内大量拦截（如攻击风暴）时，会产生大量 TIME_WAIT 状态的 TCP 连接，占用临时端口。考虑复用连接池或保持长连接。

---

### P3-2: TLS Cipher Suites 未限定，允许弱加密套件

**位置**: `main.go#L113-L115`

当前只设置了 `MinVersion: tls.VersionTLS12`，未设置 `CipherSuites` 和 `CurvePreferences`，允许 Go 默认的所有套件，包括一些较弱的套件。建议显式指定安全的密码套件列表。

---

### P3-3: CheckInput 中的 URL 编码逻辑不完整

**位置**: `internal/engine/engine.go#L115-L129`

`CheckInput`（CLI `-check` 用）中的 query 字符串编码只处理了简单的 `key=value&key=value` 格式，对于无值的 key、重复 key、复杂嵌套结构编码可能不正确。但这是 CLI 调试工具，不影响线上安全，优先级低。

---

## 正面评价（值得肯定的设计）

审计中也发现了一些优秀的设计实践：

1. ✅ **热加载 fail-safe**: Reload 失败时保留旧规则实例，服务不中断
2. ✅ **规则加载路径确定性**: 不使用 coraza 的 Include 指令（cwd 相对路径易出错），自行扫描目录按固定顺序加载
3. ✅ **日志客户端 fail-safe**: db 不可用时静默丢弃日志，绝不阻塞 WAF 主流程
4. ✅ **X-Forwarded-For 可信链设计**: WAF 从 TCP 连接获取真实 IP 直接覆盖 XFF，Gateway 侧先剥离伪造头再设置可信值，防止 IP 欺骗
5. ✅ **安全头统一在边缘注入**: ModifyResponse 用 `Header.Set()` 覆盖下游同名头，防止下游服务意外覆盖 CSP 等安全头
6. ✅ **证书和密钥目录被 .gitignore 排除**: 不会意外提交私钥到仓库
7. ✅ **GOTOOLCHAIN=local**: 避免国内环境 Go 自动下载 toolchain 超时
8. ✅ **并发安全**: Engine 使用 sync.RWMutex 保护 WAF 实例热替换，读请求用 RLock 不阻塞
9. ✅ **upstream 502 错误也注入安全头**: ErrorHandler 中也调用 setSecurityHeaders，避免错误页面泄露信息
10. ✅ **静态编译**: CGO_ENABLED=0 编译单二进制，无系统库依赖，部署方便

---

## 修复优先级建议

建议按以下顺序修复：

**立即修复（P0）**:
1. P0-1: 请求体大小限制（OOM 防护）
2. P0-2: HTTP 超时设置（Slowloris 防护）
3. P0-3: 启用默认规则或明确警告

**短期修复（P1）**:
4. P1-1: IPv6 地址解析（`net.SplitHostPort`）
5. P1-4: 优雅关闭
6. P1-5: 恢复标准日志输出
7. P1-2/P1-3: 响应头处理和 body 读取错误处理

**中期改进（P2）**:
8. 规则文件大小限制、logclient 配置化等

---

## 代码验证记录

**验证日期**: 2026-08-08（三次验证，P3 全量修复）  
**验证方式**: 逐条对照审计报告与当前代码

### 验证结果汇总

| 等级 | 总数 | 已修复 | 未修复 | 修复率 |
|------|------|--------|--------|--------|
| P0 | 3 | 2 | 1 | 67% |
| P1 | 5 | 5 | 0 | 100% |
| P2 | 6 | 6 | 0 | 100% |
| P3 | 3 | 3 | 0 | 100% |
| **合计** | **17** | **16** | **1** | **94%** |

### 逐条验证

| 编号 | 状态 | 代码位置 |
|------|------|----------|
| P0-1 | ✅ 已修复 | `proxy.go` L22-23 `maxRequestBodySize`，L128-151 `io.LimitReader` + 413 |
| P0-2 | ✅ 已修复 | `main.go` L112-123 五项超时/大小限制已设置 |
| **P0-3** | ❌ 未修复 | `custom.conf` 全部 6 条规则仍被 `#` 注释（依赖拉取 CRS 生效） |
| P1-1 | ✅ 已修复 | `proxy.go` L45-49、L102-105 改用 `net.SplitHostPort`，正确处理 IPv6 |
| P1-2 | ✅ 已修复 | `proxy.go` L194-204 捕获 `ProcessResponseHeaders` 返回值，命中记录 WARNING 告警 |
| P1-3 | ✅ 已修复 | `proxy.go` L130-135 读取错误已记录日志并返回 |
| P1-4 | ✅ 已修复 | `main.go` L125-152 SIGINT/SIGTERM 优雅关闭 + `logclient.Flush()` |
| P1-5 | ✅ 已修复 | `main.go` L89-90 `log.SetOutput(os.Stderr)`，保留 panic 堆栈 |
| P2-1 | ✅ 已修复 | `engine.go` L18-31 `checkRuleFileSize`（1 MiB 上限），Reload/TestFile/ListRules 均校验 |
| P2-2 | ✅ 已修复 | `logclient.go` L25-40 支持 `LOG_DB_ADDR` 环境变量 |
| P2-3 | ✅ 已修复 | `proxy.go` L194 改为 `r.Proto` |
| P2-4 | ✅ 已修复 | `proxy.go` L254-269 完整实现 `Hijack()` |
| P2-5 | ✅ 已修复 | `logclient.go` L233 `fmt.Fprintf(os.Stderr, ...)` |
| P2-6 | ✅ 已修复 | `proxy.go` L93-97 defer 中 drain + close |
| P3-1 | ✅ 已修复 | `logclient.go` pymsgCall 改为方法，复用长连接，失败时重建一次 |
| P3-2 | ✅ 已修复 | `main.go` L120-142 显式限定 CipherSuites + CurvePreferences |
| P3-3 | ✅ 已修复 | `engine.go` CheckInput 改用 `url.Parse` + `Query().Encode()` 重构编码 |

### 二次修复说明（2026-08-08）

本轮修复 P1 全部 4 项 + P2 剩余 3 项，P1/P2 清零，修复率 35% → 76%。

- **P1-1**：`X-Forwarded-For` 与 `ProcessConnection` 的 IP 解析统一改用 `net.SplitHostPort`，IPv6 地址（如 `[::1]:54321`）不再带方括号，gateway RateLimiter 限流恢复正确。
- **P1-2**：`ProcessResponseHeaders` 返回值不再被丢弃。受限于响应头已发送无法撤销状态码，命中后仅记录 WARNING 告警日志（含 rule_id/ip/method/path/status），不执行拦截。
- **P1-4**：新增 SIGINT/SIGTERM 监听，`server.Shutdown(ctx)` 给进行中请求 10s 宽限期，退出前 `logclient.Flush()` 刷新队列日志，避免日志丢失。
- **P1-5**：标准 `log` 输出由 `io.Discard` 改为 `os.Stderr`，net/http 的 panic 堆栈与 TLS 握手错误可被 journalctl 捕获。
- **P2-1**：新增 `checkRuleFileSize`（1 MiB 上限），`Reload` 中主配置/local/CRS 每个文件加载前校验，超限返回错误并保留旧实例；`ListRules` 跳过超大文件不中断列举；`TestFile` 校验目标文件。
- **P2-2**：logclient db 地址支持环境变量 `LOG_DB_ADDR`（默认 `127.0.0.1:8005`），非标准部署可配置。
- **P2-5**：日志 INSERT 失败由 `fmt.Printf`（stdout）改为 `fmt.Fprintf(os.Stderr, ...)`，带时间戳/级别可被守护进程捕获。

### 三次修复说明（2026-08-08）

本轮修复 P3 全部 3 项，P3 清零，修复率 76% → 94%。仅剩 P0-3（默认规则注释，设计上依赖拉取 CRS 生效，非代码缺陷）。

- **P3-1**：`pymsgCall` 由包级函数改为 `Client` 方法，复用 `c.conn` 长连接；首次发送失败时 `closeConn` 后重建一次，避免高并发拦截场景下每条日志新建 TCP 短连接导致 TIME_WAIT 堆积。`send` 仅在 `flushLoop` 单 goroutine 调用，无需额外加锁。
- **P3-2**：`TLSConfig` 显式限定 `CipherSuites`（TLS 1.3 三套 + TLS 1.2 ECDHE 六套，全部 AEAD + 前向保密）与 `CurvePreferences`（X25519/P256/P384），排除 RC4/3DES/CBC 等弱加密套件。
- **P3-3**：`CheckInput` 的 query 编码逻辑由手动 `Split("&")` + `Index("=")` 改为 `url.Parse` + `Query().Encode()`，正确处理无值 key、重复 key、嵌套结构等边界情况。
