# Blackbird / micro_servers 项目红队评估

> 评估对象：`docs/blackbird_project_profile.md`（2026-08-10 advisory 整合稿）
> 评估视角：材料红队（事实 / 推断 / 未知 + 矛盾与缺证）
> 评估时间：2026-08-10

## 一句话结论
架构主张清晰、文档可审计性极高的自托管轻量微服务框架；但"安全评级 A-"缺乏代码级实证，3 项高危整改全部处于 `human_review_pending`，当前不适合暴露于多租户或不可信内网。

## 优势（已确认事实）
- **定位成立**：砍掉 K8s 的服务注册/编排/消息总线，保留微服务拆分与统一入口收益，自托管 + MIT，价值主张自洽。
- **可审计性远超同类个人项目**：41+ 篇安全/分析文档 + 24 篇逐模块审计报告，风险闭环有基底。
- **协议基石扎实**：pymsg/libmsg 字节级兼容的 `[4B 长度][UTF-8 JSON]` 帧，支撑 v1/v2 双栈平滑迁移。
- **认证设计不弱**：PBKDF2 10 万轮、常数时间比较、RSA 签名 Token、Cookie 安全属性、fail-closed 边界。

## 关键风险（矛盾与缺证）
1. **评级名实不符**：称"B+→A-"，但 3 高危（WAF 失效 / core root RCE / 内部零信任缺失）均未核实修复。A- 更像目标而非现状。
2. **WAF 实际不拦截**：定位"唯一对外入口 + coraza"，但 `waf/rules/local/custom.conf` 的 6 条 SecRule 全被注释、无 `crs/` 目录 → 实际仅 TLS 终止 + 响应头，SQLi/XSS/命令注入零防护。
3. **core 本地 root RCE**：`parse_data_from_mq` 对 list/kill/exec 未鉴权即经 `/bin/sh -c` 执行，且 core 以 root 运行。这是致命项却被标 `human_review_pending`。
4. **内部零信任缺失**：pymsg 无握手/无鉴权/明文无 TLS，db `execute` 接受任意 action/table/where，完全依赖 127.0.0.1 隔离。
5. ~~**端口口径漂移**：MODULES.md 记 gateway:8443，README 记 8445，未同步~~ → ✅ 2026-08-10 已统一（ca:8443 → gateway:8445，含 MODULES.md/ARCHITECTURE.md/各模块 README）。
6. **密钥变量静默失效**：`.env.example` 写 `OPENAI_API_KEY`，代码读 `DEEPSEEK_API_KEY`。
7. **质量债**：v1/v2 双实现（db/db2、dnfs/dnfs2、pymsg/libmsg…）并存，无单元测试、无 CI（无 `.github/workflows`），回归与语义漂移风险高。
8. **部署半成品**：docker-compose 仅纳 3 服务，生产以 systemd 为准——容器化链路不完整。

## 缺证项（需人工复核）
- 3 高危是否在 2026-08-08 之后的提交中已修复（研究报告基于快照，文档未给代码证据）。
- docker-compose 补全状态；~~MODULES.md 端口同步状态~~（✅ 2026-08-10 已同步）。

## 优先级建议（收口路径）
- P0：启用 WAF 规则 + 补 `crs/`（直击高危 1）。
- P0：core MQ 落地 `verify_admin_token` 并降权运行（直击高危 2）。
- P1：内部服务加调用方鉴权 / pymsg 上 mTLS 或 Unix socket（直击高危 3）。
- P2：ai_review `/review` 加鉴权 + 结构化提示词；统一 `DEEPSEEK_API_KEY`；引入单元测试与 CI；双实现收口单轨。

## 成熟度判定
**个人/小团队可信内网自用 = 可用；暴露公网或多租户 = 不建议。** 文档完备度是亮点，代码整改完成度与测试覆盖是短板。
