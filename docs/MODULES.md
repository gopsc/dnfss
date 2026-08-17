# 模块清单（blackbird / micro_servers）

> 一套 DIY 的轻量级「进程托管式微服务」（UniServe Framework）。
> 混合语言：C++20（core/gateway/ca/dnfs2/db2/memdb/fsys/captcha2/lib*）、Python（static/dnfs/db/ai_review/smtp/captcha/confs/redirect80/BotOps/aibox/actpred/pybot/mtc/pymsg，多为遗留）、Go（waf）、仓颉/Cangjie（bot4cj）。
> 由 `core` 读 `services.yaml` 拉起并守护各子进程；`waf:443 → ca:8443 → gateway:8445 → 业务服务` 为对外主链路。

## 一、核心与编排

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| core | C++20 | 5200 / 8010 | 进程托管服务 PMC / Nidus，读 `services.yaml` 拉起守护子进程，提供 HTTP 管理接口（`--send/--exec/--list/--kill/--serve`），内嵌 confs 配置服务（`--confs 8010`） |

## 二、入口与网关层

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| redirect80 | Python | 80 | HTTP→HTTPS 重定向（GET/HEAD 301，其余 308 保留 body）；纯标准库 `http.server` |
| waf | Go | 443 | 唯一对外入口，终止 TLS + coraza 规则匹配（SecLang）+ 反代到 ca；`kill -HUP` 热加载；规则库 `waf/rules/` |
| ca | C++20 | 8443 | 中央认证服务（内部 HTTP），注册/登录鉴权、PBKDF2 密码哈希、RSA Token 签发校验、Cookie 认证、速率限制、身份头注入后前置转发到 gateway |
| gateway | C++20 | 8445 | 应用层网关（仅 loopback 纯转发，v0.9.0 起认证迁至 ca），反向代理（`Proxy.opened` 白名单），不验签 |

## 三、业务服务（对外，经 gateway 代理）

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| fsys | C++20 | 9200 | 前端托管（社区首页/静态页面/媒体流/文件浏览）；Python/Flask `static` 的 C++ 重写（legacy `static/` 目录保留），代理 dnfs2 笔记请求 |
| dnfs | Python/Flask | 8006 | 树状菜单（Dendrachy Network File-System），基于 pid 的节点树 CRUD，新增节点前调 ai_review 审核（遗留，已由 dnfs2 平替） |
| dnfs2 | C++20 | 8006 | dnfs 的 C++20 重实现（libhttp + libmsg），接口兼容可平替，修复 P0/P1（fail-closed 验签/所有权校验/脱敏）；支持 js/file/style/theme 特殊节点 |
| ai_review | Python/Flask | 8007 | AI 内容审核，DeepSeek + ChromaDB RAG（色情/违禁药/赌博/诈骗/仇恨暴力），fail-closed 宁可误拦 |

## 四、基础设施 / 支撑服务（仅内部调用）

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| db | Python/pymsg | 8005 | 通用数据库服务，SQL-over-TCP（SQLAlchemy Core 参数化防注入），内置集中式日志（Logs 表 + 自动清理）（遗留，已由 db2 平替） |
| db2 | C++20 | 8005 | db 的 C++20 重实现（libmsg + boost::json + libsqlite3），`execute` 路由/where 语法/响应格式对齐，可平替 |
| confs | Python/pymsg | 8010 | 统一配置分发（遗留，已内嵌进 core 的 ConfsTask）；`confs/configs/` 现仅剩 `gateway.ini` 仍由 confs 分发 |
| smtp | Python/pymsg | 8008 | 邮件服务，注册邮箱验证码后端（路由 send_email/verify_email/health），6 位码内存存储、冷却/限流/熔断（smtp3 C++ 重写待上线） |
| captcha | Python/pymsg | 8009 | 验证码服务，图形验证码（一次性、5 分钟过期），经 ca `GET /captcha/image` 代理给浏览器（遗留，已由 captcha2 平替） |
| captcha2 | C++20 | 8009 | captcha 的 C++20 重实现（pymsg），路由 image/verify/health 对齐 |
| memdb | C++20 | 8011 | 内存缓存数据库（libmemdb 模板库 + libmsg RPC），TTL 惰性驱逐，9 路由（ping/set/get/del/update/exists/size/show/expire），线程安全 + SIGINT/SIGTERM 优雅退出 |

## 五、AI 智能体与前端

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| aibox | Python | 8765 | 独立 AI 智能体系统（DeepSeek + WebSocket），自带技能系统/记忆/任务调度/前端与语音客户端；`--once` CLI 单次对话（stdout/stderr 分流），`models` 配置节支持多 OpenAI 兼容端点按档案名切换 |
| BotOps | Python/Textual | — | aibox 的终端 TUI 前端，WebSocket 连接 aibox，Markdown 增量流式渲染、思考模式、任务侧栏、自动重连 |
| actpred | Python/PyTorch | — | 动作预测（图像 + 动作历史 → 预测下一个动作），bot4cj 的「大脑」配套；支持从 `.mt` 脚本预训练 |

## 六、机器人硬件与嵌入式

| 模块 | 语言 | 端口 | 职责 |
|------|------|------|------|
| bot4cj | 仓颉/Cangjie | — (树莓派 I2C) | 机器人硬件脚本解析器，解析 `.mt` 驱动 PCA9685 舵机 / MPU6050 陀螺仪 / 扬声器，面向 Dashun 蜘蛛机器人 |
| pybot | Python | — (树莓派) | bot4cj 的 Python 迁移版：`.mt` 动作脚本解析器（零第三方依赖，与仓颉版逐语义对齐）；支持执行 / `--dry-run` / `--record` 录制 actpred episode（含 imu.npy）/ `--loop` 实时预测闭环 |
| mtc | Python | — | 命令式动作脚本（Python DSL）→ `.mt` 编译器：`exec` 注入命名空间 → 时间轴 IR（非阻塞语义、重叠截断）→ 10ms 栅格化；产物可由 pybot/bot4cj 直接执行，零第三方依赖 |
| lci | C++ | — (/dev/fb0) | 嵌入式帧缓冲图形界面（无需 X11/Wayland），矢量 GB2312 中文字库、图片渲染、实时数据可视化 |
| 3d | — (资产) | — | 机器人 / 机械臂 CAD 模型（STEP/STL/SLDPRT），bot4cj 配套硬件资产 |

## 七、共享基础库

| 模块 | 语言 | 说明 |
|------|------|------|
| libth | C++ | 线程库：`Thread`、`Cv_wait`、`Fsm`、`ITask` |
| libhttp | C++ | HTTP/WebSocket 库（Boost.Beast）：`HttpServer`（含 WS 服务/代理）、`HttpClient`（含 WS 客户端） |
| libcrypt | C++ | 加密库：`AES_CBC`、`MyRSA`、`SHA256`、`Base64`、`Crypto_Basic` |
| libsqlite3 | C++ | SQLite3 header-only 封装 |
| libmsg | C++ | pymsg 协议 C++ 实现（4B 大端长度前缀 + UTF-8 载荷），`MsgClient/Server`、`RpcClient/Server`、`AsyncQueue`，与 Python pymsg 字节级兼容 |
| libmemdb | C++20 | header-only 内存数据库模板库：三索引存储（`std::list` 主存 + `unordered_map` 键索引 + `multiset` TTL 索引），insert/remove/update/search 等操作，TTL 惰性驱逐；memdb 服务即基于此库 |
| liblogc | C++ | 统一集中式日志客户端（`us/LogC.hpp`，对应 Python `log_client.py`）：pymsg/TCP 异步批量写 db2 Logs 表，队列上限 1 万条 + 级别过滤 + fail-safe，ca/core/gateway/captcha2/smtp3 等 C++ 服务共用 |
| libtry | C | 嵌入式脚本解释器（~741 行），手写递归下降，支持中文标识符，用于嵌入式操作界面 shell |
| pymsg | Python | 自研 RPC 基础库（`[4B 长度][UTF-8 JSON]`），db 服务端与各 `log_client.py` 的基础依赖（setuptools 包） |

## 八、测试与文档

| 模块 | 说明 |
|------|------|
| tests | 黑盒测试套件（`test_db.py`/`test_gateway.py`/`test_waf.py`/`test_dnfs.py`/`test_captcha.py`/`test_smtp.py`/`test_ai_review.py`/`test_static.py`/`test_core_confs.py`），纯标准库 HTTP/TCP 用户旅程模拟，经 `test_runner.py` 统一调度（详见 [tests/blackbox/README.md](../tests/blackbox/README.md)） |
| docs | 文档聚合（security/ 审计、reviews/ 审查、reports/ 分析），70 篇 md |

## 九、根级构建与部署脚本

- `_inst.sh`：一键按依赖顺序构建安装（库先行，core 最后）
- `_setup.deb.sh` / `_setup.rhel.sh`：系统依赖安装（Debian/Ubuntu、RHEL 系）
- `docker.sh` / `docker-compose.yml`：容器化部署
- 部署路径约定 `/opt/micro_servers/`，`core` 的 `services.yaml` 即从该路径拉起其余服务

## 关键链路速记

- **请求主链路**：client → `redirect80`(:80→443) → `waf`(:443 TLS + WAF) → `ca`(:8443 认证+前置转发) → `gateway`(:8445 纯转发) → `fsys`(:9200) / `dnfs2`(:8006)
- **日志链路**：各服务 `log_client.py`（异步批量）→ `db2:8005` Logs 表（fail-safe，db2 不可用时静默丢弃）
- **配置链路**：`gateway` → `core` 内嵌 confs:8010（内存缓存 + mtime 重载，仅 gateway.ini）；其余服务配置纯 CLI 传递
- **数据/审核/邮件/缓存**：`dnfs2` → `db2:8005`（pymsg 持久化）；`dnfs2` → `ai_review:8007`（审核）；注册邮件 `ca` → `smtp:8008`；登录验证码 `ca` → `captcha2:8009`；`memdb:8011`（内存缓存，libmsg RPC，暂无业务调用方，预留热点数据/会话缓存）
- **机器人数据链**：`mtc` 编译 Python DSL → `.mt`（或手写 `.mt`）→ `pybot` 执行 / `--record` 录制 episode（images + actions.npy + imu.npy）→ `actpred` 两阶段训练 → ONNX/Hailo HEF 部署 → `pybot --loop` 实时预测闭环（bot4cj 语义对齐）
- **端口一览**：80 redirect80 · 443 waf · 8443 ca · 8445 gateway · 5200/8010 core · 9200 fsys · 8006 dnfs2 · 8005 db2 · 8007 ai_review · 8009 captcha2 · 8008 smtp · 8011 memdb · 8765 aibox
