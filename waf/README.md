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
├── waf.conf              # 主入口，Include 各子文件
├── local/                # 本地自定义规则（ID 段 100000-199999）
│   ├── custom.conf       # 示例：SQL注入/XSS/路径遍历/命令注入/扫描器UA
│   └── exceptions.conf   # 误报白名单（SecRuleRemoveById / ctl:ruleRemoveById）
└── crs/                  # OWASP CRS（git clone，不入库，ID 段 900000-999999）
```

### 规则 ID 分段

- `100000-199999` 本地自定义
- `900000-999999` OWASP CRS

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
upstream: "http://127.0.0.1:8443"
rules_dir: "./rules"
```

## 目录结构

```
waf/
├── main.go                  # 入口：服务模式 + CLI 子命令
├── config.yaml              # 配置
├── go.mod
├── internal/
│   ├── engine/
│   │   └── engine.go        # coraza 封装：加载/热加载/列举/单测
│   └── proxy/
│       └── proxy.go         # ReverseProxy + WAF 事务接入
├── rules/
│   ├── waf.conf
│   └── local/
│       ├── custom.conf
│       └── exceptions.conf
├── _set.sh                  # 编译
├── _run.sh                  # 运行
├── .gitignore
└── README.md
```

## 安装

```bash
# 1. 安装 Go 1.21+
brew install go    # macOS
# 或 sudo apt install golang-go  # Debian/Ubuntu

# 2. 编译
bash _set.sh

# 3. 准备证书
mkdir certs && cp /path/to/full_chain.crt /path/to/server.key certs/

# 4. 运行
bash _run.sh
```

## 接入 core 自启动

在 `core/init.txt` 增加：

```
bash /opt/micro_servers/waf/_run.sh
```

并把 `gateway` 改为内部 HTTP 端口（不再监听 443）。

## 命中日志

命中规则时输出 JSON 一行，便于 `jq` 分析：

```json
{"ts":"2026-07-25T10:00:00Z","action":"block","rule_id":100001,"ip":"1.2.3.4","method":"GET","path":"/","query":"id=1'+OR+'1'='1","ua":"curl/8.0","cost_ms":0}
```

聚合统计：

```bash
journalctl -u Nidus -o cat | grep '"action":"block"' | \
  jq -r '.rule_id' | sort | uniq -c | sort -rn
```

## 启用 OWASP CRS（可选）

```bash
cd rules
git clone --depth 1 https://github.com/coreruleset/coreruleset.git crs
```

在 `rules/waf.conf` 取消注释：

```apache
Include crs/coraza.conf
Include crs/rules/*.conf
```

然后 `kill -HUP $(pgrep waf)`。

## 已知限制

- 请求体全量读入内存检测，10MB 上限（`SecRequestBodyLimit`）。大文件上传场景需调大或加白名单。
- 响应体检测未启用（需缓冲整个响应，延迟成本高）。
- 限流/黑白名单未实现，可后续作为 coraza 规则或独立中间件加。

## 依赖

- Go 1.21+
- github.com/corazawaf/coraza/v3 v3.7.0
- gopkg.in/yaml.v3

## 许可证

MIT License
