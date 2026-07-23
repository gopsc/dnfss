# micro_servers

一套 DIY 的轻量级微服务架构（UniServe Framework），由 C++ 与 Python 混合实现。
`core` 作为进程托管服务负责拉起并守护各业务子进程，`gateway` 作为应用层网关统一鉴权与反向代理，`static` 与 `dnfs` 作为两个独立的业务服务对外提供能力。

## 架构总览

```
                         ┌──────────────────────────────────────────────┐
                         │              core (PMC / Nidus)              │
                         │   进程托管服务 · 监听 :5200 (HTTP 管理接口)    │
                         │   读取 init.txt 拉起并守护下列子进程          │
                         └───────────────┬──────────────────────────────┘
                                         │ 启动 / 守护
                 ┌───────────────────────┼───────────────────────┐
                 ▼                       ▼                       ▼
        ┌────────────────┐     ┌────────────────┐     ┌────────────────┐
        │   gateway      │     │     static     │     │      dnfs      │
        │  应用层网关    │     │  前端托管服务  │     │  树状菜单服务  │
        │   :9201 (入口) │     │     :9200      │     │     :8006      │
        │  鉴权 + 反代   │     │  静态/媒体文件 │     │  节点树 CRUD   │
        └────────┬───────┘     └────────────────┘     └────────────────┘
                 │ 反向代理
        ┌────────┴────────┐
        ▼                 ▼
     static:9200       dnfs:8006
```

### 请求链路

1. 客户端所有请求统一打到 `gateway:9201`。
2. `gateway` 对 `/login`、`/register` 做账号鉴权（SQLite + SHA256 + RSA 签名 Token）。
3. 其余请求携带 Token，经 `gateway` 校验后反向代理到 `Proxy.opened` 中放开的内部服务（`static:9200` / `dnfs:8006`）。
4. `static` 提供前端页面与媒体流播放，`dnfs` 提供树状节点的增删改查。

## 各服务说明

| 服务 | 语言 | 端口 | 职责 |
|------|------|------|------|
| [core](core/) | C++20 | 5200 | 进程托管服务（PMC），创建/列举/杀死/执行子进程，读取自启动列表守护业务进程，提供 HTTP 管理接口 |
| [gateway](gateway/) | C++20 | 9201 | 应用层网关（入口），用户注册/登录鉴权、Token 签发与校验、反向代理到内部服务 |
| [static](static/) | Python(Flask) | 9200 | 前端托管服务，静态页面与文件浏览、视频/音频流媒体播放、路径遍历防护 |
| [dnfs](dnfs/) | Python(Flask) | 8006 | 树状网络文件系统（Dendrachy Network File-System），基于父子 pid 的节点树增删改查 |

### core（核心模块 · 进程托管服务）

- 可执行文件 `nidus.out`，作为 systemd 服务（`Nidus.service`）常驻运行。
- 支持两种工作模式：命令行子系统调用（`--send/--exec/--list/--kill`）与 HTTP 伺服器模式（`--serve <port>`）。
- 通过命名管道（Pipe）与消息队列（Mq）实现进程间通信。
- 读取 `init.txt` 自启动列表，按行拉起各业务服务（static / dnfs / gateway）。

### gateway（应用层网关）

- 唯一对外入口，承载 HTTPS（`full_chain.crt` / `server.key`）。
- 用户表 `Users`（email + SHA256 密码哈希），登录成功后用 RSA 私钥签发 `Base64(payload)_signature` 形式的 Token。
- 反向代理时校验 Token 签名与过期时间，仅放行 `Proxy.opened` 中配置的内部地址。
- 配置示例见 [gateway/example.ini](gateway/example.ini)。

### static（前端托管服务）

- 托管 `public/` 下的前端页面（`login.html` / `register.html` / `dnfs.html` 等）。
- 支持文件浏览、视频/音频在线播放（HTTP Range 分段请求）、一键下载。
- 路径遍历防护，仅允许访问 `directory.root` 之内。
- 配置见 [static/config.ini](static/config.ini)。

### dnfs（树状菜单服务）

- 基于 `pid`（父节点 id）组织的树状节点，节点含 `title` 与 `content`。
- 提供 `/get_notes`、`/add_note`、`/delete_note`、`/update_note` 四个接口。
- 数据持久化于 `notes.db`（SQLite + SQLAlchemy）。

## 共享基础库

| 库 | 说明 |
|----|------|
| [libth](libth/) | 线程库：`Thread`、`Cv_wait`（条件变量等待）、`Fsm`、`ITask` 任务接口 |
| [libhttp](libhttp/) | HTTP/WebSocket 库：`HttpServer`、`HttpClient`、`WebSocketServer/Client`（基于 Boost.Beast） |
| [libcrypt](libcrypt/) | 加密库：`AES_CBC`、`MyRSA`、`SHA256`、`Base64`、`Crypto_Basic` |
| [libsqlite3](libsqlite3/) | SQLite3 C++ 封装 |

## 依赖

- **系统级**：`g++`（C++20）、`make`、`libboost-all-dev`、`libcurl4-gnutls-dev`、`libssl-dev`、`libsqlite3-dev`、`python3`、`python3-venv`、`patchelf`
- **Python**：Flask、Flask-CORS、Flask-SQLAlchemy、waitress（详见各服务 `requirements.txt`）

## 安装

根目录提供一键安装脚本，按依赖顺序依次构建并安装各模块（库先行，core 最后）：

```bash
# 1. 安装系统依赖
bash _setup.sh

# 2. 依次安装各模块（libth -> libhttp -> libcrypt -> gateway -> static -> dnfs -> core）
bash _inst.sh
```

单独安装某一服务时，进入对应目录执行：

```bash
bash _set.sh   # 构建 + 安装
bash _run.sh   # 运行
```

部署路径约定为 `/opt/micro_servers/`，`core` 的 `init.txt` 即从该路径拉起其余服务。

## 运行

由 `core` 守护的子进程通过 `init.txt` 自启动：

```
bash /opt/micro_servers/static/_run.sh
bash /opt/micro_servers/dnfs/_run.sh
bash /opt/micro_servers/gateway/_run.sh
```

启动 `core`（需 root 或 systemd）：

```bash
systemctl start Nidus      # 或 bash core/_run.sh
```

对外访问入口：`https://<host>:9201`。

## 关于架构定位

本项目具备微服务架构的核心特征，是一种**进程托管式轻量微服务**：

- **服务按业务能力拆分**：网关、前端托管、节点树各自独立、独立端口、独立数据存储。
- **API 网关模式**：`gateway` 统一入口，承担鉴权与反向代理。
- **进程编排/守护**：`core` 作为服务管理器拉起并守护子进程，类似简易版 supervisor。
- **多语言异构**：C++（core / gateway）与 Python（static / dnfs）混合，通过 HTTP 通信。

与云原生微服务的差异：未引入服务注册发现、容器编排、消息总线，服务地址在 `gateway/example.ini` 中静态配置。对于个人/中小规模的 DIY 场景，这种取舍在简洁性与可控性上是合理的。

## 许可证

各模块均采用 MIT License，详见各目录下的 `LICENSE`。
