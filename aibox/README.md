# 单AI智能体系统 - WebSocket版本（增强版）

一个基于 DeepSeek API 的**单 AI 智能体**系统，通过 WebSocket 与客户端交互。所有任务（文件操作、网页访问、记忆管理、任务调度等）都由 AI 对话驱动执行，支持实时流式输出与任务状态广播。内置可热重载的技能系统，智能体可通过工具自我进化（更新角色定义、自主创建技能）。

> 本模块为 `micro_servers` 项目中的独立组件，由 `core`（`PMC`）通过 `init.txt` 中的 `bash /opt/micro_servers/aibox/_run.sh` 拉起。它与主干微服务（waf/gateway/static/dnfs/db/ai_review）解耦，是另一套 AI 助手产品。

## ✨ 核心特性

- 🤖 **单 AI 智能体**：基于 DeepSeek API 的智能对话系统，支持思考模式（reasoning 流式输出）
- 🔌 **WebSocket 通信**：实时双向通信，支持流式输出与任务状态广播
- 📁 **文件操作**：读写文件、目录列表（支持大文件分页 head/tail）
- 🌐 **网页访问**：获取网页内容、提取标题、链接与正文
- 🧠 **记忆管理**：自动保存与检索对话记忆，保持对话连贯性
- ⏰ **任务系统**：支持一次性、重复（daily/weekly/monthly/custom）、即时任务，自动触发 AI 对话
- 🔧 **扩展技能**：动态加载 `~/.aibox/skills/` 下的可执行脚本或 Markdown 技能文件
- 🌍 **多语言支持**：自动检测系统语言，支持中英文
- 🧬 **智能体自我进化**：通过 `update_soul` / `update_identity` 工具动态更新角色定位与用户画像
- 🤖 **子代理**：通过 `create_subagent` 工具派生出拥有独立上下文/角色设定的轻量子代理，主代理可分解长任务、隔离上下文，子代理可选用不同模型
- 🔄 **技能热重载**：无需重启即可添加、修改或删除扩展技能
- 🔐 **SOCKS5 代理**：通过 `set_proxy` / `get_proxy` 工具为网页访问与 API 调用配置代理，可持久化；密码明文仅存内存不落盘，跨重启经 `password_env` 环境变量注入（对齐 `api_key_env` 惯例）
- 💾 **对话管理**：历史对话归档、开始新对话（重置 AI 记忆并重新加载提示词）
- 📦 **对话压缩**：消息数或 Token 数达到阈值时自动压缩，保持上下文质量
- 🛡️ **内存安全防护**：文件读取按需加载不全载、命令输出 1MB 限制、单行超长截断、工具结果多层防护防止 OOM
- 🔒 **错误自动恢复**：API 请求失败自动回滚消息，工具结果安全清洗（控制字符过滤、非字符串转换）
- 🛑 **可靠中断机制**：支持 Ctrl+C 实时中断，通过 `threading.Event` + HTTP 连接关闭 + 子进程 kill 三层取消，流式响应和长时间运行命令均可立即终止
- 💻 **CLI 一次性对话**：`--once "问题"` 不启动 WebSocket 直接执行并退出，stdout/stderr 分流可管道组合，支持 `--quiet` / `--show-reasoning` / `--worker-only` / `--model` / `--temperature` / `--once-timeout`
- 🔀 **多模型档案**：`config.json` 的 `models` 节可配置多个 OpenAI Chat Completions 兼容端点（DeepSeek/Kimi/Qwen/本地 Ollama 等），按名称切换，档案级 api_url/model/api_key_env/reasoning_enabled/timeout/temperature/display_name；`--model` 支持 `model:`/`profile:` 前缀逃逸

## 🚀 快速开始

### 环境要求

- Python 3.8+
- DeepSeek API Key
- 操作系统：Linux / macOS / Windows

### 安装

```bash
# 克隆或下载代码后，安装依赖
pip install -r requirements.txt
```

### 配置

1. **设置 DeepSeek API Key**：
```bash
export DEEPSEEK_API_KEY="your-api-key"
```

2. **（可选）配置文件**：`~/.aibox/config.json`（首次启动时自动创建默认配置）

3. **启动服务器**：
```bash
python aibox.py
```

启动后，WebSocket 服务器将运行在 `ws://localhost:8765`（地址与端口可在 `config.json` 的 `system.websocket_host` / `system.websocket_port` 中修改）。

### 管理大模型接入点（不用手写配置）

不想手写 `config.json` 的 `models` 节？用交互式菜单工具：

```bash
python model_config.py          # 交互式菜单：列出 / 添加 / 删除 / 连通性验证
python model_config.py --list   # 非交互：列出当前接入点
```

- **添加接入点**：按提示填写档案名、API 地址（附常见厂商端点参考）、模型名；Key 两种方式——环境变量名（推荐，Key 不落盘）或免密本地端点（Ollama 等）；可选显示名/温度/超时/思考模式/失败转移（从已有接入点多选）/设为默认
- **删除接入点**：编号选择 + 确认，自动清理其他档案的失败转移引用
- **连通性验证**：对选中接入点发一次最小请求，直接给出成败原因
- 添加后可立即使用：`python aibox.py --model <档案名>` 指定，或设为默认后无需 `--model`

> 注意：本模块入口文件为 `aibox.py`（当前约 3408 行，集成 WebSocket 服务器、AI 引擎、工具/技能系统、任务调度器于一体；自 v3.0 起工具类与子系统已抽离至 `tools/`、`subsystems/`、`common/`、`tests/` 包，aibox.py 仅保留核心类与编排逻辑）。`client.py` 为示例 WebSocket 客户端，`voice_client.py` 为语音客户端，`llm_client.py` 为多厂商大模型客户端抽象层，`_run.sh` 为被 `core` 拉起的启动脚本。

## 📁 目录结构

```
aibox/
├── aibox.py           # 服务主程序（WebSocket 服务器 + AI 引擎 + 任务调度器 + 核心类 DeepSeekChat/WebSocketHandler/EnhancedAIManager）
├── model_config.py    # 大模型接入点交互式管理工具（列表/添加/删除/连通性验证，不用手写配置）
├── client.py          # 示例 WebSocket 客户端
├── voice_client.py    # 语音客户端
├── llm_client.py      # 大模型客户端抽象层（多厂商切换，DeepSeekClient 兼容 OpenAI 协议）
├── entry.html         # 前端入口页
├── entry1.html        # 前端入口页（变体）
├── frontend.html      # 前端主页面
├── common/            # 基础设施包（i18n / 配置 / 常量 / 日志）
│   ├── config.py      # ConfigManager 配置管理
│   ├── constants.py   # 常量定义
│   ├── i18n.py       # 多语言
│   └── logger.py     # 日志器
├── subsystems/        # 子系统包（Wave 1/2：从 aibox.py 抽离的基础设施）
│   ├── executors.py              # CommandExecutor + SubprocessCommandExecutor
│   ├── prompt_manager.py         # PromptManager（单例，提示词按数字前缀排序加载）
│   ├── db.py                     # MemoryDatabase + MemoDatabase（SQLite，FTS5 trigram 中文检索 + LIKE 兜底）
│   ├── skill_manager.py          # SkillManager（技能发现/加载/热重载）
│   ├── conversation_history.py   # ConversationHistory（对话历史管理）
│   └── conversation_compressor.py # ConversationCompressor（对话压缩，注入 llm_client 解耦）
├── tools/             # 工具系统包（A 批 + B 批共 17 个工具类）
│   ├── base.py             # Tool 抽象基类
│   ├── registry.py         # ToolRegistry 工具注册器
│   ├── basic_tools.py      # A 批：ReadFile/WriteFile/ListFiles/SimpleWebCrawler/GetCurrentTime/Calculator
│   ├── command_tool.py     # B 批：CommandLineTool（run_command）
│   ├── skill_tools.py      # B 批：SearchSkills/ReloadSkills/InstallSkill/CreateSkill
│   ├── db_tools.py         # B 批：MemoryTool/MemoTool
│   ├── prompt_tools.py     # B 批：UpdateIdentity/UpdateSoul/SaveMemoryAndEndConversation
│   ├── proxy_tools.py      # B 批：SetProxy/GetProxy
│   └── misc_tools.py       # B 批：AskUserTool
├── tests/             # 单元测试套件（490 项全过）
│   ├── conftest.py                    # sys.path 配置 + websockets 桩
│   ├── test_basic_tools.py            # A 批工具 43 项
│   ├── test_b_tools.py                # B 批工具 58 项
│   ├── test_executors.py              # 命令执行器
│   ├── test_prompt_manager.py         # 提示词管理器
│   ├── test_db.py                     # 记忆/备忘录数据库（含中文检索/迁移）
│   ├── test_conversation_history.py   # 对话历史
│   ├── test_conversation_compressor.py # 对话压缩器
│   ├── test_skill_manager.py          # 技能管理器
│   ├── test_once_mode.py              # CLI 一次性对话模式（Batch A/B/C 15 项）
│   ├── test_models_profile.py         # 多模型档案（Batch D 17 项）
│   ├── test_config.py                 # 配置脱敏（F1 3 项）
│   ├── test_f2_fixes.py               # 超时清理/线程收尾（F2 5 项）
│   ├── test_g_fixes.py                # 第三轮审计 Batch G（20 项）
│   ├── test_h_fixes.py                # 第四轮审计 Batch H（26 项）
│   ├── test_i_fixes.py                # 遗留收尾 Batch I（16 项）
│   ├── test_subagent_max_turns.py     # 子代理最大轮次行为回归（14 项）
│   ├── test_k_fixes.py                # 第五轮审计 Batch K（35 项）
│   └── test_model_config.py           # 模型接入点管理工具（17 项）
├── skills/            # 内置扩展技能（*.py 可执行脚本 / *.md Markdown 技能）
│   ├── hello-world.py
│   ├── aes-crypto.py
│   ├── bing_search.py
│   ├── facerec.py
│   ├── gitclient.py
│   ├── ssh-exec.py
│   ├── sftp-transfer.py
│   └── ...
├── prompts/           # 系统提示词源文件（运行期复制到 ~/.aibox/prompts/）
├── SKILLS_FORMAT.md   # 技能文件格式说明
├── _run.sh            # 启动脚本（被 core 守护时调用）
├── _runcli.sh         # 命令行启动脚本
├── _set.sh            # 安装脚本
├── _setenv.sh         # 环境设置脚本
└── README.md
```

**包依赖方向**（单向，禁止反向引用）：

```
common  ←  subsystems  ←  tools  ←  aibox.py
```

- `common`：仅依赖标准库，不引用任何其他包
- `subsystems`：依赖 `common`，不引用 `tools` 或 `aibox`
- `tools`：依赖 `common` + `subsystems`，不引用 `aibox`
- `aibox.py`：聚合层，导入上述三个包

**运行测试**：

```bash
# 从仓库根目录运行
py -m pytest aibox/tests -q
# 当前：490 项全过（Linux；test_execute_timeout / test_flat_api_still_reads_key_file 为 Windows 向用例，Linux 预存失败）
```

运行期数据存放于 `~/.aibox/`：

```
~/.aibox/
├── config.json              # 配置文件
├── prompts/                 # 系统提示词目录（按文件名数字前缀排序加载）
│   ├── 00_IDENTITY.md       # 用户身份信息（最先加载）
│   ├── 1_memory_search.md   # 基础技能提示词（1-10，按数字升序加载）
│   ├── 2_memory_save.md
│   ├── 3_file_operations.md
│   ├── 4_web_access.md
│   ├── 5_memo_management.md
│   ├── 6_command_execution.md
│   ├── 7_extension_skills.md
│   ├── 8_identity_management.md
│   ├── 9_language_instruction.md
│   ├── 10_task_handling.md
│   ├── 99_SOUL.md           # AI 灵魂定义（角色、性格、能力边界）
│   └── 100_STATUS.md        # AI 状态记录（最后加载）
├── skills/                  # 扩展技能目录（运行时从仓库 skills/ 复制或用户自建）
├── workspace/               # AI 专用工作目录（临时文件、脚本、数据文件默认存放位置）
├── memories.db              # 记忆和任务数据库（SQLite）
├── current_conversation.json # 当前对话
├── conversation_history.json # 对话索引
├── archive/                 # 历史对话归档
└── tool_results/            # 工具结果缓存
```

## 📂 工作目录规范

为避免 AI 产生的临时文件污染系统其他目录，所有 AI 主动创建的临时文件、脚本、数据文件默认写入 `~/.aibox/workspace/` 目录：

- **文件操作**：`write_file` 工具默认路径为 `~/.aibox/workspace/`（用户明确指定路径时除外）
- **命令执行**：`run_command` 工具默认 `work_dir` 为 `~/.aibox/workspace/`，命令产生的文件落在该专用工作区
- **用户指定路径优先**：当用户明确要求在特定路径操作时，按用户指定路径执行
- **读取不受限**：读取系统文件、项目文件时使用实际路径，不受工作目录限制
- **技能调用**：`~/.aibox/skills/` 下的技能执行不受此限制

## 🔧 可用工具

工具由 `Tool` 基类（`tools/base.py`）派生，在 `DeepSeekChat._register_tools()` 中按 **工具集 profile** 分组注册（v2.9 编排者-执行者分层）：主对话（orchestrator）只负责管理与调度，不直接接触命令行；执行类任务经 `create_subagent` 派生拥有执行面工具的子代理（worker）完成。

### 主对话（orchestrator）工具集

| 工具名称 | 功能描述 |
|---------|---------|
| `memory` | 保存、搜索（支持分页）、删除记忆 |
| `memo` | 创建、查看、完成、搜索任务 |
| `express_emotion` | 表达情绪并记录当前情绪状态（anger/joy/sadness/surprise），可指定 `persona` 从 `~/.aibox/skills/` 加载人格技能（如 angry/coquettish/tsundere）派生携带该人格的情绪子代理，保证人格独立、不污染主对话上下文 |
| `create_subagent` | 派生子代理：独立上下文/角色设定，仅拥有执行面工具（无记忆/人格/派生/提问能力），可选独立模型（工具描述动态列出 `models` 节可用档案名，防 LLM 编造名称），用于长任务分解、上下文隔离、独立角色专注处理 |
| `search_skills` | 搜索扩展技能（支持 Markdown 和可执行技能），查到后派生子代理执行 |
| `reload_skills` | 热重载扩展技能 |
| `create_skill` | 创建新技能（AI 自主编写 Python 脚本，实际写入经子代理完成） |
| `install_skill` | 技能安装指引 |
| `ask_user` | 向用户提问（AI 主动交互） |
| `update_identity` | 更新用户身份信息（`00_IDENTITY.md`） |
| `update_soul` | 更新 AI 自我定义（`99_SOUL.md`，智能体进化） |
| `save_memory_and_end_conversation` | 保存记忆并结束对话 |
| `get_current_time` | 获取当前时间 |
| `set_proxy` | 设置 SOCKS5 代理（host/port/可选账号密码，可持久化，即时生效）；`password` 明文仅本次会话有效不落盘，跨重启用 `password_env` 指定存有密码的环境变量名 |
| `get_proxy` | 查看当前 SOCKS5 代理配置 |

### 子代理（worker）工具集

| 工具名称 | 功能描述 |
|---------|---------|
| `run_command` | 执行系统命令（实时输出，支持超时控制） |
| `read_file` | 读取文件内容（返回 markdown 指引，改用 `head`/`tail`/`sed`/`wc` 等命令行读取） |
| `write_file` | 写入或追加文件（返回 markdown 指引，改用 `mkdir -p` + `cat` 重定向写入） |
| `list_files` | 列出目录内容（返回 markdown 指引，改用 `ls`/`du`/`find` 命令行列出） |
| `web` | 访问网页、获取标题/文本/链接（返回 markdown 指引，改用 `curl` 命令行访问） |
| `calculator` | 数学计算 |
| `get_current_time` | 获取当前时间 |

## 💬 WebSocket API

### 连接

```javascript
const ws = new WebSocket('ws://localhost:8765');
```

### 发送消息格式

#### 1. 普通消息
```json
{
  "type": "message",
  "content": "你好，请帮我..."
}
```

#### 2. 命令
```json
{
  "type": "command",
  "command": "/new"
}
```

#### 3. 任务操作
```json
{
  "type": "task",
  "action": "add",
  "title": "提醒我喝水",
  "content": "下午3点喝水",
  "delay": 3600,
  "repeat_type": "daily",
  "is_immediate": false
}
```

#### 4. Ping
```json
{
  "type": "ping"
}
```

#### 5. 中断（Ctrl+C）
```json
{
  "type": "interrupt",
  "content": "用户按下了 Ctrl+C 中断当前操作"
}
```
服务端收到后立即回复 `interrupt_ack`，并通过三层取消机制（Event标志 + HTTP连接关闭 + 子进程kill）终止正在进行的AI思考和工具执行。

### 支持的命令

| 命令 | 功能 |
|-----|------|
| `/exit` | 退出连接 |
| `/new` | 开始新对话（保存当前，重置 AI 记忆，重新加载提示词） |
| `/list` | 列出所有历史对话 |
| `/memories` | 查看最近记忆（支持分页） |
| `/search <关键词>` | 按关键词搜索记忆 |
| `/memos` | 查看待办任务 |
| `/config` | 查看配置 |
| `/reload` | 重载配置 |
| `/reload_prompts` | 重载提示词 |
| `/check` | 检查过期任务 |
| `/tasks` | 查看任务调度器状态 |
| `/help` | 显示帮助 |

### 接收消息类型

| 类型 | 说明 |
|-----|------|
| `chunk` | AI 响应的**增量片段**（流式输出，chunk_buffer 发送后即清零，为内容权威来源） |
| `reasoning_start` | AI 开始思考信号（思考模式，创建思考区域展示） |
| `reasoning` | AI 思考过程增量片段（流式输出，灰色/斜体样式） |
| `reasoning_end` | AI 思考结束信号（切换到正式回答） |
| `complete` | AI 响应**收尾信号**（chunk 流式是内容权威来源，complete 仅用于收尾；完全丢帧时可用 full_text 补建） |
| `line` | 过程行（🔧 工具调用提示、工具输出、💬 助手前缀等，供任务侧栏/终端显示） |
| `error` | 错误信息（API 请求失败、解析失败等；服务端自动回滚本轮消息防止对话历史被污染） |
| `command_result` | 命令执行结果 |
| `task_status` | 任务状态更新（executing/complete/failed） |
| `task_process` | 任务执行过程中的 AI 输出 |
| `task_error` | 任务执行错误 |
| `task_added` | 任务添加成功 |
| `task_list` | 待办任务列表（`task` action=list 的返回） |
| `task_completed` | 任务完成通知 |
| `dialogue_ended` | 对话结束通知 |
| `dialogue_reset` | 对话重置完成 |
| `notification` | 通知消息 |
| `connection_ack` | 连接确认 |
| `interrupt_ack` | 中断确认 |
| `info` | 系统信息 |
| `pong` | Ping 响应 |

## ⏰ 任务系统

### 任务类型

1. **一次性任务**：在指定时间执行一次
2. **重复任务**：
   - `daily`：每天执行
   - `weekly`：每周执行
   - `monthly`：每月执行
   - `custom`：自定义间隔（分钟/小时/天）
3. **即时任务**：立即执行（`is_immediate: true`）

### 添加任务示例

```json
// 每天提醒
{
  "type": "task", "action": "add", "title": "每日健康提醒", "repeat_type": "daily"
}
```

```json
// 5分钟后执行
{
  "type": "task", "action": "add", "title": "5分钟后提醒", "delay": 300
}
```

```json
// 每30分钟重复
{
  "type": "task", "action": "add", "title": "定时检查", "repeat_type": "custom",
  "repeat_interval": 30, "repeat_interval_unit": "minutes"
}
```

```json
// 即时任务
{
  "type": "task", "action": "add", "title": "检查服务器状态", "is_immediate": true
}
```

### 任务执行流程

1. 任务到期后，系统自动标记为已触发
2. 创建新的 AI 对话，将任务内容作为用户输入
3. AI 处理任务，使用工具执行操作
4. 任务执行结果实时广播到所有 WebSocket 客户端
5. 重复任务自动计算下一次执行时间

## 🔧 扩展技能

### 技能类型

#### 1. 可执行技能
- 放在 `~/.aibox/skills/` 目录
- 文件必须可执行（`chmod +x`），建议不带扩展名
- 支持 `--help` 选项；`--help` 第一行为一句话简介，其余为使用说明

**示例**：
```python
#!/usr/bin/env python3
import argparse

def main():
    parser = argparse.ArgumentParser(description="温度转换工具 - 摄氏度转华氏度")
    parser.add_argument("-t", "--temp", type=float, required=True, help="摄氏温度值")
    args = parser.parse_args()
    fahrenheit = args.temp * 9/5 + 32
    print(f"{args.temp}°C = {fahrenheit:.1f}°F")

if __name__ == "__main__":
    main()
```

#### 2. Markdown 技能
- 文件以 `.md` 结尾
- 第一行为简介（`# 标题` 或纯文本），其余为完整技能内容

**示例**：
```markdown
# 代码审查指南
提供代码审查的检查清单和最佳实践……

## 检查要点
1. 代码风格一致性
2. 性能优化建议
3. 安全漏洞检查
4. 可维护性评估
```

### 创建技能

#### 方式1：AI 自主创建（推荐）
直接告诉 AI：
```
创建一个技能，功能是将摄氏度转换为华氏度，接受 -c 参数输入温度
```
AI 会自动使用 `create_skill` 工具编写脚本并部署到 `~/.aibox/skills/`。

#### 方式2：手动创建
1. 编写脚本，确保支持 `--help`
2. 复制到 `~/.aibox/skills/` 并去掉扩展名
3. 添加可执行权限
4. 调用 `reload_skills` 工具

```bash
cp my_skill.py ~/.aibox/skills/my_skill
chmod +x ~/.aibox/skills/my_skill
```

#### 方式3：使用安装指引
```json
{ "type": "message", "content": "使用 install_skill 工具安装 /path/to/script.py" }
```

技能格式详见 [SKILLS_FORMAT.md](SKILLS_FORMAT.md)。

## 🧠 智能体自我进化

AI 可以通过 `update_soul` 工具动态更新 `99_SOUL.md`，通过 `update_identity` 工具更新 `00_IDENTITY.md`：

```json
{ "type": "message", "content": "使用 update_soul 更新我的角色，强调我更注重创造性思维" }
```

这允许 AI 在对话中不断优化自己的：角色定位、性格特点、能力边界、行为准则、价值观。

## 📦 对话压缩

当对话达到阈值时自动压缩：

```json
// 配置示例（config.json -> context）
{
  "max_history_messages": 500,        // 历史消息上限
  "compress_enabled": true,           // 是否启用压缩
  "compress_message_threshold": 200,  // 消息数阈值
  "compress_token_threshold": 120000, // Token 数阈值
  "compress_ratio": 0.2,              // 压缩保留比例（早期对话压缩为原长的该比例）
  "compress_keep_recent": 8           // 保留最近消息数
}
```

压缩过程：
1. 检测到消息数或 Token 数超过阈值
2. 调用 AI 压缩早期对话内容
3. 保留最近的重要消息
4. 将压缩摘要作为系统消息
5. 压缩过程静默执行，不影响用户体验

## 🧩 提示词系统

提示词文件位于 `~/.aibox/prompts/`，按文件名**数字前缀升序**加载（identity → 技能 1~10 → soul → status）：

| 文件 | 用途 | 更新方式 |
|-----|------|---------|
| `00_IDENTITY.md` | 用户身份信息 | AI 通过 `update_identity` 工具更新 |
| `N_*.md`（N=1~10） | 基础技能提示词（记忆搜索/保存、文件/网页/命令/任务操作、技能扩展、身份管理、语言、任务规则） | 手动编辑，AI 通过 `/reload_prompts` 重载 |
| `99_SOUL.md` | AI 灵魂定义（角色、性格、能力边界、行为准则） | AI 通过 `update_soul` 工具更新 |
| `100_STATUS.md` | AI 状态记录 | AI 通过 `save_memory_and_end_conversation` 更新 |

> **加载顺序保证**：`00_IDENTITY.md` 最先加载（AI 先知道用户是谁），然后是 1~10 号技能提示词（按数字升序），接着是 `99_SOUL.md`（AI 的角色定位），最后是 `100_STATUS.md`（当前状态）。每轮用户输入前会附加一段简洁的工作流程提醒（不重复 system prompt 中的详细说明），工具调用后递归继续时不再重复注入。

## ⚙️ 配置说明

主要配置项（`~/.aibox/config.json`，首次启动自动生成）：

```json
{
  "system": {
    "save_dir": "~/.aibox",
    "datetime_format": "%Y-%m-%d %H:%M:%S",
    "date_format": "%Y%m%d_%H%M%S",
    "debug_level": 1,
    "load_history_by_default": true,
    "log_file": "~/.aibox/ai.log",
    "log_max_size": 10485760,
    "log_backup_count": 5,
    "websocket_host": "localhost",
    "websocket_port": 8765,
    "skills_dir": "~/.aibox/skills",
    "prompts_dir": "~/.aibox/prompts",
    "workspace_dir": "~/.aibox/workspace"
  },
  "scheduler": {
    "enabled": true,
    "check_interval": 1.0,
    "max_tasks_per_run": 10,
    "broadcast_enabled": true
  },
  "api": {
    "provider": "openai_compatible",
    "deepseek_api_url": "https://api.deepseek.com/chat/completions",
    "deepseek_model": "deepseek-v4-flash",
    "reasoning_enabled": false,
    "api_key_env": "DEEPSEEK_API_KEY",
    "timeout": 120,
    "temperature": 0.7,
    "default_model": "deepseek"
  },
  "models": {},
  ```

### 🔀 多模型档案（models 节）

`models` 可配置多个 OpenAI Chat Completions 协议兼容端点，按**档案名**切换（v3.2）：

```json
{
  "models": {
    "kimi": {
      "api_url": "https://api.moonshot.cn/v1/chat/completions",
      "model": "kimi-k2",
      "api_key_env": "MOONSHOT_API_KEY",
      "reasoning_enabled": false,
      "timeout": 120
    },
    "local": {
      "api_url": "http://localhost:11434/v1/chat/completions",
      "model": "qwen3:32b",
      "api_key_env": ""
    }
  },
  "api": { "default_model": "kimi" }
}
```

**档案字段**：

| 字段 | 说明 |
|---|---|
| `api_url` | **必填**。端点 URL（OpenAI Chat Completions 协议） |
| `model` | **必填**（除非调用时传裸模型名）。该档案使用的模型名 |
| `api_key_env` | API Key 环境变量名；**未配置回退 `api.api_key_env`；显式空串 `""` 为本地免密端点**（Ollama/vLLM）。档案端点环境变量取不到时**直接报错，绝不回退 `~/.cert/deepseek.key`**——该文件为 DeepSeek 专属凭据，跨厂商复用属于泄漏（key 文件回退仅对无档案的旧 `api` 扁平节生效） |
| `reasoning_enabled` | 档案级思考模式开关（缺省回退 `api.reasoning_enabled`） |
| `timeout` | 请求超时秒数 |
| `temperature` | 档案级默认采样温度（可选）。优先级：CLI/调用方显式传入 > 本字段 > 全局默认；reasoning 模型建议按厂商文档设置 |
| `display_name` | 展示名（可选，缺省用档案名）。`client.name` 用于对话上下文中的 assistant 名，不再恒显示 "DeepSeek" |
| `fallback` | 失败转移档案名列表（可选，如 `["kimi", "local"]`，一层不递归）。主档案瞬时故障（HTTP 429/5xx、连接失败/超时）时按序转移；认证/参数类 4xx **不转移**（配置问题响亮暴露）；失败档案进入 60s 冷却期（429 响应带 Retry-After 数字秒时按其延长）。`chat_completion`（对话压缩/轻量子代理）无条件转移；`stream_chat`（主对话流式）仅**首事件前**转移（已输出内容/用户中断绝不转移） |
| `failover_timeout_budget` | 整条转移链的总时间预算秒数（可选，缺省不限）。耗尽即停止尝试剩余档案，防止长链故障时超时叠加（3 档案最坏 3×timeout）；`FailoverClient.get_stats()` 暴露 requests/failovers/cooldown_skips/exhausted 计数，`on_failover(from, to, reason)` 回调供管理面/前端通知接入 |

**选择优先级**：`--model <档案名>`（命中 `models` 节）→ `api.default_model` 指向档案 → 回退旧 `api` 扁平节。`--model` 为**进程级覆盖**：WebSocket 服务模式（主对话、每个会话实例、定时任务实例）与 `--once` 一次性模式均生效。

`--model` 传**裸模型名**（未命中档案）时，沿用默认端点与 Key，仅覆盖模型名——即 "同端点换模型" 语义。

**前缀逃逸**（档案名与裸模型名歧义时）：`--model model:NAME` 强制按裸模型名解析（档案恰好叫 NAME 时仍走裸名语义）；`--model profile:NAME` 强制按档案名解析（不存在则明确报错，不再静默落回裸名语义）。

**兼容性**：`models` 留空 `{}` 时行为与旧版完全一致（走 `api` 节），已有配置无需任何改动。

其余配置节：

  ```json
  {
    "context": {
    "max_history_messages": 500,
    "max_context_messages": 120,
    "max_tokens": 8192,
    "max_history_conversations": 5,
    "preserve_system_prompts": true,
    "compress_enabled": true,
    "compress_message_threshold": 200,
    "compress_token_threshold": 120000,
    "compress_ratio": 0.2,
    "compress_keep_recent": 8
  },
  "memo": {
    "default_reminder_minutes": 60,
    "max_memos_per_check": 10,
    "allow_user_tasks": true
  },
  "files": {
    "max_file_size": 10485710
  },
  "commands": {
    "enabled": true
  },
  "web": {
    "timeout": 10,
    "delay": 0,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "respect_robots": false,
    "max_download_size": 104857600
  },
  "memory": {
    "db_name": "memories.db",
    "max_search_results": 100,
    "default_importance": 2
  },
  "conversation": {
    "current_file": "current_conversation.json",
    "history_file": "conversation_history.json",
    "archive_dir": "archive"
  },
  "tools": {
    "max_result_length": 1048576,
    "save_result_to_file": false,
    "tool_results_dir": "tool_results"
  },
  "compressor": {
    "min_messages_to_compress": 5,
    "weight_decay_factor": 0.01,
    "max_input_chars": 6000,
    "llm_max_tokens": 800
  },
  "proxy": {
    "socks5": {
      "enabled": false,
      "host": "127.0.0.1",
      "port": 1080,
      "username": "",
      "password": null,
      "password_env": "SOCKS5_PASSWORD"
    }
  }
}
```

> 默认模型为 `deepseek-v4-flash`（通过 `api.deepseek_model` 修改）。设置 `api.reasoning_enabled: true` 可启用思考模式，此时会在请求中添加 `thinking: {type: "enabled"}` 参数，模型会先进行思考再回答，实时展示思考过程（灰色斜体区域），回答质量更高且**支持工具调用**（文件操作、命令执行、记忆搜索等工具均可在思考模式下正常使用）。
> 
> API Key 从环境变量 `DEEPSEEK_API_KEY` 读取（亦支持 `~/.cert/deepseek.key` 文件）。
>
> **代理密码不落盘**：`proxy.socks5.password` 仅在运行期内存中有效，保存配置时强制置空；跨重启持久化请将密码存入环境变量（如 `SOCKS5_PASSWORD`），配置 `password_env` 指向该变量名。加载时检测到旧版明文密码会自动迁移（本会话保留可用，文件立即重写脱敏）。
> 
> **多厂商切换**：大模型调用已通过 `aibox/llm_client.py` 抽象层解耦，业务层不再直接依赖具体厂商。配置 `api.provider`（默认 `deepseek`）即可切换厂商，当前内置 `DeepSeekClient`（兼容 OpenAI Chat Completions 协议，Kimi / Qwen / GLM / Doubao 等绝大多数厂商均可复用）。新增厂商只需在此文件新增一个 `LLMClient` 子类并在 `create_llm_client` 工厂中注册，无需改动业务代码，为后续子代理/多模型共发预留了扩展点。

## 🛠️ 命令行参数

```bash
python aibox.py [选项]

选项:
  --verbose, -v         显示详细日志
  --debug LEVEL         调试级别 (0-3)
  --lang zh|en          强制语言
  --host HOST           WebSocket 主机
  --port PORT           WebSocket 端口
  --no-scheduler        禁用任务调度器
  --scheduler-interval  调度器检查间隔（秒）
  --config PATH         指定配置文件路径
  --check-memo          检查到期任务并退出
  --memo-add "标题|内容|时间"  添加任务
  --memo-list           列出任务
  --memo-complete ID    完成任务
  --once QUERY          一次性对话：执行 QUERY 后退出（不启动 WebSocket）
  --quiet               一次性模式静默输出（stdout 仅回答正文）
  --show-reasoning      一次性模式输出思考流（默认静默，走 stderr）
  --no-save-memory      一次性模式禁用记忆工具（干净跳过，不进请求 schema）
  --worker-only         一次性模式纯执行面（worker profile，无人格/记忆工具）
  --model NAME          模型档案名（models 节）或裸模型名（同端点换模型）；歧义时 model:NAME 强制裸名 / profile:NAME 强制档案；服务模式与 --once 均生效（进程级覆盖）
  --temperature T       覆盖配置的对话温度
  --once-timeout SEC    一次性模式全局超时（默认 300 秒）
```

### 使用示例

```bash
# 启动服务器（详细日志）
python aibox.py --verbose

# 指定端口
python aibox.py --port 9000

# 检查任务
python aibox.py --check-memo

# 添加任务（命令行）
python aibox.py --memo-add "提醒开会|下午3点会议室A|+1h"

# 查看所有任务
python aibox.py --memo-list
```

### 💻 CLI 一次性对话（--once）

不启动 WebSocket，单次执行后退出。回答正文走 **stdout**，工具过程/思考流走 **stderr**，可直接管道组合：

```bash
# 基础问答
python aibox.py --once "帮我看下当前日期"

# 管道组合（--quiet 静默模式）
python aibox.py --once --quiet "列出当前目录 py 文件" | grep ".py" | wc -l

# 按档案名选模型 / 同端点换模型
python aibox.py --once --model kimi "总结这段代码"
python aibox.py --once --model deepseek-v4-lite "1+1?"

# 纯执行面（无人格/记忆，单次收敛，适合脚本调用）
python aibox.py --once --worker-only "执行 echo hello 并返回输出"

# 显式开启思考流 / 禁用记忆写入
python aibox.py --once --show-reasoning --no-save-memory "分析这个问题"
```

**退出码**：`0` 成功 / `1` 工具执行失败 / `2` API 调用失败 / `3` 用户中断（Ctrl+C）/ `4` 全局超时。

## 🔄 架构流程

```
┌─────────────────────────────────────────────────────────┐
│                    WebSocket 客户端                      │
└─────────────────┬───────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│              WebSocket 服务器 (端口 8765)               │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐  │
│  │  消息处理器  │  │  命令处理器  │  │  任务调度器   │  │
│  └─────────────┘  └─────────────┘  └───────────────┘  │
└─────────────────┬──────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│                  AI 对话引擎 (DeepSeek)                  │
├─────────────────────────────────────────────────────────┤
│  ┌───────────┐  ┌───────────┐  ┌──────────────────┐   │
│  │ 提示词管理器│  │ 工具注册器 │  │  对话压缩器      │   │
│  └───────────┘  └───────────┘  └──────────────────┘   │
└─────────────────┬───────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│                   数据持久层                            │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌────────────────────┐   │
│  │ 记忆数据库 │  │ 备忘录DB  │  │  对话历史管理器    │   │
│  └──────────┘  └──────────┘  └────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## 📊 消息流示例

### 普通对话流程
```
Client -> Server: {type: "message", content: "帮我查天气"}
Server -> Client: {type: "line", content: "🔧 调用工具: search_skills\n"}
Server -> Client: {type: "line", content: "🔧 调用工具: bing_search\n"}
Server -> Client: {type: "chunk", content: "今日天气晴朗..."}
Server -> Client: {type: "complete", content: "今日天气晴朗，温度25°C"}
```

### 任务执行流程
```
Client -> Server: {type: "task", action: "add", title: "提醒", delay: 60}
Server -> Client: {type: "task_added", task_id: 1}

... 60秒后 ...

Server -> Client: {type: "task_status", task_id: 1, status: "executing"}
Server -> Client: {type: "task_process", task_id: "1", content: "处理中..."}
Server -> Client: {type: "task_status", task_id: 1, status: "complete"}
```

### 对话重置流程
```
Client -> Server: {type: "command", command: "/new"}
Server -> Client: {type: "command_result", content: "🔄 开始新对话..."}
Server -> Client: {type: "dialogue_reset", content: "✅ 对话状态已完全重置"}
```

## 📝 开发指南

### 添加新工具

1. 继承 `Tool` 基类（位于 [tools/base.py](tools/base.py)）
2. 实现 `get_name()`、`get_description()`、`get_parameters()`、`execute()`
3. 可选实现 `execute_streaming()` 支持流式输出（通过 `output_queue` 发送 `("line", content)` 和 `("complete", final_result)`）
4. 按工具类别放入对应文件：
   - 基础工具（文件/网页/计算/时间）→ `tools/basic_tools.py`
   - 命令执行 → `tools/command_tool.py`
   - 技能管理 → `tools/skill_tools.py`
   - 数据库工具 → `tools/db_tools.py`
   - 提示词/身份更新 → `tools/prompt_tools.py`
   - 代理配置 → `tools/proxy_tools.py`
   - 其他 → `tools/misc_tools.py`
5. 在 [tools/__init__.py](tools/__init__.py) 的 `__all__` 中导出新类
6. 在 `DeepSeekChat._register_tools()` 中按 `tool_profile`（orchestrator/worker）注册

```python
class MyTool(Tool):
    def get_name(self) -> str:
        return "my_tool"

    def get_description(self) -> str:
        return "我的自定义工具"

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {"param": {"type": "string"}},
            "required": ["param"]
        }

    def execute(self, param: str, **kwargs) -> str:
        return f"执行结果: {param}"
```

> **依赖约束**：`tools/` 包只能依赖 `common` + `subsystems` + 标准库，不得反向引用 `aibox.py` 任何符号；如需访问 aibox 内部状态，应通过构造函数注入（参考 `MemoryTool(memory_db=...)` 模式）。

> **注意**：`execute()` 必须返回字符串。工具结果会经过多层安全防护：
> 1. **工具层源头限制**：文件/网页工具返回 markdown 指引，实际 I/O 由 `run_command` 完成（`run_command` 输出≤1MB，超出立即终止进程；`read_file` 指引命令限制 500 行、单行≤10KB）
> 2. **`_sanitize_tool_result()`**：None/非字符串转为安全字符串，过滤控制字符
> 3. **最终兜底截断**：超过 `tools.max_result_length`（默认1MB字符）时截断并提示，避免破坏 API 请求格式

### 添加新命令

在 `WebSocketHandler` 的命令处理分支（`handle_command`）中添加：

```python
elif command == "/mycommand":
    result = "命令执行结果"
    await self.send_message(websocket, {"type": "command_result", "content": result})
```

### 错误恢复机制

`think_and_respond()` 在每轮对话开始前记录消息列表长度 `msgs_before_turn`。若 API 请求失败（网络错误、HTTP 错误、JSON 解析错误或其他异常），会自动调用 `_rollback_messages(msgs_before_turn)` 回滚本轮添加的所有消息（包括 user 消息、可能的 assistant/tool_calls 消息、tool 响应消息），防止格式错误的消息污染对话历史导致后续请求持续失败。客户端收到 `error` 消息后可安全重试。

## ❓ 常见问题

### Q: API Key 无效怎么办？
**A:**
1. 确认环境变量已设置：`echo $DEEPSEEK_API_KEY`
2. 检查配置文件中的 `api_key_env` 设置
3. 创建 `~/.cert/deepseek.key` 文件并写入 API Key

### Q: 技能不生效？
**A:**
1. 确认文件在 `~/.aibox/skills/` 目录
2. 确认文件可执行（`chmod +x`）
3. 调用 `reload_skills` 工具
4. 使用 `search_skills` 验证
5. 检查文件命名（不能有扩展名）

### Q: 任务没有执行？
**A:**
1. 检查调度器是否启用：`/tasks`
2. 查看任务状态：`/memos`
3. 检查系统时间是否正确
4. 查看日志：`tail -f ~/.aibox/ai.log`
5. 确认任务有 `reminder_time`

### Q: 对话压缩丢失了重要信息？
**A:** 调整 `config.json` 的 `context` 配置：
```json
"compress_message_threshold": 300,  // 调大阈值
"compress_keep_recent": 20          // 保留更多最近消息
```

### Q: 如何让 AI 使用特定技能？
**A:** 在对话中明确指示：
```
使用 search_skills 搜索天气相关技能，然后用 run_command 执行它
```

### Q: 内存或日志占用过大？
**A:**
1. 调整日志配置：`log_max_size`、`log_backup_count`
2. 清理旧对话归档
3. 定期清理工具结果缓存

### Q: WebSocket 连接断开？
**A:**
1. 检查服务器是否运行：`ps aux | grep aibox.py`
2. 确认端口未被占用：`netstat -tlnp | grep 8765`
3. 检查防火墙设置
4. 查看服务器日志：`tail -f ~/.aibox/ai.log`
5. 考虑使用 `--host 0.0.0.0` 允许外部连接

### Q: 工具调用后 API 报错无法继续？
**A:** 此问题已在 v2.1 修复：工具结果会经过安全清洗（控制字符过滤、非字符串转换、超长截断），API 请求失败时自动回滚本轮消息。如果仍出现问题：
1. 查看日志：`tail -f ~/.aibox/ai.log`
2. 使用 `/new` 命令重置对话
3. 确认工具返回值为字符串且不含二进制/控制字符

## 📄 更新日志

### v3.5
- ✅ **安全审计与修复（第三轮 Batch G + 第四轮 Batch H + 遗留收尾 Batch I）**（完整记录见 [docs/reports/aibox_audit_20260817.md](../docs/reports/aibox_audit_20260817.md)）：
  - **Batch G（llm 加固 + 线程生命周期 + 取消传播 + DB 事务）**：
    - llm_client：非瞬时 HTTP 错误体截断 `[:200]`（密钥/错误页不进异常栈）、Retry-After 上限 cap 3600s、fallback 档案缺 model 跳过并告警、fallback 去重、删除 `LLMClient._cancelled` 死状态、FailoverClient 并发契约文档化
    - 子代理取消传播：`run_full_subagent` 共享父 `_cancel_event` + 0.2s 观察者线程转发深度中断（强关在途 HTTP、kill 子进程）；`SubAgentTool` 构造注入父事件
    - drain 线程重构：批次级单线程 + stop 事件 + try/finally 统一回收（消除每次工具调用泄漏一个常驻线程）；`/new` 的 `reset_conversation` 移入线程池（不再同步获取 `_msg_lock` 冻结事件循环）；broadcast 收窄 except + unregister 改 discard；`_processing_tasks` 改按连接的任务集合登记（关闭时取消全部在途任务）
    - DB：FTS5 关键词双引号翻倍转义（`"` → `""`）、两处 `LIMIT` f-string 参数化、`mark_as_triggered` 读写收进单连接 `BEGIN IMMEDIATE` 事务（消灭并发触发竞态）；历史索引裸 except 收窄 + 归档文件名同秒加序号
  - **Batch H（第四轮审计，3 路并行审读 + Linux PoC 6/6 实证）**：
    - **H1（高危）**：修复 N5 回归——子代理共享父取消事件后首轮 think 无条件 `clear()` 会吞掉父级已置位的取消信号（用户中断 / --once 超时被绕过），共享事件时跳过 clear + 父已取消直接早退
    - `/reload_prompts` 移入线程池（N3 同类遗漏）；技能扫描（串行 `--help` 子进程）启动前预热，不再在首条消息/首个任务时冻结事件循环
    - 调度器任务级超时（`scheduler.task_timeout`，默认 600s）——AI think 卡死不再停摆整个调度循环
    - `get_recent_memories(limit=None)` 默认上限（不再全表加载）；`memo delete` 改物理删除（此前只标记完成）；`PromptManager.reload` 本地构建后原子替换（并发读者不再读到空/半截系统提示词）
    - read_file 的 sed 参数强转 int（防引号注入）；`set_proxy` 落盘失败如实报告；SSE 非 UTF-8 行容错；persona 路径 realpath 校验（软链越界拒绝）；1MB 截断无半个字符；compressor 除零防护；InstallSkillTool 指引命令路径引用；ask_user 缺参明确报错
    - **POSIX 进程组终止**：executor 的 `Popen` 加 `start_new_session` + 组信号 kill（组 ID 在 Popen 后立即捕获）——`(sleep 300 &)` 形态的孙进程持管道不再悬挂 reader 线程/泄漏 fd（实证修复前孙进程残留）
    - web 工具 scheme 白名单（http/https，`file://` 等拒绝）；registry 日志打码改包含匹配（apiKey/passwd 变体）；FailoverClient 日志 Bearer 令牌正则脱敏（网关回显 Authorization 头不再进日志）
  - **Batch I（遗留收尾）**：
    - **N9**：prompt 写入内容长度上限（`MAX_CONTENT_LENGTH=10000`，identity/soul/status 超长拒绝）——约束持久化注入面
    - **F5**：情绪子代理取消传播——`run_light_subagent` 改走 stream_chat + 观察者线程，用户中断可即时终止（此前阻塞至请求超时）
    - 提醒时间格式异常任务按"到期"返回（此前被 SQL 层字符串比较静默丢弃，任务永不执行）；`mark_as_triggered` 加 `busy_timeout=10000`（多进程同库写竞争等待而非立即报 locked）
    - 对话文件原子写（tmp + os.replace，崩溃不留半截 JSON）；`load_conversation` realpath 越界防护；WS 命令专用线程池（`/new`、`/reload_prompts` 不再占用默认池）；--once 硬超时兜底（软超时后宽限一个周期强杀进程）
    - PromptManager 单例加锁；`limit=0` 语义修正（None 才不加 LIMIT）
- ✅ **测试**：424 项全过（Linux 环境；Batch G 20 + Batch H 26 + Batch I 16 项新增；`test_execute_timeout`/`test_flat_api_still_reads_key_file` 为 Windows 向用例，Linux 预存失败与本轮无关）

### v3.4
- ✅ **多模型档案加固（H1/H2/H3/M1/M2/M3 六项修复）**：
  - **H1（凭据隔离）**：档案端点环境变量取不到时直接报错，**绝不回退读 `~/.cert/deepseek.key`**（该文件为 DeepSeek 专属凭据，跨厂商复用属于泄漏）；key 文件回退仅对无档案的旧 `api` 扁平节生效
  - **H2（schema 校验前移）**：档案缺 `api_url`/`model` 或类型错误时，解析期即抛出指明档案名与缺失字段的 `ValueError`——不再把档案名当模型名发出、不再延迟到 `requests InvalidURL` 或 API 400
  - **H3（--model 服务模式生效）**：`--model` 升级为进程级覆盖，WebSocket 服务模式（主对话/每个会话实例/定时任务实例）与 `--once` 均生效，此前服务模式静默忽略
  - **M2（防模型名幻觉）**：`create_subagent` 工具描述与 JSON Schema 动态携带 `models` 节可用档案名列表（默认档案标注），LLM 无从编造名称；`models` 为空时描述保持原样（零破坏）
  - **M1（换模型同步派生引用）**：新增 `DeepSeekChat.set_client()` 统一替换 LLM 客户端——同步刷新 `compressor.llm_client` 与 `api_key`/`name` 快照；`create_subagent` 子代理换模型经此入口，压缩链不再残留默认模型
  - **M3（reasoning 契约显式化）**：档案级 `reasoning_enabled` 从"构造后挂实例属性"改为 `LLMClient` 构造参数（三态：`True`/`False` 档案级显式、`None` 未配置回退全局），外部误设属性无从静默覆盖全局开关
- ✅ **L 批体验与兼容修复（L1/L2/L3/L5 四项）**：
  - **L1（展示名按档案）**：`client.name` = 档案 `display_name` > 档案名，用 Kimi/Qwen 时对话上下文不再误标 "DeepSeek"；旧扁平节保持 "DeepSeek"（零破坏）
  - **L2（前缀逃逸）**：`--model model:NAME` 强制裸模型名语义、`--model profile:NAME` 强制档案语义（不存在时明确报错并列出可用档案），档案名与模型名歧义可逃逸
  - **L3（档案级温度）**：档案 `temperature` 字段作为默认采样温度，优先级：CLI/调用方显式传入 > 档案字段 > 全局默认（0.7/0.3），主对话/轻量子代理/compressor 全链路生效
  - **L5（思考流字段别名）**：`_parse_delta` 识别 `reasoning_content`/`reasoning`/`thinking`/`thinking_content` 四种常见思考字段（首个命中生效），其他厂商思考流不再静默丢失
- ✅ **L4a（chat_completion 跨档案失败转移）**：档案新字段 `fallback`（档案名列表，一层不递归）；`create_llm_client` 构造 `FailoverClient` 包装链（主档案 + fallback）。瞬时故障（HTTP 429/5xx 抛 `TransientLLMError`、连接失败/超时）按链转移，认证/参数类 4xx 不转移（配置问题响亮暴露）；失败档案 60s 冷却；fallback 档案缺 Key 等环境问题跳过 + warning 不阻断主档案；配置结构错误（类型/不存在/自引用）启动期硬性拦截。当前覆盖对话压缩与轻量子代理链路，流式（`stream_chat`）转移属 L4b
- ✅ **L4b（stream_chat 首事件前转移 + 中断联动）**：流式仅在**未产出任何事件**（reasoning/content/tool_call）时转移——重放对用户不可见；已产出事件后失败直接上抛（防前端重复输出/工具增量错乱）。`LLMClient.cancel()` 置 `_cancelled` 标志，`FailoverClient.cancel()` 广播整条链并置包装器标志——用户中断引发的连接异常与真实故障不可区分，凭标志跳过转移（否则用户越中断、系统越换厂商重试）；`chat_completion` 同受标志保护。链耗尽抛最后异常（既有回滚/报错处理接住），全链冷却抛 RuntimeError
- ✅ **L4c（转移可观测性 + 预算控制 + Retry-After）**：`FailoverClient.get_stats()` 暴露 requests/failovers/cooldown_skips/exhausted 聚合计数；`on_failover(from, to, reason)` 回调（to=None 表示链耗尽，回调异常不影响主流程）供管理面/前端通知接入；档案字段 `failover_timeout_budget` 限制整链尝试总秒数（缺省不限，<=0 视为未配置），耗尽停止尝试剩余档案防超时叠加；429 响应 Retry-After（数字秒）经 `TransientLLMError.retry_after` 延长该档案冷却期（非数字格式忽略）
- ✅ **测试**：355 项全过（H1 隔离 4 + H2 校验 6 + H3 传导 6 + M2 提示 5 + M1 替换 2 + M3 契约 4 + L 批 16 + L4a 转移 15 + L4b 流式 12 + L4c 可观测 13 项新增）

### v3.3
- ✅ **安全审计与修复**（两轮审计报告见 [docs/reports/aibox_audit_20260817.md](../docs/reports/aibox_audit_20260817.md)）：
  - **Batch E（命令执行保护链修复）**：`CommandLineTool.execute_streaming` 纯委托注入的 executor（删除绕过 1MB/超时/中断保护的 150 行重复 Popen 实现）；`SubprocessCommandExecutor` 读取循环线程化（后台 reader 线程 + 0.1s 超时轮询，静默命令超时不再失效）；两处 `Popen` 改 `stdin=DEVNULL`（等待输入的命令不再永久挂起工具线程）；write_file 指引 heredoc 定界符唯一化；registry 流式转发循环加 3600s 总超时兜底。PoC 回归 5/5 + 全量单测通过
  - **Batch F（凭据保护 + 线程收尾）**：
    - **F1**：SOCKS5 代理密码不再明文落盘——`_save_config` 落盘前强制脱敏（纵深防御），新增 `password_env` 环境变量注入（对齐 `api_key_env` 惯例），旧版明文配置加载时自动迁移清除；`registry` debug 日志对 `password`/`api_key`/`token`/`secret`/`authorization` 参数打码
    - **F2**：registry 流式转发超时路径统一清理 `tool.streaming_output`/`tool.output_queue`（消除僵尸 daemon 线程经实例属性把输出混入后续会话队列的串流路径），`stream_tool_timeout` 提为实例属性（默认 3600s，测试可调）；`SubprocessCommandExecutor` reader 线程收尾——正常 EOF 后显式 close 释放管道 fd，仍阻塞时有限 join 后放弃（daemon 兜底）；实证修正：CPython 流锁下阻塞中的 readline 无法被 `close()` 中断（首版实现致超时被拖满命令时长，已按 PoC 回归修正）
- ✅ **测试**：272 项全过（Batch E PoC 回归 + F1 配置脱敏 3 项 + F2 超时清理/线程收尾 5 项新增）

### v3.2
- ✅ **CLI 一次性对话模式（`--once`）**（分三批落地，开发文档见 [docs/reports/aibox_cli_once_mode_dev_doc.md](../docs/reports/aibox_cli_once_mode_dev_doc.md)）：
  - **Batch A**：`--once QUERY` / `--quiet` / `--once-timeout`；不启动 WebSocket，执行后退出；回答走 stdout、过程走 stderr，可管道组合；SIGINT 与超时复用三层取消机制
  - **Batch B**：`--no-save-memory`（`enable_memory` 开关干净禁用 MemoryTool）、`--show-reasoning`（思考流默认静默）；压缩提示改走 output_callback 不再污染 stdout；退出码 1（工具执行失败）
  - **Batch C**：SQLite WAL（多 CLI 进程并发写锁缓解）、`--worker-only`（纯执行面 + 精简 system prompt 防人格污染）、`--model` / `--temperature` 覆盖、子代理工具失败标记透传（`_SUBAGENT_TOOL_FAILED_MARKER` → orchestrator stats）
- ✅ **多模型档案（`models` 配置节）**：配置多个 OpenAI Chat Completions 兼容端点（DeepSeek/Kimi/Qwen/本地 Ollama 等）按档案名切换；`--model` 支持档案名与裸模型名（同端点换模型）双语义；档案级 `api_key_env` 三级语义（回退 / 显式 / 空串免密）；档案级 `reasoning_enabled` 优先于全局；`models: {}` 时零破坏回退旧 `api` 节；`llm_client.py` 增加 `openai_compatible` / `openai` provider 别名
- ✅ **测试**：264 项全过（once 模式 15 项 + 多模型档案 17 项新增）

### v3.1
- ✅ **修复 FTS5 中文搜索失效**（[subsystems/db.py](subsystems/db.py)）：
  - FTS5 虚拟表改用 `tokenize='trigram'` 分词器（需 SQLite >= 3.34，环境不支持时自动退回 unicode61）
  - 旧 unicode61 表在启动时自动迁移：DROP 重建后从 memories 表回灌全量数据
  - `search_memories` 增加 LIKE 模糊检索兜底（trigram 对 <3 字符中文短词无法命中；关键词中 `%`/`_` 已转义为字面字符；MATCH 语法错误不再抛异常）
- ✅ **修复对话压缩 Token 阈值不生效**：
  - `should_compress` 补齐 Token 数判断：消息条数或 Token 数任一达到阈值即触发压缩（此前 `compress_token_threshold` 为死配置）
  - `compress_conversation` 配套：Token 触发（消息数未超阈值）时按比例压缩早期消息，不再因目标消息数计算而跳过
- ✅ **ConversationCompressor 抽离至 [subsystems/conversation_compressor.py](subsystems/conversation_compressor.py)**（Wave 3）：
  - 构造注入 `llm_client`（客户端抽象层）替代 AI 引擎实例，打破与 `DeepSeekChat` 的循环引用
  - aibox.py 3517 → 3018 行；配套 17 项单元测试（估算/阈值判断/压缩流程/LLM 失败回退/孤立 tool 消息清理）

### v3.0
- ✅ **模块化重构（aibox.py 8450 行 → 3706 行，缩减 56%）**：
  - **A 批基础工具抽离**（[tools/basic_tools.py](tools/basic_tools.py)）：`FileToolBase` / `ReadFileTool` / `WriteFileTool` / `ListFilesTool` / `SimpleWebCrawlerTool` / `GetCurrentTimeTool` / `CalculatorTool` 共 7 类，无外部子系统依赖；配套 43 项单元测试
  - **B 批管理面工具抽离**（[tools/](tools/) 目录）：`CommandLineTool` / `SearchSkillsTool` / `ReloadSkillsTool` / `InstallSkillTool` / `CreateSkillTool` / `MemoryTool` / `MemoTool` / `UpdateIdentityTool` / `UpdateSoulTool` / `SaveMemoryAndEndConversationTool` / `SetProxyTool` / `GetProxyTool` / `AskUserTool` 共 13 类，按职责分 7 个文件；配套 58 项单元测试
  - **Wave 1 子系统抽离**（[subsystems/](subsystems/) 目录）：`CommandExecutor` + `SubprocessCommandExecutor`（executors.py）、`PromptManager`（prompt_manager.py）、`MemoryDatabase` + `MemoDatabase`（db.py）、`SkillManager`（skill_manager.py）共 4 子系统；配套 70 项单元测试
  - **测试套件**：[tests/](tests/) 目录 171 项全过，覆盖 A/B 批工具与 Wave 1 子系统
  - **修复**：`subsystems/db.py` 的 `get_recent_memories(limit=None)` SQL datatype mismatch bug（`LIMIT ?` 传 None 时移除 LIMIT 子句）
  - **架构约束**：建立 `common ← subsystems ← tools ← aibox` 单向依赖，禁止反向引用；`tools/__init__.py` 与 `subsystems/__init__.py` 采用"重导出"策略，调用点无需关心子模块位置
  - **未抽离**：`TaskScheduler`、C 批工具（`ExpressEmotionTool` / `SubAgentTool`）、核心类（`DeepSeekChat` / `WebSocketHandler` / `EnhancedAIManager`）仍保留在 `aibox.py` 内（`ConversationHistory` 于 Wave 2、`ConversationCompressor` 于 v3.1 抽离）

### v2.9
- ✅ **编排者-执行者工具分层（工具集 profile）**：
  - `DeepSeekChat` 新增 `tool_profile` 参数（`orchestrator` / `worker`），`_register_tools()` 按 profile 分组装配工具集
  - **主对话（orchestrator）**：仅管理面工具（记忆/备忘录/情绪/子代理派生/技能查询与创建/人格更新/代理设置），不再直接持有 `run_command` 与文件/网页等执行面工具，执行类任务一律经 `create_subagent` 派生子代理完成
  - **子代理（worker）**：仅执行面 7 工具（`run_command` / `read_file` / `write_file` / `list_files` / `web` / `calculator` / `get_current_time`），无记忆/人格更新/派生/提问能力——修复子代理可调用 `update_soul` 改写主人格、可递归派生子代理的隐患
  - 子代理角色提示词追加行为约束：不得提问（疑问写入最终回答）、产物文件一律落盘 `~/.aibox/workspace/` 并给出绝对路径
  - 配套提示词调整：`99_SOUL.md` 角色定位改为编排者，`7_extension_skills.md` 技能执行路径改为 `search_skills` → 派生子代理执行（运行期 `~/.aibox/prompts/` 更新）

### v2.8
- ✅ **新增 `express_emotion` 工具**：表达情绪并记录当前情绪状态（anger/joy/sadness/surprise），可指定 `persona` 从 `~/.aibox/skills/` 加载人格技能文件（如 angry/coquettish/tsundere），自动派生携带该人格的情绪子代理生成回应，保证人格独立、不污染主对话上下文
- ✅ **新增 `/search <关键词>` 命令**：在 WebSocket 会话中按关键词搜索记忆，返回匹配的记忆条目

### v2.7
- ✅ **新增子代理功能（`create_subagent` 工具）**：
  - 主代理可派生出拥有**独立上下文窗口**和**独立角色设定**的轻量子代理，不污染主对话上下文
  - 轻量子代理：无工具，通过一次 LLM 补全返回结果，适合长任务分解、上下文隔离、独立角色专注处理（如代码审查、文本总结、资料研究）
  - 子代理可选用**不同模型**（复用 `llm_client` 抽象层，`create_llm_client` 新增 `model` 参数）
  - 参数：`role`（角色/系统提示词，必填）、`task`（任务，必填）、`model`/`temperature`/`max_tokens`（可选）
  - `max_tokens` 已钳制到 `[1, 主配置 DEFAULT_MAX_TOKENS]`，防止意外超限消耗

### v2.6
- ✅ **修复 Ctrl+C 中断无法停止服务器的问题**：
  - 新增 `interrupt` WebSocket 消息类型处理（BotOps 前端 Ctrl+C 发送此消息），立即回复 `interrupt_ack` 确认
  - `DeepSeekChat` 新增 `cancel()` 方法和 `_cancel_event`（threading.Event），线程安全可跨线程调用
  - **三层取消机制**：
    1. 设置取消标志，流式响应循环每轮检查，发现取消立即 break
    2. 关闭当前 HTTP response 对象，中断 `iter_lines()` 阻塞读取
    3. 终止正在运行的子进程（`SubprocessCommandExecutor.cancel()` → `process.kill()`）
  - 工具调用循环中每个工具执行前检查取消信号，中断后不再继续后续工具
  - API 请求异常/JSON解析异常/未知异常中均检查取消标志，若因取消导致异常则按中断处理而非报错
  - 中断后自动回滚本轮消息，防止污染对话历史
  - 新增 `EnhancedAIManager.cancel()` 统一入口，同时终止 AI 和命令执行器

### v2.5
- ✅ **新增 DeepSeek 思考模式支持**（Reasoning Mode）：
  - 新增 `api.reasoning_enabled` 配置项（默认 `false`），启用后在请求中添加 `thinking: {type: "enabled"}` 参数
  - 使用 `deepseek-v4-flash` 模型原生思考能力，**思考模式下完整支持工具调用**（文件操作、命令执行、记忆搜索等均可正常使用）
  - 实时展示 AI 思考过程：新增 `reasoning_start`/`reasoning`/`reasoning_end` WebSocket 消息类型
  - 思考内容在前端以灰色斜体区域独立展示，超长思考自动折叠显示最后 2000 字符
  - 即使未启用思考模式，若模型返回 `reasoning_content` 也会自动实时展示
  - API 超时默认从 30s 提高到 120s，适配思考模式更长的响应时间
- ✅ 删除重复定义的 `_add_assistant_message_with_reasoning` 方法

### v2.4
- ✅ **新增专用工作目录规范**：
  - AI 主动创建的临时文件、脚本、数据文件默认写入 `~/.aibox/workspace/`，避免污染系统其他目录
  - `write_file` 工具默认路径设为工作目录（用户明确指定路径时除外）
  - `run_command` 工具默认 `work_dir` 设为工作目录，命令产生的文件落在专用工作区
  - 新增 `system.workspace_dir` 配置项（默认 `~/.aibox/workspace`），可自定义工作目录位置
  - 在文件操作和命令执行提示词中明确工作目录规范
- ✅ 命令执行提示词补充 1MB 输出大小限制说明

### v2.3
- ✅ **修复工具输出OOM崩溃问题**：
  - `read_file`：不再一次性 `readlines()` 加载整个文件，改为按行范围按需读取；大文件 `tail=true` 使用反向块读取（>1MB文件不全文遍历）；新增单行长度限制（10KB/行，超长截断）；`max_lines` 硬上限500行防止请求超大范围
  - `run_command`：非流式执行从 `capture_output=True` 改为逐行读取并实时计数，输出达到1MB立即终止进程；流式执行同样增加1MB输出大小限制，超出终止并提示截断；统一stdout/stderr合并计数
  - `tools.max_result_length` 默认值从10KB提高到1MB字符，作为最终兜底截断
  - 形成"工具层源头限制→结果清洗→最终兜底"三层防护，彻底防止大文件/大输出导致内存溢出

### v2.2
- ✅ 修复提示词系统排序 bug：`SKILL_PREFIX = "1_"` 只匹配 `1_` 开头文件，导致 `2_`~`10_` 技能提示词排序错乱（`10_task_handling.md` 排在 `1_memory_search.md` 前面）；改用正则按数字前缀排序
- ✅ 修复 `_init_system_prompts` 使用未排序列表的问题，确保 system prompt 按 identity → 技能 → soul → status 正确顺序加载
- ✅ 精简每轮用户输入的 pre_prompt：原来约 30 行重复指令（与 system prompt 内容重复），每轮浪费 ~300 token；精简为 5 条简洁提醒
- ✅ 修复工具调用后递归继续时重复注入工作流指令的问题：新增 `is_continuation` 参数，递归时跳过 pre_prompt
- ✅ 优化默认大文件阈值：50KB/200行 → 500KB/2000行，增加 `tail=true` 和二进制文件处理指导
- ✅ 优化记忆搜索习惯："每次都要做"→"根据话题相关性判断"，避免简单问候也触发无意义搜索

### v2.1
- ✅ 修复工具调用结果破坏对话导致 API 持续请求失败的问题
  - 不再回传 `reasoning_content`（API 响应字段）到请求中
  - `tool_call.id` 为空时自动生成 fallback id，防止 API 400
  - 新增 `_sanitize_tool_result()`：None/非字符串安全转换、控制字符过滤、超长结果截断
  - `execute_tool` 异常捕获，避免工具执行异常中断整个对话
  - API 请求失败时自动回滚本轮消息（`_rollback_messages`），防止坏消息污染历史
- ✅ 修复 `complete` 消息语义：chunk 流式为内容权威来源，complete 仅用于收尾
- ✅ 修正文档：入口文件为 `aibox.py`（非 `main.py`），补充缺失的消息类型（`line`/`task_error`/`notification` 等）

### v2.0
- ✅ 新增 `update_soul` 工具（智能体自我进化）
- ✅ 新增 `create_skill` 工具（AI 自主创建技能）
- ✅ 新增 `install_skill` 工具（技能安装指引）
- ✅ 新增 `set_proxy` / `get_proxy` 工具（SOCKS5 代理）
- ✅ 新增 `task_process` 消息类型（任务执行过程广播）
- ✅ 支持 Markdown 格式技能文件
- ✅ 对话压缩支持 Token 数阈值判断
- ✅ 技能热重载（`reload_skills`）
- ✅ 记忆搜索支持分页、任务调度器支持即时任务
- ✅ 命令执行支持超时控制

### v1.0
- ✅ WebSocket 服务器、文件/网页/记忆操作、任务调度、扩展技能、多语言、对话管理

## 📝 开发指南补充

### 扩展技能格式
技能文件命名与格式详见 [SKILLS_FORMAT.md](SKILLS_FORMAT.md)：可执行脚本需支持 `--help`，Markdown 技能首行为简介。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

---

**注意**：本项目需要 DeepSeek API Key，请确保遵守 API 使用条款。
