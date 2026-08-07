# waf — Web 应用防火墙网关

基于 Go + [coraza](https://github.com/corazawaf/coraza) 的 WAF 网关，部署在 `gateway` 之前，终止 TLS 并按 SecLang 规则库匹配请求，未命中则转发到 gateway。

```
客户端 ──HTTPS──▶ waf:443 (终止 TLS + coraza 规则匹配) ──明文 HTTP──▶ gateway:8443 (鉴权 + 反代)
```

## 为什么是 Go

- `coraza-waf/coraza` 是 ModSecurity SecLang 的 Go 实现，100% 兼容 OWASP CRS v4
- `net/http/httputil.ReverseProxy` 标准库反向代理，零第三方依赖
- goroutine + net/http 吞吐量比 Python 高一个数量级
- `go build` 出单二进制，`core/init.txt` 直接拉起

## 极客友好的规则系统

| 操作 | 命令 |
|------|------|
| 编辑规则 | `vim rules/local/custom.conf` |
| 校验语法 | `./waf.bin -test rules/local/custom.conf` |
| 列出所有规则 | `./waf.bin -list` |
| 测试输入 | `./waf.bin -check "/?id=1' OR '1'='1"` |
| 热加载（不重启） | `kill -HUP $(pgrep waf)` |
| 临时禁用某文件 | `mv rules/local/x.conf x.conf.bak && kill -HUP $(pgrep waf)` (引擎仅加载 `.conf` 后缀) |

### 规则文件组织

```
rules/
├── waf.conf              # 主入口：基础配置（SecRuleEngine / SecRequestBodyLimit 等）
├── local/                # 本地自定义规则（ID 段 100000-199999）
│   ├── custom.conf       # 示例：SQL注入/XSS/路径遍历/命令注入/扫描器UA
│   ├── exceptions.conf   # 误报白名单（SecRuleRemoveById / ctl:ruleRemoveById）
│   └── tuning.conf       # CRS 调优：偏执等级 + 异常评分阈值（自动拦截的"旋钮"）
└── crs/                  # OWASP CRS（不入库，由 _fetch_crs.sh 拉取，可选）
    ├── coraza.conf       # CRS 入口（请求体处理器初始化 + tx.crs_setup_version）
    ├── LICENSE           # Apache-2.0
    └── rules/*.conf      # CRS 规则文件
```

> **注意**：engine 不使用 coraza 的 `Include` 指令（其路径相对 cwd 解析易出错），改为在 [engine.Reload](internal/engine/engine.go) 中自行扫描目录、逐个 `WithDirectivesFromFile` 加载。

### 规则加载顺序

由 [engine.go:50-89](internal/engine/engine.go#L50) `Reload` 控制，固定顺序：

1. `waf.conf` — 基础配置
2. `local/*.conf` — 本地自定义规则（按文件名字母序）
3. `crs/coraza.conf` — OWASP CRS 入口（**仅当文件存在时加载**）
4. `crs/rules/*.conf` — OWASP CRS 规则（按文件名字母序）

### 规则 ID 分段

| ID 段 | 用途 |
|-------|------|
| 100000-199999 | 本地自定义规则 |
| 200000-200006 | CRS 请求体处理器初始化（XML/JSON，由 `crs/coraza.conf` 定义，仅当启用 CRS 时存在） |
| 900000-999999 | OWASP CRS 规则（由 `crs/rules/` 提供，不手改，仅当启用 CRS 时存在） |

### 规则语法（SecLang）

```apache
SecRule REQUEST_URI|ARGS "@rx (?i)union[\s/*]+select" \
    "id:100001,phase:1,deny,status:403,log,msg:'SQL injection: UNION SELECT'"
```

字段说明：
- `REQUEST_URI|ARGS` 匹配目标（URI / 参数 / Header / Cookie 等）
- `@rx ...` 正则匹配（也支持 `@pm`、`@contains`、`@beginsWith` 等）
- `phase:1` 请求头阶段 / `phase:2` 请求体阶段 / `phase:3` 响应头 / `phase:4` 响应体
- `deny,status:403` 命中后动作
- `msg` 命中日志描述

## 异常评分制自动拦截

启用 OWASP CRS 后，WAF 采用**异常评分制**自动拦截，而非单条硬规则直接 deny——无需人工干预，按命中严重度自动分级：

- 每条规则命中时按严重度累加异常分：`CRITICAL=5`、`ERROR=4`、`WARNING=3`、`NOTICE=2`
- 当 `tx.anomaly_score >= tx.inbound_anomaly_score_threshold` 时，规则 **949110** 自动 `deny`
- **低分请求放行、高分请求拦截**，全自动

### 引擎模式（[waf.conf](rules/waf.conf)）

| 模式 | 行为 |
|------|------|
| `On` | 自动拦截：异常评分达到阈值即 deny（当前配置） |
| `DetectionOnly` | 只记录异常评分，不执行 deny（用于观察误报） |
| `Off` | 完全关闭 |

> 上线建议：先 `DetectionOnly` 跑一段时间，确认异常评分无误报后，再切回 `On` 开启自动拦截。

### 调参旋钮（[rules/local/tuning.conf](rules/local/tuning.conf)）

| 参数 | 当前值 | 说明 |
|------|--------|------|
| `tx.blocking_paranoia_level` | `1` | 偏执等级，1 最保守（误报最少）；2/3/4 递增更严、误报更多 |
| `tx.inbound_anomaly_score_threshold` | `5` | 请求侧拦截阈值，越小越敏感、误报越多；越大越宽松、漏报越多 |
| `tx.outbound_anomaly_score_threshold` | `4` | 响应侧拦截阈值 |

调参后 `kill -HUP <pid>` 热加载即可，无需重启。

> 说明：`tuning.conf` 独立于自动生成的 `crs/coraza.conf`，重跑 `_fetch_crs.sh` 不会覆盖调优配置。

## 部署形态

WAF 必须终止 TLS 才能看到明文 body，因此证书需从 gateway 迁到 WAF：

1. 把 `gateway` 的 `full_chain.crt` / `server.key` 复制到 `waf/certs/`
2. `gateway` 改为监听 `127.0.0.1:8443`，关闭 TLS（仅本机）
3. `waf` 监听 `:443`，转发到 `http://127.0.0.1:8443`

## 配置

`config.yaml`：

```yaml
listen: ":443"
tls_cert: "./certs/full_chain.crt"
tls_key:  "./certs/server.key"

# gateway 内部 HTTP 端口（gateway 需改为监听 127.0.0.1:8443 不启用 TLS）
upstream: "http://127.0.0.1:8443"

# 规则根目录，主入口为 <rules_dir>/waf.conf
rules_dir: "./rules"
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `listen` | `:443` | 监听地址 |
| `tls_cert` / `tls_key` | — | TLS 证书与私钥路径 |
| `upstream` | — | gateway 地址，**必填**（启动时硬性校验，缺失将导致进程退出） |
| `rules_dir` | `./rules` | 规则根目录 |

## 目录结构

```
waf/
├── main.go                  # 入口：服务模式 + CLI 子命令
├── config.yaml              # 配置
├── go.mod
├── internal/
│   ├── engine/
│   │   └── engine.go        # coraza 封装：加载/热加载/列举/单测/输入检测
│   ├── logclient/
│   │   └── logclient.go     # Go 版 pymsg 日志客户端（异步批量推送）
│   └── proxy/
│       └── proxy.go         # ReverseProxy + WAF 事务接入 + 安全响应头注入
├── rules/
│   ├── waf.conf
│   └── local/
│       ├── custom.conf
│       ├── exceptions.conf
│       └── tuning.conf       # CRS 调优（偏执等级 + 异常评分阈值）
├── _set.sh                  # 编译（含 go mod tidy + download + build）
├── _run.sh                  # 运行
├── _goproxy.sh              # 设置 GOPROXY 国内镜像（由 _set.sh 调用）
├── _fetch_crs.sh            # 拉取 OWASP CRS（可选）
├── .gitignore
└── README.md
```

## 安装

```bash
# 1. 安装 Go 1.21+（实际要求 1.22+，见 go.mod）
brew install go    # macOS
# 或 sudo apt install golang-go  # Debian/Ubuntu

# 2. 编译（_set.sh 会自动设置 GOPROXY 国内镜像 + GOTOOLCHAIN=local）
bash _set.sh

# 3. 准备证书
mkdir certs && cp /path/to/full_chain.crt /path/to/server.key certs/

# 4. 运行
bash _run.sh
```

> **国内环境**：[_set.sh](_set.sh) 会调用 [_goproxy.sh](_goproxy.sh) 自动设置 `GOPROXY=https://goproxy.cn,direct` 与 `GOSUMDB=sum.golang.google.cn`；并强制 `GOTOOLCHAIN=local` 避免 Go 1.21+ 自动从 `proxy.golang.org` 下载 toolchain 超时。用户已显式配置非默认代理时尊重不覆盖。

## 接入 core 自启动

在 `core/init.txt` 增加：

```
bash /opt/micro_servers/waf/_run.sh
```

并把 `gateway` 改为内部 HTTP 端口（不再监听 443）。

## 安全响应头

WAF 在边缘层统一注入安全响应头（[proxy.go:54-77](internal/proxy/proxy.go#L54-L77) `setSecurityHeaders`），覆盖所有出站响应（包括正常转发、命中拦截、上游 502）。下游服务（static/dnfs 等）的同名头会被 WAF 覆盖，避免 CSP 绕过。

| Header | 值 | 说明 |
|--------|----|------|
| X-Frame-Options | SAMEORIGIN | 允许本站 iframe 嵌套（登录页条例弹窗），防跨站点击劫持 |
| X-Content-Type-Options | nosniff | — |
| X-XSS-Protection | 1; mode=block | 现代浏览器已废弃，仅兼容旧浏览器 |
| Referrer-Policy | strict-origin-when-cross-origin | — |
| Permissions-Policy | camera=(), microphone=(), geolocation=() | — |
| Strict-Transport-Security | max-age=15552000 | HSTS 半年期，**不含子域** |
| Content-Security-Policy | default-src 'self'; script-src 'self' 'unsafe-inline'; ... | 暂保留 `'unsafe-inline'` 以兼容 forgot.html 与 games/*.html 的内联脚本 |

## 拦截日志

命中规则时 `block()` 经 logclient（Go 版 pymsg 客户端，见 [internal/logclient/](internal/logclient/)）异步批量推送到 db:8005 的 `Logs` 表（与其他服务的集中日志同链路；标准日志已静默，stdout/journalctl 中看不到拦截记录）：

- **规则命中拦截**（block 触发）：`service=waf`、`level=WARNING`
- **规则加载/解析错误**（`LogError` 回调触发，引擎初始化/热加载时的语义错误）：`service=waf`、`level=ERROR`

`block()` 推送的日志 `extra` 字段（JSON 字符串）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `rule_id` | int | 命中规则 ID |
| `ip` | string | `RemoteAddr` 原始值，含端口号（如 `1.2.3.4:54321`） |
| `method` | string | 请求方法 |
| `path` | string | 请求路径 |
| `query` | string | 查询字符串 |
| `ua` | string | User-Agent |
| `cost_ms` | int64 | 处理耗时（毫秒） |

fail-safe：db 不可用时日志静默丢弃，不影响拦截动作本身。

查询最近的拦截记录：

```bash
sqlite3 /opt/micro_servers/db/database.db \
  "SELECT ts, message, extra FROM Logs WHERE service='waf' AND level='WARNING' ORDER BY id DESC LIMIT 20;"
```

聚合统计命中规则：

```bash
sqlite3 /opt/micro_servers/db/database.db \
  "SELECT json_extract(extra,'\$.rule_id') AS rule, COUNT(*) FROM Logs \
   WHERE service='waf' AND level='WARNING' GROUP BY rule ORDER BY 2 DESC;"
```

查看规则加载/解析错误：

```bash
sqlite3 /opt/micro_servers/db/database.db \
  "SELECT ts, message, extra FROM Logs WHERE service='waf' AND level='ERROR' ORDER BY id DESC LIMIT 20;"
```

## 启用 OWASP CRS（可选）

通过 Go module proxy 拉取（不依赖 GitHub 直连，国内可用）：

```bash
bash _fetch_crs.sh            # 拉取最新版
# 或
bash _fetch_crs.sh v4.25.0    # 拉取指定版本
```

脚本会：

1. 通过 `GOPROXY=https://goproxy.cn,direct` 下载 `github.com/corazawaf/coraza-coreruleset/v4` 到模块缓存
2. 复制规则文件（`*.conf` + `*.data`）到 `rules/crs/rules/`
3. 自动生成 `rules/crs/coraza.conf`（含请求体处理器初始化规则 200000-200006 与 `tx.crs_setup_version`）
4. 复制 Apache-2.0 LICENSE

完成后 `rules/crs/coraza.conf` 存在，[engine.Reload](internal/engine/engine.go) 会**自动检测并加载**，无需改 `waf.conf`。直接热加载即可生效：

```bash
kill -HUP $(pgrep waf)
```

校验加载结果：

```bash
./waf.bin -list | head
```

> `rules/crs/` 整个目录已在 `.gitignore` 中，不入库。如需升级 CRS，重跑 `_fetch_crs.sh` 即可。

## 请求处理流程

[proxy.go:81-172](internal/proxy/proxy.go#L81) `ServeHTTP`：

1. `ProcessConnection` + `ProcessURI` + 添加请求头（含 Host）
2. `ProcessRequestHeaders` → 命中即 `block` 返回 403
3. 读取请求体（**10MB 硬限制**，超限返回 413）→ `ReadRequestBodyFrom` → `ProcessRequestBody` → 命中即 block
4. `httputil.ReverseProxy.ServeHTTP` 转发到 gateway
5. 捕获响应头 → `ProcessResponseHeaders`（仅审计日志，不拦截）
6. `ModifyResponse` 统一注入安全响应头（覆盖下游同名头）
7. defer 中确保 `r.Body` 耗尽并关闭，保证 TCP 连接复用

### 请求体大小限制

WAF 在代码层面对请求体实施 **10MB 硬限制**（[proxy.go#L19-L22](internal/proxy/proxy.go#L19-L22) `maxRequestBodySize`），与 `waf.conf` 中的 `SecRequestBodyLimit 10485760` 保持一致：

- 使用 `io.LimitReader` + 多读 1 字节检测超限
- 超限直接返回 `413 Request Entity Too Large`，不经过 Coraza 规则引擎
- 读取错误（客户端断开）记录日志后直接返回，不继续处理
- 从根本上防止超大请求体导致 OOM 崩溃

### HTTP 服务器超时防护

[main.go#L111-L122](main.go#L111) 为 `http.Server` 配置了多层超时，防御 Slowloris 等慢连接 DoS 攻击：

| 参数 | 值 | 作用 |
|------|-----|------|
| `ReadHeaderTimeout` | 10s | 读取请求头超时，**直接防御 Slowloris** |
| `ReadTimeout` | 15s | 读取整个请求（含 body）超时，防慢 POST |
| `WriteTimeout` | 30s | 写入响应超时 |
| `IdleTimeout` | 120s | keep-alive 空闲连接超时 |
| `MaxHeaderBytes` | 1MB | 请求头大小限制 |

Director 改写（[proxy.go:36-46](internal/proxy/proxy.go#L36)）：剥离 `RemoteAddr` 端口，设置 `X-Forwarded-For` 与 `X-Forwarded-Proto: https`，供 gateway 的 RateLimiter 取真实客户端 IP。

ErrorHandler（[proxy.go:48-53](internal/proxy/proxy.go#L48)）：upstream 不可达时返回 502，同时注入安全响应头，不暴露内部堆栈，仅服务端 log。

## WebSocket 代理支持

WAF 支持透明代理 WebSocket 连接（如 aibox 的 WebSocket 接口）。实现要点：

- `responseRecorder` 实现了 `http.Hijacker` 接口，将 Hijack 调用委托给底层 `http.ResponseWriter`
- WebSocket 握手阶段（HTTP Upgrade 请求/响应）仍然经过 WAF 规则检测（请求头 phase:1）
- 握手成功（101 Switching Protocols）后，连接被 Hijack，由 `httputil.ReverseProxy` 自动进行双向字节流转发
- 升级后连接转为二进制帧协议，跳过 HTTP 响应阶段的 WAF 检测，记录 Info 日志
- WebSocket 帧内容不做 WAF 检测（帧是二进制双向流，Coraza 规则引擎基于 HTTP 请求/响应模型）
- `http.Server` 的超时设置不影响 Hijack 后的 WebSocket 长连接（Hijack 后连接脱离 Server 超时管理）
- 安全响应头（CSP/HSTS 等）不注入 WebSocket 升级响应（101 响应由协议协商决定，注入非标准头可能导致客户端兼容问题）

配置要求：
- 无特殊配置，WebSocket 连接（`Upgrade: websocket`）自动识别并代理
- 需要上游服务正确处理 WebSocket 升级（如 aibox 监听 ws:// 端口）

## 已知限制

- 响应头检测（`ProcessResponseHeaders`）命中后仅记录 WARNING 告警日志，不执行拦截（响应头已发送无法撤销状态码）。如需响应头拦截需引入 buffered response writer。
- **响应体检测未启用**（未 `ProcessResponseBody`，缓冲整个响应延迟成本高）。SQL 报错回显等响应体泄漏检测缺失。
- **WebSocket 帧内容不检测**：升级后二进制帧直接转发，不经过 WAF 规则引擎（适用于内部可信服务如 aibox）。
- 限流/黑白名单未实现，可后续作为 coraza 规则或独立中间件加。
- TLS 仅设 `MinVersion: TLS1.2`，未限定 cipher suites，允许弱加密套件。
- `X-Forwarded-For` 直接覆盖原值（`Header.Set`），未按 RFC 7239 追加；当前 gateway 也只取第一个 IP，无实际影响，但语义不严格。
- CSP 仍含 `'unsafe-inline'`（为兼容 forgot.html 与 games/*.html 的内联脚本），待前端清理后可收紧。
- HSTS 已启用（半年期，不含子域），站点全量 HTTPS 覆盖后可按需收紧。
- logclient 日志推送每次新建 TCP 短连接，高并发拦截场景可能产生较多 TIME_WAIT（已支持通过环境变量 `LOG_DB_ADDR` 配置 db 地址）。
- **默认规则全部注释**：`rules/local/custom.conf` 中自定义规则默认注释，需手动取消注释或拉取 OWASP CRS 后才有实际拦截效果。启动时无 warning 提示。**注意：CRS 规则目录不入库，需先在部署机执行 `_fetch_crs.sh`；未拉取 CRS 时 WAF 开箱无拦截能力。**

## 版本日志

### v1.4（2026-08-08）— P1/P2 安全修复

- **fix(P1-1)**: IPv6 地址解析改用 `net.SplitHostPort`，修复 `[::1]:54321` 等 IPv6 地址在 X-Forwarded-For 中带方括号导致 gateway 限流失效
- **fix(P1-2)**: `ProcessResponseHeaders` 返回值不再被丢弃，命中规则时记录 WARNING 告警日志（响应头已发送无法拦截状态码）
- **fix(P1-4)**: 增加 SIGINT/SIGTERM 优雅关闭，`server.Shutdown` 等待进行中请求完成（10s 宽限期），退出前 `logclient.Flush()` 刷新队列日志
- **fix(P1-5)**: 标准日志输出到 stderr（`log.SetOutput(os.Stderr)`），保留 net/http 的 panic 堆栈与 TLS 错误，便于 journalctl 排查
- **fix(P2-1)**: 规则文件加载前校验大小（上限 1 MiB），防止超大 `.conf` 文件导致 OOM
- **fix(P2-2)**: logclient db 地址支持环境变量 `LOG_DB_ADDR`（默认 `127.0.0.1:8005`）
- **fix(P2-5)**: logclient 日志失败输出到 stderr 而非 stdout
- 修复率从 35% 提升至 76%（P1 全部修复、P2 全部修复）

### v1.3（2026-08-04）— 启用 OWASP CRS 异常评分制自动拦截

- **feat**: 启用 OWASP CRS v4.25.0 异常评分制，`SecRuleEngine On` 自动拦截评分达阈值的请求（规则 949110）
- **feat**: 新增 `rules/local/tuning.conf` CRS 调优文件，可配置偏执等级（默认 1）与异常评分阈值（inbound 5 / outbound 4）
- **feat**: `tuning.conf` 独立于自动生成的 `crs/coraza.conf`，重跑 `_fetch_crs.sh` 不会覆盖调优配置
- 校验：SQLi / XSS / 路径遍历 / 命令注入均命中 949110 拦截，正常请求放行

> **⚠️ 生效前提**：CRS 规则目录 `rules/crs/` 被 `.gitignore` 排除，不入库。**必须先在某台机器上执行 `bash _fetch_crs.sh` 拉取 CRS 规则**，`crs/coraza.conf` 存在后 engine 才会自动加载并启用自动拦截。若未拉取 CRS，实际生效的仅有 `custom.conf`（默认全注释）+ `tuning.conf`（仅 setvar），**WAF 开箱无拦截能力**。部署时请务必先拉取 CRS 或启用 `custom.conf` 中的兜底规则。

### v1.2（2026-08-01）— WebSocket 支持 + 修复

- **feat**: WebSocket 透明代理支持，`responseRecorder` 实现 `http.Hijacker` 接口
- WebSocket 握手阶段仍经过 WAF 请求头检测，升级后自动双向转发
- WebSocket 升级成功记录 Info 日志，便于追踪
- **fix(P2-3)**: `ProcessResponseHeaders` 协议版本从硬编码 `"HTTP/1.1"` 改为 `r.Proto`，支持 HTTP/2
- **fix(P2-4)**: responseRecorder 添加 Write 方法确保 WriteHeader 在首次 Write 前调用
- 补充 `bufio`/`net` import 用于 Hijacker 实现

### v1.1（2026-08-01）— 安全加固

- **P0 修复：请求体 OOM 防护**：添加 10MB 硬限制，`io.LimitReader` + 多读1字节检测超限，超限返回 413
- **P0 修复：Slowloris DoS 防护**：`http.Server` 添加 ReadHeaderTimeout(10s)/ReadTimeout(15s)/WriteTimeout(30s)/IdleTimeout(120s)/MaxHeaderBytes(1MB)
- **P1 修复：请求体读取错误处理**：`io.ReadAll` 失败时记录日志并返回，不再传递出错 body 给 ReverseProxy；`ReadRequestBodyFrom`/`ProcessRequestBody` 错误均记录日志
- **P2 修复：r.Body 连接复用**：defer 中耗尽并关闭 r.Body，确保 TCP 连接可复用
- 413 响应同样注入安全响应头

### v1.0

- 初始版本：TLS 终止 + Coraza WAF 规则匹配 + 反向代理 + SIGHUP 热加载 + 异步日志推送 + 安全响应头注入

## 依赖

- Go 1.22+（见 [go.mod](go.mod)，锁 `go 1.22.0`）
- `github.com/corazawaf/coraza/v3` **v3.3.0**（锁版本，避免 v3.7.0 要求 Go 1.25+）
- `gopkg.in/yaml.v3` v3.0.1

## 许可证

MIT License
