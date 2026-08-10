# Blackbird 架构文档（UniServe Framework）

> 权威架构参考。本文以 `core/services.yaml`（实际运行清单）与 `git HEAD` 为事实来源。
> 与根目录 `README.md` 的偏差见文末「§10 与 README 的偏差」。
> 最后核对于：2026-08-10。

---

## 1. 文档目的与范围

Blackbird 是一套 **进程托管式轻量微服务** 系统（自研 UniServe Framework），用于支撑一个带内容审核的社区/笔记类 Web 应用及其周边 AI/嵌入式子系统。本文描述：

- 系统上下文与边界
- 部署拓扑（容器级）
- 关键请求链路
- 服务清单（含语言、端口、职责、当前状态）
- 共享基础库与横切关注点
- 关键架构决策（ADR 摘要）
- 演进策略与已知漂移

**不在本文范围**：单模块内部算法、前端页面细节、硬件机器人/帧缓冲子系统的实现。

---

## 2. 系统上下文（System Context）

```
  浏览器 / 客户端
        │  HTTPS :443
        ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                    Blackbird 系统边界                         │
  │  waf → ca → gateway → {fsys, dnfs2, core}                    │
  │  内部：db2 / ai_review / smtp / captcha2 / core(confs)        │
  │  独立：aibox(+BotOps) / bot4cj / lci                         │
  └─────────────────────────────────────────────────────────────┘
        │  pymsg/TCP (内部 RPC)          │  SMTP (外部邮件)
        ▼                                ▼
   db2 / ai_review / smtp          邮件服务商 (注册验证码)
```

**参与者**：
- 终端用户：经 `:443` 访问社区首页、登录/注册、树状笔记。
- 运维/开发者：经 `core:5200` 管理接口启停子进程。
- 外部依赖：SMTP 邮件服务商（注册验证码）、DeepSeek API（ai_review）。

---

## 3. 部署拓扑（Container Diagram）

```
                          ┌──────────────────────────────────────────────────┐
                          │                core (PMC / Nidus)                 │
                          │  进程托管 :5200 (HTTP 管理)  +  内嵌 confs :8010   │
                          │  读 services.yaml 拉起并守护下列子进程           │
                          └───────────────────────┬──────────────────────────┘
                                                 │ 启动 / 守护
        ┌────────────────┬───────────────────────┼──────────────────┬─────────────────┐
        ▼                ▼                       ▼                  ▼                 ▼
  ┌────────────┐  ┌────────────┐         ┌───────────┐      ┌───────────┐    ┌───────────┐
  │ redirect80 │  │    waf     │         │    ca     │      │  gateway  │    │   fsys    │
  │   :80      │  │   :443     │         │  :8443    │      │  :8445    │    │  :9200    │
  │  Python    │  │    Go      │         │ C++20     │      │ C++20     │    │ C++20     │
  └─────┬──────┘  └─────┬──────┘         └─────▲─────┘      └─────▲─────┘    └─────▲─────┘
        │ 301/308       │ TLS+coraza           │ auth+forward    │ loopback        │ proxy
        └───────────────►│                     │                 │ pure-fwd       │ notes
                          └────────────────────►│                 │                 └────┬────┘
                                                │                 │                      │ pymsg
                                       ┌────────┴────────┐  ┌─────┴────────┐    ┌────────▼─────┐
                                       │  dnfs2 :8006    │  │  core :5200  │    │  dnfs2 :8006 │
                                       │  C++20          │  │  (管理/守护) │    │  (经 fsys 代理)│
                                       └───────▲────────┘  └──────────────┘    └──────────────┘
                                               │ pymsg
                                  ┌────────────┼─────────────┐
                                  ▼            ▼             ▼
                          ┌────────────┐ ┌───────────┐ ┌───────────┐
                          │ db2 :8005  │ │ai_review  │ │  smtp     │
                          │ C++20 pymsg│ │ :8007 Py  │ │ :8008 Py  │
                          │ +Logs 表   │ │ RAG+LLM   │ │ pymsg     │
                          └─────▲──────┘ └─────▲─────┘ └─────▲─────┘
                                │             │ 审核        │ 发码
                                │             └─────────────┘
                          ┌─────┴──────────────────────────────┐
                          │ captcha2 :8009 (C++20, pymsg)       │
                          └─────────────────────────────────────┘

  独立 / 外设（不在主链路）：
    aibox :8765 (Python WebSocket, 独立产品)
    BotOps (aibox 的 TUI 前端)
    bot4cj (Cangjie + 树莓派 I2C, 硬件)
    lci (C++ /dev/fb0, 嵌入式 GUI)
    actpred / memdb / libtry (实验 / 支撑库)

  日志链路: 各服务 log_client ──pymsg──► db2:8005 Logs 表 (fail-safe, db 不可用时静默丢弃)
  配置链路: gateway ──pymsg get──► core 内嵌 confs :8010 (confs/configs/gateway.ini, mtime 自动重载；其余服务配置纯 CLI 传递)
```

> 注：`core` 启动参数为 `./nidus.out -i . --serve 5200 --confs 8010`（`-i .` 指向 services.yaml 所在目录），`--confs` 启用内嵌配置服务（由 confs2 合并而来，与 Python confs 协议兼容）。

---

## 4. 关键请求链路

### 4.1 正常访问（已登录）
1. 客户端若以 HTTP 访问 `:80`，`redirect80` 重定向到 HTTPS `:443`（GET/HEAD→301，其余→308 保留 body）。
2. 请求打到 `waf:443`（Go/coraza）：终止 TLS，运行 SecLang 规则；命中→403。
3. 未命中规则 → 明文 HTTP 转发到 `ca:8443`，注入 `X-Forwarded-For` / `X-Forwarded-Proto`。
4. `ca` 验签 Token，注入 `X-Auth-User` / `X-Auth-Admin`，转发到 `gateway:8445`（仅 loopback）。
5. `gateway` 检查：显式 `Target-Url` 必须带 `X-Auth-User`（否则 401），且必须在 `Proxy.opened` 白名单（否则 403）；转发到 `fsys:9200` / `dnfs2:8006` / `core:5200`。
6. `fsys` 处理前端资源，笔记类请求代理到 `dnfs2:8006`。

### 4.2 注册 / 登录（ca 负责）
- `/login`、`/register`、`/reset_*`、`/captcha/image`、`/whoami` 由 `ca:8443` 直接处理（不再经 gateway 鉴权层）。
- 密码：PBKDF2-HMAC-SHA256（10 万轮 + 16B 随机盐），常数时间比较。
- Token：`Base64(payload) + "_" + RSA-Sign(payload)`，默认 1h 过期；RSA 密钥内存生成，重启即全失效；jti 黑名单 + 用户版本号双轨吊销。
- 注册邮件验证码：ca 经 pymsg/TCP 调 `smtp:8008` 发送与校验；登录强制 CAPTCHA（`captcha2:8009`）。
- 用户数据：ca 经 pymsg/TCP 调 `db2:8005` 存取（`Users` 表，首位注册者即管理员）。
- 速率限制：登录 5/5min、注册 3/10min、发码 IP+邮箱双窗口，按 `X-Forwarded-For` 计数。

### 4.3 笔记写入（含内容审核）
`fsys → dnfs2:8006` → 新增节点前 `dnfs2` 调 `ai_review:8007 /review`（fail-closed，异常一律拦截）→ 审核通过后经 pymsg 调 `db2:8005 execute` 持久化。

### 4.4 日志 / 配置（横切）
- 各服务内置 `log_client`（异步队列 + 后台批量 flush）→ pymsg 写 `db2:8005` 的 `Logs` 表；`db2` 自带 30 天/100 万条清理线程。
- `gateway` 启动时经 pymsg `get` 从 `core:8010` 拉取 `gateway.ini`；`confs/configs/` 下 mtime 变化自动重载。其余服务配置纯 CLI 传递（CLI > 环境变量 > 默认值），不依赖 confs。

---

## 5. 服务清单（当前事实状态）

| 服务 | 语言 | 端口 | 绑定 | 职责 | 状态 |
|------|------|------|------|------|------|
| **core** | C++20 | 5200 / 8010 | 5200 管理 / 8010 loopback | 进程托管(PMC) + 内嵌 confs 配置服务 | 生产 |
| **redirect80** | Python | 80 | 0.0.0.0 | HTTP→HTTPS 重定向 | 生产 |
| **waf** | Go | 443 | 0.0.0.0 | 边缘网关：TLS 终止 + coraza WAF | 生产 |
| **ca** | C++20 | 8443 | 127.0.0.1 | 中央认证 + 身份头注入 + 前置转发 | 生产 (v1.1.0) |
| **gateway** | C++20 | 8445 | 127.0.0.1 | 纯转发反向代理（loopback 强制自检） | 生产 (v0.9.0) |
| **fsys** | C++20 | 9200 | 127.0.0.1 | 前端托管 + dnfs 代理（static 的 C++ 重写） | 生产 |
| **dnfs2** | C++20 | 8006 | 127.0.0.1 | 树状笔记 CRUD | 生产（dnfs 的 C++ 重写） |
| **db2** | C++20 | 8005 | 127.0.0.1 | 通用 DB(pymsg execute) + 日志汇聚 | 生产（db 的 C++ 重写） |
| **ai_review** | Python | 8007 | 127.0.0.1 | AI 内容审核（DeepSeek + ChromaDB RAG） | 生产 |
| **smtp** | Python | 8008 | 127.0.0.1 | 邮件验证码后端（pymsg） | 生产（smtp3 C++ 重写待上线） |
| **captcha2** | C++20 | 8009 | 127.0.0.1 | 图形验证码（pymsg） | 生产（captcha 的 C++ 重写） |
| **aibox** | Python | 8765 | 127.0.0.1 | 独立 AI 智能体（WebSocket） | 独立产品 |
| **BotOps** | Python | — | — | aibox 的 TUI 前端 | 独立 |
| **bot4cj** | Cangjie | — | 树莓派 I2C | 机器人脚本解析器 | 独立硬件 |
| **lci** | C++ | — | /dev/fb0 | 嵌入式帧缓冲 GUI | 独立硬件 |
| **actpred** | Python | — | — | ML 预训练/推理 | 实验 |
| **memdb** | C++20 | — | — | 内存数据库 | 实验/支撑 |
| **libtry** | C | — | — | 嵌入式脚本解释器 | 支撑 |

### 已被替代、不应再部署的遗留模块（不在 services.yaml）
| 遗留 | 替代 | 说明 |
|------|------|------|
| `static` (Python) | `fsys` | 前端托管已 C++ 重写 |
| `db` (Python) | `db2` | DB 服务已 C++ 重写 |
| `dnfs` (Python) | `dnfs2` | 笔记服务已 C++ 重写 |
| `captcha` (Python) | `captcha2` | 验证码已 C++ 重写 |
| `confs` (Python) | 并入 `core` | 配置服务内嵌进 core（ConfsTask） |
| `smtp3` (C++) | （预备） | 代码就绪，services.yaml 仍用 Python `smtp`，尚未平替 |

---

## 6. 共享基础库

| 库 | 语言 | 说明 |
|----|------|------|
| libth | C++20 | 线程 / 条件变量 / FSM / 任务接口 |
| libhttp | C++20 | HTTP/WebSocket（Boost.Beast，服务端 + 客户端） |
| libcrypt | C++20 | AES_CBC / RSA / SHA256 / Base64 |
| libsqlite3 | C++20 | SQLite3 header-only 封装 |
| libmsg | C++20 | pymsg 协议 C++ 实现（4B 大端长度前缀 + UTF-8 JSON），与 Python pymsg 字节级兼容，提供 RpcClient/RpcServer/AsyncQueue |
| liblogc | C++20 | 统一集中式日志客户端（`us/LogC.hpp`，对应 Python `log_client.py`）：pymsg/TCP 异步批量写 db2 Logs 表，队列上限 1 万条 + 级别过滤 + fail-safe，C++ 服务共用 |
| libtry | C | 嵌入式脚本解释器（递归下降，中文标识符） |
| pymsg | Python | 自研 RPC 基础库（`[4B 大端长度][UTF-8 JSON]`），Python 侧 db/log_client 依赖 |

**协议约定**：内部 RPC 统一用 pymsg（TCP 长度前缀帧 + JSON）；对外 HTTP；ca↔gateway 之间为明文 HTTP（TLS 已由 waf 终止）。

---

## 7. 横切关注点

### 7.1 安全分层（纵深防御）
- **边缘**：`waf` 终止 TLS + coraza 规则（本地自定义 100000–199999 / OWASP CRS 900000–999999），命中即 403。
- **应用层**：`ca` 负责鉴权（PBKDF2 + RSA Token + CAPTCHA + 速率限制）；`gateway` 仅 loopback，身份完全信任 ca 注入头。
- **网络隔离**：`db2/ai_review/smtp/captcha2/confs` 仅监听 loopback，不经 gateway 直访（`Proxy.closed`）。
- **fail-closed 文化**：`ai_review` 异常即拦截；`dnfs2` Token 验签失败返 503；`gateway` 错误响应脱敏；SMTP 内部错误对外统一通用码（如 3103）。

### 7.2 可观测性
- 集中式日志：所有服务经 `log_client` 异步写 `db2` 的 `Logs` 表（service/level/message/trace_id/extra）。
- **缺口**：无分布式追踪（trace_id 仅记录未串联）、无指标/监控中间件、无告警。

### 7.3 配置管理
- `core` 内嵌 confs（`:8010`）统一分发 `confs/configs/*.ini`；内存缓存 + mtime 重载；路径遍历防护。
- 三层覆盖：confs 默认值 → 环境变量 → 命令行参数。

### 7.4 弹性 / 容错
- 启动顺序敏感：`core` 拉起顺序为 db2→ai_review→dnfs2→smtp→fsys→waf→ca→gateway→captcha2→aibox→redirect80；pymsg 客户端带 `retry_on_refused` 等依赖就绪。
- 日志 fail-safe：db2 不可用时静默丢弃，不影响业务。
- 有状态单点：`smtp` / `captcha2` 验证码存进程内存，**不支持多实例水平扩展**（需换 Redis 才能扩）。

---

## 8. 关键架构决策（ADR 摘要）

**ADR-1｜认证从 gateway 剥离为独立 ca 模块（v0.9.0 / ca v1.0.0）**
- 背景：gateway 同时承担鉴权与转发，职责过重、RSA 密钥与业务逻辑耦合。
- 决策：ca 独立负责 Token 签发/验签、登录/注册/限流/CAPTCHA，gateway 退居 `127.0.0.1:8445` 纯转发。
- 后果：+1 网络跳数；ca 成为安全边界（loopback + strip-then-set）；gateway 不再依赖 libcrypt/OpenSSL。

**ADR-2｜核心服务 C++20 重写（db→db2, dnfs→dnfs2, static→fsys, captcha→captcha2, confs→core；smtp3 就绪）**
- 背景：Python 服务依赖 venv/pip/Flask，且部分存在 P0/P1 安全问题。
- 决策：用 libhttp+libmsg+boost 重写，接口字节级兼容（pymsg/HTTP 路由不变），可平替。
- 后果：去掉 Python 运行时依赖、修复安全缺陷、提升吞吐；过渡期需双版本并存维护；ai_review/aibox/smtp/redirect80 仍保留 Python。

**ADR-3｜confs 配置服务内嵌进 core**
- 背景：独立 confs 进程增加编排复杂度。
- 决策：confs2 合并为 `core` 的 `ConfsTask`，由 `--confs <port>` 启用。
- 后果：少一个守护进程；core 略微变重；配置协议与目录结构不变。

**ADR-4｜pymsg 作为内部 RPC 标准**
- 决策：内部数据访问统一 `[4B 大端长度][UTF-8 JSON]` over TCP，C++/Python 双实现字节级兼容。
- 后果：业务服务不直接连数据库，数据访问层解耦；引入轻量自研协议（无现成生态）。

---

## 9. 演进策略与已知缺口

- **无服务注册发现 / 容器编排**：服务地址在 `waf/config.yaml` 与 `gateway` 的 `Proxy.*` 中静态配置，由 `core` 进程托管。适合个人/中小规模 DIY，但扩展需手工。
- **smtp3 待上线**：C++ 重写已完成并通过安全审计，但 services.yaml 仍指向 Python `smtp`，建议择机平替以消除最后一块 Python 有状态服务。
- **可观测性缺口**：缺 tracing/metrics/告警，建议后续引入轻量方案（或复用 db2 Logs + 定时聚合）。
- **水平扩展限制**：smtp/captcha 内存态为单点，若需多实例需引入 Redis 等共享存储。

---

## 10. 与 README.md 的偏差（已同步，2026-08-10）

以下为本文相对于根 `README.md` 的纠正点。2026-08-10 已按本清单回填 README 并同步 MODULES.md，状态如下：

1. **端口**：✅ 已修正。README 端口表原写 `gateway 8443`，现已改为 `ca:8443`、`gateway:8445`。
2. **前端模块**：✅ 已修正。README 现以 `fsys`（C++ 重写）为主表，`static` 标注遗留。
3. **C++ 重写版未进主表**：✅ 已修正。README 模块表现以 `db2`/`dnfs2`/`captcha2` 为主，`db`/`dnfs`/`captcha` 标注遗留，`confs` 标注已并入 `core`。
4. **init.txt**：✅ 已同步。core 已于 2026-08 移除 init.txt 兼容，仅支持 `services.yaml`（提交 b0d71a4）；README/本文件均已更新为 `-i . --serve 5200 --confs 8010`。
5. **fsys README 图示滞后**：✅ 已修正（2026-08-10）。`fsys/README.md` 架构图与配置章节已更新为 `ca:8443 → gateway:8445`、纯 CLI 配置。
6. **工作记忆偏差**：✅ 已同步。`ca:8443 → gateway:8445`、`dnfs2`、confs 内嵌 core 均已写入各文档。
7. **dnfs2 README 图示滞后**：✅ 已修正（2026-08-10）。`dnfs2/README.md` 架构图与验签描述已更新为 ca:8443（CLI 参数 `--gateway-url` 指向 ca）。

---

## 11. 文档管理约定（遵循既有规则）

- 全局架构文档置于 `docs/`（本文件）。
- 模块细节写在各模块目录 `README.md`。
- 审计/审查/报告类文档分别入 `docs/security`、`docs/reviews`、`docs/reports`。
