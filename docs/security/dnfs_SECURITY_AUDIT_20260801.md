# dnfs 模块安全审计报告（Python 版）

**原始审计日期**: 2026-08-01  
**代码实证复核（最后更新）**: 2026-08-10  
**审计范围**: `dnfs/dnfs.py`（723 行）、`security_middleware.py`、`log_client.py`  
**代码版本**: dev_qing 分支  
**历史评级**: B（2026-08-01 审计）

---

> ## ⚠️ 废弃声明
>
> **Python dnfs 已废弃，生产环境实际运行的是 C++20 重写版 `dnfs2`**（见 [`dnfs2_SECURITY_AUDIT_20260808.md`](dnfs2_SECURITY_AUDIT_20260808.md)，评级 **A**）。`core/init.txt` 第 3 行启动的是 `dnfs2/_run.sh`，Python dnfs 不在运行清单内。
>
> 本审计仅覆盖 **Python dnfs 的历史代码状态**，用于溯源与和 dnfs2 的修复对照。**不要在线上启用 Python dnfs**——它的多数 P0/P1 安全问题未修复（见下方清单），且这些问题在 dnfs2 中已全部解决。

---

## 1. 模块定位与状态

dnfs = **Dendrachy Network File-System**，基于父子关系（`pid`）组织的树状节点服务，监听 `127.0.0.1:8006`，提供节点的增删改查，写操作需 gateway 签发的 Token。

- **Python dnfs（本审计对象）**：Flask + Waitress，历史实现，已废弃。
- **C++ dnfs2（生产实现）**：libhttp + libmsg + boost::json，接口兼容，安全基线显著提升（评级 A）。

接口（4 个 `POST`）：`get_notes`（无鉴权）、`add_note`、`delete_note`、`update_note`（后三者需 Bearer Token；add/update 写入前经 `ai_review` 内容审核）。

## 2. 架构与数据流

```
客户端 → waf:443 → ca:8443(认证) → gateway:8445(纯转发, loopback)
   → dnfs2:8006 ──pymsg──► db2:8005 (execute 路由, 落 SQLite Notes 表)
            └──HTTP──────► ai_review:8007 (add/update 写入前内容审核)
```

- **Token 验签**：dnfs 不持有密钥，统一转发 `gateway /whoami` 由 gateway 完成 RSA 验签 + 过期校验；gateway 不可达 → fail-closed 返回 503。
- **数据操作**：经 pymsg RPC 调用 db 服务 `_route: "execute"`，不直接连库（规避 SQL 注入）。
- **特殊节点（管理员专属）**：`content` 为 `{"_sp":"link|js|image|file",...}` 信封时前端差异化渲染；仅 `is_admin` 可发布。

## 3. 审计结论与评级

| 等级 | 原始审计(2026-08-03 验证) | 当前代码实证(2026-08-10) |
|------|---------------------------|---------------------------|
| P0 | 1 项，已修复 1 | 2 项：部分修复 1，未修复 1* |
| P1 | 2 项，已修复 2 | 5 项：未修复 5 |
| P2 | 2 项，未修复 2 | 5 项：全部未修复 |

\* P0-4（父节点删除 TOCTOU）在 Python dnfs 中仍**未修复**，但其"未先检查 `success`"的子问题在 dnfs2 中已修，仅残余 TOCTOU 窗口（需 db2 事务支持）。

**评级说明**：Python dnfs 历史评级为 **B**（基于 2026-08-01 仅 5 项发现的快照）。按当前代码实证的全量 16 项发现，若仍上线则应下调评级——其 P0-4（树结构完整性）、P1-6/7/8/9/11（请求体、配置一致性、父节点校验、启动等待、类型转换）等高危问题均未修复。**正确做法是不再启用 Python dnfs，统一使用 dnfs2。**

## 4. 完整发现清单（代码实证，2026-08-10）

> 编号沿用 `dnfs/README.md`「已知问题与风险」的权威枚举（P0-1~4 / P1-5~11 / P2-12~16，共 16 项）。
> 状态依据对 `dnfs/dnfs.py` 与 `log_client.py` 的逐行核对。
> **已合入代码注释（dnfs.py）并移出本节的项**：P0-1、P0-3、P1-5、P1-10（均为 ✅ 已修复/已解决），其安全决策见对应代码注释。本节仅保留未修复/部分修复的开放项，共 12 项。

### 🔴 P0 — 严重问题

| 编号 | 问题 | 状态 | 代码位置 | 说明 |
|------|------|------|----------|------|
| P0-2 | `review_content()` fail-closed 与返回类型不一致 | ⚠️ 基本修复 | `dnfs.py:156-202` | 异常路径（HTTPError/URLError/JSONDecodeError）均拒绝请求；但 HTTPError 且响应体为合法 JSON 时返回 **dict**（L196 `return json.loads(error_body)`），与成功路径返回的 **bool** 类型不一致，存在 `True != dict` → 一律拦截（仍 fail-closed，但类型契约不干净，建议清理） |
| P0-4 | 有子节点的父节点可能被删除（树结构完整性） | ❌ 未修复 | `dnfs.py:510-525` + `db/create_sqlite3.sql:13-19` | 应用层有子节点检查（`if len(notes['data']) > 0: return 400`），但：① **数据库层 `Notes.pid` 无外键约束**；② 子节点查询与删除间存在 **TOCTOU 竞态**；③ `notes['data']` 在 **未先确认 `success`** 时访问（L519），`success=False` 时抛 KeyError |

### 🟠 P1 — 高危问题

| 编号 | 问题 | 状态 | 代码位置 | 说明 |
|------|------|------|----------|------|
| P1-6 | 请求体大小无限制 | ❌ 未修复 | `dnfs.py:723` | `serve(app, host=_ADDR, port=_PORT)` 未配置 `max_request_body_size`，建议与 WAF 对齐设 10MB |
| P1-7 | `log_client.py` 配置与主模块不一致 | ❌ 未修复 | `log_client.py:32-33` | 日志客户端直读 `DB_HOST`/`DB_PORT` 环境变量，不拉取 confs，与主模块配置源不一致（dnfs2 已复用主配置修复） |
| P1-8 | `update_note` 改 pid 时未校验目标父节点存在 | ❌ 未修复 | `dnfs.py:571-673` | `update_note` 直接更新 `pid`，当 `pid≠0` 时不检查父节点是否存在（dnfs2 已用 `_note_exists` 校验） |
| P1-9 | db RPC 客户端未启用启动等待 | ❌ 未修复 | `dnfs.py:133` | `_rpc = RpcClient(_DBV2_ADDR, _DBV2_PORT, "dnfs")` 无 `retry_on_refused=True`；db 未就绪时相关 API 直接失败（dnfs2 已加 `retry_on_refused=true`） |
| P1-11 | `note_id` 缺少强制类型转换 | ❌ 未修复 | `dnfs.py:474,577` | 仅类型提示 `note_id: int = data.get('id')` / `id: int = data.get('id')`，依赖 SQLite 类型亲和性隐式匹配（dnfs2 已用 `get_int()` + `is_int64()` 强校验） |

### 🟡 P2 — 中危问题

| 编号 | 问题 | 状态 | 代码位置 | 说明 |
|------|------|------|----------|------|
| P2-12 | 错误响应泄露内部信息 | ❌ 未修复 | `dnfs.py:371,395,400,420,523,540,561,615` | 错误消息拼接 `request.remote_addr` 与用户名返回客户端（如 `未授权访问，从{ip}`、`特殊节点仅管理员可发布，用户{user}，从{ip}`）；应仅记服务端日志（dnfs2 已清除此类泄露） |
| P2-13 | CORS 配置过于宽松 | ❌ 未修复 | `dnfs.py:309` | `CORS(app)` 默认允许所有来源（`*`），建议限制为实际前端域名 |
| P2-14 | `_call_dbv2` 返回值访问前未统一检查 success | ❌ 未修复 | `dnfs.py:387,444,519,537` | 多处直接访问 `notes['data']` 而不先确认 `success==True`，db 业务错误时可能 KeyError |
| P2-15 | `pid` 参数判断逻辑不严谨 | ❌ 未修复 | `dnfs.py:432` | `if not pid: pid = 0` 将空字符串/None/0 等非法输入默默转为 0，掩盖参数错误 |
| P2-16 | 遗留注释代码 | ❌ 未修复 | `dnfs.py:17,491-507,636-648,677-711,715-718` | 大量注释掉的旧版 SQLAlchemy 直连逻辑与已废弃接口，影响可读性 |

## 5. 历史审计记录（2026-08-01 / 2026-08-03，作溯源）

原始审计仅覆盖 5 项发现，其验证结论如下（与第 4 节对应编号映射）：

| 原编号 | 原问题 | 状态 | 映射至本章 |
|--------|--------|------|-----------|
| P0-1 | Token 无 RSA 签名验证 | ✅ 已修复 | 已合入代码注释 (dnfs.py) |
| P1-1 | Token 过期校验不严谨 | ✅ 已修复 | 已合入代码注释 (dnfs.py) |
| P1-2 | is_admin 权限完全依赖 Token 字段 | ✅ 已修复 | 已并入 P0-1（gateway 服务端基于 Admin 白名单判定） |
| P2-1 | AI 审核 fail-closed 实现需验证 | ❌ 未修复（类型不一致） | P0-2 |
| P2-2 | 节点删除无确认机制 / 无速率限制 | ❌ 未修复 | P0-4（及一般加固项） |

原始审计的「新增安全机制」表中以下项经代码实证仍然成立：
- Token 验签委托 gateway（`dnfs.py:312-351`）；fail-closed 503（`dnfs.py:336-339`）
- 配置白名单合并（`dnfs.py:55-63`）；配置完整性校验（`dnfs.py:100-112`）
- 特殊节点审核脱敏（`dnfs.py:273-286`）
- 统一异常处理 `_handle_exp`（`dnfs.py:294-305`）
- 异步日志 fail-safe（`log_client.py:66-72,147-148`）

## 6. db 错误处理一致性矩阵（Python dnfs）

Python dnfs 多处直接访问 `notes['data']` 而未先确认 `success`，与 dnfs2 的「db 业务错误统一返回 500」形成对比：

| 位置 | 查询目的 | 行为 | 风险 |
|------|---------|------|------|
| `add_note` 父节点检查 | L387 `notes['data']` | 未查 success | success=False 时 KeyError |
| `get_notes` | L444 `notes['data']` | 未查 success | success=False 时 KeyError |
| `delete_note` 子节点检查 | L519 `notes['data']` | 未查 success | success=False 时 KeyError |
| `delete_note` 所有权检查 | L537 `notes['data']` | 未查 success | success=False 时 KeyError |

> dnfs2 已将全部 select 的 db 业务错误统一返回 500（见 `dnfs2_SECURITY_AUDIT_20260808.md` §6 矩阵），此项在 dnfs2 已闭合。

## 7. 与 dnfs2 的修复对照

| 本章编号 | Python dnfs 状态 | dnfs2 状态 |
|---------|-----------------|-----------|
| P0-2 | ⚠️ 基本修复 | ✅ 已修复（返回类型干净） |
| P0-4 | ❌ 未修复 | ⚠️ success 检查已修，TOCTOU 残余（L-2） |
| P1-6 | ❌ 未修复 | ✅ 已修复（`setMaxBodySize(10MB)`） |
| P1-7 | ❌ 未修复 | ✅ 已修复（日志复用主配置） |
| P1-8 | ❌ 未修复 | ✅ 已修复（pid≠0 校验父节点） |
| P1-9 | ❌ 未修复 | ✅ 已修复（`retry_on_refused=true`） |
| P1-11 | ❌ 未修复 | ✅ 已修复（`get_int()` 强类型） |
| P2-12 | ❌ 未修复 | ✅ 已修复（清除 IP/用户名泄露） |
| P2-13 | ❌ 未修复 | 记录（前端架构限制 L-7） |
| P2-14 | ❌ 未修复 | ✅ 已修复（`_call_db` 统一查 success） |
| P2-15 | ❌ 未修复 | ✅ 已修复（严格类型 + 负数校验） |
| P2-16 | ❌ 未修复 | ✅ 已清理 |

**结论**：dnfs2 基本闭合了 Python dnfs 的全部 P0/P1 及大部分 P2；Python dnfs 的未修复项无需在 Python 版继续投入，统一以 dnfs2 为准。

## 8. 修复路线图（仅针对需要继续维护 Python dnfs 的极端场景）

### 立即（如被迫启用 Python dnfs）
- [ ] P0-4：Notes 表 `pid` 加 `FOREIGN KEY ... REFERENCES Notes(id) ON DELETE RESTRICT`；应用层先查 `success` 再访问 `data`
- [ ] P1-6：`serve()` 配置 `max_request_body_size=10MB`
- [ ] P2-14：封装统一的 db 调用检查 `success`

### 短期
- [ ] P1-7：日志客户端复用 confs 主配置
- [ ] P1-8：`update_note` 改 pid 前校验父节点存在
- [ ] P1-9：`RpcClient` 启用 `retry_on_refused=True`
- [ ] P1-11：`note_id` 显式 `int()` 转换 + 校验
- [ ] P2-12：错误消息去除 IP/用户名，仅记日志

### 中期
- [ ] P2-13：CORS 限制为实际前端域名
- [ ] P2-15：`pid` 参数严格校验（拒绝空串/None）
- [ ] P2-16：清理遗留注释代码
- [ ] P0-2：清理 `review_content` 返回类型不一致

> **推荐替代方案**：直接弃用 Python dnfs，全部流量走 dnfs2（已在 `core/init.txt` 生效）。
