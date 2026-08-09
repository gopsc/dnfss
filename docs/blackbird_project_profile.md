# Blackbird / micro_servers 项目资料长文档

> 文档类型：项目资料 / 综合参考手册（面向开发者、维护者、审计者与潜在贡献者）
> 资料来源：仓库主 `README.md`、各模块 `README.md`、`docs/INDEX.md`、`docs/MODULES.md`、`docs/security/*`、`docs/reviews/*`、`docs/reports/blackbird_research_report.md`、`docs/security/SECURITY_HARDENING_PLAN_20260803.md`、`docs/blackbird_promo_pack.md`
> 整理日期：2026-08-10
> 文档状态：`advisory`（基于仓库现有文档整合；涉及最新代码整改状态的部分标注为 `human_review_pending`，需结合代码人工复核）

---

## 0. 文档说明

本文档将 Blackbird（仓库名 `micro_servers`，框架名 **UniServe Framework**）过去分散在 40+ 篇说明、审计与报告中的信息，整合为一份可按章节查阅的项目资料。

- **事实边界**：所有模块职责、端口、协议描述以主 `README.md`（2026-08-10 口径，已含 `ca` 中央认证模块）为准；安全发现以 `blackbird_research_report.md`（2026-08-08 快照）与 `docs/security/` 逐模块审计为准。
- **口径差异提醒**：`docs/MODULES.md` 仍记 `gateway:8443`，属 `ca` 认证前置化之前的旧口径；当前拓扑为 `waf:443 → ca:8443 → gateway:8445`，以主 `README.md` 为准。
- **质量状态约定**：基于文档推断与整合的结论标记为 `advisory`；涉及"代码是否已修复某高危项"的判断标记为 `human_review_pending`，因其需对照最新提交逐文件核验。

---

## 1. 项目概述

### 1.1 一句话定位

Blackbird 是一套 **DIY 的轻量级"进程托管式微服务"框架**，用 C++20 / Python / Go / 仓颉(Cangjie) 混合实现，让个人与中小团队用一台服务器、一个脚本，拥有「安全入口 + 统一鉴权 + 数据库 + AI 审核 + 业务功能」的完整能力，免去 Kubernetes 等重型基础设施的复杂度。

### 1.2 设计哲学与核心主张

- **能力拆分但重量极轻**：保留微服务"拆分、统一入口、安全分层、可观测"的真正收益，砍掉服务注册发现、容器编排、消息总线等重型组件。
- **你完全掌控**：数据自托管、代码 MIT 开源，不绑定任何云厂商。
- **多语言自由组合**：核心用 C++（性能/系统），胶水与业务用 Python（效率），边缘网关用 Go（coraza 生态），硬件控制用仓颉（树莓派）。
- **进程托管式编排**：一个 `core` 进程当"保姆"，读 `init.txt` 拉起并守护所有子服务。

### 1.3 适用对象

| 群体 | 诉求 | Blackbird 的契合点 |
|------|------|-------------------|
| 独立开发者 / 个人站长 | 不想被运维压垮 | 一键脚本 + systemd，对外只记一个域名 |
| 中小团队 | 可控、低成本内部中台 | 不依赖外部 SaaS，数据握在自己手里 |
| 注重数据主权者 | 数据绝不能出本机 | 全程自托管，MIT 可改 |
| AI / 硬件爱好者 | 想玩智能体、嵌入式舵机 | aibox 智能体 + bot4cj 舵机 + lci 帧缓冲界面 |

### 1.4 与云原生微服务的差异

未引入服务注册发现、容器编排、消息总线；服务地址在 `waf/config.yaml` 与 `gateway` 配置中静态配置。对于个人/中小规模 DIY 场景，这种取舍在简洁性与可控性上合理，但也意味着横向扩展需自行演进。

### 1.5 许可证与归属

各模块均采用 **MIT License**（详见各目录 `LICENSE`）。项目由个人开发者「qing」维护，仓库地址 `https://git.cancerai.top/qing/blackbird`。

---

## 2. 架构总览

### 2.1 UniServe 架构定义

以 `core`（C++20）为统一宿主进程，读取 `init.txt` 并经 shell 拉起多语言微服务。各服务通过内部 HTTP 对外暴露业务能力，通过 `pymsg/TCP`（自研 RPC）做数据访问与日志汇聚。

### 2.2 当前拓扑（含 ca 认证前置化）

```
                         ┌──────────────────────────────────────────────┐
                         │              core (PMC / Nidus)              │
                         │   进程托管服务 · 监听 :5200 (HTTP 管理接口)    │
                         │   读取 init.txt 拉起并守护下列子进程          │
                         └───────────────┬──────────────────────────────┘
                                         │ 启动 / 守护
          ┌──────────────┬──────────────┼──────────────┬──────────────────┬──────────────┐
          ▼              ▼              ▼              ▼                  ▼              ▼
   ┌──────────────┐ ┌─────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
   │   redirect80 │ │   waf   │ │    ca    │ │   gateway    │ │    fsys      │ │    dnfs      │
   │  :80 重定向  │ │ :443 入口│ │ :8443 认证│ │ :8445 纯转发 │ │  :9200 前端  │ │  :8006 树状  │
   └──────────────┘ └────┬────┘ └────┬─────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
                         │ TLS终止   │ 鉴权+前置   │ 反向代理         │ 代理笔记        │ pymsg
                         │ coraza   │ 转发        │ (loopback)       │                 │
                         └──────────┴─────►───────┴──────────────────┴─────────────────┘
   认证链路(v0.9.0 起): waf:443 ──HTTP──► ca:8443 (认证+前置转发) ──HTTP──► gateway:8445 (纯转发, 仅 loopback) ──► 内部服务
   日志链路: 各服务 ──(log_client, pymsg)──► db:8005 (Logs 表，fail-safe 静默丢弃)
   配置链路: 业务服务 ──(pymsg get)──► confs:8010（mtime 自动重载）
```

> `db2` / `dnfs2` 分别是 `db` / `dnfs` 的 C++20 重实现，架构位置与 Python 版一致，接口兼容可平替。`BotOps`、`lci`、`bot4cj` 不在主图中（终端前端 / 帧缓冲界面 / 树莓派硬件，不联网）。

### 2.3 core 进程托管模型

- 可执行文件 `nidus.out`，以 systemd 单元 `Nidus.service` 常驻（崩溃守护由 systemd 负责，core 自身不含自动重启逻辑）。
- 两种模式：命令行子系统（`--send/--exec/--list/--kill`）与 HTTP 伺服器（`--serve <port>`）。
- 经命名管道(Pipe)与消息队列(Mq)实现进程间通信；读 `init.txt` 自启动列表，按行拉起 ai_review / db / dnfs / smtp / static / waf / ca / gateway / captcha / aibox。

### 2.4 请求链路（8 步）

1. 客户端以 HTTP 访问 `:80`，由 `redirect80` 重定向到 HTTPS `:443`（GET/HEAD 301，其余 308 保留 body）。
2. 所有请求先打到 `waf:443`，终止 TLS、运行 coraza 规则，命中即 403。
3. 未命中请求以明文 HTTP 转发到 `ca:8443`，透传 `X-Forwarded-For` / `X-Forwarded-Proto`。
4. `ca` 对 `/login`、`/register` 做账号鉴权（PBKDF2 + RSA Token + 可选 CAPTCHA + 速率限制；注册经 pymsg/TCP 调 `smtp:8008` 发码），用户数据走 `db:8005`。
5. 其余请求携带 Token，经 `ca` 验签后注入 `X-Auth-User` 转发到 `gateway:8445`（仅 loopback，仅做存在性检查），再由 gateway 反代到 `Proxy.opened` 放开的内部服务（`fsys:9200` / `dnfs:8006`）。
6. `db:8005` 与 `ai_review:8007` 在 `Proxy.closed` 中，仅允许内部调用。
7. `dnfs` 处理节点 CRUD 时经 pymsg/TCP 调 `db` 持久化，新增节点前调 `ai_review` 审核。
8. 各服务经 `log_client.py` 异步批量推送日志到 `db` 的 `Logs` 表（fail-safe）。

### 2.5 端口一览

| 端口 | 服务 | 绑定 | 说明 |
|------|------|------|------|
| 80 | redirect80 | 0.0.0.0 | HTTP→HTTPS 重定向 |
| 443 | waf | 0.0.0.0 | HTTPS 对外入口（TLS + coraza） |
| 8443 | ca | 127.0.0.1 | 中央认证（内部 HTTP） |
| 8445 | gateway | 127.0.0.1 | 纯转发反代（loopback） |
| 5200 | core | 127.0.0.1 | PMC HTTP 管理接口 |
| 9200 | fsys | 127.0.0.1 | 前端托管（经 gateway 代理） |
| 8006 | dnfs/dnfs2 | 127.0.0.1 | 树状菜单（经 gateway 代理） |
| 8005 | db/db2 | 127.0.0.1 | 数据库服务（pymsg/TCP） |
| 8007 | ai_review | 127.0.0.1 | AI 审核（仅内部调用） |
| 8009 | captcha | 127.0.0.1 | 验证码（经 gateway 代理） |
| 8008 | smtp | 127.0.0.1 | 邮件服务（仅内部调用） |
| 8010 | confs | 127.0.0.1 | 统一配置（pymsg/TCP） |
| 8765 | aibox | 127.0.0.1 | AI 智能体（WebSocket，独立产品） |

---

## 3. 模块清单（按职责分组）

### 3.1 核心与编排

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| **core** | C++20 | 5200 | 进程托管服务 PMC/Nidus，读 `init.txt` 拉起守护子进程，提供 HTTP 管理接口（`--send/--exec/--list/--kill/--serve`） |

核心设计：systemd 常驻、root 运行（见第 5 章风险）、Pipe/Mq 进程通信、无内置看门狗（崩溃自愈依赖 systemd）。

### 3.2 入口与网关层

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| **redirect80** | Python | 80 | HTTP→HTTPS 重定向（GET/HEAD 301 / 其余 308），纯标准库 |
| **waf** | Go | 443 | 唯一对外入口，终止 TLS + coraza 规则匹配（SecLang）+ 反代到 ca；`kill -HUP` 热加载；规则库 `waf/rules/` |
| **ca** | C++20 | 8443 | 中央认证服务（v1.0.0，内部 HTTP），注册/登录鉴权、Token 签发校验、身份头注入后前置转发到 gateway |
| **gateway** | C++20 | 8445 | 应用层网关（v0.9.0，仅 loopback），纯转发反代，不验签（认证已迁至 ca） |

**ca 关键设计**：PBKDF2-HMAC-SHA256（10 万轮 + 16 字节盐）、常数时间比较防 timing attack；Token 为 `Base64(payload)+"_"+RSA-Sign(payload)`，默认 1 小时过期，RSA 密钥启动时内存生成（重启即失效）；`HttpOnly+Secure+SameSite=Lax` 的 `q4_token` Cookie；登录 5 次/5 分钟、注册 3 次/10 分钟速率限制；首位注册者即管理员（无默认管理员账号）。

**gateway 关键点**：纯转发，存在性不变量——显式 `Target-Url` 请求必须携带 ca 注入的 `X-Auth-User`，缺失一律 401；`Target-Url` 必须在 `Proxy.opened` 白名单。

### 3.3 业务服务（对外，经 gateway 代理）

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| **fsys**（原 static） | Python/Flask | 9200 | 前端托管：社区首页/静态页/媒体流/文件浏览；**2026-08 由 static 重命名为 fsys**（core/init.txt 已同步），过渡期仍保留 legacy `static/` 目录 |
| **dnfs** | Python/Flask | 8006 | 树状菜单（Dendrachy Network File-System），基于 pid 的节点树 CRUD，新增节点前调 ai_review 审核 |
| **dnfs2** | C++20 | 8006 | dnfs 的 C++20 重实现（libhttp + libmsg），接口完全兼容，修复 P0/P1（fail-closed 验签/所有权校验/脱敏） |
| **ai_review** | Python/Flask | 8007 | AI 内容审核，DeepSeek + ChromaDB RAG（色情/违禁药/赌博/诈骗/仇恨暴力），fail-closed 宁可误拦 |

### 3.4 基础设施 / 支撑服务（仅内部调用）

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| **db** | Python/pymsg | 8005 | 通用数据库服务，SQL-over-TCP（SQLAlchemy Core 参数化防注入），内置集中式日志（Logs 表 + 自动清理） |
| **db2** | C++20 | 8005 | db 的 C++20 重实现（libmsg + boost::json + libsqlite3），`execute` 路由/where 语法/响应格式对齐，可平替 |
| **confs** | Python/pymsg | 8010 | 统一配置分发，集中存放各服务配置（`confs/configs/`），内存缓存 + mtime 自动重载，路径遍历防护 |
| **smtp** | Python/pymsg | 8008 | 邮件服务，注册邮箱验证码后端（send_email/verify_email/health），6 位码内存存储、冷却/限流/熔断 |
| **captcha** | Python/pymsg | 8009 | 验证码服务，图形验证码（一次性、5 分钟过期），经 ca 的 `GET /captcha/image` 代理给浏览器 |

### 3.5 AI 智能体与前端

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| **aibox** | Python | 8765 | 独立 AI 智能体系统（DeepSeek + WebSocket），自带技能系统/记忆/任务调度/前端与语音客户端；由 core 拉起但与主干微服务无调用关系 |
| **BotOps** | Python/Textual | — | aibox 的终端 TUI 前端，WebSocket 连接 aibox，Markdown 增量流式渲染、思考模式、任务侧栏、自动重连 |
| **actpred** | Python/PyTorch | — | 动作预测（图像 + 动作历史 → 预测下一个动作），bot4cj 的「大脑」配套；支持从 `.mt` 脚本预训练 |

### 3.6 机器人硬件与嵌入式

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| **bot4cj** | 仓颉/Cangjie | — (树莓派 I2C) | 机器人硬件脚本解析器，解析 `.mt` 驱动 PCA9685 舵机 / MPU6050 陀螺仪 / 扬声器，面向 Dashun 蜘蛛机器人 |
| **lci** | C++ | — (/dev/fb0) | 嵌入式帧缓冲图形界面（无需 X11/Wayland），矢量 GB2312 中文字库、图片渲染、实时数据可视化 |
| **3d** | — (资产) | — | 机器人 / 机械臂 CAD 模型（STEP/STL/SLDPRT），bot4cj 配套硬件资产 |

### 3.7 共享基础库

| 库 | 语言 | 说明 |
|----|------|------|
| **libth** | C++ | 线程库：`Thread`、`Cv_wait`、`Fsm`、`ITask` |
| **libhttp** | C++ | HTTP/WebSocket 库（Boost.Beast）：`HttpServer`（含 WS 服务/代理）、`HttpClient`（含 WS 客户端） |
| **libcrypt** | C++ | 加密库：`AES_CBC`、`MyRSA`、`SHA256`、`Base64`、`Crypto_Basic` |
| **libsqlite3** | C++ | SQLite3 header-only 封装 |
| **libmsg** | C++ | pymsg 协议 C++ 实现（4B 大端长度前缀 + UTF-8 载荷），`MsgClient/Server`、`RpcClient/Server`、`AsyncQueue`，与 Python pymsg 字节级兼容 |
| **libtry** | C | 嵌入式脚本解释器（~741 行），手写递归下降，支持中文标识符，用于嵌入式操作界面 shell |
| **pymsg** | Python | 自研 RPC 基础库（`[4B 长度][UTF-8 JSON]`），db 服务端与各 `log_client.py` 的基础依赖（setuptools 包） |
| **memdb** | C++ | 内存数据库（internal 组件，未在主 README 模块表列出） |

### 3.8 测试与文档

| 项 | 说明 |
|----|------|
| **tests** | 黑盒测试套件（`test_db_*`/`test_gateway_*`/`test_waf_*`/`test_dnfs_*`），子进程 + HTTP 验证，前后自动备份恢复 db |
| **docs** | 文档聚合（security/ 审计、reviews/ 审查、reports/ 分析、INDEX.md），41+ 篇 md |

---

## 4. 通信协议：pymsg / libmsg

这是 Blackbird 异构架构的通信基石。

- **帧结构**：`[4 字节大端长度][UTF-8 JSON]`，单帧上限 **64 MiB**。
- **跨语言一致**：Python `pymsg` 与 C++ `libmsg` 字节级兼容，是 v1/v2 双栈迁移的关键支撑。
- **资源防护**：慢连接总 deadline 防护 + 有界线程池，控制资源耗尽类风险。
- **错误脱敏**：`_sanitize_error` 以正则替换绝对路径，降低信息泄露面。
- **安全现状（重要）**：协议**无握手、无鉴权、明文无 TLS**，安全性完全依赖 `127.0.0.1` 主机隔离。一旦内网隔离被突破（同机多租户或某内部服务失陷），所有跨进程调用即暴露。详见第 5 章与加固计划。

---

## 5. 安全体系

### 5.1 边界鉴权优势（已达 A-）

gateway（及 ca）的边界鉴权设计扎实，安全评级由 **B+ 提升至 A-**（Gateway v0.8.0 修复 P0：错误消息脱敏、SIGTERM 退出、doAccept 无限递归、8 秒关闭超时）：

- 口令：PBKDF2-HMAC-SHA256（10 万轮 + 16 字节盐），常数时间比较防时序攻击。
- Token：RSA-2048 + PKCS#1 v1.5 签名，双轨吊销（jti 黑名单 + 用户版本号）。
- 多层防护：CAPTCHA 一次性、登录 5/300s、注册 3/600s、邮箱级双窗口限流。
- Cookie：`HttpOnly;Secure;SameSite=Lax`。
- 管理面 fail-closed：`core` HTTP 接口绑 127.0.0.1 且 `verify_admin_token`。

### 5.2 已核实缺口（来自 2026-08-08 研究报告的审计结论）

> 以下结论基于研究报告对当时代码快照的核实，整理时（2026-08-10）部分项已在 `ca` 引入、C++ 重写潮与加固计划中进入整改。**具体是否已在最新提交修复，需逐文件人工复核，状态标记为 `human_review_pending`。**

| 级别 | 缺口 | 说明 | 关联整改 |
|------|------|------|----------|
| 高危 1 | WAF 规则失效 | `waf/rules/local/custom.conf` 的 6 条 SecRule 全部被注释，无 `crs/` 目录 → WAF 仅做 TLS 终止 + 响应头，不拦截 SQLi/XSS/命令注入 | 加固计划阶段一目标；待启用规则 |
| 高危 2 | core MQ 未鉴权即 exec | `parse_data_from_mq` 对 list/kill/exec 未鉴权即经 `/bin/sh -c` 执行；core 以 root 运行 → 本地 root RCE | 加固计划阶段二核心项 |
| 高危 3 | 内部服务零信任缺失 | pymsg 无认证；db 的 `execute` 路由接受任意 action/table/where，仅依赖 127.0.0.1 隔离 → 边界失守后可免认证读写全库 | 加固计划阶段三（AES-GCM） |
| 中危 1 | ai_review 未鉴权 + 提示注入 | `/review` 仅 per-IP 限流、无鉴权；用户输入直接拼入系统提示词 → 审核绕过 + 开放 LLM 端点 | aibox_open_dev_plan 跟踪 |
| 中危 2 | 密钥配置缺陷 | `.env.example` 写 `OPENAI_API_KEY` 而代码读 `DEEPSEEK_API_KEY` → 变量名不一致的静默失效 bug | 待对齐 |

### 5.3 安全加固计划（三层防线）

`docs/security/SECURITY_HARDENING_PLAN_20260803.md` 定义了"隔离层 + 认证层 + 加密层"三阶段目标：

- **阶段一（隔离加固，P0）**：所有内部服务绑 127.0.0.1（✅ 已实现 db/confs/captcha/gateway/static/smtp/dnfs）；防火墙仅放行 443、外网端口扫描验证仍 `[ ]` 待办。
- **阶段二（core 密钥注入，P1）**：core 启动时生成进程组密钥，经 `execve` 的 envp 注入 `MICROSERV_KEY`，子进程 fail-fast 读取——仍 `[ ]` 待办。
- **阶段三（AES-GCM 通信加密，P1）**：编写 `microcrypt` 库，接入 pymsg 帧与 HTTP 调用，防篡改/重放——仍 `[ ]` 待办。

### 5.4 安全审计覆盖（`docs/security/`，41+ 篇）

包含全局审计（DEPLOY / DEPS / SECURITY_HARDENING_PLAN）与逐模块审计报告（waf、gateway×3、core、db、db2、pymsg、dnfs、dnfs2、static/fsys、ai_review、smtp、smtp3、captcha、captcha2、confs、libmsg、libhttp、libsqlite3、libth、aibox、BotOps、actpred）。`docs/reviews/` 另有 4 篇代码审查日志。可审计性远超多数同类项目，是风险闭环的基底。

---

## 6. 部署与运维

### 6.1 依赖

- **系统级**：`g++`（C++20）、`make`、`libboost-all-dev`、`libcurl4-gnutls-dev`、`libssl-dev`、`libsqlite3-dev`、`python3` + venv + pip、`patchelf`、`golang-go`（Go 1.22+，用于 waf）、`-ljpeg -lpng`（lci）。
- **Python**：Flask、Flask-CORS、Flask-SQLAlchemy、SQLAlchemy、waitress、openai、chromadb、python-dotenv、nuitka。
- **Go**：corazawaf/coraza v3.3.0、gopkg.in/yaml.v3。

### 6.2 安装链（一键脚本）

```bash
# 1. 系统依赖
bash _setup.deb.sh        # Debian/Ubuntu
# 或
bash _setup.rhel.sh       # RHEL/CentOS/Rocky/Fedora

# 2. 依次构建安装（库先行，core 最后）
bash _inst.sh
```

单独安装某服务：`bash _set.sh`（构建+安装）、`bash _run.sh`（运行）。部署路径约定 `/opt/micro_servers/`。

### 6.3 运行

`init.txt` 自启动顺序重要（redirect80 → waf → gateway → …）。启动 core：

```bash
systemctl start Nidus      # 或 bash core/_run.sh
```

对外入口：`https://<host>:443`（waf 终止 TLS 后转发）。

### 6.4 WAF 与 gateway 协作

启用 WAF 后证书从 gateway 迁到 waf：`cp gateway/full_chain.crt gateway/server.key waf/certs/`。gateway 改内部 HTTP 模式（`tls=off`、`address=127.0.0.1`、`port=8443`→现 8445）。独立部署（不用 WAF）时 gateway 自行 `tls=on`、`port=443`、`address=0.0.0.0`。

### 6.5 容器化现状

`docker-compose.yml` 的 services 块**仅纳入 db / ai_review / dnfs 三个服务**（另含 networks 与 volumes），core/waf/gateway/fsys/confs/captcha/smtp 未纳入 → 编排不完整。生产部署当前以 systemd 链路为准。

---

## 7. 测试与质量

- **黑盒测试**：`tests/blackbox/` 约 8 个集成测试，覆盖 db / dnfs / gateway / waf / static / captcha / smtp / ai_review；无单元测试、无 CI（仓库无 `.github/workflows`）。
- **文档/审计完备度**：`docs/security/` 24+ 份逐模块审计 + `docs/reviews/` 评审日志，可审计性极高。
- **双实现（v1/v2）重构负担**：db↔db2、dnfs↔dnfs2、pymsg↔libmsg、libtry↔libth、lci↔bot4cj 并存，正以「C++ 重写 Python 微服务」为目标迁移。任一逻辑修正需两语言同步，存在语义漂移风险——建议重构收口时优先合并单轨。

---

## 8. 开发路线图与近期重点（2026-08）

### 8.1 C++ 重写潮（2026-08-09 引入）

| 新增/重写模块 | 说明 |
|--------------|------|
| **ca** | 中央认证服务（C++ 2463 行），认证独立化，接管原 gateway 端口前置鉴权 |
| **captcha2** | C++ 重写 captcha（含 stb_image_write），修复 P0 大小写缺陷 |
| **confs2** | C++ 重写 confs（libmsg RpcServer 替代 pymsg），协议兼容零改动 |
| **smtp3** | C++ 重写 smtp（1013 行，SMTP_SSL 手写），审计 7 项遗留一并修复 |
| **core ConfsTask** | C++ 内嵌配置任务（557 行） |
| **gateway 精简** | `src/main.cpp` 大幅精简（-1980 行），认证逻辑下沉至 ca |

### 8.2 认证独立化（auth_service_plan）

`waf → ca → gateway` 前置拓扑：ca 统一鉴权并前置转发，gateway 退化为纯转发（仅 loopback），下游 / WAF / 前端零改动。对应开发文档见 `docs/reports/auth_service_plan.md`。

### 8.3 关键 dev docs 索引（`docs/reports/`）

core_service_registry_dev_doc（注册中心）、auth_service_plan（CA 模块）、aibox_open_dev_plan / aibox_file_network_cli_refactor（aibox 安全沙箱与 CLI 化）、libcrypt_CBC_deprecation_dev_doc（AES-GCM 迁移）、libhttp_remote_addr_dev_doc（X-Remote-Addr 透传）、captcha_cpp_rewrite / smtp_cpp_rewrite / confs2_cpp_rewrite（C++ 重写方案）、actpred_pretrain_test（预训练与推理测试）。

### 8.4 安全评级演进

Gateway v0.8.0 修复 P0 后，**安全评级 B+ → A-**；随 ca 引入与 C++ 重写潮加固，整体向"端到端一致鉴权"收敛（详见第 5 章待办）。

---

## 9. 许可证与推广

- **许可证**：各模块 MIT。
- **价值主张摘要**（详见 `docs/blackbird_promo_pack.md`）：
  - "微服务的好处，Kubernetes 的麻烦，二选一？Blackbird 说：都要，但只留好处。"
  - "你的数据、你的服务、你的规则——一台机器，全部自己说了算。"
  - Slogan 候选：*轻装微服务，主权握手中*；*一台服务器，一座可控的城*；*微服务的纯粹主义*。

---

## 10. 风险与待办（收口）

| 风险 / 待办 | 级别 | 状态 | 来源 |
|------------|------|------|------|
| WAF 检测规则未启用 | 高危 | `human_review_pending`（计划阶段一目标） | 研究报告 / 加固计划 |
| core MQ 未鉴权 RCE（root 运行） | 高危 | `human_review_pending`（计划阶段二） | 研究报告 / 加固计划 |
| 内部 pymsg/db 零信任缺失 | 高危 | `human_review_pending`（计划阶段三） | 研究报告 / 加固计划 |
| ai_review 未鉴权 + 提示注入 | 中危 | `human_review_pending` | 研究报告 / aibox_open_dev_plan |
| 密钥变量名不一致（DEEPSEEK/OPENAI） | 中危 | `advisory`（待对齐） | 研究报告 |
| docker-compose 仅 3 服务，不完整 | P2 | `advisory` | 研究报告 |
| 无单元测试 / 无 CI | P2 | `advisory` | 研究报告 |
| v1/v2 双实现维护成本 | 结构性 | `advisory`（建议收口合并） | 研究报告 |
| 文档口径差异（MODULES 记 gateway:8443） | 低 | `advisory`（以 README 为准） | 本文档核实 |

### 建议交付清单

- [ ] 启用 WAF 规则并补建 `crs/`（直击高危 1）
- [ ] 为 core MQ 通道落地 `verify_admin_token` 并降权运行（直击高危 2）
- [ ] 内部服务加调用方鉴权 / pymsg 上 mTLS 或 Unix socket（直击高危 3）
- [ ] ai_review `/review` 加鉴权 + 结构化提示词拼接
- [ ] 统一 `DEEPSEEK_API_KEY` 与 `.env.example`
- [ ] 补全 compose 或文档声明 systemd 为唯一受支持链路
- [ ] 引入单元测试与 CI，降低双实现回归风险

---

## 续接胶囊

- **文档类型**：项目综合资料长文档（Blackbird / micro_servers）
- **目标读者**：开发者、维护者、安全审计者、潜在贡献者
- **核心目标**：将分散在 40+ 篇仓库文档中的架构、模块、协议、安全、部署、路线图整合为单一可查阅手册
- **已完成章节**：第 0–10 章（概述、架构、模块清单、pymsg 协议、安全体系、部署运维、测试质量、路线图、许可证、风险收口）
- **已确认事实**：主 README 拓扑（waf:443→ca:8443→gateway:8445）；ca 为 C++ 认证前置模块；C++ 重写潮（ca/captcha2/confs2/smtp3/gateway 精简/core ConfsTask）；安全评级 B+→A-；MIT 许可
- **待核验事项**：3 项高危 + 2 项中危是否已在最新提交修复（研究报基于 2026-08-08 快照）；docker-compose 补全状态；MODULES.md 端口口径同步
- **下一节唯一目标**：（可选）补充"各模块 API 速查表"或"从零部署实战 walkthrough"
- **续写口令**：`继续编辑 docs/blackbird_project_profile.md，当前停于第 10 章风险收口；已含架构/模块/协议/安全/部署/路线图；约束：以主 README 拓扑为准、高危缺口标注 human_review_pending、不虚构修复状态；下一目标：补模块 API 速查表或部署实战。`
