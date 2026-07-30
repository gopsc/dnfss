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
| 临时禁用某文件 | `mv rules/local/x.conf x.conf.bak && kill -HUP $(pgrep waf)` |

### 规则文件组织

```
rules/
├── waf.conf              # 主入口：基础配置（SecRuleEngine / SecRequestBodyLimit 等）
├── local/                # 本地自定义规则（ID 段 100000-199999）
│   ├── custom.conf       # 示例：SQL注入/XSS/路径遍历/命令注入/扫描器UA
│   └── exceptions.conf   # 误报白名单（SecRuleRemoveById / ctl:ruleRemoveById）
└── crs/                  # OWASP CRS（不入库，由 _fetch_crs.sh 拉取）
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
| 200000-200006 | CRS 请求体处理器初始化（XML/JSON，由 `crs/coraza.conf` 定义） |
| 900000-999999 | OWASP CRS 规则（由 `crs/rules/` 提供，不手改） |

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
| `upstream` | — | gateway 地址，**必填** |
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
│   └── proxy/
│       └── proxy.go         # ReverseProxy + WAF 事务接入
├── rules/
│   ├── waf.conf
│   └── local/
│       ├── custom.conf
│       └── exceptions.conf
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

## 拦截日志

命中规则时 `block()` 经 logclient（Go 版 pymsg 客户端，见 [internal/logclient/](internal/logclient/)）异步批量推送到 db:8005 的 `Logs` 表（与其他服务的集中日志同链路；标准日志已静默，stdout/journalctl 中看不到拦截记录）：

- `service=waf`、`level=WARNING`
- `message`：`block rule_id=100001 GET /path`
- `extra`（JSON 字符串）：`rule_id` / `ip` / `method` / `path` / `query` / `ua` / `cost_ms`

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

[proxy.go:57-117](internal/proxy/proxy.go#L57) `ServeHTTP`：

1. `ProcessConnection` + `ProcessURI` + 添加请求头（含 Host）
2. `ProcessRequestHeaders` → 命中即 `block` 返回 403
3. 读全量请求体 → `ReadRequestBodyFrom` → `ProcessRequestBody` → 命中即 block
4. `httputil.ReverseProxy.ServeHTTP` 转发到 gateway
5. 捕获响应头 → `ProcessResponseHeaders`

Director 改写（[proxy.go:36-46](internal/proxy/proxy.go#L36)）：剥离 `RemoteAddr` 端口，设置 `X-Forwarded-For` 与 `X-Forwarded-Proto: https`，供 gateway 的 RateLimiter 取真实客户端 IP。

ErrorHandler（[proxy.go:48-51](internal/proxy/proxy.go#L48)）：upstream 不可达时返回 502，不暴露内部堆栈，仅服务端 log。

## 已知限制

- 请求体全量读入内存检测，10MB 上限（`SecRequestBodyLimit`）。大文件上传场景需调大或加白名单。
- **响应体检测未启用**：仅 `ProcessResponseHeaders`，未 `ProcessResponseBody`（缓冲整个响应延迟成本高）。SQL 报错回显等响应体泄漏检测缺失。
- 限流/黑白名单未实现，可后续作为 coraza 规则或独立中间件加。
- `http.Server` 未设 `ReadTimeout` / `WriteTimeout` / `IdleTimeout`，存在慢连接攻击风险。
- TLS 仅设 `MinVersion: TLS1.2`，未限定 cipher suites，允许弱加密套件。
- `X-Forwarded-For` 直接覆盖原值，未按 RFC 7239 追加；当前 gateway 也只取第一个 IP，无实际影响，但语义不严格。

## 依赖

- Go 1.22+（见 [go.mod](go.mod)，锁 `go 1.22.0`）
- `github.com/corazawaf/coraza/v3` **v3.3.0**（锁版本，避免 v3.7.0 要求 Go 1.25+）
- `gopkg.in/yaml.v3` v3.0.1

## 许可证

MIT License
