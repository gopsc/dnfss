# micro_servers 文档索引

所有项目文档的统一入口。

## 目录结构

```
docs/
├── INDEX.md                              # 本文件（文档总索引）
├── security/                             # 安全审计与加固相关
├── reviews/                              # 代码审查日志
└── reports/                              # 其他分析报告
```

---

## 一、项目总览

| 文档 | 路径 | 说明 |
|------|------|------|
| 项目 README | [../README.md](../README.md) | 架构总览、服务说明、安装运行指南 |

---

## 二、安全文档 (`security/`)

### 全局审计与加固

| 文档 | 说明 |
|------|------|
| [DEPLOY_SECURITY_AUDIT_20260801.md](security/DEPLOY_SECURITY_AUDIT_20260801.md) | 部署配置安全审计（docker-compose、systemd、防火墙、TLS 等） |
| [DEPS_SECURITY_AUDIT_20260801.md](security/DEPS_SECURITY_AUDIT_20260801.md) | 依赖安全审计（第三方库版本、已知漏洞、许可证） |
| [SECURITY_HARDENING_PLAN_20260803.md](security/SECURITY_HARDENING_PLAN_20260803.md) | 安全加固计划（内网通信安全加固开发计划） |

### 各模块安全审计报告

| 模块 | 文档 | 审计范围 |
|------|------|----------|
| waf | [waf_SECURITY_AUDIT_20260801.md](security/waf_SECURITY_AUDIT_20260801.md) | Go WAF 网关（TLS、coraza、反向代理） |
| gateway | [gateway_SECURITY_AUDIT_20260801.md](security/gateway_SECURITY_AUDIT_20260801.md) | C++20 应用网关（鉴权、Token、反向代理） |
| core | [core_SECURITY_AUDIT_20260801.md](security/core_SECURITY_AUDIT_20260801.md) | C++20 进程托管服务（PMC/Nidus） |
| db | [db_SECURITY_AUDIT_20260801.md](security/db_SECURITY_AUDIT_20260801.md) | Python 数据库服务（pymsg、SQLAlchemy、日志收集） |
| pymsg | [pymsg_SECURITY_AUDIT_20260801.md](security/pymsg_SECURITY_AUDIT_20260801.md) | Python RPC 基础库（长度前缀帧、MsgClient/Server） |
| dnfs | [dnfs_SECURITY_AUDIT_20260801.md](security/dnfs_SECURITY_AUDIT_20260801.md) | Python 树状菜单服务 |
| static | [static_SECURITY_AUDIT_20260801.md](security/static_SECURITY_AUDIT_20260801.md) | Python 前端托管/文件服务 |
| ai_review | [ai_review_SECURITY_AUDIT_20260801.md](security/ai_review_SECURITY_AUDIT_20260801.md) | Python AI 内容审核（DeepSeek + RAG） |
| smtp | [smtp_SECURITY_AUDIT_20260801.md](security/smtp_SECURITY_AUDIT_20260801.md) | Python 邮件服务（验证码发送/校验） |
| captcha | [captcha_SECURITY_AUDIT_20260801.md](security/captcha_SECURITY_AUDIT_20260801.md) | Python 验证码服务 |
| confs | [confs_SECURITY_AUDIT_20260801.md](security/confs_SECURITY_AUDIT_20260801.md) | Python 统一配置服务 |
| libmsg | [libmsg_SECURITY_AUDIT_20260806.md](security/libmsg_SECURITY_AUDIT_20260806.md) | C++ RPC 基础库（pymsg 协议帧、MsgClient/Server、RpcClient/Server） |
| libhttp | [libhttp_SECURITY_AUDIT_20260803.md](security/libhttp_SECURITY_AUDIT_20260803.md) | C++ HTTP/WebSocket 库（请求/响应/WS/静态文件） |
| aibox | [aibox_SECURITY_AUDIT_20260801.md](security/aibox_SECURITY_AUDIT_20260801.md) | Python AI 智能体系统（WebSocket） |
| BotOps | [BotOps_SECURITY_AUDIT_20260801.md](security/BotOps_SECURITY_AUDIT_20260801.md) | BotOps 运维控制面板 |

---

## 三、代码审查 (`reviews/`)

| 文档 | 说明 |
|------|------|
| [core_CODE_REVIEW_LOG_20260730.md](reviews/core_CODE_REVIEW_LOG_20260730.md) | Core 模块代码审查日志（2026-07-30，自动化审查） |
| [DEPLOY_DEFECT_AUDIT_20260806.md](reviews/DEPLOY_DEFECT_AUDIT_20260806.md) | 部署缺陷审查（2026-08-06，首次原生部署实录，含修复记录） |

---

## 四、分析报告 (`reports/`)

| 文档 | 说明 |
|------|------|
| [bot4cj_analysis_20260525_00.md](reports/bot4cj_analysis_20260525_00.md) | bot4cj 机器人模块分析报告（Markdown） |
| [bot4cj_analysis_20260525_00.html](reports/bot4cj_analysis_20260525_00.html) | bot4cj 机器人模块分析报告（HTML） |

---

## 五、各模块 README（保留在原目录）

各模块根目录的 `README.md` 保留在原位，方便 GitHub 直接浏览：

### 核心服务

| 模块 | 路径 | 说明 |
|------|------|------|
| core | [../core/README.md](../core/README.md) | 进程托管服务（PMC/Nidus） |
| waf | [../waf/README.md](../waf/README.md) | WAF 网关（Go, coraza） |
| gateway | [../gateway/README.md](../gateway/README.md) | 应用层网关（C++20） |
| db | [../db/README.md](../db/README.md) | 通用数据库服务（pymsg） |
| confs | [../confs/README.md](../confs/README.md) | 统一配置服务 |
| static | [../static/README.md](../static/README.md) | 前端托管服务 |
| dnfs | [../dnfs/README.md](../dnfs/README.md) | 树状网络文件系统 |
| ai_review | [../ai_review/README.md](../ai_review/README.md) | AI 内容审核 |
| captcha | [../captcha/README.md](../captcha/README.md) | 验证码服务 |
| smtp | [../smtp/README.md](../smtp/README.md) | 邮件服务 |
| redirect80 | [../redirect80/README.md](../redirect80/README.md) | HTTP 重定向服务 |

### 共享库

| 库 | 路径 | 说明 |
|----|------|------|
| pymsg | [../pymsg/README.md](../pymsg/README.md) | Python RPC 基础库 |
| libhttp | [../libhttp/README.md](../libhttp/README.md) | C++ HTTP/WebSocket 库 |
| libcrypt | [../libcrypt/README.md](../libcrypt/README.md) | C++ 加密库 |
| libth | [../libth/README.md](../libth/README.md) | C++ 线程库 |
| libsqlite3 | [../libsqlite3/README.md](../libsqlite3/README.md) | C++ SQLite3 封装 |

### 其他模块

| 模块 | 路径 | 说明 |
|------|------|------|
| aibox | [../aibox/README.md](../aibox/README.md) | AI 智能体系统 |
| bot4cj | [../bot4cj/README.md](../bot4cj/README.md) | 机器人硬件脚本解析器（仓颉） |
| BotOps | [../BotOps/README.md](../BotOps/README.md) | 运维控制面板 |
| lci | [../lci/README.md](../lci/README.md) | LCI 交互式终端框架 |
| tests | [../tests/blackbox/README.md](../tests/blackbox/README.md) | 黑盒测试套件 |

### Robot 文档 (README.robot.md)

| 模块 | 路径 |
|------|------|
| pymsg | [../pymsg/README.robot.md](../pymsg/README.robot.md) |
| libhttp | [../libhttp/README.robot.md](../libhttp/README.robot.md) |
| libtry | [../libtry/README.robot.md](../libtry/README.robot.md) |
| aibox | [../aibox/README.robot.md](../aibox/README.robot.md) |
| bot4cj | [../bot4cj/README.robot.md](../bot4cj/README.robot.md) |

---

## 六、aibox 技能与任务文档

aibox 模块的技能提示词和任务模板属于运行时数据，保留在原目录：

| 类型 | 路径 |
|------|------|
| 技能格式说明 | [../aibox/SKILLS_FORMAT.md](../aibox/SKILLS_FORMAT.md) |
| 技能目录 | [../aibox/skills/](../aibox/skills/) |
| 任务目录 | [../aibox/tasks/](../aibox/tasks/) |
