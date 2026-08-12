# aibox 编排者-执行者工具分层实施方案

> 状态：待实施 · 日期：2026-08-13 · 基线：aibox v2.8（tools 包抽离后，aibox.py 7753 行）
> 前置：`14db64f refactor(aibox): 工具基类 Tool/ToolRegistry 抽离至 tools 包`

## 1. 背景与目标

当前 aibox 主对话与完整子代理（`run_full_subagent`）注册**同一份全量工具集**（21 个工具），带来三个问题：

1. **安全隐患**：子代理可调用 `update_soul` / `update_identity` 改写主人格文件，且携带 `create_subagent` 可无限递归派生。
2. **上下文膨胀**：主对话 system prompt 携带全部 21 个工具的 schema 与使用说明，token 成本高。
3. **职责混杂**：命令执行、记忆管理、情绪表达交织在同一上下文中，长任务易污染主对话。

**目标架构**（编排者-执行者分层）：

- **主对话（orchestrator）**：只保留情绪管理、记忆管理、任务调度、子代理派生等"管理面"工具，**不直接接触命令行**。
- **子代理（worker）**：唯一拥有命令行/文件/网页等"执行面"工具的角色，独立上下文，无记忆/人格/派生能力。
- 轻量子代理（`run_light_subagent`，供 `express_emotion` 使用）保持不变。

## 2. 现状分析（关键代码位置）

| 位置 | 说明 |
|------|------|
| `aibox.py:5667` `DeepSeekChat.__init__` | 无工具集参数，构造即调 `_register_tools` |
| `aibox.py:5766` `_register_tools()` | 无条件注册全部 21 个内置工具 + `search_skills` |
| `aibox.py:5392` `run_full_subagent()` | 创建完整 `DeepSeekChat(system_prompt=sub_role, ...)`，子代理因此获得全量工具（含 `update_soul`/`create_subagent`）|
| `aibox.py:5363` `run_light_subagent()` | 无工具，单次 LLM 补全，供 `ExpressEmotionTool`(:3408) 使用 |
| `aibox.py:5524` `SubAgentTool` | `create_subagent` 工具，调 `run_full_subagent` |
| `~/.aibox/prompts/` | 运行期提示词（7_extension_skills.md 等指导 AI 直接用 `run_command` 执行技能）|

## 3. 工具分配方案

### orchestrator（主对话）— 13 个工具

| 工具 | 保留理由 |
|------|---------|
| `memory` / `memo` / `save_memory_and_end_conversation` | 记忆与任务管理是主代理核心职责 |
| `express_emotion` / `ask_user` | 情绪管理与用户交互只在主对话有意义 |
| `create_subagent` | 唯一的执行入口 |
| `search_skills` | 只查不执行，用于路由决策（查到技能后派 worker 执行）|
| `reload_skills` / `install_skill` / `create_skill` | 技能管理面；后两者为指引型工具，实际文件写入通过 worker 完成 |
| `update_soul` / `update_identity` | 人格进化收敛到主代理（同时消除子代理越权隐患）|
| `set_proxy` / `get_proxy` | 代理配置影响 worker 的 web/curl，归管理面 |
| `get_current_time` | 任务调度/记忆时间戳需要 |

### worker（子代理）— 7 个工具

| 工具 | 说明 |
|------|------|
| `run_command` | 命令行执行（含技能脚本执行）|
| `read_file` / `write_file` / `list_files` | 文件操作三件套（指引型）|
| `web` | 网页访问指引 |
| `calculator` / `get_current_time` | 执行辅助 |

### 明确不给 worker 的工具

`memory` / `memo`（记忆隔离）、`update_soul` / `update_identity`（人格保护）、`create_subagent`（断递归）、`express_emotion`、`ask_user`（**worker 不得向用户提问**，疑问写入返回结果）。

## 4. 改动清单

### 4.1 `aibox.py` — 工具 profile 参数化（核心改动）

**a) 模块级常量**（`DeepSeekChat` 类定义前）：

```python
# 工具集 profile：orchestrator=主对话管理面，worker=子代理执行面
TOOL_PROFILE_ORCHESTRATOR = "orchestrator"
TOOL_PROFILE_WORKER = "worker"
```

**b) `DeepSeekChat.__init__`（:5667）** 增加参数 `tool_profile: str = TOOL_PROFILE_ORCHESTRATOR`，保存为 `self.tool_profile`，`_register_tools` 调用处透传。

**c) `_register_tools()`（:5766）** 按 profile 分支装配：

```python
def _register_tools(self, exit_callback=None, command_executor=None):
    if self.tool_profile == TOOL_PROFILE_WORKER:
        builtin_tools = [
            CommandLineTool(command_executor or SubprocessCommandExecutor(self.logger), self.logger),
            ReadFileTool(self.logger),
            WriteFileTool(self.logger),
            ListFilesTool(self.logger),
            SimpleWebCrawlerTool(self.logger),
            CalculatorTool(self.logger),
            GetCurrentTimeTool(self.logger),
        ]
        self.tool_registry.register_many(builtin_tools, is_builtin=True)
        return  # worker 不需要技能管理器与 search_skills

    # —— 以下为 orchestrator 分支，即现有装配逻辑删去执行面 7 工具 ——
    builtin_tools = [GetCurrentTimeTool(self.logger)]
    # memory/memo/update_soul/update_identity/save_memory/ask_user/
    # express_emotion/reload_skills/install_skill/create_skill/
    # set_proxy/get_proxy/SubAgentTool 保持现有条件注册逻辑
    ...
```

注意：orchestrator 分支从现有列表（:5768-5807）中移除 `CalculatorTool`、`CommandLineTool`、`ReadFileTool`、`WriteFileTool`、`ListFilesTool`、`SimpleWebCrawlerTool`，其余不变。

**d) `run_full_subagent()`（:5392）** 创建子实例时传 `tool_profile=TOOL_PROFILE_WORKER`：

```python
sub_chat = DeepSeekChat(
    system_prompt=sub_role,
    tool_profile=TOOL_PROFILE_WORKER,   # 新增
    command_executor=SubprocessCommandExecutor(),
    ...
)
```

### 4.2 子代理角色提示词补强（`run_full_subagent` 内 `sub_role` 拼接）

现有结尾指令（"任务完成后直接给出最终回答"）追加 worker 行为约束：

```
【约束】你没有记忆/提问/派生能力。遇到不确定的信息，用工具自行查证；
确实无法完成时，在最终回答中明确说明原因与已完成的部分。
产物文件一律写入 ~/.aibox/workspace/ 并在回答中给出绝对路径。
```

### 4.3 运行期提示词调整（`~/.aibox/prompts/`，部署后手工更新）

- `99_SOUL.md`：角色定位改为"编排者"——拆解任务、派生子代理执行、汇总结果；明确自己**不直接执行命令**。
- `7_extension_skills.md`：技能执行路径从"直接 run_command"改为"`search_skills` 查到技能 → `create_subagent` 派 worker 执行 → 汇总结论"。
- `6_command_execution.md`：改为说明命令执行须经子代理（或直接停用该提示词）。
- `10_task_handling.md`：定时任务执行同样走子代理路径。

> 提示词为运行期数据不在仓库内，实施时在部署机 `~/.aibox/prompts/` 修改，并用 `/reload_prompts` 生效。

### 4.4 文档更新

- `aibox/README.md`：工具表按 orchestrator/worker 重新分组；"架构流程"图补子代理层；更新日志加 v2.9 条目。

## 5. 已知限制与风险

| 风险 | 说明 | 对策 |
|------|------|------|
| 延迟与成本上升 | 每次执行多一跳 LLM + 结束摘要（`_summarize_subagent_conversation`） | 简单问答主代理直接回答不派生；提示词明确"仅在需要执行时派生" |
| 信息失真 | 主代理只见摘要（8000 字符截断） | worker 产物落盘 workspace，主代理用 `search_skills`/派生复查；后续可提升摘要上限 |
| 中断不彻底 | worker 自建 `SubprocessCommandExecutor`（:5392 内），主代理 Ctrl+C 无法 kill worker 的子进程 | 现状已有该缺陷；后续改进：worker executor 注册到主 `_command_executor` 链或全局进程表 |
| 任务调度器 | TaskScheduler 触发的对话为主代理，执行类任务须学会派生 | 提示词 10_task_handling.md 明确路径；上线后观察任务执行日志 |
| 回退 | 行为变更较大 | 单提交实现，`git revert` 即可完整回退 |

## 6. 验证方案

```bash
cd /opt/blackbird/aibox

# 1. 导入冒烟（系统 python 缺 websockets 时用桩模块）
python3 -m py_compile aibox.py

# 2. profile 装配断言（桩掉 websockets/requests 后）
python3 - <<'EOF'
import sys, types
for n in ("websockets",): sys.modules.setdefault(n, types.ModuleType(n))
import aibox
o = aibox.DeepSeekChat(tool_profile="orchestrator")
w = aibox.DeepSeekChat(tool_profile="worker")
on = sorted(t.get_name() for t in o.tool_registry.get_all_tools())
wn = sorted(t.get_name() for t in w.tool_registry.get_all_tools())
assert "run_command" not in on and "create_subagent" in on
assert set(wn) == {"run_command","read_file","write_file","list_files",
                   "web","calculator","get_current_time"}
assert "update_soul" not in wn and "create_subagent" not in wn
print("orchestrator:", on); print("worker:", wn)
EOF
```

（注：构造 `DeepSeekChat` 需 `DEEPSEEK_API_KEY`，`_setenv.sh` 已从 `~/.cert/deepseek.key` 导出。）

**端到端**（部署机）：

1. `bash _run.sh` 启动，BotOps 连接。
2. 问"帮我查看系统内存"→ 预期：主代理调 `create_subagent`，任务侧栏见 worker 执行 `free -h`，主代理汇总回答；主对话流中不出现 `run_command` 工具调用。
3. 问"记住我喜欢喝绿茶"→ 预期：主代理直接调 `memory`，不派生子代理。
4. 问"使用 nmapskills 技能扫描本机"→ 预期：`search_skills` → `create_subagent` 执行技能脚本。
5. Ctrl+C 中断一个执行中的子代理任务，确认 `interrupt_ack` 与回滚正常。
6. `/tasks` 添加即时任务"统计 ~/.aibox/workspace 文件数"，确认调度器路径走子代理执行。

## 7. 后续可选扩展（不在本期）

- `config.json` 增加 `agent.worker_model`，worker 用更廉价模型降本。
- worker 进程注册到全局取消链，实现彻底中断。
- 摘要失真问题严重时可改为 worker 结果直通主对话（`task_process` 已有广播通道）。
