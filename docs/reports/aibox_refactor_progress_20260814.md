# aibox 模块化重构进度报告

> 状态：A/B 批工具 + Wave 1 子系统已抽离 · 日期：2026-08-14 · 基线：aibox v3.0
> 测试：171 项全过（10.79s）
> 关联文档：
> - [aibox_orchestrator_worker_plan.md](aibox_orchestrator_worker_plan.md)（v2.9 编排者-执行者分层方案）
> - [aibox_open_dev_plan.md](aibox_open_dev_plan.md)（开放版安全裁剪方案）
> - [aibox_SECURITY_AUDIT_20260801.md](../security/aibox_SECURITY_AUDIT_20260801.md)（安全审计基线）

## 1. 重构目标

将单文件 8450 行的 `aibox.py` 按职责拆分为多个内聚包，建立清晰的依赖方向与测试基线，降低维护成本、为后续工具/子系统扩展提供稳定骨架。

**目标架构**（单向依赖，禁止反向引用）：

```
common  ←  subsystems  ←  tools  ←  aibox.py
```

| 层 | 职责 | 依赖 |
|---|---|---|
| `common/` | 配置、常量、i18n、日志 | 标准库 |
| `subsystems/` | 命令执行器、提示词、数据库、技能管理 | `common` |
| `tools/` | Tool 基类 + 17 个工具类 | `common` + `subsystems` |
| `aibox.py` | 核心类聚合（DeepSeekChat/WebSocketHandler/EnhancedAIManager + 调度/压缩/历史） | 上述三包 |

## 2. 已完成工作

### 2.1 A 批基础工具抽离（`tools/basic_tools.py`，342 行）

7 个无外部子系统依赖的工具类：

| 类 | 工具名 | 说明 |
|---|---|---|
| `FileToolBase` | — | 文件操作基类（normalize_path / check_path_safety） |
| `ReadFileTool` | `read_file` | 读文件指引（head/tail/sed） |
| `WriteFileTool` | `write_file` | 写文件指引（heredoc） |
| `ListFilesTool` | `list_files` | 列目录指引（ls） |
| `SimpleWebCrawlerTool` | `web` | 网页访问指引（curl + 代理） |
| `GetCurrentTimeTool` | `get_current_time` | 时间获取 |
| `CalculatorTool` | `calculator` | 四则运算 |

**测试**：[tests/test_basic_tools.py](../../aibox/tests/test_basic_tools.py) 43 项全过

### 2.2 B 批管理面工具抽离（`tools/` 目录，共 13 类）

| 文件 | 类 | 工具名 |
|---|---|---|
| `command_tool.py` | `CommandLineTool` | `run_command` |
| `skill_tools.py` | `SearchSkillsTool` / `ReloadSkillsTool` / `InstallSkillTool` / `CreateSkillTool` | `search_skills` / `reload_skills` / `install_skill` / `create_skill` |
| `db_tools.py` | `MemoryTool` / `MemoTool` | `memory` / `memo` |
| `prompt_tools.py` | `UpdateIdentityTool` / `UpdateSoulTool` / `SaveMemoryAndEndConversationTool` | `update_identity` / `update_soul` / `save_memory_and_end_conversation` |
| `proxy_tools.py` | `SetProxyTool` / `GetProxyTool` | `set_proxy` / `get_proxy` |
| `misc_tools.py` | `AskUserTool` | `ask_user` |

**测试**：[tests/test_b_tools.py](../../aibox/tests/test_b_tools.py) 58 项全过

### 2.3 Wave 1 子系统抽离（`subsystems/` 目录）

| 文件 | 类 | 行数 |
|---|---|---|
| `executors.py` | `CommandExecutor` + `SubprocessCommandExecutor` | 279 |
| `prompt_manager.py` | `PromptManager`（单例，数字前缀排序加载） | 241 |
| `db.py` | `MemoryDatabase` + `MemoDatabase`（SQLite） | 530 |
| `skill_manager.py` | `SkillManager` | 186 |

**测试**：
- [tests/test_executors.py](../../aibox/tests/test_executors.py)
- [tests/test_prompt_manager.py](../../aibox/tests/test_prompt_manager.py)
- [tests/test_db.py](../../aibox/tests/test_db.py)
- [tests/test_skill_manager.py](../../aibox/tests/test_skill_manager.py)

共 70 项全过。

### 2.4 工具系统基础设施

- [tools/base.py](../../aibox/tools/base.py)（115 行）：`Tool` 抽象基类，定义 `get_name` / `get_description` / `get_parameters` / `execute` / `execute_streaming` / `validate_params` / `format_result` 接口
- [tools/registry.py](../../aibox/tools/registry.py)（135 行）：`ToolRegistry` 注册器，支持热重载与流式输出分发
- [tools/__init__.py](../../aibox/tools/__init__.py) / [subsystems/__init__.py](../../aibox/subsystems/__init__.py)：采用"重导出"策略，外部统一用 `from tools import Tool, ReadFileTool`，调用点无需关心子模块位置

### 2.5 关键 Bug 修复

- **`subsystems/db.py` 的 `get_recent_memories(limit=None)` SQL datatype mismatch**：`LIMIT ?` 传 `None` 时 SQLite 报错；修复为 `limit` 为 `None` 时移除 `LIMIT` 子句
- **`test_basic_tools.py` 路径漂移**（2026-08-14 修复）：4 个测试用 `filepath="aibox.py"` 假设 pytest 从 `aibox/` 目录运行；改用 pytest `tmp_path` fixture 创建临时文件，测试不再依赖 cwd

## 3. 量化指标

| 指标 | 起点（v2.8） | 当前（v3.0） | 变化 |
|---|---|---|---|
| `aibox.py` 行数 | 8450 | 3706 | -56% |
| 抽离工具类数 | 0 | 17 | +17 |
| 抽离子系统数 | 0 | 4 | +4 |
| 测试用例数 | 0 | 171 | +171 |
| 测试运行时长 | — | 10.79s | — |

## 4. 测试套件现状

```
aibox/tests/ (171 项，10.79s)
├── test_basic_tools.py      43 项  A 批工具（文件/网页/时间/计算器）
├── test_b_tools.py          58 项  B 批工具（13 个管理面工具）
├── test_executors.py        —     命令执行器
├── test_prompt_manager.py   —     提示词管理器
├── test_db.py               —     记忆/备忘录数据库
└── test_skill_manager.py    —     技能管理器
```

运行命令：

```bash
# 从仓库根目录
py -m pytest aibox/tests -q
```

## 5. 剩余工作（未抽离）

### 5.1 aibox.py 剩余类清单（3706 行）

| 类 | 起始行 | 批次 | 说明 |
|---|---|---|---|
| `ConversationCompressor` | L70 | Wave 3 | 对话压缩器，依赖 `llm_client` |
| `TaskScheduler` | L570 | Wave 2 | 任务调度器，依赖 `MemoDatabase` |
| `ConversationHistory` | L986 | Wave 2 | 对话历史管理 |
| `ExpressEmotionTool` | L1309 | C 批 | 情绪表达工具，依赖 `run_light_subagent` |
| `SubAgentTool` | L1574 | C 批 | 子代理派生工具，依赖 `run_full_subagent` |
| `DeepSeekChat` | L1700 | 核心类 | 保留入口（编排逻辑） |
| `WebSocketHandler` | L2715 | 核心类 | 保留入口 |
| `EnhancedAIManager` | L3377 | 核心类 | 保留入口 |

### 5.2 建议优先级

| 优先级 | 批次 | 内容 | 风险 |
|---|---|---|---|
| 高 | Wave 2 | `TaskScheduler` + `ConversationHistory` 抽离至 `subsystems/` | 低（依赖已抽离的 `MemoDatabase`） |
| 中 | Wave 3 | `ConversationCompressor` 抽离至 `subsystems/` | 中（需评估与 `DeepSeekChat` 的循环依赖，参考 lessons learned） |
| 中 | C 批 | `ExpressEmotionTool` + `SubAgentTool` 抽离至 `tools/` | 中（依赖 `DeepSeekChat` 的 `run_light_subagent` / `run_full_subagent`，需通过依赖注入或协议接口打破循环） |
| 低 | 核心类 | `DeepSeekChat` / `WebSocketHandler` / `EnhancedAIManager` | 保留在 `aibox.py`，作为聚合入口 |

### 5.3 风险点

1. **`ConversationCompressor` 与 `DeepSeekChat` 的循环依赖**：压缩器需调用 LLM，而 `DeepSeekChat` 持有压缩器；需通过依赖注入或协议接口打破（参考 project_memory 中 lessons learned）
2. **C 批工具依赖 `DeepSeekChat` 内部方法**：`run_light_subagent` / `run_full_subagent` 是 `DeepSeekChat` 的方法，抽离工具时需先抽离这些方法或改为函数式调用
3. **测试覆盖盲区**：C 批工具（`ExpressEmotionTool` / `SubAgentTool`）和 Wave 2/3 子系统尚无单元测试，抽离时应同步补测

## 6. 文档同步状态

| 文档 | 状态 | 备注 |
|---|---|---|
| [aibox/README.md](../../aibox/README.md) | ✅ 已同步 v3.0 | 行数、目录结构、开发指南、v3.0 更新日志已更新 |
| 本报告 | ✅ 新建 | 归档已完成工作与剩余规划 |
| [aibox_orchestrator_worker_plan.md](aibox_orchestrator_worker_plan.md) | 历史 | v2.9 分层方案（已实施） |
| [aibox_open_dev_plan.md](aibox_open_dev_plan.md) | 待评估 | 开放版裁剪方案，与本次模块化正交 |
| [aibox_file_network_cli_refactor.md](aibox_file_network_cli_refactor.md) | 待评估 | 文件/网络/CLI 重构计划 |

## 7. 验证记录

```
$ py -m pytest aibox/tests -q
........................................................................ [ 42%]
........................................................................ [ 84%]
...........................                                              [100%]
171 passed in 10.79s
```

- 测试基线：2026-08-14
- 运行环境：Windows + PowerShell 5
- Python：通过 `py` 启动器调用（`python` 不在 PATH）
