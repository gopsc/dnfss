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
- 🔄 **技能热重载**：无需重启即可添加、修改或删除扩展技能
- 🔐 **SOCKS5 代理**：通过 `set_proxy` / `get_proxy` 工具为网页访问与 API 调用配置代理，可持久化
- 💾 **对话管理**：历史对话归档、开始新对话（重置 AI 记忆并重新加载提示词）
- 📦 **对话压缩**：消息数或 Token 数达到阈值时自动压缩，保持上下文质量

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

> 注意：本模块入口文件为 `aibox.py`（单文件约 8200 行，集成 WebSocket 服务器、AI 引擎、工具/技能系统、任务调度器于一体）。`client.py` 为示例 WebSocket 客户端，`voice_client.py` 为语音客户端，`_run.sh` 为被 `core` 拉起的启动脚本。

## 📁 目录结构

```
aibox/
├── aibox.py           # 服务主程序（WebSocket 服务器 + AI 引擎 + 工具/技能系统 + 任务调度器）
├── client.py          # 示例 WebSocket 客户端
├── voice_client.py    # 语音客户端
├── entry.html         # 前端入口页
├── entry1.html        # 前端入口页（变体）
├── frontend.html      # 前端主页面
├── skills/            # 内置扩展技能（*.py 可执行脚本 / *.md Markdown 技能）
│   ├── hello-world.py
│   ├── aes-crypto.py
│   ├── bing_search.py
│   ├── facerec.py
│   ├── gitclient.py
│   ├── ssh-exec.py
│   ├── sftp-transfer.py
│   └── ...
├── SKILLS_FORMAT.md   # 技能文件格式说明
├── _run.sh            # 启动脚本（被 core 守护时调用）
├── _runcli.sh         # 命令行启动脚本
├── _set.sh            # 安装脚本
├── _setenv.sh         # 环境设置脚本
└── README.md
```

运行期数据存放于 `~/.aibox/`：

```
~/.aibox/
├── config.json              # 配置文件
├── prompts/                 # 系统提示词目录
│   ├── 00_IDENTITY.md       # 用户身份信息
│   ├── 1_*.md               # 基础技能提示词（文件操作、网页访问等）
│   ├── 99_SOUL.md           # AI 灵魂定义（角色、性格、能力边界）
│   └── 100_STATUS.md        # AI 状态记录
├── skills/                  # 扩展技能目录（运行时从仓库 skills/ 复制或用户自建）
├── memories.db              # 记忆和任务数据库（SQLite）
├── current_conversation.json # 当前对话
├── conversation_history.json # 对话索引
├── archive/                 # 历史对话归档
└── tool_results/            # 工具结果缓存
```

## 🔧 可用工具

AI 智能体可以调用以下工具来完成任务（由 `Tool` 基类派生，在 `DeepSeekChat` 中注册）：

| 工具名称 | 功能描述 |
|---------|---------|
| `read_file` | 读取文件内容（支持分页、head/tail） |
| `write_file` | 写入或追加文件 |
| `list_files` | 列出目录内容 |
| `web` | 访问网页、获取标题/文本/链接 |
| `memory` | 保存、搜索（支持分页）、删除记忆 |
| `memo` | 创建、查看、完成、搜索任务 |
| `run_command` | 执行系统命令（实时输出，支持超时控制） |
| `search_skills` | 搜索扩展技能（支持 Markdown 和可执行技能） |
| `reload_skills` | 热重载扩展技能 |
| `create_skill` | 创建新技能（AI 自主编写 Python 脚本） |
| `install_skill` | 技能安装指引 |
| `ask_user` | 向用户提问（AI 主动交互） |
| `update_identity` | 更新用户身份信息（`00_IDENTITY.md`） |
| `update_soul` | 更新 AI 自我定义（`99_SOUL.md`，智能体进化） |
| `save_memory_and_end_conversation` | 保存记忆并结束对话 |
| `get_current_time` | 获取当前时间 |
| `calculator` | 数学计算 |
| `set_proxy` | 设置 SOCKS5 代理（host/port/可选账号密码，可持久化，即时生效） |
| `get_proxy` | 查看当前 SOCKS5 代理配置 |

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

### 支持的命令

> 注意：记忆搜索通过 `memory` 工具（在对话中由 AI 调用）完成，本系统**不存在** `/search` 斜杠命令。

| 命令 | 功能 |
|-----|------|
| `/exit` | 退出连接 |
| `/new` | 开始新对话（保存当前，重置 AI 记忆，重新加载提示词） |
| `/list` | 列出所有历史对话 |
| `/memories` | 查看最近记忆（支持分页） |
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
| `complete` | AI 响应**收尾信号**（chunk 流式是内容权威来源，complete 仅用于收尾；完全丢帧时可用 full_text 补建） |
| `line` | 过程行（🔧 工具调用提示、工具输出、💬 助手前缀、思考提示等，供任务侧栏/终端显示） |
| `reasoning` | AI 思考过程（思考模式，仅 debug_level≥3 发送） |
| `error` | 错误信息（API 请求失败、解析失败等；服务端自动回滚本轮消息防止对话历史被污染） |
| `command_result` | 命令执行结果 |
| `task_status` | 任务状态更新（executing/complete/failed） |
| `task_process` | 任务执行过程中的 AI 输出 |
| `task_error` | 任务执行错误 |
| `task_added` | 任务添加成功 |
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

提示词文件位于 `~/.aibox/prompts/`：

| 文件 | 用途 | 更新方式 |
|-----|------|---------|
| `00_IDENTITY.md` | 用户身份信息 | AI 通过 `update_identity` 工具更新 |
| `1_*.md` | 基础技能提示词 | 手动编辑，AI 通过 `reload_prompts` 重载 |
| `99_SOUL.md` | AI 灵魂定义（角色、性格） | AI 通过 `update_soul` 工具更新 |
| `100_STATUS.md` | AI 状态记录 | AI 通过 `save_memory_and_end_conversation` 更新 |

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
    "prompts_dir": "~/.aibox/prompts"
  },
  "scheduler": {
    "enabled": true,
    "check_interval": 1.0,
    "max_tasks_per_run": 10,
    "broadcast_enabled": true
  },
  "api": {
    "deepseek_api_url": "https://api.deepseek.com/chat/completions",
    "deepseek_model": "deepseek-v4-flash",
    "api_key_env": "DEEPSEEK_API_KEY",
    "timeout": 30,
    "temperature": 0.7
  },
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
    "max_result_length": 10000,
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
    "type": "",
    "host": "",
    "port": 0,
    "username": "",
    "password": ""
  }
}
```

> 默认模型为 `deepseek-v4-flash`（通过 `api.deepseek_model` 修改）。API Key 从环境变量 `DEEPSEEK_API_KEY` 读取（亦支持 `~/.cert/deepseek.key` 文件）。

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

1. 继承 `Tool` 基类（位于 `aibox.py`）
2. 实现 `get_name()`、`get_description()`、`get_parameters()`、`execute()`
3. 可选实现 `execute_streaming()` 支持流式输出（通过 `output_queue` 发送 `("line", content)` 和 `("complete", final_result)`）
4. 在 `DeepSeekChat._register_tools()` 中注册

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

> **注意**：`execute()` 必须返回字符串。工具结果会经过 `_sanitize_tool_result()` 清洗（None/非字符串转为安全字符串，控制字符被过滤，超长结果截断），避免破坏 API 请求格式。

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
