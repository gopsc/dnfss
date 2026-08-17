# aibox CLI 单次调用模式 · 开发文档

> 文档类型：功能开发计划
> 模块：aibox（单 AI 智能体系统）
> 日期：2026-08-17
> 状态：方案研究（待评审）
> 影响范围：`aibox/aibox.py`（仅增量改动，WebSocket 模式零回归）

---

## 1. 概述

### 1.1 背景与动机

aibox 目前只有两种使用方式：

| 使用方式 | 入口 | 特点 |
|----------|------|------|
| WebSocket 服务器 | `python aibox.py` → `ws://localhost:8765` | 长连接、多轮对话、前端/BotOps 交互（核心场景） |
| 备忘录 CLI | `--check-memo` / `--memo-add` / `--memo-list` / `--memo-complete` | 只做任务 CRUD，不触发 AI 对话 |

缺少一种**"问一次就退"**的 CLI 一次性对话模式。这导致以下场景无法覆盖：

1. **脚本化调用**：无法在 shell 脚本 / cron / CI 流水线中把 AI 当命令行工具用（如 `aibox --once "统计当前目录 py 文件行数" | grep ...`）
2. **资源受限环境**：树莓派 / NPU 盒子上不想常驻一个 WebSocket 服务进程，只想按需拉起、用完即退
3. **调试与测试**：复现某个 prompt 行为必须启动服务器 + 前端连接，链路太重
4. **进程级隔离执行**：Worker 子代理当前是**同进程线程**（`run_full_subagent` → `DeepSeekChat`），子代理崩溃/死循环会影响主服务；有了 CLI 单次模式后，可演进为"独立子进程执行"，OS 级隔离

### 1.2 设计目标

1. **新增 `--once QUERY` 参数**：执行一次性 AI 对话（含完整工具调用链），结果流式输出到 stdout，完成后进程退出
2. **纯增量、零回归**：不改动 WebSocket 模式任何现有行为；不加参数时行为与现状完全一致
3. **复用现有架构**：复用 `EnhancedAIManager` / `DeepSeekChat` / Orchestrator-Worker 工具分层 / 三层中断机制，不重写对话逻辑
4. **管道友好**：stdout 只出 AI 回答正文，过程行（🔧 工具调用等）走 stderr，退出码语义化，可被 shell 脚本安全组合
5. **状态隔离明确**：单次模式默认不加载历史对话、不写对话归档，记忆写入显式可控

### 1.3 非目标

- 不做多轮对话 CLI（交互式 REPL 已由 `client.py` 覆盖）
- 不做 HTTP/REST 单次接口（如需可在后续批次评估，复用同一内核）
- 不改变 Worker 子代理的线程内执行方式（进程化是后续可选演进，不在本期范围）

---

## 2. 现状分析

### 2.1 入口结构（aibox.py）

```
parse_args()                      # L2786: argparse 定义
main()                            # L2927: 主入口
  ├── 语言/配置/调试覆盖          # L2931-2959
  ├── 目录创建 + 日志初始化       # L2961-2973
  ├── SubprocessCommandExecutor   # L2976
  ├── EnhancedAIManager           # L2978-2981
  ├── handle_check_commands()     # L2983: --check-memo 处理后 return
  ├── handle_memo_commands()      # L2986: --memo-* 处理后 return
  └── manager.run()               # L2990: WebSocket 服务器（唯一终点）
```

关键观察：

- `handle_check_commands` / `handle_memo_commands` 已经是**"处理后即退出"的 CLI 模式先例**，单次对话模式可完全沿用该模式：在 `manager.run()` 之前插入 `handle_once_mode(manager)`，命中则执行并 return
- `EnhancedAIManager.add_ai()`（L2584）已封装完整的 AI 实例组装（memory_db / memo_db / history_manager / prompt_manager / command_executor 注入），单次模式可直接复用
- `DeepSeekChat.think_and_respond(input_text, output_callback)`（L1308）是同步阻塞函数，天然适合 CLI 直接调用——不需要 asyncio 事件循环

### 2.2 output_callback 消息类型（流式输出契约）

`think_and_respond` 通过 `output_callback(msg_type, content)` 实时推送：

| msg_type | 语义 | CLI 模式去向 |
|----------|------|--------------|
| `chunk` | AI 回答增量片段（**权威内容来源**） | **stdout**（流式 write + flush） |
| `complete` | 收尾信号（内容与 chunk 累积重复） | 仅作收尾标记，不重复输出 |
| `reasoning_start/reasoning/reasoning_end` | 思考模式流式 | stderr（可用 `--show-reasoning` 控制） |
| `line` | 过程行：🔧 工具调用、工具输出、💬 前缀 | stderr |
| `ai_break` | 工具调用分段信号 | 忽略（CLI 无 widget 概念） |
| `error` | API/解析错误 | stderr + 退出码 2 |
| `dialogue_ended` | AI 主动结束对话 | 正常收尾 |

### 2.3 需要处理的技术要点

| 要点 | 现状 | 风险与对策 |
|------|------|-----------|
| **压缩输出污染** | `_think_and_respond_impl` L1346 中 `print("📦 对话压缩...")` 直接打 stdout | 单次模式默认不加载历史 → 消息极少 → 几乎不触发压缩；另需确认该 print 走 stderr 或抑制（Batch C 处理） |
| **记忆写入并发** | 多个 CLI 进程并发写 `memories.db`（SQLite） | 默认 `--once` 模式 AI 侧仍可调用 `memory` 工具（orchestrator profile）；SQLite 有文件锁兜底，低频并发无碍；提供 `--no-save-memory` 开关时通过提示词引导（见 §3.4） |
| **Ctrl+C 中断** | 三层取消机制（Event + HTTP close + 子进程 kill）依赖 `cancel()` 被调用 | CLI 模式注册 `signal.SIGINT` handler 调 `manager.cancel()`，与 WebSocket `handle_interrupt` 路径等价 |
| **子代理输出转发** | `SubAgentTool._subagent_output` 经 `send_line` → `output_queue` → 回调链 | CLI 模式的 `output_callback` 会自然收到子代理的 `line`/`reasoning`，全部导流 stderr，无需特殊处理 |
| **对话归档写入** | `global_exit_handler` 会调 `history_manager.end_current_conversation()` | 单次模式传 `exit_callback=None`（与 TaskScheduler 的任务实例一致），AI 主动 `save_memory_and_end_conversation` 时不触发全局归档 |

---

## 3. 设计方案

### 3.1 命令行接口

```bash
# 基本用法：一次性对话，结果输出到 stdout
python aibox.py --once "帮我统计当前目录下所有 py 文件的行数"

# 静默模式：只输出最终回答正文（工具过程完全静默，适合管道）
python aibox.py --once "把 README.md 翻译成英文摘要" --quiet

# 显式控制
python aibox.py --once "..." --no-save-memory     # 不写入记忆数据库
python aibox.py --once "..." --timeout 120         # 全局超时 120 秒（默认 300）
python aibox.py --once "..." --show-reasoning      # stderr 显示思考过程（默认不显示）
```

新增参数（全部可选，`--once` 之外均有默认值）：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--once QUERY` | str | — | 一次性对话模式：执行 QUERY 后退出，不启动 WebSocket |
| `--quiet` | flag | false | 仅 stdout 输出回答正文；过程行/thinking 全静默 |
| `--no-save-memory` | flag | false | 禁止本次对话写入记忆数据库 |
| `--timeout SEC` | int | 300 | 全局执行超时，超时 kill 并退出码 4 |
| `--show-reasoning` | flag | false | 在 stderr 显示思考过程流 |

### 3.2 退出码语义

| 退出码 | 含义 |
|--------|------|
| 0 | 成功（AI 正常完成回答） |
| 1 | 工具执行链路异常（如 run_command 失败且 AI 报告失败） |
| 2 | API 请求失败（网络/HTTP/JSON 解析，服务端已回滚消息） |
| 3 | 用户中断（Ctrl+C） |
| 4 | 全局超时（--timeout 到期） |

> Batch A 只保证 0 / 2 / 3 / 4；退出码 1 的判定（从 AI 文本中识别失败）在 Batch B 实现，初版可统一归 0。

### 3.3 核心实现（Batch A，约 60~80 行增量）

**改动点 1：`parse_args()`（aibox.py L2786）** 追加 §3.1 的 5 个参数定义。

**改动点 2：新增 `handle_once_mode()`**，插入 `main()` 中 `handle_memo_commands()` 之后、`manager.run()` 之前：

```python
def handle_once_mode(manager, args) -> bool:
    """一次性对话模式：命中 --once 则执行并返回 True（进程退出）。"""
    if not args.once:
        return False

    ai = manager.add_ai()
    ai.history_manager = None          # 单次模式不加载/不写对话历史
    if args.no_save_memory:
        ai.memory_db = None            # 工具注册时已注入；此处断引用防止运行期写入
        # 注：Batch A 中 memory 工具已在 _register_tools 注册；
        # 完整禁用见 §3.4 说明

    exit_code = [0]

    def cli_callback(msg_type, content):
        if msg_type == "chunk":
            sys.stdout.write(content)
            sys.stdout.flush()
        elif msg_type == "complete":
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif msg_type == "error":
            print(content, file=sys.stderr)
            exit_code[0] = 2
        elif msg_type == "line" and not args.quiet:
            sys.stderr.write(content)
            sys.stderr.flush()
        elif msg_type.startswith("reasoning") and args.show_reasoning:
            sys.stderr.write(content)
            sys.stderr.flush()
        # ai_break / dialogue_ended 忽略

    # Ctrl+C → 三层取消（与 WebSocket interrupt 路径等价）
    def _sigint(sig, frame):
        manager.cancel()
    signal.signal(signal.SIGINT, _sigint)

    # 全局超时：守护线程到点 cancel
    timer = threading.Timer(args.timeout, manager.cancel)
    timer.daemon = True
    timer.start()
    try:
        response = ai.think_and_respond(args.once, cli_callback)
        if response is None or (isinstance(response, str)
                                and response.startswith(f"\n{i18n.get('error_prefix')}")):
            exit_code[0] = max(exit_code[0], 2)
        # 被 cancel 打断时 think_and_respond 返回 interrupt 文案
        if ai._is_cancelled() and timer.is_alive() is False:
            exit_code[0] = 4
    finally:
        timer.cancel()
        ai.cancel()  # 兜底清理子进程

    sys.exit(exit_code[0])
```

**改动点 3：`main()` 一行插入**：

```python
    if handle_memo_commands(manager):
        return

    if handle_once_mode(manager, args):   # 新增
        return

    manager.run()
```

> 注：`handle_once_mode` 内部 `sys.exit()`，return 分支实际不可达，保留 return 仅为与 `handle_check_commands` / `handle_memo_commands` 模式一致。

### 3.4 记忆写入的处理

`--no-save-memory` 的**完整实现**需要在工具注册层禁用 `MemoryTool`，但 `_register_tools` 依赖构造期注入的 `memory_db`。Batch A 采用轻量方案：

- **Batch A**：仅断 `ai.memory_db = None` 引用 + 文档声明"`--once` 场景不建议依赖记忆"；AI 若调用 `memory` 工具会因注入的 db 为 None 由工具自身报错兜底（`MemoryTool.execute` 内部访问 db 返回错误字符串，不会崩溃）
- **Batch B**：`DeepSeekChat.__init__` 增加 `enable_memory: bool = True` 形参，为 False 时 `_register_tools` 跳过 `MemoryTool` 注册，工具 schema 不出现在请求中，LLM 无从调用——这才是干净方案

### 3.5 与既有架构约束的对齐

| 项目硬约束 | 本方案如何满足 |
|-----------|---------------|
| WebSocket localhost:8765 通信 | 不受影响；`--once` 不启动 WebSocket，互斥使用 |
| Worker 仅 7 种执行工具 | 不受影响；单次模式沿用 orchestrator profile，工具分层不变 |
| 工具输出 ≤1MB + 超时中断 | 不受影响；复用 `SubprocessCommandExecutor` |
| 单向依赖 common←subsystems←tools←aibox | 满足；改动全部在 aibox.py 聚合层 |
| 新增/变更模块三处同步（根README/docs/MODULES/ARCHITECTURE） | 本功能非新模块，但落地时需同步更新 `aibox/README.md` 的"命令行参数"章节 |

---

## 4. 分批次实施计划

遵循项目"增量重构、低风险先行"惯例：

### Batch A：最小可用版（核心，建议先做）

- [ ] `parse_args()` 新增 `--once` / `--quiet` / `--timeout` 三个参数
- [ ] 新增 `handle_once_mode()`（§3.3 实现）
- [ ] `main()` 插入调用点
- [ ] SIGINT handler + 全局超时 Timer
- [ ] 退出码 0/2/3/4
- [ ] **验收**：`python aibox.py --once "现在几点了"` 正常输出并退出；`echo $?` 为 0；WebSocket 模式回归（启动服务器 + client.py 对话一轮）

### Batch B：体验与边界补齐

- [ ] `--no-save-memory` / `--show-reasoning` 参数落地
- [ ] `DeepSeekChat.__init__` 增加 `enable_memory` 开关，干净禁用 MemoryTool
- [ ] `_think_and_respond_impl` L1346 压缩提示 `print` 改走 stderr（或注入 print 回调）
- [ ] 退出码 1（工具失败识别）
- [ ] 补充 `tests/test_once_mode.py`：mock LLM 客户端，验证 stdout/stderr 分流、退出码、超时路径
- [ ] 更新 `aibox/README.md` 命令行参数章节

### Batch C：安全加固（可选，按需）

- [ ] SQLite WAL 模式（`PRAGMA journal_mode=WAL`），缓解多 CLI 进程并发写锁等待
- [ ] `--worker-only` 开关：`tool_profile=worker` 纯执行面模式，供其他模块以子进程方式调用 AI 执行器（为"Worker 进程级隔离"演进铺路）
- [ ] `--model` / `--temperature` 覆盖参数

### 明确不做（本期）

- 多轮交互式 CLI REPL（`client.py` 已覆盖）
- HTTP 单次接口
- Worker 子进程化（架构演进议题，单独立项）

---

## 5. 风险清单

| 风险 | 等级 | 缓解 |
|------|------|------|
| 冷启动开销（PromptManager 加载 + SQLite 初始化 + LLM 握手 ≈1~3s） | 低 | 可接受取舍；批量场景文档引导用 WebSocket 长连接 |
| CLI 模式与 WebSocket 模式代码 drift | 中 | 复用 `EnhancedAIManager.add_ai()`，单次模式新增代码控制在 80 行内，核心对话逻辑零复制 |
| `print` 污染 stdout 破坏管道 | 中 | Batch A 用 `--quiet` 兜底；Batch B 根治压缩提示 print |
| 并发进程 SQLite 写锁 | 低 | 低频无碍；Batch C WAL 加固 |
| Ctrl+C 时子进程泄漏 | 低 | 复用现成三层取消；`finally` 中 `ai.cancel()` 兜底 |

---

## 6. 测试方案

### 6.1 单元测试（tests/test_once_mode.py，Batch B）

| 用例 | 验证点 |
|------|--------|
| 正常问答 | mock LLM 返回纯文本 → stdout 含回答、退出码 0 |
| 工具调用链 | mock LLM 返回 tool_calls → line 走 stderr、chunk 走 stdout |
| API 失败 | mock LLM 抛 RequestException → 退出码 2、stderr 含错误 |
| 超时 | mock LLM 阻塞 → Timer 触发 cancel → 退出码 4 |
| SIGINT | 发送 SIGINT → 三层取消触发 → 退出码 3 |
| `--quiet` | 过程行不输出到任何流 |
| `--no-save-memory` | memories.db 无新增行 |

### 6.2 回归测试

- [ ] `py -m pytest aibox/tests -q` 现有 232 项全过
- [ ] WebSocket 模式手测：启动服务器 + `client.py` 完成一轮对话 + Ctrl+C 中断

### 6.3 手动验收脚本

```bash
# 基础问答（无工具）
python aibox.py --once "用一句话介绍你自己" ; echo "exit=$?"

# 工具调用链（会派生 worker 子代理执行命令）
python aibox.py --once "在 ~/.aibox/workspace 下创建一个 hello.txt 写入 hello" ; echo "exit=$?"

# 管道组合
python aibox.py --once --quiet "列出当前目录 py 文件" | grep ".py" | wc -l

# 超时路径
python aibox.py --once --timeout 5 "运行 sleep 60" ; echo "exit=$?"   # 期望 4
```

---

## 7. 后续演进方向（不在本期）

1. **Worker 进程级隔离**：`run_full_subagent` 的线程内 `DeepSeekChat` → `subprocess.run(["python", "aibox.py", "--once", "--worker-only", ...])`，子代理崩溃不波及主服务，可用 OS 用户/chroot 做权限隔离
2. **HTTP 单次接口**：`POST /chat` 无状态接口，与 `--once` 共享内核
3. **结构化输出**：`--json` 输出 `{answer, tool_calls, exit_reason}` JSON，供程序消费

---

## 8. 决策记录

| 决策点 | 结论 | 理由 |
|--------|------|------|
| 新入口文件还是复用 aibox.py | 复用 aibox.py + `--once` 参数 | 沿用 `--check-memo` 先例；避免维护两份入口 drift |
| stdout 输出协议 | chunk 流式 + 末尾换行 | 与 WebSocket 的 chunk 权威语义一致；管道可逐段消费 |
| 过程行去向 | stderr | Unix 管道惯例：stdout 是数据、stderr 是元信息 |
| 单次模式是否加载历史 | 否 | "一次性"语义明确；避免历史污染导致行为不可复现 |
| 是否默认写记忆 | 默认与 orchestrator 一致（可写），`--no-save-memory` 显式关闭 | 与 WebSocket 模式行为对齐，减少心智负担 |
