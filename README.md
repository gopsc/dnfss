# micro_servers

一套 DIY 的轻量级微服务架构（UniServe Framework），由 C++、Python 与 Go 混合实现。
`core` 作为进程托管服务负责拉起并守护各业务子进程，`waf` 作为前置 WAF 网关终止 TLS 并做规则匹配，`gateway` 作为应用层网关统一鉴权与反向代理，`db` 提供通用数据库访问与集中式日志收集，`ai_review` 提供 AI 内容审核，`static` 与 `dnfs` 作为业务服务对外提供能力。

## 架构总览

```
                         ┌──────────────────────────────────────────────┐
                         │              core (PMC / Nidus)              │
                         │   进程托管服务 · 监听 :5200 (HTTP 管理接口)    │
                         │   读取 init.txt 拉起并守护下列子进程          │
                         └───────────────┬──────────────────────────────┘
                                         │ 启动 / 守护
          ┌──────────────────┬───────────┼───────────┬──────────────────┬──────────────┐
          ▼                  ▼           ▼           ▼                  ▼              ▼
 ┌──────────────┐  ┌──────────────┐  ┌────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
 │     waf      │  │   gateway    │  │ static │  │     dnfs     │  │      db      │  │  ai_review   │
 │  WAF 网关    │  │  应用层网关  │  │前端托管│  │  树状菜单    │  │ 通用数据库   │  │ AI内容审核   │
 │ :443 (入口)  │  │ :8443 (内部) │  │ :9200  │  │    :8006     │  │    :8005     │  │    :8007     │
 │ coraza+反代  │  │ 鉴权 + 反代  │  │静态/媒体│  │  节点CRUD    │  │ SQL+日志收集 │  │ DeepSeek+RAG │
 └──────┬───────┘  └──────┬───────┘  └────────┘  └──────┬───────┘  └──────────────┘  └──────────────┘
        │ HTTPS终止         │ 反向代理                │ 调用
        └──────────────────►│                         ├──────────► db:8005
                            ├────────────────► static:9200
                            └────────────────► dnfs:8006 ──► ai_review:8007

 日志链路: waf / gateway / static / dnfs / ai_review / captcha / smtp / aibox ──(log_client)──► db:8005/log/ingest
```

### 请求链路

1. 客户端所有请求先打到 `waf:443`（HTTPS），WAF 终止 TLS、运行 coraza 规则匹配，命中则直接返回 403。
2. 未命中规则的请求由 WAF 以明文 HTTP 转发到 `gateway:8443`，并设置 `X-Forwarded-For` / `X-Forwarded-Proto` 头。
3. `gateway` 对 `/login`、`/register` 做账号鉴权（SQLite + PBKDF2 密码哈希 + RSA 签名 Token + 可选 CAPTCHA + 速率限制）。
4. 其余请求携带 Token，经 `gateway` 校验后反向代理到 `Proxy.opened` 中放开的内部服务（`static:9200` / `dnfs:8006`）。
5. `db:8005` 与 `ai_review:8007` 在 `Proxy.closed` 中，仅允许内部服务间调用，不对外暴露。
6. `dnfs` 处理节点增删改查时，通过 HTTP 调用 `db` 服务完成数据持久化，新增节点前调用 `ai_review` 进行内容审核。
7. 各服务通过 `log_client.py` SDK 异步批量推送运行日志到 `db:8005/log/ingest`，实现集中式日志收集（fail-safe，db 不可用时静默丢弃）。

## 各服务说明

| 服务 | 语言 | 端口 | 职责 |
|------|------|------|------|
| [core](core/) | C++20 | 5200 | 进程托管服务（PMC），创建/列举/杀死/执行子进程，读取自启动列表守护业务进程，提供 HTTP 管理接口 |
| [waf](waf/) | Go | 443 | WAF 网关（入口），终止 TLS、coraza 规则匹配（SecLang）、反向代理到 gateway，支持 SIGHUP 热加载规则 |
| [gateway](gateway/) | C++20 | 8443 | 应用层网关（内部 HTTP），用户注册/登录鉴权、Token 签发与校验、反向代理到内部服务 |
| [static](static/) | Python(Flask) | 9200 | 前端托管服务，社区首页、静态页面与文件浏览、视频/音频流媒体播放、路径遍历防护 |
| [dnfs](dnfs/) | Python(Flask) | 8006 | 树状网络文件系统（Dendrachy Network File-System），基于父子 pid 的节点树增删改查，集成 AI 内容审核 |
| [db](db/) | Python(Flask) | 8005 | 通用数据库服务，基于 SQLAlchemy Core 的 SQL-over-HTTP，支持 select/insert/update/delete，参数化查询防注入；内置集中式日志收集（/log/* 路由） |
| [ai_review](ai_review/) | Python(Flask) | 8007 | AI 内容审核服务，DeepSeek LLM + ChromaDB RAG，支持人工反馈纠偏 |
| [captcha](captcha/) | Python(Flask) | 8009 | 验证码服务，生成图形验证码（5 分钟过期、一次性），供 gateway 登录/注册可选启用 CAPTCHA 校验 |
| [smtp](smtp/) | Python(Flask) | 8008 | 邮件服务，发送/校验邮箱验证码（/send_email、/verify_email），注册流程的邮件验证码后端，SMTP 账号配置在 .env |
| [aibox](aibox/) | Python | 8765 | 独立 AI 智能体系统（DeepSeek + WebSocket），自带技能系统/记忆/任务调度，由 core 拉起但与主干微服务无调用关系 |

### core（核心模块 · 进程托管服务）

- 可执行文件 `nidus.out`，作为 systemd 服务（`Nidus.service`）常驻运行。
- 支持两种工作模式：命令行子系统调用（`--send/--exec/--list/--kill`）与 HTTP 伺服器模式（`--serve <port>`）。
- 通过命名管道（Pipe）与消息队列（Mq）实现进程间通信。
- 读取 `init.txt` 自启动列表，按行拉起各业务服务（ai_review / db / dnfs / smtp / static / waf / gateway / captcha / aibox）。

### waf（WAF 网关）

- 唯一对外入口，终止 TLS（`certs/full_chain.crt` / `certs/server.key`），监听 443 端口。
- 基于 [coraza](https://github.com/corazawaf/coraza) v3.3.0（ModSecurity SecLang 的 Go 实现），规则匹配命中后返回 403。
- 未命中规则的请求以明文 HTTP 反向代理到 `gateway:8443`，透传 `X-Forwarded-For` / `X-Forwarded-Proto`。
- 规则库位于 [waf/rules/](waf/rules/)，分两段：本地自定义（ID 100000-199999）与 OWASP CRS（ID 900000-999999，可选）。
- 极客操作：`kill -HUP <pid>` 热加载规则；`waf -list` 列规则；`waf -test <file>` 校验语法；`waf -check "<input>"` 测试单条输入。
- 配置见 [waf/config.yaml](waf/config.yaml)，详见 [waf/README.md](waf/README.md)。

### gateway（应用层网关 · v0.3.0-security）

- 内部 HTTP 服务，默认监听 127.0.0.1:8443，不再承载 TLS（TLS 已由 WAF 终止）。
- 通过 `Server.tls = on/off` 配置切换 HTTPS/HTTP 模式（[example.ini](gateway/example.ini)），独立部署时仍可启用 TLS。
- **密码安全**：PBKDF2-HMAC-SHA256（10 万轮 + 16 字节随机盐），密码强度校验（8+ 字符、4 类中至少 3 类），登录时自动迁移旧 SHA256 哈希，常数时间比较防 timing attack。
- **Token 机制**：`Base64(payload) + "_" + RSA-Sign(payload)`，默认 1 小时过期，验签通过才放行。
- **CAPTCHA**：`/captcha` 接口生成验证码（5 分钟过期、一次性使用），登录与注册均可选启用。
- **速率限制**：登录 5 次/5 分钟、注册 3 次/10 分钟，按 `X-Forwarded-For`（WAF 注入）客户端 IP 计数。
- **反向代理**：`Target-Url` 必须在 `Proxy.opened` 白名单中（`closed` 仅记录用途，实际隔离靠"不在 opened 中即拒绝"），缺省走 `Proxy.default`（不需 Token，服务前端静态资源）。
- **自动建表**：启动时若 `Users` 表不存在则自动创建，首位注册者即为管理员。
- 详见 [gateway/README.md](gateway/README.md)。

### static（前端托管服务）

- 托管 `public/` 下的前端页面：社区首页（`index.html`）、登录/注册、树状笔记（`dnfs.html`）、文件浏览（`__view.html`）等。
- 支持文件浏览、视频/音频在线播放（HTTP Range 分段请求）、一键下载。
- 路径遍历防护，仅允许访问 `directory.root` 之内。
- 统一品牌"树状菜单"，支持深色模式（`css/theme.css`）。
- 可使用 Nuitka 编译为独立二进制（`fsys.bin`）。
- 配置见 [static/config.ini](static/config.ini)。

### dnfs（树状菜单服务）

- 基于 `pid`（父节点 id）组织的树状节点，节点含 `title` 与 `content`。
- 提供 `/get_notes`、`/add_note`、`/delete_note`、`/update_note` 四个接口。
- 数据持久化通过 HTTP 调用 `db` 服务（`http://127.0.0.1:8005/execute`），不再直接操作数据库。
- 新增节点前调用 `ai_review` 服务（`http://127.0.0.1:8007/review`）进行内容审核，审核不通过则拒绝写入。

### db（通用数据库服务）

- 单一接口 `POST /execute`，接收结构化 JSON（action + table + where + values），翻译为 SQL 执行。
- 支持 select / insert / update / delete，运算符包括 eq、ne、gt、lt、ge、le、like、in，支持排序与分页。
- 基于 SQLAlchemy Core 表反射，参数化查询，零 SQL 注入风险。
- 仅监听 127.0.0.1，单线程，自动回滚，不暴露 DDL 操作。
- 默认使用 SQLite（`database.db`），可通过 `DATABASE_URL` 环境变量切换至 PostgreSQL / MySQL。
- **内置日志子模块**（[log_module.py](db/log_module.py)）：
  - `log_internal()` — db 进程内部直接写 Logs 表，无 HTTP 往返，防递归设计。
  - `/log/ingest` — 接收外部服务批量推送日志（单次上限 100 条，校验 service / level / message）。
  - `/log/query` — 结构化查询（service / level / trace_id / 时间区间 / 关键字模糊搜索 / 排序分页）。
  - `/log/stats` — 按 service / level 分组统计。
  - `/log/purge` — 手动清理指定时间之前的日志。
  - `/log/health` — 健康检查。
  - 后台守护线程每小时自动清理：保留 30 天 / 上限 100 万条。
- **统一日志客户端 SDK**（[log_client.py](db/log_client.py)）：各服务复制即用，纯标准库实现（urllib），异步非阻塞（内存队列 + 后台线程，50 条/2 秒批量 flush），fail-safe 设计（db 不可用时静默丢弃，不影响业务）。

### ai_review（AI 内容审核服务）

- 基于 DeepSeek LLM + ChromaDB 向量检索的 RAG 审核架构。
- 审核类别：色情、违禁药品、赌博、诈骗、仇恨/暴力/恐怖主义，灰色地带内容从严判定。
- 接口：`POST /review`（提交审核）、`POST /rag/feedback`（人工纠偏）、`GET /rag/list`（知识库列表）、`GET /health`（健康检查）。
- RAG 检索 top-3 相似已标注样本注入 prompt，MD5 去重，API 异常时 fail-open。
- 配置通过 `.env` 文件（已 gitignore），含 DeepSeek API 密钥、模型参数、监听地址。

### captcha（验证码服务）

- 生成图形验证码，Token 一次性使用、5 分钟过期。
- 接口：`GET /captcha`（生成）、`POST /verify`（校验）、`POST /new`（刷新）；答案错误返回 `200 {valid:false}`（Token 照常消费），仅参数 / Token 错误返回 `400`。
- 由 `gateway` 在 `/login`、`/register` 中可选启用 CAPTCHA 校验（字段 `captcha_token` / `captcha_answer`）。
- 内存存储，不支持多实例水平扩展（需换 Redis）；详见 [captcha/README.md](captcha/README.md)。

### smtp（邮件服务）

- 注册流程的邮箱验证码后端，基于 Flask + waitress，监听 127.0.0.1:8008。
- 接口：`POST /send_email`（生成 6 位验证码并经 SMTP_SSL 发送至收件人，60 秒有效）、`POST /verify_email`（校验）。
- 验证码存于进程内存，SMTP 账号通过 `.env` 配置（`SMTP_SERVER` / `SMTP_PORT` / `SMTP_USER` / `__PASSWORD`）。

### aibox（独立 AI 智能体系统）

- 一套相对独立的单智能体产品，基于 DeepSeek + WebSocket（:8765），由 `core` 拉起但与 waf / gateway / dnfs 等主干微服务无调用关系。
- 自带技能系统（`skills/`）、任务调度器、记忆管理、前端页面与语音客户端（`voice_client.py`）。
- 默认模型 `deepseek-v4-flash`，配置在 `config.json`；详见 [aibox/README.md](aibox/README.md)。

## 共享基础库

| 库 | 说明 |
|----|------|
| [libth](libth/) | 线程库：`Thread`、`Cv_wait`（条件变量等待）、`Fsm`、`ITask` 任务接口 |
| [libhttp](libhttp/) | HTTP/WebSocket 库：`HttpServer`、`HttpClient`、`WebSocketServer/Client`（基于 Boost.Beast） |
| [libcrypt](libcrypt/) | 加密库：`AES_CBC`、`MyRSA`、`SHA256`、`Base64`、`Crypto_Basic` |
| [libsqlite3](libsqlite3/) | SQLite3 C++ 封装（Header-only） |

### Python 共享组件

| 文件 | 说明 |
|------|------|
| [log_client.py](db/log_client.py) | 统一日志客户端 SDK，各服务复制即用（纯标准库，异步批量推送） |
| [security_middleware.py](db/security_middleware.py) | Flask 安全响应头中间件（CSP / X-Frame-Options / nosniff 等），各服务各持一份副本 |

## 端口一览

| 端口 | 服务 | 绑定地址 | 说明 |
|------|------|----------|------|
| 443 | waf | 0.0.0.0 | HTTPS 对外入口（终止 TLS + coraza 规则匹配） |
| 8443 | gateway | 127.0.0.1 | 应用层网关（内部 HTTP，由 waf 转发） |
| 5200 | core | — | PMC HTTP 管理接口 |
| 9200 | static | 127.0.0.1 | 前端托管（经 gateway 代理） |
| 8006 | dnfs | 127.0.0.1 | 树状菜单（经 gateway 代理） |
| 8005 | db | 127.0.0.1 | 数据库服务（仅内部调用） |
| 8007 | ai_review | 127.0.0.1 | AI 审核（仅内部调用） |
| 8009 | captcha | 127.0.0.1 | 验证码服务（独立组件） |
| 8008 | smtp | 127.0.0.1 | 邮件服务（仅内部调用，注册验证码邮件后端） |
| 8765 | aibox | 127.0.0.1 | AI 智能体（WebSocket，独立产品） |

## 依赖

- **系统级**：`g++`（C++20）、`make`、`libboost-all-dev`、`libcurl4-gnutls-dev`、`libssl-dev`、`libsqlite3-dev`、`python3`、`python3-venv`、`python3-pip`、`patchelf`、`golang-go`（Go 1.22+，用于 waf）
- **Python**：Flask、Flask-CORS、Flask-SQLAlchemy、SQLAlchemy、waitress、openai、chromadb、python-dotenv、nuitka（详见各服务 `requirements.txt`）
- **Go**：corazawaf/coraza v3.3.0、gopkg.in/yaml.v3（详见 [waf/go.mod](waf/go.mod)）

## 安装

根目录提供一键安装脚本，按依赖顺序依次构建并安装各模块（库先行，core 最后）：

```bash
# 1. 安装系统依赖
bash _setup.deb.sh        # Debian/Ubuntu
# 或
bash _setup.rhel.sh       # RHEL/CentOS/Rocky/Fedora

# 2. 依次安装各模块（libth -> libhttp -> libcrypt -> libsqlite3 -> gateway -> waf -> static -> dnfs -> db -> core）
bash _inst.sh
```

单独安装某一服务时，进入对应目录执行：

```bash
bash _set.sh   # 构建 + 安装
bash _run.sh   # 运行
```

部署路径约定为 `/opt/micro_servers/`，`core` 的 `init.txt` 即从该路径拉起其余服务。

> **注意**：`ai_review` 未包含在 `_inst.sh` 中，需单独进入 `ai_review/` 目录执行 `_set.sh` 安装，并配置 `.env` 文件（DeepSeek API 密钥等）。

> **注意**：waf 首次编译需联网下载 Go 依赖，[waf/_set.sh](waf/_set.sh) 会自动调用 [waf/_goproxy.sh](waf/_goproxy.sh) 设置 GOPROXY 国内镜像。如需手动设置：`go env -w GOPROXY=https://goproxy.cn,direct`。

## 运行

由 `core` 守护的子进程通过 `init.txt` 自启动（顺序很重要，waf 必须先于 gateway）：

```
bash /opt/micro_servers/ai_review/_run.sh
bash /opt/micro_servers/db/_run.sh
bash /opt/micro_servers/dnfs/_run.sh
bash /opt/micro_servers/smtp/_run.sh
bash /opt/micro_servers/static/_run.sh
bash /opt/micro_servers/waf/_run.sh
bash /opt/micro_servers/gateway/_run.sh
bash /opt/micro_servers/captcha/_run.sh
bash /opt/micro_servers/aibox/_run.sh
```

启动 `core`（需 root 或 systemd）：

```bash
systemctl start Nidus      # 或 bash core/_run.sh
```

对外访问入口：`https://<host>:443`（由 waf 终止 TLS 后转发到 gateway）。

### WAF 与 gateway 的协作

WAF 启用后，证书从 gateway 迁移到 waf：

```bash
mkdir -p /opt/micro_servers/waf/certs
cp /opt/micro_servers/gateway/full_chain.crt /opt/micro_servers/gateway/server.key /opt/micro_servers/waf/certs/
```

gateway 改为内部 HTTP 模式（[example.ini](gateway/example.ini) 中 `tls = off`、`address = 127.0.0.1`、`port = 8443`）。

如需独立部署（不使用 WAF），把 gateway 的 `tls = on`、`port = 443`、`address = 0.0.0.0`，证书留在 gateway 目录即可。

## 关于架构定位

本项目具备微服务架构的核心特征，是一种**进程托管式轻量微服务**：

- **服务按业务能力拆分**：WAF 网关、应用网关、前端托管、节点树、数据库、内容审核各自独立、独立端口、独立数据存储。
- **API 网关模式**：`waf` 作为边缘网关统一入口，`gateway` 承担鉴权与反向代理，内部服务通过 `closed` 列表隔离。
- **进程编排/守护**：`core` 作为服务管理器拉起并守护子进程，类似简易版 supervisor。
- **多语言异构**：C++（core / gateway）、Python（static / dnfs / db / ai_review）、Go（waf）混合，通过 HTTP 通信。
- **数据访问层解耦**：`db` 服务将数据库操作收敛为统一的 HTTP 接口，业务服务无需直连数据库。
- **AI 能力集成**：`ai_review` 以 RAG 增强的 LLM 审核为业务层提供内容安全能力，支持人工反馈闭环。
- **集中式可观测性**：`db` 内置日志收集服务（/log/* 路由），各服务通过 `log_client.py` SDK 异步推送日志，实现全链路汇聚、查询与统计，无需引入额外中间件。
- **安全分层**：`waf` 在边缘做规则匹配（OWASP CRS / 自定义 SecLang 规则），`gateway` 在应用层做鉴权，`db` 仅监听本机，多层防护。

与云原生微服务的差异：未引入服务注册发现、容器编排、消息总线，服务地址在 `waf/config.yaml` 与 `gateway/example.ini` 中静态配置。对于个人/中小规模的 DIY 场景，这种取舍在简洁性与可控性上是合理的。

## 许可证

各模块均采用 MIT License，详见各目录下的 `LICENSE`。
