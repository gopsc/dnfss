# micro_servers

一套 DIY 的轻量级微服务架构（UniServe Framework），由 C++ 与 Python 混合实现。
`core` 作为进程托管服务负责拉起并守护各业务子进程，`gateway` 作为应用层网关统一鉴权与反向代理，`db` 提供通用数据库访问，`ai_review` 提供 AI 内容审核，`static` 与 `dnfs` 作为业务服务对外提供能力。

## 架构总览

```
                         ┌──────────────────────────────────────────────┐
                         │              core (PMC / Nidus)              │
                         │   进程托管服务 · 监听 :5200 (HTTP 管理接口)    │
                         │   读取 init.txt 拉起并守护下列子进程          │
                         └───────────────┬──────────────────────────────┘
                                         │ 启动 / 守护
          ┌──────────────────┬───────────┼───────────┬──────────────────┐
          ▼                  ▼           ▼           ▼                  ▼
 ┌──────────────┐  ┌──────────────┐  ┌────────┐  ┌──────────────┐  ┌──────────────┐
 │   gateway    │  │    static    │  │  dnfs  │  │      db      │  │  ai_review   │
 │  应用层网关  │  │ 前端托管服务 │  │树状菜单│  │ 通用数据库   │  │ AI内容审核   │
 │  :443 (入口) │  │    :9200     │  │ :8006  │  │    :8005     │  │    :8007     │
 │ 鉴权 + 反代  │  │ 静态/媒体    │  │节点CRUD│  │ SQL-over-HTTP│  │ DeepSeek+RAG │
 └──────┬───────┘  └──────────────┘  └───┬────┘  └──────────────┘  └──────────────┘
        │ 反向代理                        │ 调用
        ├────────────────► static:9200    ├──────────► db:8005
        └────────────────► dnfs:8006      └──────────► ai_review:8007
```

### 请求链路

1. 客户端所有请求统一打到 `gateway:443`（HTTPS）。
2. `gateway` 对 `/login`、`/register` 做账号鉴权（SQLite + SHA256 + RSA 签名 Token）。
3. 其余请求携带 Token，经 `gateway` 校验后反向代理到 `Proxy.opened` 中放开的内部服务（`static:9200` / `dnfs:8006`）。
4. `db:8005` 与 `ai_review:8007` 在 `Proxy.closed` 中，仅允许内部服务间调用，不对外暴露。
5. `dnfs` 处理节点增删改查时，通过 HTTP 调用 `db` 服务完成数据持久化，新增节点前调用 `ai_review` 进行内容审核。

## 各服务说明

| 服务 | 语言 | 端口 | 职责 |
|------|------|------|------|
| [core](core/) | C++20 | 5200 | 进程托管服务（PMC），创建/列举/杀死/执行子进程，读取自启动列表守护业务进程，提供 HTTP 管理接口 |
| [gateway](gateway/) | C++20 | 443 | 应用层网关（入口），用户注册/登录鉴权、Token 签发与校验、反向代理到内部服务 |
| [static](static/) | Python(Flask) | 9200 | 前端托管服务，社区首页、静态页面与文件浏览、视频/音频流媒体播放、路径遍历防护 |
| [dnfs](dnfs/) | Python(Flask) | 8006 | 树状网络文件系统（Dendrachy Network File-System），基于父子 pid 的节点树增删改查，集成 AI 内容审核 |
| [db](db/) | Python(Flask) | 8005 | 通用数据库服务，基于 SQLAlchemy Core 的 SQL-over-HTTP，支持 select/insert/update/delete，参数化查询防注入 |
| [ai_review](ai_review/) | Python(Flask) | 8007 | AI 内容审核服务，DeepSeek LLM + ChromaDB RAG，支持人工反馈纠偏 |

### core（核心模块 · 进程托管服务）

- 可执行文件 `nidus.out`，作为 systemd 服务（`Nidus.service`）常驻运行。
- 支持两种工作模式：命令行子系统调用（`--send/--exec/--list/--kill`）与 HTTP 伺服器模式（`--serve <port>`）。
- 通过命名管道（Pipe）与消息队列（Mq）实现进程间通信。
- 读取 `init.txt` 自启动列表，按行拉起各业务服务（ai_review / db / dnfs / static / gateway）。

### gateway（应用层网关）

- 唯一对外入口，承载 HTTPS（`full_chain.crt` / `server.key`），默认监听 443 端口。
- 用户表 `Users`（email + SHA256 密码哈希），登录成功后用 RSA 私钥签发 `Base64(payload)_signature` 形式的 Token。
- 反向代理时校验 Token 签名与过期时间，仅放行 `Proxy.opened` 中配置的内部地址。
- `Proxy.closed` 配置内部专用服务（db:8005 / ai_review:8007），禁止外部直接访问。
- 配置示例见 [gateway/example.ini](gateway/example.ini)。

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

### ai_review（AI 内容审核服务）

- 基于 DeepSeek LLM + ChromaDB 向量检索的 RAG 审核架构。
- 审核类别：色情、违禁药品、赌博、诈骗、仇恨/暴力/恐怖主义，灰色地带内容从严判定。
- 接口：`POST /review`（提交审核）、`POST /rag/feedback`（人工纠偏）、`GET /rag/list`（知识库列表）、`GET /health`（健康检查）。
- RAG 检索 top-3 相似已标注样本注入 prompt，MD5 去重，API 异常时 fail-open。
- 配置通过 `.env` 文件（已 gitignore），含 DeepSeek API 密钥、模型参数、监听地址。

## 共享基础库

| 库 | 说明 |
|----|------|
| [libth](libth/) | 线程库：`Thread`、`Cv_wait`（条件变量等待）、`Fsm`、`ITask` 任务接口 |
| [libhttp](libhttp/) | HTTP/WebSocket 库：`HttpServer`、`HttpClient`、`WebSocketServer/Client`（基于 Boost.Beast） |
| [libcrypt](libcrypt/) | 加密库：`AES_CBC`、`MyRSA`、`SHA256`、`Base64`、`Crypto_Basic` |
| [libsqlite3](libsqlite3/) | SQLite3 C++ 封装（Header-only） |

## 端口一览

| 端口 | 服务 | 绑定地址 | 说明 |
|------|------|----------|------|
| 443 | gateway | 0.0.0.0 | HTTPS 对外入口 |
| 5200 | core | — | PMC HTTP 管理接口 |
| 9200 | static | 127.0.0.1 | 前端托管（经 gateway 代理） |
| 8006 | dnfs | 127.0.0.1 | 树状菜单（经 gateway 代理） |
| 8005 | db | 127.0.0.1 | 数据库服务（仅内部调用） |
| 8007 | ai_review | 127.0.0.1 | AI 审核（仅内部调用） |

## 依赖

- **系统级**：`g++`（C++20）、`make`、`libboost-all-dev`、`libcurl4-gnutls-dev`、`libssl-dev`、`libsqlite3-dev`、`python3`、`python3-venv`、`python3-pip`、`patchelf`
- **Python**：Flask、Flask-CORS、Flask-SQLAlchemy、SQLAlchemy、waitress、openai、chromadb、python-dotenv、nuitka（详见各服务 `requirements.txt`）

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

## 运行

由 `core` 守护的子进程通过 `init.txt` 自启动：

```
bash /opt/micro_servers/ai_review/_run.sh
bash /opt/micro_servers/db/_run.sh
bash /opt/micro_servers/dnfs/_run.sh
bash /opt/micro_servers/static/_run.sh
bash /opt/micro_servers/gateway/_run.sh
```

启动 `core`（需 root 或 systemd）：

```bash
systemctl start Nidus      # 或 bash core/_run.sh
```

对外访问入口：`https://<host>:443`。

## 关于架构定位

本项目具备微服务架构的核心特征，是一种**进程托管式轻量微服务**：

- **服务按业务能力拆分**：网关、前端托管、节点树、数据库、内容审核各自独立、独立端口、独立数据存储。
- **API 网关模式**：`gateway` 统一入口，承担鉴权与反向代理，内部服务通过 `closed` 列表隔离。
- **进程编排/守护**：`core` 作为服务管理器拉起并守护子进程，类似简易版 supervisor。
- **多语言异构**：C++（core / gateway）与 Python（static / dnfs / db / ai_review）混合，通过 HTTP 通信。
- **数据访问层解耦**：`db` 服务将数据库操作收敛为统一的 HTTP 接口，业务服务无需直连数据库。
- **AI 能力集成**：`ai_review` 以 RAG 增强的 LLM 审核为业务层提供内容安全能力，支持人工反馈闭环。

与云原生微服务的差异：未引入服务注册发现、容器编排、消息总线，服务地址在 `gateway/example.ini` 中静态配置。对于个人/中小规模的 DIY 场景，这种取舍在简洁性与可控性上是合理的。

## 许可证

各模块均采用 MIT License，详见各目录下的 `LICENSE`。
