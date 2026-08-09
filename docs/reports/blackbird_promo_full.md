# blackbird 项目「完整版」宣传文案

> 本文档为 blackbird（UniServe Framework / micro_servers）项目创作的**完整版**宣传文案，基于项目 Git 仓库调研结论提炼与合理延展，未虚构任何调研范围外的能力或数据。

---

## 一、项目定位 + 品牌一句话

**定位句**：blackbird 是一套 DIY 的轻量级微服务架构，由 C++20、Python、Go 与仓颉（Cangjie）混合实现，主打进程托管式微服务的轻量、自主可控与安全落地。

**品牌一句话（Slogan）**：**轻量微服务，自主可控更安全。**

**理念 / 愿景**：把微服务的控制权交还给开发者自己——不必被云原生的重基建绑架，用最少的依赖，跑起一套属于你自己的、安全可信的服务集群。

---

## 二、品牌故事 / 背景

我们见过太多团队想拥有自己的服务集群，却被 Kubernetes、服务注册发现、消息总线这一整套云原生重基建劝退。容器编排很强大，但代价是复杂度、学习成本与对外部平台的深度绑定。

blackbird 想回答一个更朴素的问题：如果我只想要"几个常驻进程、一套鉴权、一道防火墙、一份集中日志"，能不能用最轻的方式自己搞定？于是有了这套进程托管式的微服务框架——一个核心进程读着 `init.txt` 就把业务子进程都拉起来并守护好，模块各司其职、各司其语言。

从边缘私有化部署到自托管社区，从 AI 内容审核到机器人硬件联动，blackbird 把"自建可控"这件事压到了一条安装脚本的重量级。它不追求成为另一个云原生怪兽，而是成为你手里那把趁手的、可信的轻刀。

---

## 三、核心架构概览

blackbird 以 **`core` 进程托管核心**（C++20）为基石：它读取 `init.txt`，拉起并守护所有业务子进程，对外提供 `:5200` 的 HTTP 管理接口，并以 `Nidus.service` 身份常驻于 systemd。所有模块以独立进程形态运行，由 core 统一托管、统一看门。

**请求链路（8 步）**：
客户端 → `redirect80:80`（HTTP→HTTPS 301/308 重定向）→ `waf:443`（HTTPS 终止 + coraza WAF 防护）→ `gateway:8443`（统一鉴权 + 反向代理白名单）→ `static:9200` / `dnfs:8006`（前端托管 / 树状菜单节点 CRUD）。其中 `db:8005` 与 `ai_review:8007` 仅内部调用；`dnfs` 在新增节点前会先调用 `ai_review` 完成内容审核，审核不通过则拒绝写入。

**旁路链路**：
- **日志链路**：各业务服务经 `log_client` 将日志推送至 `db` 的 Logs 表，并支持自动清理，实现集中式日志收集。
- **配置链路**：`confs` 配置中心通过 pymsg/TCP 向各模块分发统一配置并支持热重载。

模块地图一览：
- **核心服务**：`core`（进程托管）· `redirect80`（HTTP→HTTPS 重定向）· `waf`（前置 WAF 网关）· `gateway`（应用层网关）· `static`/`dnfs`（前端托管 / 树状菜单）· `db`/`db2`（数据+日志，Python/C++ 双实现）· `ai_review`（AI 内容审核）· `confs`（统一配置）· `captcha`/`smtp`（验证码 / 邮件）。
- **独立产品**：`aibox`（AI 智能体）· `BotOps`（aibox 终端前端）· `bot4cj`（机器人硬件控制）· `lci`（嵌入式帧缓冲图形界面）。
- **共享基础库**：libhttp / libcrypt / libsqlite3 / libmsg / libth / libtry（C++ 基础库与嵌入式脚本引擎）· pymsg / log_client / security_middleware（Python 共享组件）。

---

## 四、功能特性详解

- **进程托管（core）**：C++20 核心读 `init.txt` 拉起并守护各业务子进程，HTTP 管理接口 `:5200`（`/list`、`/exec`、`/kill`，需管理员 Token 鉴权），systemd 常驻。
- **HTTP→HTTPS 重定向（redirect80）**：极简 Python 标准库服务，监听 :80，GET/HEAD 返回 301、其余方法返回 308，无缝升级到 HTTPS。
- **边缘安全 WAF（waf）**：Go 实现的前置网关，终止 TLS 并运行 coraza v3.3.0（OWASP CRS + 自定义规则），命中即返回 403。
- **统一鉴权网关（gateway）**：C++20 应用层网关，统一鉴权（PBKDF2 + RSA Token + 验证码 + 速率限制）与反向代理白名单。
- **数据库 + 集中日志（db / db2）**：Python/pymsg 与 C++20 双实现，通用数据库访问（SQL-over-TCP、参数化防注入）+ 内置 Logs 表与自动清理的集中式日志，接口完全兼容可平替。
- **树状菜单（dnfs / dnfs2）**：Python 与 C++20 双实现，基于父子 pid 的节点树增删改查，新增节点前调用 AI 内容审核。
- **AI 内容审核（ai_review）**：Python 基于 DeepSeek + RAG 实现，采用 fail-closed 设计，宁可拒绝也不放过风险内容。
- **统一配置中心（confs）**：pymsg/TCP 配置中心，向各模块分发配置并支持热重载。
- **AI 智能体（aibox）**：独立 Python 智能体，基于 DeepSeek + WebSocket 技能系统自主执行任务。
- **机器人硬件控制（bot4cj）**：仓颉（Cangjie）实现的硬件控制，解析 `.mt` 脚本驱动舵机与陀螺仪。

---

## 五、技术亮点

- **多语言混编协同**：C++20 / Python / Go / 仓颉各取所长，核心性能与业务敏捷兼得，用最合适的语言写最合适的模块。
- **自研 pymsg RPC**：协议为 `[4 字节大端长度][UTF-8 JSON]`，在 C++ 与 Python 间实现字节级兼容，轻量无外部依赖。
- **开箱即用安全**：WAF（coraza）+ 网关多重鉴权 + AI 内容审核 + 集中日志四层叠加，多模块安全审计持续覆盖（gateway 评级 A-，dnfs2 评级 A，P0 严重问题已全部修复）。
- **共享基础库**：libhttp / libcrypt / libsqlite3 / libmsg / libth / libtry 等 C++ 基础库，与 pymsg / log_client / security_middleware 等 Python 共享组件，避免重复造轮子。
- **一键部署**：`_setup.deb.sh` / `_setup.rhel.sh` / `_inst.sh` 安装脚本 + systemd 守护，一条命令拉起整套集群。
- **MIT 开源友好**：各模块均基于 MIT License，可自由使用、修改与再分发。

---

## 六、适用场景 / 谁该用

- **边缘 / 私有化部署**：不想依赖公云、需要在本地或边缘节点自托管服务集群的团队。
- **自托管社区 / 论坛**：需要前端托管、树状菜单与集中日志的内容站点。
- **需要 AI 审核的内容平台**：新增内容时自动合规审查，fail-closed 兜底风险。
- **教学 / 实验微服务架构**：轻量、可读、多语言混编，是理解微服务与 RPC 的绝佳样本。
- **硬件 + 服务联动**：通过 bot4cj 仓颉脚本驱动舵机/陀螺仪，做软硬一体项目。

---

## 七、快速上手（极简）

```bash
# 1. 下载并执行对应发行版的一键安装脚本（Debian / RHEL 示例）
curl -fsSL https://git.cancerai.top/qing/blackbird/-/raw/main/_setup.deb.sh | bash
# 或 RHEL 系：_setup.rhel.sh ；通用：_inst.sh

# 2. 编辑 init.txt，声明需要托管的子进程模块（顺序很重要）
#    （db / confs / ai_review / dnfs / smtp / static /
#     redirect80 / waf / gateway / captcha / aibox）

# 3. 启动托管核心（注册为 systemd 服务 Nidus.service）
systemctl enable --now Nidus.service

# 4. 访问 :443 即走完整安全链路（redirect80 → waf → gateway → 业务服务）
#    core 管理接口 :5200 提供 /list /exec /kill（需管理员 Token）
curl -X POST http://localhost:5200/list \
  -H "Authorization: Bearer <your_admin_token>"
```

> 注：具体脚本名与参数以仓库 `main` 分支最新文件为准；首次部署建议放行 `:80`、`:443` 端口（内部服务均绑 127.0.0.1，无需额外放行）。

---

## 八、多平台版本文案

### 8.1 GitHub / README 完整简介（约 150–220 字）

> **blackbird** 是一套 DIY 的轻量级微服务架构（别名 UniServe Framework / micro_servers），由 C++20、Python、Go 与仓颉混合实现。它以进程托管核心为核心，内置 WAF 网关、统一鉴权、AI 内容审核与集中日志，主打轻量、自主可控与安全。自研 pymsg RPC 实现跨语言字节级通信，共享基础库降低重复成本，一键安装脚本 + systemd 守护让整套集群开箱即用。各模块均基于 MIT License 开源。适合边缘私有化部署、自托管社区、AI 审核内容平台与软硬一体实验。把微服务的控制权，握回自己手里。

### 8.2 公众号长导语（约 200–300 字，故事感 + 号召）

你有没有想过，自己搭一套"能用的"微服务集群，到底要付出多大的代价？

许多人卡在第一步：Kubernetes、服务注册、消息总线……云原生很强大，却也把"自建可控"变成了高门槛的奢侈品。blackbird 想做的，是把这件事重新变轻——一个 C++20 核心读着一份 `init.txt`，就把 WAF、网关、数据库、AI 审核一个个进程稳稳托管起来，systemd 常驻，一条脚本装好。

它不追逐重基建，而是把轻量、自主可控、安全三件事做到位：Go 写的 WAF 在前端挡刀，C++ 网关做统一鉴权，Python 调 DeepSeek 做内容审核，仓颉脚本还能直接驱动机器人舵机。多语言混编，各司其职；MIT 开源，随心改造。

如果你也想把服务集群的控制权握回自己手里，不妨点开仓库，给它一颗 Star。轻量微服务，自主可控更安全——这一次，由你来定义。

### 8.3 技术社区 / 小红书短文案（约 80–120 字）

自己搭微服务一定要上 K8s 吗？blackbird 给你另一种答案：C++20/Python/Go/仓颉多语言混编的轻量进程托管微服务，自带 WAF + 统一鉴权 + AI 内容审核 + 集中日志，一条脚本开箱即用，MIT 开源。边缘私有化、自托管社区、软硬一体都能跑。把控制权握回自己手里，欢迎 Star & Fork 🌟

---

## 九、号召行动（CTA）+ 开源信息

**把微服务的控制权，握回自己手里。**

- ⭐ **Star** 项目，让更多人看到这套轻量自主可控的微服务框架：https://git.cancerai.top/qing/blackbird
- 🍴 **Fork** 仓库，按你的场景裁剪模块、改写脚本、替换鉴权策略。
- 💬 提交 Issue 与 PR，一起把安全评级推向更高。

**开源协议**：blackbird 各模块均基于 **MIT License** 发布，可自由使用、修改、再分发，仅需保留版权与许可声明。轻量、开放、自主可控——欢迎共建。

---

*文档由文案创作专家笔澜（Penn）基于项目调研结论创作，内容均源自仓库可验证信息，未作能力虚构。*
