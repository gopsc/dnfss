# aibox 模块安全与质量审计报告

- **日期**：2026-08-17（第一轮）/ 2026-08-18（第二轮复审）/ 2026-08-18（第三轮：遗留复核 + v3.4 专审）/ 2026-08-18（第四轮：3 路并行审读 + Batch H 修复）/ 2026-08-19（第五轮：4 路并行审读，聚焦历届未覆盖区域）/ 2026-08-20（第六轮：交叉视角——修复回归验证 / 并发与生命周期 / 降级路径 / 修复间相互作用）/ 2026-08-20（第七轮：skills 全量脚本 / 前端深维度 / 客户端三文件 / WS+CLI 命令面全量校验）
- **审计基线**：第一轮 HEAD `ce142fe`（Batch A–D 后）→ 第二轮 HEAD `177a9ac`（Batch E 修复后）→ 第三轮 HEAD `aa15841`（L4a/b/c 落地后）→ 第四轮 HEAD `d1f780e`（Batch G + SOCKS5 验证后）→ 第五轮 HEAD `d513c5f`（Batch I + model_config.py 后，含工作区未提交的 max_turns 钳制）→ 第六轮 HEAD `7dc6796`（Batch K 全落地 + 五轮收束文档同步后）→ 第七轮 HEAD `e43aeea`（Batch L 落地后）
- **审计范围**：`aibox.py`（~3458 行）+ `llm_client.py` + `model_config.py` + `tools/`（7 文件）+ `subsystems/`（6 文件）+ `common/`（5 文件）+ 前端 HTML（frontend.html / entry.html / entry1.html）+ `skills/` 脚本 + `client.py` / `voice_client.py`（前端与技能脚本为第五轮首次覆盖；第七轮完成 skills 全量逐文件与命令面逐条覆盖）
- **方法**：第一轮逐文件代码审读 + PoC 动态验证；第二轮 Batch E 修复复审 + 全量重审 + 6 处关键发现实证抽查；第三轮遗留项复核 + v3.4 专审；第四轮 3 路并行审读 + 主审逐条复核 + Linux PoC 实证（6/6）；第五轮 4 路并行审读（聚焦历届未覆盖区域）+ 主审逐条复核 + Linux PoC 实证（2/2）；第六轮 4 路并行审读（Batch K 修复回归验证与修复自身缺陷面 / 并发与资源生命周期交叉 / 数据边界与故障降级 / 工具链与修复间相互作用）+ 主审逐条代码复核（Windows 开发机，无 Linux PoC，动态行为以代码级核验 + Windows 测试基线代替）；第七轮 4 路并行审读（skills 全量安全面 / 前端深维度 / model_config+voice_client+client 深审 / WS+CLI 命令面全量逐条校验）+ 主审对全部中危项逐条代码实证（Windows 开发机）

### 轮次总览

| 轮次 | 日期 | 基线 HEAD | 新发现 | 修复 | 测试基线 |
|---|---|---|---|---|---|
| 第一轮 | 2026-08-17 | `ce142fe` | H1~H3 / M1~M4 / L1~L3 | Batch E（H1/H2/H3/M1/M2） | 264 passed |
| 第二轮 | 2026-08-18 | `177a9ac` | N1~N17 | Batch F（F1/F2 落地，F3~F6 转后续批次） | 272 passed |
| 第三轮 | 2026-08-18 | `aa15841` | P1~P7 | Batch G（G1~G5：P1~P6 + N2/N3/N5/N7/N8/N10/N12~N16/L1） | 375 passed |
| 第四轮 | 2026-08-18 | `d1f780e` | Batch H 全项（含 N5 取消传播回归，高危） | Batch H + Batch I 遗留收尾 | 424 passed |
| 第五轮 | 2026-08-19 | `d513c5f` | J1~J16（高危 2 / 中危 5 / 低危与信息 9） | Batch K（波次 1~5 全落地） | 490 passed |
| 第六轮 | 2026-08-20 | `7dc6796` | R1~R3（均低危；Batch K 回归验证零回归） | Batch L（R1~R3 全落地） | 490 passed（Linux）/ 493 passed（Windows） |
| 第七轮 | 2026-08-20 | `e43aeea` | 46 项（高危 0 / 中危 11 / 低危 26 / 信息 9；Batch L 回归验证零回归） | Batch M 波次 1 已落地（S1/S4/S5/S7/S8），波次 2~5 待落地 | 493 passed / 4 failed（平台环境项）/ 1 skipped（Windows） |

---

## 一、总体结论

**第一轮**（基线 ce142fe）：架构设计（分层、参数化 SQL、密钥隔离、消息回滚）整体健康，但**命令执行链存在一条系统性断裂**：实际运行路径绕过了精心设计的执行器，导致 1MB 硬约束、超时、中断三项保护同时失效。

**第二轮**（基线 177a9ac）：Batch E 五项修复（H1/H2/H3/M1/M2）中四项确认修复到位（PoC 5/5 验证），**M2 修复不完整**（超时路径跳过工具属性清理，僵尸输出问题仍存）。第二轮全量重审新发现 **1 高危 + 9 中危 + 若干低危**，其中三项与 Batch E 修复直接相关（reader 线程泄漏 / 子代理超时盲区 / M2 残留），其余为存量问题（代理密码明文落盘、drain 线程泄漏、事件循环阻塞等）。详见"八、第二轮复审"。

**第三轮**（基线 aa15841）：v3.4 多模型档案与 L4 失败转移新代码**整体高质量**（免密端点密钥隔离、用户中断绝不转移、fallback 链硬性校验，均有测试锚定），新发现 P1~P7 多为低/信息级。Batch G 按 5 波落地 20 项修复（含前两轮遗留的 N2/N3/N5/N7/N8/N10/N12~N16/L1），375 passed。

**第四轮**（基线 d1f780e）：3 路并行审读发现 Batch H 全项——其中 **H1 为 N5 修复引入的高危回归**（子代理共享父取消事件后首轮 `clear()` 吞掉取消信号，取消传播被绕过）；全部落地并经 Linux PoC 6/6 实证。Batch I 完成遗留收尾（N9 注入面长度上限、Q5 提醒异常、Q8 原子写、F9 --once 硬超时兜底等），424 passed。

**第五轮**（基线 d513c5f）：聚焦历届未覆盖区域（WS 深审、model_config.py、前端与 skills 脚本首覆盖），发现 J1~J16——含 **J1 任务系统投毒**（畸形时间/数字字段致调度器失败热循环 + 新连接握手即崩）与 **J2 前端 XSS**（AI 输出/命令结果全链路 innerHTML 渲染）两项高危；Batch K 波次 1~5 全落地，J1~J16 全数修复，490 passed 零回归。

**第六轮**（基线 7dc6796）：交叉验证轮——Batch K 修复回归验证（J1~J16 逐项在位）、并发与生命周期、降级路径、修复间相互作用四路审读。**零回归、无修复引入的缺陷**；新发现 R1~R3 均为低危（`/memos` 裸 strptime 加固漏点 / handle_interrupt 全局 executor 取消的架构残留 / broadcast 超时后半开连接有界泄漏），已随 **Batch L 全部修复落地**。见"十六、第六轮审计"。

**第七轮**（基线 e43aeea）：四路新维度——skills 全量 29 脚本逐文件（继 J12 点修后首次）、前端三页面深维度、model_config/voice_client/client 深审、WS+CLI 命令面逐条校验；主审对全部中危项逐条代码实证。Batch L 零回归；新发现 **46 项**（高危 0 / 中危 11 / 低危 26 / 信息 9），集中于 skills 凭据进命令行（S1/S4/S5/S7，违反项目自定 J12 条例）、skills 路径边界（S2/S9/S11/S12，含 vision.py 任意文件经云 API 渗出的提示注入通道）、前端功能缺陷（F1 清空按钮必抛 TypeError / F2 手动断开被重连复活）、voice_client input 线程泄漏（M1）与命令面输入规整（W 系列）。Batch M 修复建议（5 波次）已给出，待落地。见"十七、第七轮审计"。

**最终状态**：七轮累计发现 118 项（高危 7 / 中危 37 / 低危与信息 74）；前六轮 72 项**全部修复落地**（Batch E/F/G/H/I/K/L），第七轮 **Batch M 波次 1 已落地**（S1/S4/S5/S7/S8 凭据收口，见"十八"），波次 2~5（41 项，中危 6 / 低危与信息 35）待落地；维持原判仅限设计决策项，见"十五、最终状态与遗留清单"与"十六、第六轮审计"。

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

### 11.1 新发现与修复（Batch H，19 项全落地）

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

---

## 十三、第五轮审计（2026-08-19，HEAD `d513c5f`）

方法：4 路并行审读（① aibox.py 的 WS/调度器 async 区域 ② model_config.py + client.py + voice_client.py ③ subsystems/ + tools/ 回归与残留面 ④ 前端 HTML + skills 脚本）+ 主审逐条复核关键候选 + Linux PoC 实证（2/2）。审计焦点为历届未覆盖区域：`WebSocketHandler`（前四轮仅点修、从未逐行深审）、`model_config.py`（新文件）、前端与技能脚本（首次覆盖）。

### 13.1 新发现——高危（2 项）

#### J1：畸形时间/数字字段打穿任务系统——调度器失败热循环 + 每个新连接握手即崩（系统性）

三个入库路径对时间与数字字段**零校验**：CLI `--memo-add`（`aibox.py:3157-3181`，reminder 段原样入库）、WS `task add`（`aibox.py:2366-2394`，`repeat_interval`/`repeat_end_time` 原样入库，未认证单条消息即可复现）、memo 工具（`tools/db_tools.py:518`）。而 I 批 Q5 修复让格式异常的 `reminder_time` 按"到期"返回（`db.py:447-452`），使这类行进入下游多个**无防护解析点**：

| 解析点 | 后果 |
|---|---|
| 调度器执行前验证 `datetime.strptime`（`aibox.py:186`） | ValueError 在 `mark_as_triggered` 之前抛出 → 任务既不触发也不完成，每个 `check_interval`（默认 1s）重复 pick→异常→向全体客户端广播 failed，**永久热循环** |
| `repeat_end_time` strptime（`db.py:611/657`） | `mark_as_triggered` 事务 rollback+raise（`db.py:602-604`）→ 未标记触发 → 同一热循环 |
| `repeat_interval` 比较（`db.py:641`） | WS 传 `null` → None `> 0` 抛 TypeError → 同上 |
| `get_pending_tasks_for_user` strptime（`db.py:767`） | 该方法经 `send_welcome_info` 在 `register` 内调用（`aibox.py:2165→2237`），而 `register` 位于 `handle_connection` 的 try 块**之外**（`aibox.py:2944`）→ **每个新连接在握手阶段即抛 ValueError**，且 finally 不执行，`connected_clients`/`client_sessions` 登记永久泄漏 |

一条畸形任务行即可同时触发调度器热循环与全部新连接握手失败（服务事实不可用，需手动清库）。修复建议：① 入库端校验（`add_memo`/WS add 对时间串 strptime 预检、`repeat_interval` 强制 int 化，非法即拒绝并报错）；② 下游解析点 try/except 兜底（调度器验证失败 → 强制完成或挂起该任务并告警，`db.py:767` 解析失败按 `is_overdue=True`）；③ `register` 调用移入 try/finally。

#### J2：前端 XSS——AI 输出与命令执行结果全链路 innerHTML 渲染（提示注入 → 浏览器代码执行）

三个前端页面均用 marked 9.1.6（该版本起**移除内置 sanitize**）且全程无 DOMPurify：

- `frontend.html:1051/1339`、`entry.html:1127/1422`、`entry1.html:548/593/616`：`textDiv.innerHTML = marked.parse(content)` 直渲 AI 流式输出（chunk/task_process）
- `entry1.html:536`：`addSystemMessage` 将 **command_result（任意命令执行结果）**/task_status/error 模板拼接进 innerHTML，连 markdown 开关都绕不过
- frontend/entry 的未知消息类型兜底分支同样送入该渲染（`frontend.html:1493`、`entry.html:1583`）

攻击链成立：被网页内容/用户消息诱导的 AI 输出（或命令输出）含 `<img src=x onerror=...>` 即在前端执行脚本，可窃取会话、代发指令。修复：marked 输出统一经 DOMPurify（或本地化 marked + 禁用原始 HTML），`addSystemMessage` 改 textContent/转义。

### 13.2 新发现——中危（5 项）

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| J3 | FTS 外部内容表删除顺序错误，索引条目永久残留（**PoC 实证**） | `db.py:144-148`、`:277-281` | `delete_memory` 先 `DELETE FROM memories` 再删 `memories_fts`——外部内容表（content=memories）删除需从内容表回读旧值才能摘除索引项，内容行先删导致摘除无效。PoC（SQLite 3.46）：现序删除后 MATCH 仍命中已删 rowid；反序则干净。后果：索引随删除次数膨胀。搜索经 inner JOIN（`db.py:188`）不返回脏结果，故影响限于膨胀/卫生。修复：调换两条 DELETE 顺序 |
| J4 | model_config 连通性验证异常回显响应体，Bearer 令牌可进终端 | `model_config.py:197-198` | `test_profile` 直接 `str(e)` 回显；429/5xx 的 `TransientLLMError` 消息含 `response.text[:200]`，网关/WAF 错误页可能回显 Authorization 头（`llm_client.py:29-31` 注释自认）；`_sanitize_log_msg` 只护 logger 路径不护异常文本。修复：异常消息复用脱敏函数 |
| J5 | 配置文件非原子写 + 损坏静默回退默认 → 整文件洗掉链 | `common/config.py:300-301`、`prompt_manager.py:175/198/221` | `open('w')+json.dump` 直写，崩溃留半截 JSON；`_load_config` 解析失败静默改用全默认（`config.py:184-186`），此时任一 save 用"默认+本次改动"整体覆盖——proxy/api/context 等所有手工配置丢失。Q8 原子写只覆盖了 conversation_history。修复：tmp+os.replace 推广至 config/prompt 写路径，损坏时备份并拒绝覆盖 |
| J6 | PromptManager/ConfigManager 单例初始化竞态 → 系统提示词翻倍 | `prompt_manager.py:62-64`、`common/config.py:22-29` | `__new__` 有锁但 `__init__` 的 `if not self._prompts: self._load_prompts()` 无锁，`_prompts` 为类属性——多线程首例并发双重加载，整份系统提示词翻倍（token 浪费 + 行为异常）；ConfigManager 单例完全无锁同型。修复：初始化纳入同一把锁 |
| J7 | broadcast 串行 await + send_message 吞异常 → 僵死客户端可卡死全局广播 | `aibox.py:2274-2281`、`:2269-2272` | `send_message` 吞掉全部异常，broadcast 的 except→unregister 为死代码（发送失败永不回收）；`ping_interval=None`（`:3000`，有意禁用）下半开 TCP 无法探测，backpressure 下 `websocket.send` 无超时可无限期阻塞，卡住广播与调度器循环。修复：每发送 `asyncio.wait_for` 超时，超时即 unregister |

### 13.3 新发现——低危与信息级

| # | 级别 | 问题 | 位置 |
|---|---|---|---|
| J8 | 低 | model_config 交互输入 `float()`/`int()` 在容错逻辑之前执行，非数字输入 traceback 退出 | `model_config.py:330-331` |
| J9 | 低 | MemoDatabase 初始化不设 WAL（依赖与 MemoryDatabase 的创建顺序）；`busy_timeout=10000` 仅 `mark_as_triggered` 一条路径，add/complete/delete 仍默认 5s | `db.py:303-343, :542` |
| J10 | 低 | memo 批量删除未实现：schema 声明 `memo_ids`，实现只处理 `memo_id`，批量请求静默忽略 | `db_tools.py:375-379, :753-762` |
| J11 | 低 | `/new` 取消-重置竞态：cancel 后仅轮询 2s `is_busy()`，新回合与 reset 无序抢 `_msg_lock`（RLock 无公平性），消息可能"消失" | `aibox.py:2466-2484` |
| J12 | 低 | skills 注入面：nmapskills.py 的 `-n` 参数直入 python-nmap（内部拼 shell）；screenshot.py 的 `--filename/--output-dir` 拼进 PowerShell 脚本串（仅 Windows 分支）；ssh-exec/sftp-transfer `AutoAddPolicy` + 密码经命令行参数（ps 可见） | `skills/` |
| J13 | 低 | CDN 脚本（marked/hljs/socket.io）无 SRI integrity 属性 | 三个前端页 |
| J14 | 信息 | voice_client：接收循环异常后成僵尸连接（无重连）、录音 tmp 文件（`delete=False`）异常路径滞留 /tmp、`add_chunk` 先置 active 致聊天前缀永不打印（死代码） | `voice_client.py:183, 440-484, 268-273` |
| J15 | 信息 | aibox.py：`full_response_queue` 收集后从未消费（`:289-296`）；未知 task action 无 else 静默吞掉；批量/任务级广播门控不一致（禁用广播后任务级仍发）；流式热路径每 chunk 两条 INFO 日志；once 模式 `except KeyboardInterrupt` 死代码（SIGINT handler 已替换） | `aibox.py` 多处 |
| J16 | 信息 | executors `timeout<=0` 完全禁用超时，registry 兜底超时只放弃等待不 kill → 孤儿进程；skill_manager 同名技能（foo 与 foo.md）键互覆盖、`--help` 输出无长度上限 | `executors.py:147`、`registry.py:124`、`skill_manager.py:134, :50-53` |

### 13.4 Batch E~I 修复回归检查（21 项全部在位）

N7 FTS 引号翻倍、L1 LIMIT 参数化、N10 BEGIN IMMEDIATE、N8 流式预算、M1 heredoc 定界符、read_file sed 强转、web scheme 白名单、_save_config 透传、compressor 除零、InstallSkill shlex.quote、ask_user required、Q5 异常按到期（引出 J1）、Q8 对话原子写、Q7b realpath、N9 万字符上限、get_recent_memories limit 上限、memo 物理删除、PromptManager.reload 原子替换、limit=0 语义、日志打码包含匹配、F5/F6/F9——均在位无回归。唯一"单例加锁"项部分在位（PromptManager `__new__` 有锁、`__init__` 无锁，见 J6）。

### 13.5 实证记录

```
[PASS] J3-a 现序删除（先 memories 后 fts）：MATCH 仍命中已删 rowid → 索引残留确认
[PASS] J3-b 反序删除（先 fts 后 memories）：MATCH 干净 → 修复方向确认
（J1 链路各环节经代码级核验：入库零校验 ×3 路径 + 下游无防护解析 ×4 点位）
```

### 13.6 修复建议（按优先级）

1. **K1（高，J1）**：任务字段入口校验（`add_memo` strptime 预检 + `repeat_interval` int 化，WS add/CLI/memo 工具三入口统一经 db 层拒绝非法值）+ 调度器预检与 `db.py:767` try/except 兜底 + `register` 移入 try/finally
2. **K2（高，J2）**：三个前端页接入 DOMPurify（本地化部署避免新增 CDN 依赖，顺带解 J13）；entry1 `addSystemMessage` 改转义/textContent
3. **K3（中，J3/J5/J6）**：FTS 删除换序（两行改动）；config/prompt 写路径推广 `_atomic_write_json` + 损坏备份拒绝覆盖；PromptManager/ConfigManager 初始化纳入锁
4. **K4（中，J4/J7）**：test_profile 异常消息脱敏；broadcast 每发送 wait_for 超时回收
5. **K5（低，择机）**：J8~J12、J14~J16 随批处理；skills 注入面（J12）建议文档化"技能为可信代码"边界并对 nmap/screenshot 参数加 shlex.quote

### 13.7 测试基线（Linux）

全量 **455 passed** = 424 + 子代理 max_turns 回归 14（本轮审计前落地的钳制修复）+ 期间环境修复。零回归；2 个预存 Windows 向失败同前。

---

## 十四、Batch K 波次 1 修复落地（2026-08-19，快赢批）

按 §13.6 波次 1 实施四项快赢修复（J3/J4/J6/J8）。

### 14.1 修复清单

| # | 修复内容 | 文件/位置 |
|---|---|---|
| J3 | FTS 外部内容表删除换序：先删 `memories_fts` 再删 `memories`（索引摘除需从内容表回读旧值，原顺序致索引条目永久残留）——单条与批量删除两处 | `subsystems/db.py:140-153, :277-289` |
| J4 | 连通性验证异常消息脱敏：`test_profile` 回显前经 `_sanitize_log_msg` 抹掉 Bearer 令牌（网关/WAF 错误页回显 Authorization 头不再进终端） | `model_config.py:197-200` |
| J6 | 单例初始化竞态：PromptManager `__init__` 的检查+加载收进 `_instance_lock`（此前 `__new__` 锁只防双实例，并发首例 `__init__` 双重加载致系统提示词整份翻倍）；ConfigManager 补 `_instance_lock`，`__new__`/`__init__` 均加锁 | `subsystems/prompt_manager.py:62-68`、`common/config.py:22-37` |
| J8 | 交互输入容错：温度/超时原始字符串直传 `add_profile`（此前交互层抢先 `float()`/`int()`，非数字输入 traceback 退出，内部容错为死代码） | `model_config.py:328-336` |

### 14.2 新增测试（`tests/test_k_fixes.py` 10 例，全部通过）

J3 ×3（单条/批量删除无 FTS 残留 + 删后搜索不命中）、J4 ×2（令牌脱敏 + 普通错误不误伤）、J6 ×2（PromptManager/ConfigManager 8 线程 barrier 并发首例：单实例且仅加载一次）、J8 ×3（非法温度/超时友好报错 + 数字字符串接受并钳制）。

### 14.3 测试基线（Linux）

全量 **465 passed** = 455 + Batch K 波次 1 新增 10。零回归；2 个预存 Windows 向失败同前。

### 14.4 波次 2 落地（2026-08-19，K1 任务系统投毒）

按 §13.6 建议 ①②③ 实施，另顺带修复一个审计未覆盖的存量 bug。

| # | 修复内容 | 文件/位置 |
|---|---|---|
| ① 入库校验 | `add_memo` 开头校验并抛 ValueError：`reminder_time`/`repeat_end_time` strptime 预检、`repeat_interval` 强转 int（数字字符串接受）、`repeat_type` 白名单、custom 必须 interval>0——CLI/WS/memo 工具三入口（`add_user_task` 为薄包装）一处覆盖 | `subsystems/db.py:349-380` |
| ① WS 提示 | `handle_task_message` 的 add 分支捕获 ValueError → 发送"任务添加失败: …"友好错误（此前落入顶层笼统"处理失败"） | `aibox.py:2392-2406` |
| ② 调度器预检 | `aibox.py:186` strptime 包 try/except：格式异常按到期执行（Q5 语义，走 mark_as_triggered 完成/续算），不再抛 ValueError 致任务永不触发、每秒重试热循环 | `aibox.py:184-199` |
| ② 触发事务防御 | `_should_end_repeat`/`_calculate_next_reminder` 的 `repeat_end_time` strptime 解析失败视为无结束时间；`custom` 分支 `repeat_interval` int() 失败按 0 处理（不再 TypeError 打崩 BEGIN IMMEDIATE 事务） | `subsystems/db.py:590-680` |
| ② 展示路径防御 | 新增 `_is_overdue_time` 静态方法，`get_pending_tasks_for_user` 解析失败按到期展示——不再抛 ValueError 打崩新连接握手（aibox.py:2944 register 的崩溃源之一） | `subsystems/db.py:760-780` |
| ③ register 移入 try | `handle_connection` 的 `register` 移入 try 块：握手期异常（展示路径崩溃等）不再绕过 finally，会话登记由 unregister（discard 幂等）正常回收 | `aibox.py:2941-2947` |
| **附带修复** | `add_user_task` 签名缺失 `repeat_end_time` 参数：WS task add 传该字段必 TypeError（落入顶层"处理失败"，任务静默失败且用户困惑）——补参数转发 | `subsystems/db.py:738-760` |

### 14.5 波次 2 新增测试（`tests/test_k_fixes.py` 追加 13 例，文件共 23 例全过）

- add_memo 校验 ×7：畸形 reminder_time / repeat_end_time / repeat_type / repeat_interval / custom+0 拒绝且零行入库、数字字符串 "30" 强转接受、add_user_task 透传
- 存量脏数据防御 ×4：mark_as_triggered 完成畸形一次性任务 / 处理文本 repeat_interval / 畸形 repeat_end_time 视为无结束、展示路径 is_overdue=True
- WS + 调度器 ×2：畸形 repeat_end_time 友好错误不入库；调度器预检按到期执行并标记触发（stub execute_task_as_conversation）
- Q5 旧用例适配：`test_malformed_time_task_returned_as_due` 改用 SQL 直插构造存量脏数据（add_memo 已拒绝畸形时间）

### 14.6 测试基线（Linux）

全量 **478 passed** = 465 + 波次 2 新增 13。零回归；2 个预存 Windows 向失败同前。

### 14.7 波次 3 落地（2026-08-19，K2 前端 XSS + J13 SRI）

| # | 修复内容 | 文件/位置 |
|---|---|---|
| J2 渲染净化 | 三页面新增 `renderMarkdown()` helper：`DOMPurify.sanitize(marked.parse(content))`；DOMPurify 缺失时退化为转义文本（只显示不执行，防 XSS 兜底）——替换全部 7 处 `marked.parse` 直渲（AI 流式输出/历史重渲染/任务流输出） | `frontend.html`、`entry.html`、`entry1.html` |
| J2 系统消息 | `entry1.addSystemMessage` 由 innerHTML 模板拼接改为 DOM 构建 + textContent（command_result/error 等任意命令输出不再可注入 HTML） | `entry1.html:532-547` |
| J2 任务分隔条 | 任务标题（服务端可控）改 createTextNode 插入 | `entry1.html:573-584` |
| J13 SRI | 全部 CDN 脚本（marked 9.1.6 / hljs 11.8.0 / socket.io 4.5.0 / DOMPurify 3.2.4 新增）补 `integrity` + `crossorigin="anonymous"`，哈希由实际下载文件计算 | 三页面 `<head>` |

**验证记录**：HTML 结构解析全部 OK；`marked.parse` 仅存于 helper 内部；残留 innerHTML 均为静态字面量（空壳 div/复制按钮）；SRI 哈希与实际 CDN 下载文件一致。**本机无浏览器/node 运行时，未做实机页面验证**——DOMPurify 行为为业界标准，建议部署后人工开一次页面确认渲染正常。

### 14.8 波次 4 落地（2026-08-19，J5 原子写推广 + J7 broadcast 超时）

| # | 修复内容 | 文件/位置 |
|---|---|---|
| J5 | 新增 `common/atomic.py`：`atomic_write_text`（mkstemp 随机名 + fsync + os.replace，失败清理临时文件）；config 的 `_save_config`/`_save_default_config`、prompt_manager 三处写路径（identity/soul/status）、conversation_history 的 `_atomic_write_json` 全部接入（顺带修复旧固定 .tmp 名多进程互踩） | `common/atomic.py`（新）、`common/config.py`、`subsystems/prompt_manager.py`、`subsystems/conversation_history.py` |
| J5 | `_load_config` 损坏备份：JSON 解析失败先复制 `config.json.corrupt.<时间戳>` 再回退默认——杜绝"半截文件 → 静默默认 → 下次保存整体覆盖"链 | `common/config.py:190-200` |
| J7 | `broadcast` 改每客户端并发发送 + `asyncio.wait_for` 超时（`broadcast_send_timeout`，默认 5s，测试可调），超时即 `unregister` 回收——此前 `send_message` 吞异常使 except→unregister 为死代码，半开 TCP（ping 禁用）卡死广播与调度器循环 | `aibox.py:2276-2302` |

**测试**：`tests/test_k_fixes.py` 追加 5 例（原子写无残留 / 损坏备份 / prompt 原子写 / 僵死客户端超时回收 / 正常客户端不误伤），文件共 28 例全过。3 个既有保存失败用例（L2/test_model_config）改用 patch 原子写函数模拟磁盘失败（写路径不再经过 builtins.open）。

全量 **483 passed** = 478 + 波次 4 新增 5。零回归；2 个预存 Windows 向失败同前。

### 14.9 波次 5 落地（2026-08-19，K5 低危随批）

| # | 修复内容 | 文件/位置 |
|---|---|---|
| J9 | MemoDatabase 新增 `_connect()` 统一连接工厂（busy_timeout=10000 覆盖全部写路径，仅剩 mark_as_triggered 一条路径的旧状消除）；`_init_database` 显式 `PRAGMA journal_mode=WAL`（不再依赖与 MemoryDatabase 的创建顺序） | `subsystems/db.py` |
| J10 | memo 批量删除补实现：`_delete_memo` 支持 `memo_ids` 列表（schema 早已声明却从未实现，批量请求此前静默忽略），返回删除计数 | `tools/db_tools.py:747-770` |
| J11 | `/new` 取消-重置竞态：排队 reset 前等待该连接 `is_processing` 清零（≤2s）——避免新回合在旧上下文执行后被 reset 抹掉（消息"消失"） | `aibox.py:2510-2518` |
| J12 | skills 注入面：nmapskills 的 hosts 参数 `shlex.quote`（python-nmap 内部拼 shell）；screenshot.py PowerShell 分支路径单引号转义（`'` → `''`）；SKILLS_FORMAT.md 新增"安全边界"节（技能即可信代码：参数引号转义、凭据禁入命令行、AutoAddPolicy MITM 风险） | `skills/nmapskills.py`、`skills/screenshot.py`、`SKILLS_FORMAT.md` |
| J14 | voice_client：`add_chunk` 死代码修复（前缀判断前移，此前"AI助手:"前缀永不打印）；录音临时文件任何异常路径清理（wave.open/whisper 失败、arecord 超时滞留含语音 wav）；接收循环异常退出标记断开（此前成僵尸连接） | `voice_client.py` |
| J15 | `full_response_queue` 死代码移除（收集后从未消费）；未知 task action 明确报错（此前静默吞掉）；任务级广播门控对齐 `broadcast_enabled`（6 处）；流式热路径每 chunk INFO 日志降为 debug；once 模式死代码 `except KeyboardInterrupt` 移除（SIGINT 由自定义 handler 接管） | `aibox.py` 多处 |
| J16 | `SkillManager.MAX_HELP_LENGTH=2000`（--help 输出进系统提示词，防撑爆上下文）；同名键冲突确定性解决（可执行优先，Markdown 跳过并告警，此前由 listdir 顺序决定）；executors `timeout<=0` 语义注释（有意禁用，registry 3600s 兜底仍在） | `subsystems/skill_manager.py`、`subsystems/executors.py` |

**测试**：`tests/test_k_fixes.py` 追加 7 例（WAL/busy_timeout、批量删除 ×2、未知 action、同名键 ×1、--help 截断），文件共 35 例全过。

全量 **490 passed** = 483 + 波次 5 新增 7。零回归；2 个预存 Windows 向失败同前。

**第五轮审计（Batch K）至此全部落地**：J1~J16 高危/中危/低危全部修复（J14 的 voice_client 重连设计、J15 的 once 模式 SIGINT 二次 Ctrl+C 升级退出等未采纳项已在报告记录）。

---

## 十五、最终状态与遗留清单（2026-08-19，五轮收束）

### 15.1 累计结论

五轮审计（2026-08-17 ~ 2026-08-19，Batch E~K）累计发现 **69 项**（高危 7 / 中危 26 / 低危与信息 36），**全部修复落地**，无未修复安全发现。历轮高危处置一览：

| 高危 | 问题 | 修复 |
|---|---|---|
| H1~H3 | 命令执行链三重保护同时失效 | Batch E 恢复 executor 链（PoC 5/5） |
| N1 | SOCKS5 代理密码明文落盘 | Batch F1 落盘脱敏 + password_env 迁移 |
| H1（第四轮） | N5 修复引入的取消传播回归 | Batch H 共享事件跳过 clear + 早退检查 |
| J1 | 任务系统投毒（畸形字段热循环 + 握手崩溃） | Batch K1 三入口校验 + 调度器/展示路径兜底 |
| J2 | 前端全链路 XSS（innerHTML 直渲） | Batch K2 DOMPurify + textContent + SRI |

### 15.2 维持原判 / 设计项 / 未采纳

| 项 | 类别 | 说明 |
|---|---|---|
| Q7a | 维持记录 | stream_chat POST 返回前取消空窗，受 timeout 有界；根治需线程化 POST（§12.2） |
| M4（旧编号） | 维持 | shlex POSIX 转义 Windows 失效，Linux 部署不受影响（§11.3） |
| Q9 | 维持 | 代理认证 curl 指引修复会引入密码进日志/会话史的新泄漏（§11.3 / §12.2） |
| F8 | 设计项 | WS 限速/空闲回收，本地单用户产品不设（§12.2） |
| N9 确认门槛 | 设计项 | prompt 更新确认门槛未做（涉及交互设计）；注入面已由 Batch I 内容长度上限约束（§10.1 / §12.1） |
| P7 | 已消解 | timeout_budget 语义为"尝试间预算"，已在 FailoverClient 文档注明（§10.1） |
| J14 | 未采纳 | voice_client 自动重连设计（§14.9） |
| J15 | 未采纳 | once 模式二次 Ctrl+C 升级退出（§14.9） |
| compressor 裸 print / estimate_tokens 双计 | 保留 | 终端过程展示 / 既有算法测试锚定（§12.2） |

### 15.3 最终测试基线

全量 **490 passed**（Linux，`aibox/venv`），零回归；`test_execute_timeout` / `test_flat_api_still_reads_key_file` 为 Windows 向用例（ping 语法 / 依赖 `~/.cert/deepseek.key`），Linux 预存失败与历轮改动无关。

---

## 十六、第六轮审计（2026-08-20，HEAD `7dc6796`）

方法：4 路并行审读（① Batch K 修复回归验证与修复自身缺陷面 ② 并发与资源生命周期交叉 ③ 数据边界与故障降级 ④ 工具链与修复间相互作用）+ 主审逐条代码复核。审计环境为 Windows 开发机（无 Linux PoC），动态行为以代码级核验 + Windows 测试基线代替。

### 16.1 Batch K 修复回归验证（J1~J16 全部在位，零回归）

| # | 核验结论 | 关键证据 |
|---|---|---|
| J1 | **五重防御链闭环** | 入库校验（`db.py:365-387`，空串/None 正确跳过 falsy 分支）→ WS 友好错误（`aibox.py:2405-2424`）→ 调度器预检（`aibox.py:184-196`）→ 触发事务防御（`db.py:590-680`）→ 展示防御 `_is_overdue_time`（`db.py:814`）→ register 入 try（`aibox.py:2989-2992`）。全库 strptime 调用点排查（19 处）：CLI `--memo-list`、get_pending_memos（Q5）、memo 工具三处均已防护，唯一漏点见 R1 |
| J2 | **净化链完整** | 三页面 `marked.parse` 全部收敛于 `renderMarkdown()` helper 内（8 处调用点逐一核验）；残留 innerHTML 均为静态字面量（复制按钮/空容器）；DOMPurify 缺失退化分支仅转义 `<`——在 innerHTML 文本节点上下文中足以阻断标签注入（无法开口标签即无法创建元素），判定安全 |
| J5 | **原子写闭环** | `atomic.py` mkstemp 随机名 + fsync + os.replace，BaseException 路径清理临时文件；config 损坏备份（`config.py:193-199, 206-213`）与 `_save_default_config` 原子写均在位 |
| J7 | **语义正确** | `aibox.py:2277-2301` 每客户端并发 + wait_for 超时 + unregister（discard 幂等）；CancelledError 为 BaseException 不被 `except Exception` 捕获，任务取消语义保留——实现与注释一致。残留面见 R3 |
| J9/J10/J11/J16 | 抽查在位 | `_connect()` 统一 busy_timeout；批量删除非事务但计数诚实（`deleted/n`）；/new 等待 2s 为尽力而为（超时继续 reset，行为已注释）；`MAX_HELP_LENGTH` 按 str 码点切片无半个多字节字符风险（第五轮 L5 顾虑在此消解）、同名键可执行优先在位 |
| FailoverClient | 契约清晰 | P4 并发契约（单线程串行 + 跨线程 cancel）文档化；非流式 None 透传（认证类 4xx 响亮暴露）、流式仅首事件前转移、链耗尽抛最后异常——均与 §9.2 设计一致 |

历届修复抽查（Batch E~I 的 H2/L5/L9/N4/N6/N8/M2/N13/N14）：executors reader 线程收尾、输出 1MB 上限（流式/非流式两路）、registry 流式预算与统一清理、broadcast 快照——均在位。

### 16.2 新发现（3 项，均为低危）

#### R1：`/memos` WS 命令裸 strptime——J1 加固漏点

`aibox.py:2575`：`/memos` 命令对每条 memo 的 `reminder_time` 直接 `datetime.strptime(...)` 无 try/except。J1 加固了 `get_pending_tasks_for_user` 展示路径与调度器路径，但 `/memos` 列表路径漏防：存量脏数据（J1 落库前入库、或直接 SQL 写入的畸形时间）→ ValueError → 落入 handle_message 顶层兜底，用户收到笼统"处理失败"，`/memos` 功能不可用。同功能的 CLI 路径（`aibox.py:3187-3192`）与 memo 工具三处（`db_tools.py:623/697/801`）均已防护，唯独此点遗漏。**影响**：单命令报错，无崩溃/热循环（顶层 except 接住），仅脏数据库场景触发。

#### R2：handle_interrupt 取消全局 command_executor——会话隔离架构残留

`aibox.py:2369-2371`：任一 WS 客户端发送 interrupt，除取消本会话 AI（`ai.cancel()` 已覆盖会话自身 executor，见 `DeepSeekChat.cancel` `aibox.py:1094-1096`）外，还取消 `ai_manager.command_executor`——这是 **CLI 主 AI 实例**的执行器（`aibox.py:2835/2894`）。单实例时代的遗留代码：CLI 交互对话与 WS 服务并发运行时，WS 用户的一次中断会误杀终端用户正在执行的命令（跨上下文干扰）。**影响**：可用性问题，无安全面扩大；对会话自身冗余（其 executor 已被 ai.cancel 终止）。

#### R3：broadcast 超时 unregister 后半开连接残留——J7 修复的残留面

`aibox.py:2290-2298`：broadcast 超时路径调用 `unregister`（回收 `connected_clients`/`client_sessions` 并 cancel 会话 AI），但①不关闭 websocket、②不取消 `_processing_tasks`。`ping_interval=None`（有意禁用）下 `handle_connection` 的 `async for` 接收循环对半开 TCP 永不退出——连接对象、协程与任务登记泄漏直至进程重启（**有界**：每半开连接一份，J7 已消除其对广播/调度器主循环的阻塞，主要危害已解）。次级场景：慢客户端（发送超 5s 但 TCP 存活）被 unregister 后恢复，后续消息在 `handle_user_message:2727-2728` 被**静默丢弃**（无提示，无崩溃）。**影响**：有界资源泄漏 + 边缘场景静默丢消息。

### 16.3 修复建议（Batch L，已全落地）

| # | 建议 | 规模 |
|---|---|---|
| R1 | `/memos` 的 strptime 包 try/except，解析失败按"待执行"展示（对齐 CLI `:3187-3192` 模式） | ~4 行 |
| R2 | 移除 `handle_interrupt` 中全局 executor 取消（`:2369-2371`）——会话隔离下全局取消无正当受益方，CLI AI 的中断由其自身入口处理 | -3 行 |
| R3 | unregister 内 `await websocket.close()` + 取消该连接 `_processing_tasks`（与 `handle_connection` finally 对齐）；或维持现状记录为设计项（有界泄漏，进程重启即清） | ~8 行 |

三项均为低危，不构成阻塞；建议与下次功能迭代随批处理。

### 16.3a Batch L 修复落地（2026-08-20，随批当日完成）

| # | 修复内容 | 文件/位置 |
|---|---|---|
| R1 | `/memos` 的 strptime 包 try/except（ValueError/TypeError 按待执行展示），对齐 CLI `--memo-list` 模式 | `aibox.py`（/memos 分支） |
| R2 | 移除 `handle_interrupt` 对 `ai_manager.command_executor` 的全局取消——WS 中断不再误杀 CLI 主 AI 的子进程；注释说明 ai.cancel() 已覆盖会话自身 executor | `aibox.py` handle_interrupt |
| R3 | `unregister` 补齐回收：取消该连接 `_processing_tasks` 在途任务（pop + cancel，幂等）+ `await websocket.close()`（try/except 兜底，幂等）——broadcast 超时路径（J7）与连接正常关闭路径（finally）行为对齐，半开连接对象/协程/任务登记不再泄漏 | `aibox.py` unregister |

**新增测试**（`tests/test_l_fixes.py` 6 例，全部通过）：

- R1 ×2：畸形 reminder_time 不抛错按待执行展示（command_result 而非 error）、合法过期仍标 overdue（语义不回归）
- R2 ×2：interrupt 不触碰全局 executor、本会话 AI 仍被取消（中断能力不回归）
- R3 ×2：unregister 关闭 websocket + 取消在途任务 + 三处登记清空、重复注销幂等且 close 异常被兜底

**测试基线（Windows 开发机）**：全量 **493 passed / 4 failed / 1 skipped**（26s）= 487 + Batch L 新增 6。4 失败与审计基线完全相同（平台环境项），零回归。

### 16.4 测试基线（Windows 开发机）

审计时全量 487 passed / 4 failed / 1 skipped；Batch L 落地后 **493 passed / 4 failed / 1 skipped**（+6 新增）。4 失败均为平台环境项，与代码质量无关：

- `test_symlink_outside_skills_dir_rejected`：Windows 符号链接创建需管理员权限（WinError 87）
- `test_truncated_output_has_no_replacement_char`：`python3` 不在 Windows PATH
- `test_markdown_skipped_when_executable_same_name` / `test_help_output_truncated`：J16 两例依赖 POSIX 可执行位语义（Windows 下 chmod 不产生执行位，"可执行"技能被跳过加载）

Linux 基线（`aibox/venv`）维持 **490 passed** 不受影响。

### 16.5 第六轮结论

交叉验证轮：**Batch K 修复零回归**（J1~J16 逐项在位，历届 E~I 修复抽查均在位），**无修复引入的回归**；新发现 R1~R3 均为低危边界项（加固漏点/架构残留/J7 残留面），无高危、无中危，**已随 Batch L 全部修复落地**。六轮累计发现 **72 项**（高危 7 / 中危 26 / 低危与信息 39），**全部修复落地**，无未修复安全发现。

---

## 十七、第七轮审计（2026-08-20，HEAD `e43aeea`）

方法：4 路并行审读（① `skills/` 全量 29 个脚本安全面——继 J12 点修后首次逐文件覆盖 ② 前端三页面深维度——DOM 注入面收敛复核 / 连接生命周期 / CDN-SRI 完整性 / 页面收敛 ③ `model_config.py` / `voice_client.py` / `client.py` 深审 ④ WS 协议面与 CLI 命令面全量逐条校验——此前按发现修，未逐命令核对）+ 主审对全部中危项逐条代码复核（Windows 开发机，无 Linux PoC）。

### 17.1 Batch L 修复回归验证（R1~R3 全部在位，零回归）

| # | 核验结论 | 关键证据 |
|---|---|---|
| R1 | 在位 | `aibox.py:2586-2595` `/memos` 畸形 reminder_time try/except 按"待执行"展示 |
| R2 | 在位 | `aibox.py:2368-2384` `handle_interrupt` 仅会话级 `ai.cancel()`，全局 executor 取消已移除，注释在位 |
| R3 | 在位 | `aibox.py:2179-2196` `unregister` pop+cancel `_processing_tasks` + `websocket.close()` 兜底幂等 |

### 17.2 新发现——中危（11 项，主审逐条代码实证）

#### S1：`gitclient.py` GITHUB_TOKEN 嵌入 clone 命令行（凭据进 argv + 错误回显）

`skills/gitclient.py:91-94`：`_auth_url` 把 token 拼进 `https://{TOKEN}@...` 作为 git 子进程 argv——同机 `ps` 可见；git 报错 stderr（含完整带 token URL）经 `clone()` 返回技能 stdout（`:168`）→ token 进 AI 会话日志。违反本项目 J12"凭据不进命令行"条例。**修法**：改 `git -c http.extraheader="Authorization: token ..."` 或 GIT_ASKPASS，stderr token 脱敏（~10 行）。

#### S2：`gitclient.py delete` 对任意路径 `shutil.rmtree`，无 WORKSPACE 边界

`skills/gitclient.py:271-280`：LLM 可控 `--repo_path` + `--confirm` 即递归删除任意目录（如 `~`）；未校验位于 `WORKSPACE_DIR` 内、未校验是 git 仓库。**修法**：realpath 前缀匹配 WORKSPACE + 要求 `.git` 存在（~6 行）。

#### S4：`ssh-exec.py` SSH 密码必填且走命令行

`skills/ssh-exec.py:30`：`-p/--password` `required=True`——密码必进 `ps` 与 run_command 会话记录（示例还写了 `-p 12345678`）。**修法**：改环境变量/凭据文件读取（~8 行）。

#### S5：`sftp-transfer.py` `-p/--password` 命令行传密码

`skills/sftp-transfer.py:64, 86-88`：`get_password` 优先取 `args.password`，与 S4 同类。**修法**：仅保留 getpass/环境变量途径（~6 行）。

#### S7：`aes-crypto.py` AES 密钥经 `--key` 命令行传入

`skills/aes-crypto.py:452`：`--key` 十六进制密钥直进 argv → `ps` + 会话日志，直接违反 J12 条例。**修法**：仅保留 `--key-file`/`--load-key`（~5 行）。

#### S9：`camera.py --filename` 未校验，任意路径写文件+建目录

`skills/camera.py:343-344`：`os.path.join(CAMERA_DIR, args.filename)`——绝对路径使 join 失效、`../` 可逃逸；`capture_photo` 还对 dirname 无条件 `makedirs`。LLM 可控文件名 → 以 aibox 用户身份任意路径写 JPEG/任意建目录（内容不可控，主要为破坏/落点）。**修法**：basename 比对拒绝分隔符（~4 行）。

#### S11：`facerec.py person_name` 拼路径未校验 → 任意 .jpg 写入/删除

`skills/facerec.py:213, 240, 263`：`os.path.join(known_faces_dir, f"{person_name}.jpg")`——`../../tmp/x` 穿越：`add-known` 任意路径写 .jpg；`remove-known` 可删系统任意 .jpg；`get-info` 探测任意 .jpg。**修法**：person_name 白名单正则（~5 行）。

#### S12：`vision.py --image-path` 任意文件读取并外发第三方云 API

`skills/vision.py:28-29, 42`：无文件类型校验，任意本地文件（`~/.cert/163mail.json`、`~/.ssh/id_rsa`）被 base64 后 POST 到 `ark.cn-beijing.volces.com`。AI 被网页/邮件内容诱导调用 → 本地敏感文件外发云端（提示注入 → 数据渗出通道）。**修法**：校验扩展名/魔数确为图片（~5 行）。

#### F1：清空聊天按钮必然抛出 TypeError，功能半失效（entry/frontend 双页面同源缺陷）

`entry.html:1729-1737`、`frontend.html:1640-1651`：`.welcome-message` 在 `#messages-container` 内部（`entry.html:822-823`），`innerHTML=''` 已将其删除 → `querySelector` 返回 null → `null.cloneNode(true)` 抛 TypeError；后续 `clearAllStreamingMessages()`、计数清零（frontend）全部中断不执行。100% 可复现。**修法**：先 cloneNode 缓存再清空，两行对调（两页各 2 行）。

#### F2：手动断开后，已排程的自动重连定时器未取消，连接被"复活"（三页面同类）

`entry.html:1517-1522, 1697-1701`：断线排程匿名 `setTimeout`（3s）无引用不可清除；手动断开置 `socket=null` 恰好满足重连条件 `!socket` → 倒计时到期自动重建连接，违背用户明确操作意图。`frontend.html:1428-1433, 1607-1616` 同款（闭包不判 manualDisconnect）；`entry1.html:735-739, 753-761` 有 `reconnectTimer` 变量但 disconnect 未 clearTimeout。**修法**：手动断开处清除/作废定时器 + 重连闭包判 manualDisconnect（每页 2-3 行）。

#### M1：`voice_client.py` input 线程在 Ctrl+C 后泄漏并偷吃后续输入

`voice_client.py:624-626, 644-646`：`asyncio.to_thread(lambda: input(prompt))` 线程不可取消——KeyboardInterrupt 打断 await 后底层线程仍阻塞 stdin readline；循环 continue 又起新 input 线程，旧线程永久泄漏并"偷走"后续输入整行（读到无人消费即丢弃），表现为偶发丢行。`client.py:735-755` 已有现成正确方案（单守护线程 + Queue + call_soon_threadsafe）。**修法**：照搬 client.py 方案（~20 行）。

### 17.3 新发现——低危（26 项）

| # | 位置 | 问题 | 修法 |
|---|---|---|---|
| S3 | `gitclient.py:162,178,195` | branch 直进 git argv 无 `--` 分隔，`--branch=--upload-pack=...` 选项注入 | ref 前插 `--` 或白名单（~4 行） |
| S6 | `sftp-transfer.py:170-172` | 递归下载远端文件名未校验，恶意服务端 `../` 路径穿越写本地 | 拒绝含 `/\`..` 的 item（~3 行） |
| S8 | `aes-crypto.py:69,331-332` | 生成密钥明文 print 进会话日志；密钥文件默认权限 | 写受保护文件 + chmod 600（~4 行） |
| S10 | `screenshot.py:156-159` | `--filename` 同类路径拼接未校验（J12 已修 PS 转义，本项仅遍历写 PNG） | 同 S9（~3 行） |
| S13 | `test-auto-appimage.sh:66` | sed 替换串未转义，文件名含 shell 元字符 → 生成的 AppRun 脚本注入 | sed 特殊字符转义/printf 生成（~4 行） |
| F3 | 三页面连接按钮 | CONNECTING 中重复点击产生孤儿连接/双后端会话 | 点击即禁按钮或先 close 非 CLOSED 旧连接（每页 1-2 行） |
| F4 | 三页面 `<link>` CSS ×2 | font-awesome/highlight.js 样式表无 SRI（J13 只覆盖 script；CSS 劫持仅 UI 伪装，无 JS 执行） | 补 integrity+crossorigin（共 6 行） |
| F5 | 三页面消息列表 | 消息无限追加 + `data-raw-content` 双份存储，长会话内存双倍线性增长 | 超阈值头部裁剪（每页 ~5 行，可不做） |
| F6 | `frontend.html:1427` | 重连提示"N/5 秒后重试"与实际 `3000*N`ms 行为不符 | 文案修正（1 行） |
| M2 | `model_config.py` 主循环 | input 未捕获 EOFError/KeyboardInterrupt，管道耗尽/Ctrl+C 直接 traceback | 主循环包 try/except（~4 行） |
| M3 | `model_config.py:138,174` | `api` 节被改坏为非 dict 时 setdefault 后索引赋值抛 TypeError/AttributeError | isinstance 检查后重置（2-3 行） |
| M4 | `model_config.py:166-168,70,265` | `fallback` 为字符串时子串误判 + 拆成单字符数组写配置 + 展示误导 | isinstance(list) 规整（每处 1-2 行） |
| M5 | `model_config.py:75-133` | `api_key_env` 不校验合法变量名；display_name/model/api_url 不拒控制字符不限长（终端注入回显） | 正则 + isprintable + 限长（~10 行） |
| M6 | `model_config.py` 读写模式 | "启动读全量→交互数分钟→全量覆盖写"，与运行中服务并发保存 lost-update（原子写不防） | 保存前 reload 重放变更（~5 行）或文档约定 |
| M7 | `voice_client.py:350-353` | 启动麦克风探测 arecord 无 timeout，设备挂死则永久卡死 | 加 timeout=5 + 捕获（2 行） |
| M8 | `client.py:799-817` | run() 正常退出后 main() 无 finally 关 websocket，服务端见异常断连 | finally disconnect（3 行） |
| W1 | `aibox.py:2388,2493,2740,2392` | WS 入口字段无类型检查，非 str 落顶层兜底"处理失败"；title 传 dict → sqlite3 InterfaceError（J1 except 拦不住） | 三入口 isinstance 规整（~15-20 行） |
| W2 | `aibox.py:2394,2414` | task add `delay` 无类型/范围校验：非数字 TypeError、负数立即执行、10¹⁸ OverflowError | float 转换+钳制+友好 error（~5 行） |
| W3 | `aibox.py:2468-2469` | task complete 缺 task_id 静默无响应，与 J15 口径不一致 | else 回 error（2 行） |
| W4 | `aibox.py:2611-2612` | `/search ` 空关键词 → FTS 语法错被吞 → LIKE `%%` 兜底返回至多 100 条刷屏 | 空 query 回用法提示（2 行） |
| W5 | `aibox.py:2636-2637` 对比 `3422-3438` | `/reload` 整体重载静默丢弃 CLI 进程级覆盖（--debug/--no-scheduler 等） | reload 后重放 CLI 覆盖（~8 行）或回包注明 |
| W6 | `aibox.py:3115-3118, 3143` | 端口占用/--port 99999 等启动失败打印堆栈后**退出码 0**，守护脚本误判正常退出；--port 无范围校验 | sys.exit(1) + 1-65535 校验（~6 行） |
| W7 | `aibox.py:3437-3438, 3147` | `--scheduler-interval -1` → asyncio.sleep（负值） 空转热循环 CPU 打满；`0` 被 falsy 静默忽略 | 钳制 max(0.1, v)（2 行） |
| W8 | `aibox.py:3157,3314` | `--temperature -1/99` 无钳制直达 API 400（仅 --once 路径；失败模式安全） | 入口钳制（2 行） |
| W11 | `aibox.py:3422-3438` | config.json 的 system/scheduler 节为非 dict + CLI 覆盖 → main() 启动即 TypeError 崩 | 赋值前 isinstance 回退默认节（~6 行） |
| W12 | `aibox.py:3226-3248` | CLI `--memo-add "|x"` 空标题入库；`"a|b|"` 空 reminder_time 绕过 J1 校验 → 调度器按到期立即触发 | add_memo 标题非空 ValueError + CLI 空串规整 None（~6 行） |

### 17.4 新发现——信息级（9 项）

| # | 位置 | 说明 |
|---|---|---|
| S14 | `ssh-exec.py:36`、`sftp-transfer.py:94` | paramiko AutoAddPolicy 信任任意主机密钥（MITM 面）——SKILLS_FORMAT.md 已声明"仅限可信网络"，实证记录 |
| S15 | `test_qemu_run.sh:86` | 缺 qemu 时自动 `sudo apt install`，无人工确认——知悉项 |
| F7 | `entry.html:930,1223`、`frontend.html:899,1144` | 模板内 `id="task-id-display"` 多次克隆产生重复 ID（功能不受影响，HTML 不规范） |
| F8 | `entry.html:1036-1039,1761-1767`、`frontend.html:998,1649` | 死代码：空 scroll 监听 / 未调用的 preserveRawContent / 恒为 0 的 toolCallCount（~15 行可清理） |
| F9 | `entry1.html:833` | 更换背景 blob URL 未 revoke（量级极小） |
| M9 | `model_config.py:371` | 提示"最长 20 秒"与实际超时 30s 不符（1 行文案） |
| M10 | `voice_client.py:51,102,351` | `/dev/null`+arecord 为 POSIX 专属，Windows 必静默禁用——若刻意（部署 Linux）可忽略，建议文件头注明平台要求 |
| W9 | `aibox.py:3067` | WS max_size 依赖库默认 1MiB，超限 1009 关连接走正常回收（R3），无应用层"消息过大"提示 |
| W10 | `aibox.py:2330-2331, 2734-2736` | 非 dict JSON 走顶层兜底；未知 `/` 命令当聊天发给 AI 有 token 费用副作用（else 分支为既有设计，改否属产品决策） |

### 17.5 查过但无问题（摘要）

- **skills（17 Python + 4 Shell + 8 Markdown 全量）**：163mail（凭据走 `~/.cert` 文件+SSL）、ads1115/ads1256（纯寄存器+argparse choices）、bing_search（quote 编码+无凭据）、doc2txt、hello-world、info、md2docx、nmapskills（J12 在位）、famer.sh/qing_famer.sh（变量全引用）、test_qemu_run.sh（mktemp -d）；无 pickle/yaml.load/eval/exec、无可预测临时文件；**8 个 Markdown 技能零诱导越权/泄信内容**
- **前端三页面**：无 eval/new Function/document.write/insertAdjacentHTML/动态 location；DOMPurify 单参默认调用（无 ADD_TAGS/ADD_ATTR 放行）；全部服务器消息汇点经净化链，三页面无漏修；全部 `<script src>` 有 SRI+crossorigin 且版本锁死；JSON.parse 均有 try/catch；无 localStorage/sessionStorage/cookie 读写；心跳 setInterval 有关闭清理
- **model_config.py**：写配置实证走原子写（`config.py:322` → `atomic_write_text`）；J4 脱敏唯一异常出口完整；J8 数字容错在位；档案名 `PROFILE_NAME_RE` 白名单；fallback 清理遍历全档案、default_model 正确清除
- **voice_client.py**：J14 录音临时文件全异常路径清理、僵尸连接修复均在位；arecord 参数全字面量无注入
- **client.py**：无凭据读写；stdin 单守护线程+Queue 无线程泄漏；自动重连有手动断开抑制+指数退避封顶+循环关闭保护；显示去重 LRU 上限
- **WS/CLI 命令面**：12 条斜杠命令中 8 条无用户可控字段；/exit、/new（J11 门控）、/list、/config（无敏感项）、/memories、/memos（R1）、/reload_prompts、/check、/tasks、/help 逐条核对无问题；task list/complete SQL 全参数化；/search 注入面零（FTS 引号翻倍 + LIKE %/_/\\ 转义 + 参数化）；is_processing 门控事件循环内原子、finally 必清；会话三条回收路径（finally/unregister/done_callback）对齐无泄漏；ask_user 为同步纯返回工具无 pending 状态（协议面不存在"应答流"）；handle_interrupt 幂等；broadcast J7 语义正确；--once-timeout 双处钳制；handle_once_mode 超时/取消/硬超时/BrokenPipe/空响应路径完整

### 17.6 修复建议（Batch M，按优先级分波）

| 波次 | 内容 | 项 | 预估规模 |
|---|---|---|---|
| 波次 1 | 凭据进命令行统一收口（环境变量/凭据文件，对齐 J12 条例与 api_key_env 惯例） | S1/S4/S5/S7 + S8 | ~30 行 |
| 波次 2 | 路径与文件名边界校验（realpath 前缀/basename 比对/白名单正则/git argv `--`/sed 转义/远端 item 校验/图片魔数） | S2/S9/S11/S12 + S3/S6/S10/S13 | ~35 行 |
| 波次 3 | 前端功能缺陷（F1 清空按钮 TypeError/F2 重连复活/F3 孤儿连接/F4 CSS SRI/F6 文案） | F1/F2 + F3/F4/F6 | ~20 行 |
| 波次 4 | 命令面输入规整（W1 类型检查/W2 delay 钳制/W3 缺参报错/W4 空词提示/W6 退出码+端口校验/W7 间隔钳制/W8 温度钳制/W11 节类型回退/W12 空标题）+ W5 择优 | W1~W8/W11/W12 | ~45 行 |
| 波次 5 | voice_client/client 收尾（M1 input 线程改造/M7 探测超时/M8 finally 断开）+ model_config 边角（M2~M5 输入规整） | M1~M5/M7/M8 | ~40 行 |

定级说明：本地单用户产品保守定级——S1/S4/S5/S7 的实质泄漏面是"凭据进 AI 会话日志/会话史"而非同机 ps（同用户）；S12 为提示注入→云渗出通道，是本轮最接近高危的项（内容可控性低、需 AI 被诱导两条件，维持中危）；F1/F2 为 100% 可复现功能缺陷，无安全后果。全部 46 项无一可崩连接/越权/远控。

### 17.7 测试基线（Windows 开发机）

审计时全量 **493 passed / 4 failed / 1 skipped**（25.5s），与第六轮 Batch L 后基线完全相同；4 失败均为平台环境项（symlink 权限 / python3 缺失 / POSIX 可执行位语义 ×2），与代码无关。Linux 基线维持 490 passed。

### 17.8 第七轮结论

四路新维度审读 + 主审逐条复核：**Batch L 零回归**；新发现 **46 项**（高危 0 / 中危 11 / 低危 26 / 信息 9），集中在两块此前未系统覆盖的存量面——**skills 凭据/路径边界**（S 系列，多违反项目自定 J12 条例）与**命令面输入规整**（W 系列），前端与客户端以功能缺陷为主（F1/F2/M1）。七轮累计发现 **118 项**（高危 7 / 中危 37 / 低危与信息 74）。Batch M 修复建议见 §17.6；**波次 1（S1/S4/S5/S7/S8）已落地**，见 §18。

---

## 十八、Batch M 修复记录（波次 1：凭据收口，2026-08-20）

范围：§17.6 波次 1——凭据进命令行统一收口（S1/S4/S5/S7 + S8），对齐 J12"凭据不进命令行"条例与 `api_key_env` 环境变量名惯例。

| # | 修复内容 | 关键变更 |
|---|---|---|
| S1 | `gitclient.py` token 不再嵌入 URL | `_run_git` 统一经 `GIT_CONFIG_COUNT/KEY_0/VALUE_0` 环境变量传 `http.extraheader`（git≥2.31），clone/pull/push 全覆盖；token 不进 argv/远端 URL/.git/config；新增 `_scrub_token` 对 stdout/stderr/异常统一脱敏；删除 `_auth_url` |
| S4 | `ssh-exec.py` 移除 `-p/--password` | 密码仅经环境变量 `AIBOX_SSH_PASS` 提供，未设置退出码 2 并提示；帮助文本同步 |
| S5 | `sftp-transfer.py` 移除 `-p/--password` | 密码经环境变量 `AIBOX_SFTP_PASS` 优先，未设置且未用 `--key` 时回退 getpass 交互；密钥认证不再强制索密码；帮助文本同步 |
| S7 | `aes-crypto.py` 移除 `--key` | 密钥仅经 `--key-file`（hex 文本）/`--load-key`（JSON）文件途径；`args.key` 流程改局部 `key_input`；帮助与错误提示同步 |
| S8 | `aes-crypto.py` 密钥输出收口 | `generate_random_key` 不再打印密钥内容（提示经 `--save-key` 保存）；`save_key_iv` 写后 `os.chmod 0o600`（不支持 POSIX 权限平台静默跳过） |

**验证**：
- 四文件 `py_compile` 通过；`aes-crypto --help` 确认 `--key` 已移除
- 功能闭环实测（Windows）：`--gen-key --gen-iv --save-key`（密钥未打印）→ `--load-key --encrypt-text` 加解密正常；`--key 0011` 被 argparse 拒绝（exit 2）
- 残留引用核查：全仓库无 `args.password`/`args.key`（aes）残留；`SKILLS_FORMAT.md:238` J12 条例无需变更（代码现已与条例一致）
- 全量测试 **493 passed / 4 failed（平台环境项）/ 1 skipped**，与审计基线完全一致，零回归

**波次 2~5 待落地**：路径边界（S2/S9/S11/S12+低危同类）、前端功能（F1/F2 等）、命令面规整（W 系列）、客户端收尾（M1 等）。

## 十九、Batch M 修复记录（波次 2：路径边界，2026-08-20）

范围：§17.6 波次 2——用户/AI 可控文件名与人名校验，统一拦截"经 `os.path.join` 逃逸目标目录"类问题（S2/S3/S9/S11/S12 + S6/S10/S13）。

| # | 修复内容 | 关键变更 |
|---|---|---|
| S2 | `gitclient.py` `delete` 限工作区 | `os.path.realpath` 双重校验：必须在 `WORKSPACE_DIR` 内且含 `.git` 目录，否则拒绝 |
| S3 | `gitclient.py` 分支名白名单 | `_REF_RE=^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,127}$`（首字符禁 `-` 防 git 选项注入），clone/pull/push 三入口统一 `_valid_ref` 拦截 |
| S9 | `camera.py` `--filename` 校验 | 新增 `validate_filename`：拒绝 `/`、`\`、绝对路径、`.`/`..`；capture 分支接入，非法即 exit 1 |
| S10 | `screenshot.py` `--filename` 校验 | 同款 `validate_filename`，`take_screenshot` 内拦截返回失败（此前 J12 只挡了 PS 注入，路径逃逸未挡） |
| S11 | `facerec.py` person_name 白名单 | `_PERSON_NAME_RE=^(?!\.+$)[\w.-]{1,64}$`（`\w` UNICODE 兼容中文人名），add/remove/get-info 三方法统一拦截 |
| S12 | `vision.py` 图片魔数校验 | 新增 `detect_image_mime`（JPEG/PNG/GIF/WebP/BMP 魔数白名单），非图片文件拒绝读取外发；data URL MIME 由硬编码 `image/jpeg` 改为按魔数实测 |
| S6 | `sftp-transfer.py` 目录成员名校验 | 新增 `safe_member_name`，`download_dir`/`upload_dir` 双向过滤 listdir 条目——防恶意服务端构造 `../../x` 条目在递归下载时逃逸本地目录 |
| S13 | `test-auto-appimage.sh` sed 注入 | `create_apprun` 对 `$exec_name` 白名单 `^[A-Za-z0-9._+-]+$`，非法即退出（此前含 `/`/`&` 可破坏/注入 `sed -i "s/EXEC_NAME/.../g"`） |

**验证**：
- 六文件 `py_compile` 通过（Windows 无 bash，`test-auto-appimage.sh` 变更为单条正则守卫，低风险）
- 校验器功能实测：camera 放行 `ok.jpg`/拦截 `../evil.jpg`、`a/b`、`a\b`；facerec 放行 `张三`/`Li-Si_2.0`/拦截 `../etc`、`..`、空串；vision PNG 魔数识别正确/文本文件拒绝；sftp 放行正常名/拦截 `..`、`a/b`、`a\b`、空串
- 全量测试 **493 passed / 4 failed（既有平台环境项：symlink 权限、`python3` 缺 PATH、Windows 可执行位语义）/ 1 skipped**，与波次 1 基线完全一致，零回归

**波次 3~5 待落地**：前端功能（F1/F2 等）、命令面规整（W 系列）、客户端收尾（M1 等）。

## 二十、Batch M 修复记录（波次 3：前端功能，2026-08-20）

范围：§17.6 波次 3——前端三页面功能缺陷（F1/F2 + F3/F4/F6）。F5（消息无限追加内存增长）按审计口径"可不做"跳过。

| # | 修复内容 | 关键变更 |
|---|---|---|
| F1 | 清空聊天按钮 TypeError（entry/frontend 同源） | `.welcome-message` 在 `#messages-container` 内，`innerHTML=''` 后 querySelector 必为 null——两页面统一改为"先 cloneNode 缓存 → 清空 → 回插"，frontend 后续的计数清零不再被中断 |
| F2 | 手动断开后重连定时器"复活"连接（三页面） | `entry.html` 新增 `reconnectTimer` 句柄，disconnect-btn 处 `clearTimeout`；`frontend.html` 重连闭包到期时再判 `manualDisconnect`；`entry1.html` `disconnectWebSocket` 补 `clearTimeout(reconnectTimer)` |
| F3 | CONNECTING 中重复点击产生孤儿连接（三页面） | `entry.html`/`entry1.html` 连接前无条件 `disconnect()` 旧 socket（原仅 `connected` 才断开）；`frontend.html` 关闭条件放宽为 `readyState !== CLOSED` |
| F4 | 三页面 6 个 CSS `<link>` 无 SRI | font-awesome 6.0.0 与 highlight.js github-dark 11.8.0 补 `integrity`+`crossorigin`（sha384 哈希经 curl 拉取实测计算，与 J13 脚本 SRI 口径对齐） |
| F6 | frontend 重连文案与实际行为不符 | "N/5 秒后重试" → "`3*N` 秒后重试（N/5）"，与 `3000*reconnectAttempts` 实际延迟对齐 |

**验证**：
- 三页面全部内联脚本 `node --check` 通过
- 全量测试 **493 passed / 4 failed（既有平台环境项）/ 1 skipped**，与波次 1/2 基线完全一致，零回归（前端无 pytest 覆盖，语法级验证兜底）

**波次 4~5 待落地**：命令面规整（W1~W8/W11/W12 + W5 择优）、客户端收尾（M1~M5/M7/M8）。
