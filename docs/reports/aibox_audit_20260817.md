# aibox 模块安全与质量审计报告

- **日期**：2026-08-17（第一轮）/ 2026-08-18（第二轮复审）/ 2026-08-18（第三轮：遗留复核 + v3.4 专审）/ 2026-08-18（第四轮：3 路并行审读 + Batch H 修复）
- **审计基线**：第一轮 HEAD `ce142fe`（Batch A–D 后）→ 第二轮 HEAD `177a9ac`（Batch E 修复后）→ 第三轮 HEAD `aa15841`（L4a/b/c 落地后）→ 第四轮 HEAD `d1f780e`（Batch G + SOCKS5 验证后）
- **审计范围**：`aibox.py`（~3310 行）+ `llm_client.py` + `tools/`（7 文件）+ `subsystems/`（6 文件）+ `common/`（5 文件）
- **方法**：第一轮逐文件代码审读 + PoC 动态验证；第二轮 Batch E 修复复审 + 全量重审 + 6 处关键发现实证抽查；第三轮遗留项复核 + v3.4 专审；第四轮 3 路并行审读 + 主审逐条复核 + Linux PoC 实证（6/6）

---

## 一、总体结论

**第一轮**（基线 ce142fe）：架构设计（分层、参数化 SQL、密钥隔离、消息回滚）整体健康，但**命令执行链存在一条系统性断裂**：实际运行路径绕过了精心设计的执行器，导致 1MB 硬约束、超时、中断三项保护同时失效。

**第二轮**（基线 177a9ac）：Batch E 五项修复（H1/H2/H3/M1/M2）中四项确认修复到位（PoC 5/5 验证），**M2 修复不完整**（超时路径跳过工具属性清理，僵尸输出问题仍存）。第二轮全量重审新发现 **1 高危 + 9 中危 + 若干低危**，其中三项与 Batch E 修复直接相关（reader 线程泄漏 / 子代理超时盲区 / M2 残留），其余为存量问题（代理密码明文落盘、drain 线程泄漏、事件循环阻塞等）。详见"八、第二轮复审"。

---

## 二、高危发现（3 项）

### H1：`run_command` 实际路径绕过注入的 executor——三重保护同时失效

证据链：
- `aibox.py:1012` 将 `command_executor` 注入 `CommandLineTool`
- 但 `tools/command_tool.py:86-219` 的 `execute_streaming` 自带一套 **150 行的重复 Popen 实现**，`self.executor` 全程未使用（死注入）
- `tools/registry.py:71-86` 检测到 `execute_streaming` 即走该路径——**生产 100% 命中**

后果：

| 保护 | 设计位置 | 实际状态 |
|---|---|---|
| 1MB 输出上限（硬约束） | `executors.py:69` | **失效**——command_tool 的 `output_lines` 无限累积 |
| `cancel()` 中断 | `executors.py:76-86` 只 kill `_current_process` | **失效**——实际进程引用只在 command_tool 局部变量，三层中断的第三层对 run_command 不生效 |
| 超时/截断统一语义 | executors | 旁路（见 H2） |

附带问题：`msvcrt.kbhit()` 检查的是**当前进程**键盘而非子管道（`command_tool.py:123-125` 无意义代码）；循环内重复 `import select`。

### H2：超时机制对无输出命令完全失效（PoC 已证）

`readline()` 是阻塞调用，超时检查只在**两次 readline 之间**执行。无输出命令会让 readline 阻塞到进程自然退出：

```
PoC：timeout=3s，命令 powershell Start-Sleep 15（无 stdout 输出）
结果：readline blocked 15.2s (timeout was 3s)
VERDICT: timeout mechanism ineffective for silent commands: True
```

两处同病：`command_tool.py:106-165` 与 `executors.py:97-113`。`--once` 模式有 `--once-timeout` 兜底，**WebSocket 模式无兜底**。

### H3：`stdin=subprocess.PIPE` 从不写入也不关闭——死锁放大器

`executors.py:156`、`command_tool.py:93`：等待 stdin 的命令（`cat` 无参、交互式 `python`、`ssh`）永不退出 → 与 H2 叠加：**readline 永久阻塞 + 超时检测永不执行 + cancel 杀不到进程** = 工具线程永久挂起。应为 `stdin=subprocess.DEVNULL`。

---

## 三、中危发现（4 项）

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| M1 | **heredoc 定界符注入** | `basic_tools.py:179` | `content` 含独立行 `AIBOX_EOF` 时 heredoc 提前终止，**后续行成为 shell 命令执行**。AI 写文件内容是不可信输入（可被网页内容/用户消息诱导）。修复：定界符唯一化（检测冲突则换 `AIBOX_EOF_2` 递增） |
| M2 | 工具流式转发循环可永久挂起 | `registry.py:90-107` | `while thread.is_alive() or not empty()` + `get(0.1)`——H3 场景下永不退出；`join(5)` 超时后返回但 daemon 线程继续向输出队列写（僵尸输出混入后续对话流） |
| M3 | web 工具无协议/目标校验 | `basic_tools.py:315-338` | `url` 未限制 scheme，`curl file:///etc/shadow`、内网地址均可达（SSRF）。本地单用户产品风险有限，建议白名单 http/https |
| M4 | shlex.quote POSIX 转义在 Windows 失效 | `basic_tools.py:99-112` 等全部指引命令 | 单引号在 cmd.exe 无效，Windows 下 `head/sed/grep` 指引命令执行异常。Linux 部署（树莓派）不受影响 |

---

## 四、低危发现（3 项）

| # | 问题 | 位置 |
|---|---|---|
| L1 | `get_pending_memos` 的 `LIMIT {limit}` f-string 拼接 | `db.py:417`——当前调用链 limit 均为内部 int，无外部直达；防御性参数化即可 |
| L2 | `check_path_safety` 仅查大小，无路径边界 | `base.py:96-110`——by design（AI 可全盘读写，本地单用户），已确认设计意图 |
| L3 | `executors.execute()` finally kill 后未 `wait()` | `executors.py:205-207`——Windows 句柄短暂滞留 |

**做得好的**：SQL 全面参数化（LIKE 通配符正确转义 `db.py:200-209`）、API key 零日志泄漏、WAL 已启用、消息回滚/tool 配对修复、`_msg_lock`/`_process_lock` 使用正确、工具实例按连接隔离。

---

## 五、架构合规矩阵

| 硬约束 | 状态 |
|---|---|
| WebSocket :8765 | ✅ |
| 依赖单向 `common←subsystems←tools←aibox` | ✅ |
| Worker 仅 7 执行工具 | ✅ |
| 工具输出 ≤1MB + 超时 + 中断 | ❌ **H1/H2/H3 三项全破** |
| prompt 三级排序加载 / sqlite 线程安全 | ✅ |

---

## 六、修复建议（一批搞定 H1/H2/H3，约 ±120 行）

1. **删除 command_tool.py 的重复 Popen 实现**，`execute_streaming` 改为一行委托 `self.executor.execute_streaming(output_queue, ...)` → 1MB、cancel、统一语义全部自动恢复
2. **executors.py 读取循环改线程化**：后台 reader 线程做阻塞 `readline` 入队列，主循环带超时轮询队列——跨平台解决 H2
3. `stdin=subprocess.DEVNULL`（H3）
4. M1 定界符唯一化 + M2 转发循环加总超时，顺手可修

---

## 七、修复落地（Batch E，2026-08-17）

五项修复（H1/H2/H3/M1/M2）已全部实施并通过验证：

| # | 修复 | 位置 | 状态 |
|---|---|---|---|
| H1 | `CommandLineTool.execute_streaming` 改为纯委托 `executor.execute_streaming`，删除 150 行重复 Popen（-165 行） | `tools/command_tool.py` | ✅ |
| H2 | `_read_with_limit` 线程化：后台 reader 线程阻塞 `readline` 入队，主循环 0.1s 轮询 + 超时/1MB 检查；shell 已死但 reader 无 EOF 时 1s 宽限排空（Windows shell=True 孙进程持管道场景） | `subsystems/executors.py` | ✅ |
| H3 | 两处 Popen 均改 `stdin=subprocess.DEVNULL` | `subsystems/executors.py` | ✅ |
| M1 | heredoc 定界符唯一化：content 含独立行与 `AIBOX_EOF` 相同时递增 `_X` 后缀直到唯一 | `tools/basic_tools.py` | ✅ |
| M2 | registry 转发循环加 3600s 总超时兜底，超时后放弃等待并返回错误信息 | `tools/registry.py` | ✅ |

### PoC 回归验证（`aibox/tests/_poc_audit_regression.py`，5/5 PASS）

```
[PASS] PoC-1 无输出命令超时(H2): 3.0s 返回（修复前阻塞15s+），结果含超时: True
[PASS] PoC-2 stdin等待命令不挂起(H3): 0.2s 返回（修复前永久挂起）
[PASS] PoC-3 1MB输出截断: 0.2s，结果 1.00MB，含截断标记: True
[PASS] PoC-4 cancel中断: cancel 后 3.0s 结束（修复前杀不到进程需等满120s）
[PASS] PoC-5 工具层1MB贯通(H1): 0.2s，工具层结果 1.00MB（修复前无限累积）
```

PoC-2 说明：`stdin=DEVNULL` 下等待输入的命令立即收到 EOF 快速退出（0.2s），优于按超时返回；判定标准为"不永久挂起"。

### 单元测试

aibox 全量单测 **264 passed**（零回归）。

### 遗留项（本轮未处理）

- M3（web 工具 scheme/目标校验）、M4（Windows 下 shlex.quote 转义失效）、L1/L3（防御性加固）——低优先级，待后续批次
- 架构合规矩阵"工具输出 ≤1MB + 超时 + 中断"一项修复后为 ✅

---

## 八、第二轮复审（2026-08-18，HEAD `177a9ac`）

方法：Batch E 修复质量复审 + 全量重审（3 路并行审读 + 主审对 6 处关键发现逐一实证抽查）。

### 8.1 Batch E 修复复审结论

| # | 修复 | 复审结论 |
|---|---|---|
| H1 | command_tool 纯委托 executor | ✅ 到位（PoC-5 验证 1MB 贯通） |
| H2 | reader 线程 + 超时轮询 | ✅ 到位（PoC-1 验证），**但有两个残余**：N4 reader 线程泄漏、N5 子代理超时盲区 |
| H3 | stdin=DEVNULL | ✅ 到位（PoC-2 验证） |
| M1 | heredoc 定界符唯一化 | ✅ 到位 |
| M2 | registry 转发总超时 | ⚠️ **不完整**（见 N6） |

### 8.2 第一轮遗留项状态

| # | 问题 | 状态 |
|---|---|---|
| M3 | web 工具无 scheme 校验 | 未修复（实证：`basic_tools.py:319+` url 未限制，`file://` 等可达） |
| M4 | shlex.quote POSIX 转义 Windows 失效 | 未修复（实证：`sed -r`/`grep -oP` 指引，Linux 部署不受影响） |
| L1 | `LIMIT {limit}` f-string | 未修复，且**新发现同型第二处**（`db.py:417` 与 `db.py:459` `get_immediate_memos`） |
| L2 | check_path_safety 无路径边界 | 维持 by design（本地单用户，AI 可全盘读写为设计意图） |
| L3 | execute() finally kill 后未 wait() | 未修复（实证：`executors.py:236-241` 无 wait；`cancel()` 同样无 wait） |

### 8.3 新发现——高危（1 项）

#### N1：SOCKS5 代理密码明文持久化到配置文件

`common/config.py:238-251`（实证）：`SetProxyTool.execute` 默认 `save=True` → `set_proxy` 将 `password` 明文写入 `_config["proxy"]["socks5"]` → `_save_config` 用 `json.dump` 全量落盘 `~/.aibox/config.json`。与 api_key 走环境变量的隔离设计形成反差，任何能读该文件的进程可获取代理凭据。

修复建议：密码不落盘（仅内存）或走系统凭据库；必须落盘时对称加密 + `os.chmod(0o600)`；`save` 默认改 `False`。

### 8.4 新发现——中危（9 项）

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| N2 | drain_thread 每次工具调用泄漏一个永不退出的线程 | `aibox.py:1232-1243`（实证） | 循环条件只查 cancel 事件不查队列空，注释与实现不符；闭包持 output_callback（含 websocket）阻碍 GC；长期运行累积线程/内存 |
| N3 | 同步 threading.RLock 在 async 协程中阻塞事件循环 | `aibox.py:1855-1902, 2275, 2376` | `/new` 的 2s 轮询竞态窗口后仍可能 `with self._msg_lock:` 冻结整个事件循环 → 服务假死 |
| N4 | reader 线程泄漏（H2 残余） | `subsystems/executors.py:130-144` | 宽限 1s break 后 reader 仍阻塞 readline（Windows shell=True 孙进程持管道），stdout 未 close、reader 未 join，常驻服务累积 fd 与线程 |
| N5 | --once 超时对子代理完全无效（H2 盲区） | `aibox.py:3052-3058, 638-649` | Timer 到点只 `manager.cancel()` 父代理；子代理独立 `_cancel_event`/独立 executor/独立 HTTP client，cancel 不传播，进程挂起至子代理自行结束 |
| N6 | registry 超时路径跳过工具属性清理（M2 残余） | `tools/registry.py:99, 116`（实证 L114-120） | 两个超时 return 均跳过 `streaming_output=False`/`output_queue=None`；daemon 线程继续写实例属性 `tool.output_queue`，该属性被后续调用覆盖到新会话队列 → 僵尸输出串流 |
| N7 | FTS5 关键词注入 | `subsystems/db.py:179` | 关键词内 `"` 未转义（应翻倍），可破坏短语边界改变 AND/OR 语义；语法错误时回退 LIKE，影响有限 |
| N8 | 流式输出旁路 TOOL_MAX_RESULT_LENGTH | `tools/db_tools.py:97-98` + `registry.py:104-110` | 超长记忆 content 经流式通道完整推到前端，100000 字符上限仅约束非流式返回值 |
| N9 | prompt 注入存储 | `tools/prompt_tools.py:64, 127, 227` | Agent 可控内容原样写入 `00_IDENTITY.md`/`99_SOUL.md`/`100_STATUS.md` 并在后续会话作为系统提示词加载，形成持久化注入；update_soul 可自我强化 |
| N10 | mark_as_triggered TOCTOU 竞态 | `subsystems/db.py:509-570` | 跨连接先读后写，并发触发 trigger_count 失真；建议 BEGIN IMMEDIATE 同事务读写 |

### 8.5 新发现——低危（7 项）

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| N11 | debug 日志打印工具敏感参数 | `tools/registry.py:66, 126` | debug=3 时 set_proxy 的 password 明文进 `~/.aibox/ai.log`（日志无 0600） |
| N12 | _load_persona 路径遍历（限 .md） | `aibox.py:527-543` | persona 参数 LLM 可控未净化，可读 SKILLS_DIR 外 .md 文件并注入子代理 |
| N13 | broadcast 裸 except + unregister KeyError 逃逸 | `aibox.py:2076-2082, 1971-1973` | 吞 CancelledError；并发注销时 `remove` 抛 KeyError；建议 `discard` + `except Exception` |
| N14 | _processing_tasks 按 ws id 覆盖，旧任务失联 | `aibox.py:2728-2742` | 连接关闭只能 cancel 最后一个任务；handle_command 不受 is_processing 保护 |
| N15 | llm_client 异常含完整响应体 | `llm_client.py:172-174` | 某些网关 4xx 页回显 Authorization 头 → 密钥进异常栈；建议截断 500 字符 |
| N16 | 历史索引裸 except + 归档文件名撞名覆盖 | `subsystems/conversation_history.py:60-66, 135-137` | Ctrl+C 被吞后空索引覆盖原文件；时间戳粒度不足同秒覆盖 |
| N17 | InstallSkillTool skill_path 无校验 | `tools/skill_tools.py:252-304` | 可引导复制任意敏感文件进技能目录，经 search_skills show_content 预览泄露 |

杂项（低危，随批处理）：`executors.py:209/301/338` 裸 except 应收窄为 TimeoutExpired；`db.py` 多处 `with sqlite3.connect` 不显式 close（CPython 下无害，PyPy 下句柄延迟）；`config.py:31-34` AI_CONFIG_PATH 可重定向配置加载（本地单用户风险有限）；skill_manager 技能目录代码执行面维持 by design（建议文档化可信边界）。

### 8.6 更新后的架构合规矩阵

| 硬约束 | 第一轮 | 第二轮 |
|---|---|---|
| WebSocket :8765 | ✅ | ✅ |
| 依赖单向 `common←subsystems←tools←aibox` | ✅ | ✅ |
| Worker 仅 7 执行工具 | ✅ | ✅ |
| 工具输出 ≤1MB + 超时 + 中断 | ❌ H1/H2/H3 全破 | ⚠️ **executor 链已恢复**；残余：流式 db_tools 旁路长度上限（N8）、子代理超时无效（N5）、reader 泄漏（N4） |
| prompt 三级排序加载 / sqlite 线程安全 | ✅ | ⚠️ sqlite 主流程安全；mark_as_triggered 竞态（N10）、FTS5 注入（N7）待修 |

### 8.7 Batch F 修复建议（按优先级）

1. **F1（高）**：N1 代理密码脱敏——`set_proxy` 落盘前抹除 password 字段 + `save` 默认 False
2. **F2（中）**：N6 registry 超时路径补清理（两个 return 前置 `tool.streaming_output=False; tool.output_queue=None`）+ N4 reader 线程收尾（break 后 `stdout.close()` + `reader.join(2)`）
3. **F3（中）**：N5 子代理 cancel 传播——`run_full_subagent` 传递父 `_cancel_event`，每轮 think 前检查
4. **F4（中）**：N2 drain_thread 加 sentinel 退出 + N3 reset_conversation 移出事件循环（run_in_executor）
5. **F5（中）**：N7 FTS5 转义 + N8 流式截断 + N10 同事务读写 + 顺带修 L1（两处 LIMIT 参数化）+ L3（finally wait）
6. **F6（低）**：N9 prompt 内容过滤/确认门槛 + N11 日志脱敏 + N12 persona 路径校验 + N13/N14/N15/N16/N17 择机处理 + M3/M4 视部署平台决定

### 8.8 Batch F 修复进展

**F1 ✅ 已落地（2026-08-18）**——N1 + N11 一并修复：

- `common/config.py`：`set_proxy` 新增 `password_env` 参数；`_save_config` 落盘前 deepcopy 脱敏（明文密码强制置 None，纵深防御，任何保存路径均拦截）；`get_proxy` 按"内存明文优先 → password_env 查环境变量"解析，下游（llm_client / web 工具 `_proxy_curl_args`）无感知；加载时检测旧版明文自动迁移（内存保留供本会话 + 立即重写脱敏文件，一次性清除存量）
- `tools/proxy_tools.py`：`SetProxyTool` 新增 `password_env` 参数，结果显示"密码来源"（环境变量 / 仅本次会话 / 无）
- `tools/registry.py`（N11）：debug 日志对 `password/api_key/token/secret/authorization` 参数打码
- 测试：新增 `tests/test_config.py` 3 例（落盘无明文 / password_env 解析 / 旧配置迁移），全量 267 passed
- 行为变化：已有明文密码的配置重启后代理需重设密码或改用环境变量（迁移日志有提示）——清除存量明文的必然代价

**F2 ✅ 已落地（2026-08-18）**——N6 + N4 一并修复：

- N6（registry 超时路径补清理）：两个超时 return（deadline 命中、join(5) 超时）统一改为 `timed_out` 标记 + 出口统一执行 `tool.streaming_output=False` / `tool.output_queue=None`；僵尸 daemon 线程的 `send_line` 落到 None 上不再外写，消除僵尸输出串流路径。`stream_tool_timeout` 提为实例属性（默认 3600s，测试可调）
- N4（reader 线程收尾，**含二次修正**）：初版按审计建议"break 后 `stdout.close()` + `reader.join(2)`"实现，PoC 回归实证 **close 会阻塞**——CPython 流对象内部有锁，readline 持锁阻塞时 `close()` 一同挂起直到 readline 返回（PoC-1 从 3s 退化到 15.2s，恰为命令 Start-Sleep 时长）。修正后策略：reader 已正常退出（EOF）→ 显式 close 释放管道 fd；reader 仍阻塞 → 只做 `join(timeout=2)` 后放弃（daemon 兜底，孙进程退出收 EOF 后自然结束，fd 随管道消亡回收），主流程及时返回
- **审计建议修正记录**：8.3 节 N4 与 8.7 节 F2 中"close stdout 可中断阻塞 readline（CPython 行为）"的表述**不成立**，已按实测修正——跨线程中断阻塞中的管道 read 无便携方案，正确做法是控制泄漏窗口而非强行中断
- **测试**：新增 `tests/test_f2_fixes.py` 5 例（N6 超时/正常清理 ×2、N4 及时返回 ×2 + EOF 后 close ×1），PoC 回归 5/5（PoC-1/4 恢复 ~5s：超时 3s + join 宽限 2s），全量 272 passed

---

## 九、第三轮审计（2026-08-18，HEAD `aa15841`）

方法：前两轮遗留项逐条实证复核 + v3.4 新增代码（多模型档案 / L4a/b/c 失败转移，`llm_client.py`）全量审读 + 测试基线确认（全量 **355 passed**）。

### 9.1 遗留项修复状态复核

| # | 问题 | 状态 | 实证 |
|---|---|---|---|
| L3 | execute() kill 后未 wait() | ✅ **已修** | `executors.py:222-230`：kill/terminate → `wait(timeout=2)` → 超时再 kill 兜底 |
| N17 | InstallSkillTool 任意文件复制 | ✅ **已消解（设计变更）** | 工具重构为纯指引（只返回安装说明文本，不再执行 shell/复制）；`CreateSkillTool` 增加 skill_name 正则校验（`^[A-Za-z0-9_-]+$`） |
| N1/N11 | 代理密码脱敏 / 日志打码 | ✅ 在位 | F1 修复无回归 |
| N4/N6 | reader 收尾 / registry 超时清理 | ✅ 在位 | F2 修复无回归 |
| N2 | drain 线程泄漏 | ❌ 未修 | `aibox.py:1280-1303`：循环条件仍只查 cancel 事件，注释"自动退出"与实现不符，每次工具调用泄漏一个常驻线程 |
| N3 | `/new` 阻塞事件循环 | ❌ 未修 | `aibox.py:2325`：2s 轮询竞态窗口后仍 `with self._msg_lock:`（同步 RLock acquire 于事件循环线程） |
| N5 | 子代理 cancel 不传播 | ❌ 未修 | `aibox.py:643-654`：子代理独立 `_cancel_event`/executor/client；`--once` Timer（3110-3113）只 cancel 父代理 |
| N7 | FTS5 引号未翻倍 | ❌ 未修 | `db.py:178`：`f'"{kw}"'` 直接拼，关键词含 `"` 破坏短语边界 |
| N8 | 流式旁路长度上限 | ❌ 未修 | `registry.py:120-129`：`("line", content)` 无限转发；`aibox.py:1326` 的 `MAX_RESULT_LENGTH` 只管回喂 LLM 的最终 result |
| N9 | prompt 注入存储 | ❌ 未修 | `prompt_tools.py`：confirm 默认 True，Agent 可控内容仍直写 00/99/100 文件 |
| N10 | mark_as_triggered TOCTOU | ❌ 未修 | `db.py:509-525`：跨连接先 `get_memo` 后 UPDATE，无 BEGIN IMMEDIATE |
| N12 | _load_persona 路径遍历 | ❌ 未修 | `aibox.py:529-545`：persona LLM 可控，`os.path.join(SKILLS_DIR, name + ".md")` 无边界校验 |
| N13 | broadcast 裸 except | ❌ 未修 | `aibox.py:2131`：`except:` 吞 CancelledError |
| N14 | _processing_tasks 覆盖 | ❌ 未修 | `aibox.py:2788`：按 ws id 覆盖登记；先完成任务的 done_callback 会误 pop 后续任务的登记 |
| N15 | 异常含完整响应体 | ⚠️ 部分改善 | 瞬时路径已截断 `[:200]`（163/213 行）；**非瞬时路径仍全量**（`llm_client.py:215-216`） |
| N16 | 历史索引裸 except / 同秒覆盖 | ❌ 未修 | `conversation_history.py:64` 裸 `except:`；136 行归档文件名秒级时间戳 |
| M3/M4 | web scheme 校验 / shlex Windows | ❌ 未修 | 维持原判（Linux 部署不受影响） |
| L1 | LIMIT f-string | ❌ 未修 | `db.py:417, 459` 两处在位（内部 int 直达，防御性待修） |

### 9.2 v3.4 新代码审计（多模型档案 + L4 失败转移）

**设计评价：整体高质量**。免密端点与密钥隔离（`allow_key_file=False` 防跨厂商凭据泄漏）、用户中断绝不转移（cancel 广播全链 + `_cancelled` 门控）、流式仅首事件前重放（`produced_any` 门控）、fallback 链一层不递归 + 自引用/悬空引用硬性报错、构造失败跳过不阻断主档案——关键决策均正确且有测试锚定（`test_models_profile.py` 约 60 个 L4 用例）。

新发现（P 系列）：

| # | 级别 | 问题 | 位置 | 说明 |
|---|---|---|---|---|
| P1 | 中 | 非瞬时 HTTP 错误响应体无截断（N15 残留） | `llm_client.py:215-216` | `RuntimeError(f"HTTP {code}: {response.text}")` 全量响应体进异常栈；部分网关 4xx 页回显 Authorization 头 → 密钥进日志。与瞬时路径 `[:200]` 不一致，建议同样截断 |
| P2 | 中 | Retry-After 无上限 | `llm_client.py:290` | `max(1.0, float(v))` 无上界——异常/恶意服务端 `Retry-After: 999999` 使档案冷却 11.5 天。建议 cap（如 `min(v, max(cooldown_seconds, 3600))`） |
| P3 | 低中 | fallback 档案缺 model 不校验 | `llm_client.py:671-673` | 主档案经 `_require_model` 硬性报错，fallback 档案直接 `fb_profile.get("model")` → model=None → API 400 → 表现为神秘 None 而非清晰配置错误。建议构造链时同样校验 |
| P4 | 低 | FailoverClient 非线程安全 | `llm_client.py:321-334` | `_cancelled`/`_call_started`/`_stats` 无锁。当前调用面无实际并发（compressor 在 `_msg_lock` 内同步调用、子代理独立 client），但 cancel（WebSocket 线程）与 generator 启动时 `_cancelled = False` 复位存在理论竞态窗口。建议注释声明"非并发契约"或加锁 |
| P5 | 低 | `LLMClient._cancelled` 死状态 | `llm_client.py:52, 76` | 只写不读：cancel() 置位后无消费方也从不清除，真正判断靠 FailoverClient 自身标志。建议删除或在底层 client 消费 |
| P6 | 低 | fallback 列表重复项不查重 | `llm_client.py:660-673` | `["kimi","kimi"]` 会在链内构造两个同名 client，冷却键同名冲突、重复撞同端点；影响有限 |
| P7 | 信息 | timeout_budget 仅在尝试间检查 | `llm_client.py:363-366, 404, 443` | 预算不截断单档案进行中的阻塞读（单次仍可超时到 timeout）。与文档"限制整条链尝试的总秒数"有细微出入，可接受，建议文档注明"尝试间预算" |

### 9.3 架构合规矩阵（第三轮）

| 硬约束 | 状态 |
|---|---|
| WebSocket :8765 | ✅ |
| 依赖单向 `common←subsystems←tools←aibox` | ✅ |
| Worker 仅 7 执行工具 | ✅ |
| 工具输出 ≤1MB + 超时 + 中断 | ⚠️ executor 链 ✅；残余：N8 流式旁路、N5 子代理超时、N2 drain 泄漏 |
| prompt 三级排序 / sqlite 线程安全 | ⚠️ N7/N10 待修 |
| 多模型档案凭据隔离（v3.4 新增维度） | ✅ 跨厂商 Key 不互发、免密端点显式声明 |

### 9.4 Batch G 修复建议（按优先级）

1. **G1（中）**：P1 非瞬时路径响应体截断 `[:200]` + P2 Retry-After 上限 cap——两处各一行改动
2. **G2（中）**：P3 fallback 档案补 `_require_model` 校验（缺 model 的 fallback 跳过并告警，与缺 Key 行为对齐）
3. **G3（中）**：N5 子代理 cancel 传播（`run_full_subagent` 传递父 `_cancel_event`，每轮 think 前检查）+ N2 drain 线程加 sentinel/空队列退出
4. **G4（中低）**：N3 `/new` 的 `reset_conversation` 移入 `run_in_executor` + N8 registry 转发行截断 + N12 persona 路径边界校验（`os.path.realpath` 前缀检查）
5. **G5（低）**：N7 FTS 引号翻倍 + N10 BEGIN IMMEDIATE + L1 LIMIT 参数化 + N13 `except Exception`/`discard` + N14 任务登记改按 task id 集合 + N16 收窄 except + 归档文件名加毫秒/序号
6. **G6（低/择机）**：P4 并发契约注释或加锁 + P5 死状态清理 + P6 fallback 去重 + N9 prompt 更新确认门槛 + M3/M4 视部署平台决定

### 9.5 测试基线

全量 **355 passed**（21.8s，Windows 开发机 `py -3`），含 L4 失败转移约 60 用例、Batch E PoC 回归 5/5、Batch F1/F2 修复测试 8 例。零回归。

---

## 十、Batch G 修复落地（2026-08-18，第三轮审计修复）

按 §9.4 建议分 5 波实施，每波全量测试验证后进入下一波。

### 10.1 修复清单

| 波次 | # | 修复内容 | 文件/位置 |
|---|---|---|---|
| W1 | P1 | 非瞬时 HTTP 错误响应体截断 `[:200]`（与瞬时路径对齐，密钥/错误页不进异常栈） | `llm_client.py:216` |
| W1 | P2 | Retry-After 解析上限 `min(max(1.0, v), 3600.0)`——恶意/异常头最多冷却 1h | `llm_client.py:284-296` |
| W1 | P3 | fallback 档案构造前补 `_validate_profile` + `_require_model`，配置缺陷跳过并告警（对齐缺 Key 行为） | `llm_client.py:681-695` |
| W1 | P5 | 删除 `LLMClient._cancelled` 死状态（只写不读；真实机制在 FailoverClient），cancel() 注释说明职责边界 | `llm_client.py:48-49, 71-76` |
| W1 | P6 | fallback 列表 `seen` 去重（自引用硬性报错保持不变，测试锚定） | `llm_client.py:669-679` |
| W1 | P4 | FailoverClient 类文档补并发契约声明（非线程安全 + 当前调用面为何安全） | `llm_client.py:324-327` |
| W2 | N8 | registry 流式转发受 `TOOL_MAX_RESULT_LENGTH` 约束：累计预算 + 超限截断 + 停止转发提示（一次性） | `tools/registry.py:113-145` |
| W2 | N12 | persona 路径边界校验：normcase+abspath 前缀检查，越界/绝对路径拒绝 | `aibox.py:536-544` |
| W3 | N2 | drain 线程重构：批次级单线程 + 独立 stop 事件 + try/finally 统一回收（正常/异常/中断路径均 join≤2s）；`set_output_queue(None)` 移入 finally | `aibox.py:1254-1386` |
| W3 | N5 | 子代理取消传播：`run_full_subagent` 新增 `cancel_event` 参数→子代理共享父事件（think 前置/工具间/流式事件间既有检查点联动）+ 0.2s 观察者线程转发深度中断（强关在途 HTTP、kill 子进程），finally 通知退出；`SubAgentTool` 构造注入父 `_cancel_event` | `aibox.py:621-624, 669-684, 773-775, 786-790, 897-902, 1145-1147` |
| W4 | N3 | `/new` 的 `reset_conversation` + `prompt_manager.reload()` 移入 `run_in_executor`（不再同步获取 `_msg_lock` 冻结事件循环） | `aibox.py:2386-2400` |
| W4 | N13 | broadcast 裸 except → `except Exception`（不再吞 CancelledError）；unregister `remove` → `discard`（幂等） | `aibox.py:2080-2084, 2185-2192` |
| W4 | N14 | `_processing_tasks` 改按 websocket 键的任务集合登记：done_callback 从集合 discard；连接关闭取消该连接全部在途任务（此前仅最后一个） | `aibox.py:2022, 2847-2870` |
| W5 | N7 | FTS5 关键词内双引号翻倍转义（`"` → `""`），MATCH 短语语法合法 | `subsystems/db.py:178-182` |
| W5 | L1 | 两处 `LIMIT {limit}` f-string → `LIMIT ?` 参数化（`get_pending_memos`/`get_immediate_memos`） | `subsystems/db.py:419-421, 462-465` |
| W5 | N10 | `mark_as_triggered` 读写收进单连接 `BEGIN IMMEDIATE` 事务（消灭 check-then-act 竞态：并发触发不再双计数/双推进 repeat） | `subsystems/db.py:515-590` |
| W5 | N16 | 历史索引加载裸 except → `except Exception` + 告警；归档文件名冲突时加 `_N` 序号（同秒归档不覆盖） | `subsystems/conversation_history.py:58-69, 138-146` |

未实施（按 §9.4 G6 择机项）：N9（prompt 更新确认门槛，涉及交互设计）、M3/M4（web scheme 校验 / shlex Windows，Linux 部署不受影响）、P7（timeout_budget 语义为文档注记，已在 FailoverClient 文档说明）。

### 10.2 新增测试

`tests/test_g_fixes.py` **20 例**（全部通过）：
- P1/P2 ×4：非瞬时错误体截断、Retry-After cap/正常值/非法值
- P3/P5/P6 ×2：fallback 缺 model 跳过 + 重复去重（链 = [main, dupe, ok2]）、死状态删除
- N2 ×2：drain 线程批次结束/工具异常路径均回收
- N5 ×3：子代理共享父取消事件 / 未传时保留自身 / SubAgentTool 注册注入链路
- N7 ×2：含引号关键词不破坏检索、常规检索不回归
- N8 ×1：120000 字符流式输出被截断至预算 + 提示标记
- N10 ×2：缺失 id 事务路径返回 None、一次性任务完成语义
- N12 ×3：`../` 越界/绝对路径拒绝、合法 persona 正常加载
- N16 ×1：同秒两次归档文件名不冲突

N3/N13/N14 为 async WebSocket 路径，依赖事件循环与真实连接对象，由现有 WebSocket 测试面回归（全量通过）。

### 10.3 测试基线

全量 **375 passed**（22.5s，Windows 开发机 `py -3`）= 原 355 + Batch G 新增 20。零回归。

---

## 十一、第四轮审计（2026-08-18，HEAD `d1f780e`）

方法：3 路并行审读（aibox.py 全文 / llm_client+common+tools / subsystems+tests）+ 主审对全部候选发现逐条代码复核，关键项 Linux 动态实证（PoC 6/6）。

### 11.1 新发现与修复（Batch H，13 项全落地）

| # | 级别 | 问题 | 位置 | 修复 |
|---|---|---|---|---|
| H1 | **高危** | **N5 修复引入回归**：子代理共享父取消事件后，首轮 `_think_and_respond_impl` 无条件 `clear()` 吞掉父级已置位的取消信号（用户中断 / --once 超时恰落在子代理生成窗口时被清，观察者线程随之失效）——取消传播被绕过，子代理跑完全程 | `aibox.py:675, 1470` | 共享事件时置 `_shared_cancel_event` 跳过 clear；赋事件后立即检查 is_set 直接返回取消 |
| M1 | 中 | `/reload_prompts` 在事件循环线程同步获取 `_msg_lock` 冻结整个事件循环（N3 同类遗漏，只修了 `/new`） | `aibox.py:2492` | 移入 `run_in_executor`（与 /new 一致） |
| M2 | 中 | SkillManager 懒加载在事件循环线程串行执行 `--help` 子进程（每技能 ≤10s），首条消息/首个任务触发时冻结服务 | `aibox.py:2885-2891` | 启动前预热 WS 与调度器两个共享实例 |
| M3 | 中 | 调度器任务无超时：AI think 卡死 → `await ai_future` 永久阻塞 → 调度循环停摆、后续任务全部丢失（已 mark_as_triggered 不重试） | `aibox.py:333` | `call_later` 任务级超时（`scheduler.task_timeout`，默认 600s）→ `ai.cancel()`，循环继续 |
| M4 | 中 | `get_recent_memories(limit=None)` 无 LIMIT 全表加载（db_tools 分页传 None），记忆库增长后单次调用全量入内存 | `db.py:234` | None → 默认上限（对齐 search_memories 语义） |
| M5 | 中 | `memo delete` 实为 `complete_memo(force)`——只标记完成不删行，重复任务被推进而非移除，stats 计数失真 | `db_tools.py:747` | db 层新增 `delete_memo` 物理删除，工具改调 |
| M6 | 中 | `PromptManager.reload()` 先清空 `_prompts` 再逐文件重读：并发连接构建 system 消息时读到空/半截列表（N3 移线程池后放大） | `prompt_manager.py:298` | 本地构建新列表后原子替换 |
| L1 | 低 | read_file 的 `start_line/end_line` 未强转直拼 sed 单引号模板——LLM 传含引号字符串可闭合引号注入生成的指引命令 | `basic_tools.py:97` | int() 强转 + 范围夹取 |
| L2 | 低 | `_save_config` 吞异常仅 print：磁盘只读时 set_proxy 仍报"已保存"，重启后配置丢失无感知 | `config.py:284` | `_save_config` 返回落盘结果，set_proxy 透传，工具如实报告 |
| L3 | 低 | SSE 行 `decode("utf-8")` 遇非 UTF-8 字节抛 UnicodeDecodeError，整条流中断且不触发 failover | `llm_client.py:222` | `errors="replace"` |
| L4 | 低 | persona 路径校验用 abspath 未解析符号链接：skills 目录内软链指向任意文件可绕过前缀检查被读取注入 | `aibox.py:536` | realpath 后再做前缀校验 |
| L5 | 低 | 1MB 截断按字节切后 `errors='replace'` 解码，行尾出现半个字符 � | `executors.py:150` | 改 `errors='ignore'` |
| L6 | 低 | compressor 压缩块全空内容时 `original_tokens=0` → 除零崩溃 | `conversation_compressor.py:517` | `max(original_tokens, 1)` |
| L7 | 低 | InstallSkillTool 指引命令 `ls -l {src}`/`cp {src} {dst}` 未引用：LLM 可控路径含空格/元字符时 shell 拆词/注入 | `skill_tools.py:269-285` | shlex.quote 源与目标路径 |
| L8 | 低 | ask_user 的 required 为空，LLM 不传 question 时 `str + None` 抛 TypeError | `misc_tools.py:42` | required 补 question + 缺参明确报错 |
| L9 | 低 | Linux 孙进程持管道泄漏（N4 残余变体）：`(sleep 300 &)` 形态 shell 已死但孙进程持写端，reader 阻塞无 EOF；**实证修复前孙进程残留** | `executors.py` | POSIX `start_new_session` + 进程组信号（组 ID 在 Popen 后立即捕获，shell 死亡后 getpgid 不可用）；宽限窗口到期强杀进程组 |
| L10 | 低 | web 工具 url 无 scheme 校验（M3 遗留）：`file://` 等经 curl 指引可达（SSRF） | `basic_tools.py:319` | http/https 白名单 |
| L11 | 低 | registry 日志打码精确匹配：apiKey/passwd 等变体漏网 | `registry.py:19` | 子串包含匹配（含 apiKey/passwd/credential） |
| L12 | 低 | FailoverClient 异常消息经 `_log` 落 ai.log：网关错误页若在 200 字符内回显 Authorization 头则密钥进日志 | `llm_client.py` | `_log`/on_failover 前 Bearer 令牌正则脱敏 |

### 11.2 修复实证（Linux PoC 6/6 PASS）

```
[PASS] L-1 无输出命令超时: 3.0s 返回，含超时（原 PoC-1 为 Windows powershell 命令）
[PASS] L-3 1MB截断: 结果 1.00MB（Linux 等价命令）
[PASS] L-4 cancel中断: cancel 后 2.0s 结束
[PASS] L-5 工具层1MB贯通: 结果 1.00MB
[PASS] L-9 孙进程持管道不悬挂: 1.1s 返回，reader 正常收尾
[PASS] L-9 孙进程已被清理: 残留无（修复前 `(sleep 300 &)` 孙进程残留至自然退出）
```

L-9 实证过程说明：首版实现按"break 后 killpg"处理，但 shell 已死时 `os.getpgid(pid)` 抛 ProcessLookupError，组 ID 无法反查，孙进程仍残留（实证 FAIL 抓出）。修正为 **Popen 成功后立即捕获组 ID 并保存**，killpg 用保存值——6/6 通过。

### 11.3 遗留项（维持原判 / 择机）

- **N9**（prompt 注入存储）：confirm 默认 True、100_STATUS 直写无门槛——涉及交互设计（用户确认门槛），维持 G6 择机项
- **M4/M3（旧编号）**：shlex Windows 转义（Linux 部署不受影响）、Q9 代理认证 curl 指引（修复需把密码嵌入命令串，反而引入密码进日志/会话史的新泄漏，维持现状）
- **F5** express_emotion 不可中断、**F6** /new 占用默认线程池、**F8** WS 限速/空闲回收、**F9** --once 软超时（H1 修复后取消可传播，残余窗口受 timeout 有界）、**Q7a** stream_chat 取消空窗（受 timeout 有界）、Q4 busy_timeout 重试、Q5 提醒格式异常静默、Q7b load_conversation 无调用面、Q8 对话文件原子写、compressor 裸 print / token 双计、单例 __new__ 无锁、db.py `if limit:` limit=0 边界——均为低危/信息级，下批处理
- **测试环境注记**：`test_executors.py::test_execute_timeout` 与 `test_models_profile.py::test_flat_api_still_reads_key_file` 为 Windows 向用例（ping -n 语法 / 依赖 ~/.cert/deepseek.key），Linux 环境预存失败，与本轮改动无关（git stash 实证）

### 11.4 测试基线（Linux，`aibox/venv`）

全量 **408 passed** = 原 382（Linux 环境）+ Batch H 新增 26。零回归；2 个预存环境性失败同前。PoC 回归脚本为 Windows 命令集，Linux 用等价命令实证（见 11.2）。

---

## 十二、Batch I 修复落地（2026-08-18，遗留项收尾）

按 §11.3 遗留清单分 3 波实施：安全项 → 稳定性项 → 信息级顺手项。

### 12.1 修复清单

| # | 级别 | 修复内容 | 文件/位置 |
|---|---|---|---|
| N9 | 中 | prompt 写入内容长度上限（持久化注入面约束）：`PromptManager.MAX_CONTENT_LENGTH=10000`，update_identity/update_soul/update_status 超长拒绝（manager 兜底 + 工具友好提示）；save_memory_and_end_conversation 状态内容超长拒绝 | `prompt_manager.py`、`prompt_tools.py` |
| Q7b | 低 | `load_conversation` realpath 前缀校验，`../` 越界/软链逃逸返回 None（当前无 LLM 调用面，防御性加固） | `conversation_history.py:348` |
| F5 | 低 | 情绪子代理取消传播：`run_light_subagent` 新增 `cancel_event`，改走 stream_chat（可被 cancel 强关在途 HTTP）+ 观察者线程；父已取消早退；`ExpressEmotionTool` 构造注入父 `_cancel_event` | `aibox.py:513, 608-660, 1177` |
| Q4 | 低 | `mark_as_triggered` BEGIN IMMEDIATE 前 `PRAGMA busy_timeout = 10000`——多进程同库（CLI+服务）写竞争等待锁释放而非 5s 后直接报 locked | `db.py:527` |
| Q5 | 低 | 提醒时间格式异常任务不再静默丢弃：去掉 SQL 层 `reminder_time <= ?` 预过滤（字符串比较会静默排除格式异常行），时间过滤改由 Python 二次验证权威判断；格式异常按"到期"返回并告警 | `db.py:405-411, 442-444` |
| Q8 | 低 | 对话文件原子写：`_atomic_write_json`（tmp + os.replace）覆盖历史索引/当前对话/归档三处写路径，崩溃不留半截 JSON | `conversation_history.py` |
| F6 | 低 | WS 命令专用线程池：`WebSocketHandler._cmd_executor`（max_workers=2），`/new`、`/reload_prompts` 不再占用默认池（繁忙 AI 时多连接并发 /new 不再耗尽线程池）；服务退出时 shutdown | `aibox.py` |
| F9 | 低 | --once 硬超时兜底：软超时 cancel 后若 AI 卡死在不可中断路径（HTTP 空窗、忽略信号的子进程），宽限一个超时周期后 `os._exit(4)` 强杀，保证硬性结束语义 | `aibox.py:3283-3299` |
| — | 信息 | PromptManager 单例 `__new__` 加锁（并发首例化竞态防护） | `prompt_manager.py:49-53` |
| — | 信息 | `get_pending_memos`/`get_immediate_memos` 的 `if limit:` → `if limit is not None`（limit=0 语义修正，None 才不加 LIMIT） | `db.py:420, 464` |

### 12.2 维持原判（未改动）

- **Q7a**（stream_chat 取消空窗）：POST 返回前 `_current_response` 未赋值，cancel 无法中断——受 timeout 有界；根治需线程化 POST + 轮询取消，复杂度高，维持记录
- **M4（旧编号）**（shlex Windows 转义）：Linux 部署不受影响
- **Q9（代理认证 curl 指引）**：修复会把密码嵌入命令串（进日志/会话史），反而引入新泄漏，维持现状
- **F8**（WS 限速/空闲回收）：本地单用户产品设计项
- compressor 裸 print（终端过程展示，保留）、`estimate_tokens` 英文双计（测试锚定既有算法，仅影响压缩阈值）

### 12.3 新增测试（`tests/test_i_fixes.py` 16 例，全部通过）

N9 ×5（manager 兜底/身份/灵魂/状态超长拒绝 + 正常写入）、Q7b ×2（越界拒绝 + 正常加载）、F5 ×3（父已取消早退/流式中断/注册注入链路）、Q5 ×2（格式异常按到期返回 + 正常过滤不回归）、Q8 ×1（无 .tmp 残留 + JSON 合法）、F6 ×1（专用线程池存在与释放）、limit=0 ×2。

### 12.4 测试基线（Linux，`aibox/venv`）

全量 **424 passed** = 原 408 + Batch I 新增 16。零回归；2 个预存环境性失败同前。
