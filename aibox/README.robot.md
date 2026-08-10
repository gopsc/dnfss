# AIBOX 模块技术手册

> 文档版本：2026-07-30
> 代码版本：v2.0
> 语言：Python 3.8+
> 定位：单 AI 智能体系统（独立于微服务体系）

---

## 一、模块概述

### 1.1 定位

**AIBOX** 是一个基于 DeepSeek API 的单 AI 智能体系统，通过 WebSocket 与客户端双向通信。所有任务（文件操作、网页访问、记忆管理、任务调度等）都由 AI 对话驱动执行。

### 1.2 与微服务体系的关系

| 属性 | 说明 |
|------|------|
| 启动方式 | `core`(PMC) 通过 `init.txt` 拉起 |
| 监听端口 | 8765（WebSocket） |
| 与主干关系 | **完全独立**，不调用 waf/gateway/dnfs 等服务 |
| 数据存储 | 本地 `~/.aibox/`（不使用 db:8005） |
| 前端 | 独立 HTML 页面 |

### 1.3 核心特性

| 特性 | 说明 |
|------|------|
| AI 对话 | DeepSeek API + 思考模式（reasoning 流式输出） |
| 实时通信 | WebSocket 双向通信 + 流式输出 |
| 工具系统 | 19 个内置工具（文件/网页/记忆/任务/命令等） |
| 技能扩展 | 热重载 `.py` 脚本和 `.md` 技能 |
| 记忆管理 | 自动保存/检索对话记忆 |
| 任务调度 | 一次性/重复/即时任务 |
| 自我进化 | AI 动态更新角色与用户画像 |
| 对话压缩 | 自动压缩长对话历史 |
| 多语言 | 中英自动检测 |

---

## 二、技术架构

### 2.1 技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 语言 | Python 3.8+ | 主程序 |
| 通信 | websockets | WebSocket 服务器 |
| AI | DeepSeek API | 智能对话、工具调用、流式输出 |
| 存储 | SQLite | 记忆数据库、备忘录任务 |
| HTTP | requests | 网页访问、API 调用 |
| HTML 解析 | beautifulsoup4 | 网页内容提取 |
| SSH | paramiko | SSH 远程执行（技能） |
| 加密 | cryptography | AES 加密（技能） |

### 2.2 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      WebSocket 客户端                       │
│  (entry.html / frontend.html / client.py / voice_client.py) │
└────────────────────────┬────────────────────────────────────┘
                         │ ws://localhost:8765
┌────────────────────────▼────────────────────────────────────┐
│                   WebSocket 服务器 (main.py)                │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ 消息处理器   │  │ 命令处理器   │  │  任务调度器      │  │
│  │  (handle_msg) │  │ (handle_cmd) │  │  (MemoScheduler) │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
└─────────┼─────────────────┼───────────────────┼─────────────┘
          │                 │                   │
┌─────────▼─────────────────▼───────────────────▼─────────────┐
│                  AI 对话引擎 (DeepSeekChat)                 │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ 提示词管理器 │  │ 工具注册器   │  │  对话压缩器      │  │
│  │ (PromptMgr)  │  │ (ToolRegistry)│ │ (ConversationComp)│  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
└─────────┼─────────────────┼───────────────────┼─────────────┘
          │                 │                   │
┌─────────▼─────────────────▼───────────────────▼─────────────┐
│                     数据持久层                              │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ 记忆数据库   │  │ 备忘录 DB   │  │  对话历史管理器  │  │
│  │ (memories.db) │  │ (memos.db)   │  │ (JSON files)     │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 核心数据流

```
客户端请求 → WebSocketHandler.handle_message()
    ↓
┌─── 消息类型判断 ───┬─── 命令类型判断 ───┬─── 任务操作判断 ───┐
│   type="message"   │  type="command"    │   type="task"      │
└─────────┬─────────┴─────────┬─────────┴─────────┬─────────┘
          │                   │                   │
          ▼                   ▼                   ▼
    AI 对话引擎         命令处理器          任务管理器
    (DeepSeekChat)    (handle_command)    (handle_task)
          │                   │                   │
          ▼                   ▼                   ▼
    工具调用循环       系统命令执行       任务 CRUD 操作
    (ToolRegistry)    (/new, /list...)   (add, complete...)
          │                   │                   │
          ▼                   ▼                   ▼
    流式响应生成       命令结果返回       任务状态广播
    (chunk → complete) (command_result) (task_status)
```

---

## 三、代码结构

### 3.1 文件结构

```
aibox/
├── main.py              # 核心（~8000 行）
│   ├── WebSocketHandler    # WebSocket 消息处理
│   ├── DeepSeekChat        # AI 对话引擎
│   ├── PromptManager       # 提示词管理
│   ├── Tool + 19 个工具类  # 工具系统
│   ├── MemoScheduler       # 任务调度器
│   ├── MemoryManager       # 记忆管理
│   └── ConfigManager       # 配置管理
├── client.py            # CLI WebSocket 客户端
├── voice_client.py      # 语音客户端（语音输入/输出）
├── entry.html           # Web 前端入口
├── frontend.html        # Web 主页面
├── skills/              # 内置扩展技能
│   ├── hello-world.py    # 示例技能
│   ├── bing_search.py    # 必应搜索
│   ├── ssh-exec.py       # SSH 远程执行
│   ├── sftp-transfer.py  # SFTP 传输
│   ├── facerec.py        # 人脸识别
│   ├── aes-crypto.py     # AES 加密
│   ├── gitclient.py      # Git 操作
│   └── *.md              # Markdown 技能（prompt）
├── tasks/ITIL.md        # ITIL 任务模板
├── SKILLS_FORMAT.md     # 技能格式规范
├── requirements.txt
├── _run.sh              # core 拉起的启动脚本
├── _runcli.sh           # CLI 启动
├── _set.sh              # 依赖安装
├── _setenv.sh           # 环境变量设置
└── README.md            # 用户文档
```

### 3.2 main.py 核心类

| 类名 | 行号 | 功能 |
|------|------|------|
| `Tool` | L3642 | 工具基类（抽象方法：get_name/execute） |
| `WebSocketHandler` | ~L1900 | WebSocket 消息处理 |
| `DeepSeekChat` | ~L2400 | AI 对话引擎 |
| `PromptManager` | ~L1050 | 提示词文件管理 |
| `ConfigManager` | ~L1080 | 配置加载/合并 |
| `MemoryManager` | ~L5200 | 记忆 CRUD |
| `MemoScheduler` | ~L4700 | 任务调度器 |
| `ConversationCompressor` | ~L6480 | 对话压缩 |

### 3.3 运行期数据

```
~/.aibox/
├── config.json                    # 配置（自动生成默认值）
├── prompts/
│   ├── 00_IDENTITY.md             # 用户身份（AI 可更新）
│   ├── 1_*.md                     # 基础技能提示词
│   ├── 99_SOUL.md                 # AI 灵魂定义（AI 可更新）
│   └── 100_STATUS.md              # AI 状态记录
├── skills/                        # 用户扩展技能
├── memories.db                    # 记忆库（SQLite）
├── memos.db                       # 备忘录任务（SQLite）
├── current_conversation.json      # 当前对话上下文
├── conversation_history.json      # 对话索引
├── archive/                       # 历史对话归档
├── tool_results/                  # 工具执行结果缓存
└── ai.log                         # 日志（自动轮转）
```

---

## 四、工具系统

### 4.1 工具基类

```python
class Tool:
    """工具基类，所有工具必须继承此类"""
    
    def get_name(self) -> str:
        """返回工具名称（抽象方法，必须实现）"""
        pass
    
    def get_description(self) -> str:
        """返回工具描述"""
        return ""
    
    def get_parameters(self) -> Dict:
        """返回工具参数（JSON Schema 格式）"""
        return {"type": "object", "properties": {}}
    
    def execute(self, **kwargs) -> str:
        """执行工具（抽象方法，必须实现）"""
        pass
    
    def execute_streaming(self, **kwargs):
        """流式执行（可选实现）"""
        return None
```

### 4.2 内置工具一览

| 工具名 | 类名 | 行号 | 功能 |
|--------|------|------|------|
| `read_file` | `ReadFileTool` | L5762 | 读取文件（支持 head/tail/paging） |
| `write_file` | `WriteFileTool` | L5884 | 写入/追加文件 |
| `list_files` | `ListFilesTool` | L5961 | 目录列表 |
| `web` | `SimpleWebCrawlerTool` | L5637 | 网页抓取（标题/正文/链接） |
| `memory` | `MemoryTool` | L5280 | 记忆 CRUD（search/save/delete） |
| `memo` | `MemoTool` | L4753 | 任务 CRUD（add/complete/list/search） |
| `run_command` | `CommandLineTool` | L6150 | 系统命令执行（支持超时） |
| `search_skills` | `SearchSkillsTool` | L4076 | 搜索扩展技能 |
| `reload_skills` | `ReloadSkillsTool` | L4199 | 热重载技能 |
| `create_skill` | `CreateSkillTool` | L4409 | AI 自主创建技能 |
| `install_skill` | `InstallSkillTool` | L4306 | 技能安装指引 |
| `ask_user` | `AskUserTool` | L4275 | 向用户提问 |
| `update_identity` | `UpdateIdentityTool` | L4518 | 更新用户身份 |
| `update_soul` | `UpdateSoulTool` | L4582 | 更新 AI 灵魂 |
| `save_memory_and_end_conversation` | `SaveMemoryAndEndConversationTool` | L4646 | 保存记忆并结束对话 |
| `get_current_time` | `GetCurrentTimeTool` | L6055 | 获取当前时间 |
| `calculator` | `CalculatorTool` | L6095 | 数学计算 |
| `set_proxy` | `SetProxyTool` | L6415 | 设置 SOCKS5 代理 |
| `get_proxy` | `GetProxyTool` | L6453 | 查看代理配置 |

### 4.3 工具执行流程

```
AI 决定调用工具 → 生成 JSON 格式工具调用
    ↓
DeepSeekChat.process_tool_calls()
    ↓
┌─── 解析工具名 ───┬─── 解析参数 ───┬─── 注册工具 ───┐
└─────────────────┴───────────────┴─────────────────┘
    ↓
ToolRegistry.get_tool(name)
    ↓
tool.execute(**params)
    ↓
┌─── 结果处理 ───┬─── 错误处理 ───┬─── 长结果截断 ───┐
└─────────────────┴───────────────┴─────────────────┘
    ↓
返回结果给 AI → AI 继续生成响应
```

---

## 五、WebSocket 协议

### 5.1 客户端 → 服务器

| 类型 | 字段 | 说明 |
|------|------|------|
| `message` | `content` | 普通对话文本 |
| `command` | `command` | 斜杠命令（如 `/new`, `/help`） |
| `task` | `action`, `title`, ... | 任务操作（add/complete/list） |
| `ping` | — | 心跳检测 |

### 5.2 服务器 → 客户端

| 类型 | 字段 | 说明 |
|------|------|------|
| `chunk` | `content` | AI 响应片段（流式） |
| `complete` | `content` | AI 响应完成 |
| `reasoning` | `content` | AI 思考过程 |
| `error` | `content` | 错误信息 |
| `command_result` | `content` | 命令执行结果 |
| `task_status` | `task_id`, `status` | 任务状态更新 |
| `task_process` | `task_id`, `content` | 任务执行过程 |
| `dialogue_ended` | — | 对话结束 |
| `dialogue_reset` | — | 对话重置完成 |
| `info` | `content` | 系统信息 |
| `pong` | — | Ping 响应 |

### 5.3 消息流示例

```
// 普通对话
Client:  {type: "message", content: "你好"}
Server:  {type: "chunk", content: "你好！"}
Server:  {type: "complete", content: "你好！有什么可以帮你的吗？"}

// 工具调用
Server:  {type: "tool_call", tool: "web", args: {url: "..."}}
Server:  {type: "chunk", content: "网页内容：..."}
Server:  {type: "complete", content: "网页已获取"}

// 任务执行
Client:  {type: "task", action: "add", title: "提醒", delay: 60}
Server:  {type: "command_result", content: "任务已添加"}
// 60 秒后
Server:  {type: "task_status", task_id: 1, status: "executing"}
Server:  {type: "task_process", task_id: "1", content: "处理中..."}
Server:  {type: "task_status", task_id: 1, status: "complete"}
```

---

## 六、任务系统

### 6.1 任务类型

| 类型 | 字段 | 说明 |
|------|------|------|
| 一次性 | `reminder_time` | 指定执行时间 |
| 重复（daily） | `repeat_type: "daily"` | 每天执行 |
| 重复（weekly） | `repeat_type: "weekly"`, `weekday` | 每周指定日 |
| 重复（monthly） | `repeat_type: "monthly"`, `day` | 每月指定日 |
| 重复（custom） | `repeat_type: "custom"`, `interval`, `unit` | 自定义间隔 |
| 即时 | `is_immediate: true` | 立即执行 |

### 6.2 任务调度流程

```
MemoScheduler._run()  # 后台线程
    ↓
检查所有未完成任务
    ↓
┌─── 到达时间？ ───┬─── 未完成？ ───┬─── 允许用户任务？ ───┐
└─────────────────┴───────────────┴─────────────────┘
    ↓
创建新的 AI 对话（将任务内容作为用户输入）
    ↓
AI 处理任务（可能调用多个工具）
    ↓
标记任务已触发
    ↓
广播结果到所有客户端
    ↓
计算下一次执行时间（重复任务）
```

### 6.3 数据库表结构

```sql
-- memories.db
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    content TEXT,
    importance INTEGER DEFAULT 2,
    created_at TEXT,
    updated_at TEXT,
    embedding BLOB  -- 预留向量嵌入
);

-- memos.db
CREATE TABLE memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    content TEXT,
    reminder_time TEXT,
    repeat_type TEXT,
    repeat_interval INTEGER,
    repeat_interval_unit TEXT,
    is_immediate INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    is_triggered INTEGER DEFAULT 0,
    created_at TEXT,
    user_id TEXT
);
```

---

## 七、记忆系统

### 7.1 记忆操作

| 操作 | AI 调用 | 说明 |
|------|---------|------|
| 保存 | `memory(action="save", content=...)` | AI 主动保存重要信息 |
| 搜索 | `memory(action="search", query=...)` | 按关键词搜索记忆 |
| 列表 | `memory(action="list", page=...)` | 分页浏览所有记忆 |
| 删除 | `memory(action="delete", id=...)` | 删除指定记忆 |

### 7.2 自动记忆

- AI 在对话结束时（`/exit`）自动保存关键信息
- 可配置 `system.auto_save_memory` 开关
- 重要度（importance）1-5 级

---

## 八、对话压缩

### 8.1 压缩触发条件

| 条件 | 阈值（默认） | 配置项 |
|------|-------------|--------|
| 消息数超过 | 200 条 | `compress_message_threshold` |
| Token 数超过 | 120000 | `compress_token_threshold` |

### 8.2 压缩流程

```
检测到阈值触发
    ↓
保留最近 8 条消息（compress_keep_recent）
    ↓
对早期消息按时间衰减加权（weight_decay_factor=0.01）
    ↓
调用 AI 生成摘要（max_input_chars=6000）
    ↓
将摘要作为系统消息插入
    ↓
压缩后继续对话
```

### 8.3 压缩配置

```json
{
  "compressor": {
    "min_messages_to_compress": 5,
    "weight_decay_factor": 0.01,
    "max_input_chars": 6000,
    "llm_max_tokens": 800
  }
}
```

---

## 九、扩展技能系统

### 9.1 技能类型

| 类型 | 格式 | 存储位置 |
|------|------|---------|
| 可执行脚本 | Python/Shell，支持 `--help` | `~/.aibox/skills/` |
| Markdown 技能 | `.md` 文件，首行为简介 | `~/.aibox/skills/` |

### 9.2 内置技能

| 技能名 | 文件 | 功能 |
|--------|------|------|
| hello-world | `skills/hello-world.py` | 示例技能 |
| bing_search | `skills/bing_search.py` | 必应搜索 |
| ssh-exec | `skills/ssh-exec.py` | SSH 远程执行 |
| sftp-transfer | `skills/sftp-transfer.py` | SFTP 文件传输 |
| facerec | `skills/facerec.py` | 人脸识别 |
| aes-crypto | `skills/aes-crypto.py` | AES 加密/解密 |
| gitclient | `skills/gitclient.py` | Git 操作封装 |

### 9.3 技能热重载

```python
# 新技能放置到 ~/.aibox/skills/
# 调用 reload_skills 工具
# AI 下次对话时即可使用
```

### 9.4 技能格式规范

详见 [SKILLS_FORMAT.md](SKILLS_FORMAT.md)

---

## 十、配置参考

### 10.1 完整配置项

#### system 段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `save_dir` | str | `~/.aibox` | 数据存储目录 |
| `datetime_format` | str | `%Y-%m-%d %H:%M:%S` | 日期时间格式 |
| `date_format` | str | `%Y%m%d_%H%M%S` | 文件名日期格式 |
| `debug_level` | int | `1` | 调试级别 0-3 |
| `load_history_by_default` | bool | `true` | 启动时加载历史对话 |
| `log_file` | str | `~/.aibox/ai.log` | 日志文件路径 |
| `log_max_size` | int | `10485760` | 日志最大字节数（10MB） |
| `log_backup_count` | int | `5` | 日志备份数量 |
| `websocket_host` | str | `localhost` | WebSocket 监听地址 |
| `websocket_port` | int | `8765` | WebSocket 监听端口 |
| `skills_dir` | str | `~/.aibox/skills` | 技能目录 |
| `prompts_dir` | str | `~/.aibox/prompts` | 提示词目录 |

#### scheduler 段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 启用任务调度器 |
| `check_interval` | float | `1.0` | 检查间隔（秒） |
| `max_tasks_per_run` | int | `10` | 单次最大触发任务数 |
| `broadcast_enabled` | bool | `true` | 广播任务执行结果 |

#### api 段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | str | `deepseek` | 模型厂商，用于 `llm_client` 工厂路由 |
| `deepseek_api_url` | str | `https://api.deepseek.com/chat/completions` | API URL |
| `deepseek_model` | str | `deepseek-v4-flash` | 模型名 |
| `api_key_env` | str | `DEEPSEEK_API_KEY` | API Key 环境变量名 |
| `timeout` | int | `30` | 请求超时（秒） |
| `temperature` | float | `0.7` | 生成温度 0-1 |

#### context 段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_history_messages` | int | `500` | 历史消息上限 |
| `max_context_messages` | int | `120` | 上下文窗口 |
| `max_tokens` | int | `8192` | 最大生成 Token |
| `max_history_conversations` | int | `5` | 保留历史对话数 |
| `preserve_system_prompts` | bool | `true` | 保留系统提示词 |
| `compress_enabled` | bool | `true` | 启用对话压缩 |
| `compress_message_threshold` | int | `200` | 压缩消息阈值 |
| `compress_token_threshold` | int | `120000` | 压缩 Token 阈值 |
| `compress_ratio` | float | `0.2` | 压缩保留比例 |
| `compress_keep_recent` | int | `8` | 保留最近消息数 |

#### memo 段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `default_reminder_minutes` | int | `60` | 默认提醒分钟数 |
| `max_memos_per_check` | int | `10` | 单次最大触发任务数 |
| `allow_user_tasks` | bool | `true` | 允许用户创建任务 |

#### proxy 段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | str | `""` | 代理类型（socks5/http） |
| `host` | str | `""` | 代理主机 |
| `port` | int | `0` | 代理端口 |
| `username` | str | `""` | 代理用户名 |
| `password` | str | `""` | 代理密码 |

### 10.2 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（必填） |
| `AI_CONFIG_PATH` | 自定义配置文件路径 |

---

## 十一、命令行参数

```bash
python main.py [选项]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--verbose`, `-v` | flag | `false` | 显示详细日志 |
| `--debug` | int | `None` | 调试级别 0-3 |
| `--lang` | str | `None` | 强制语言 zh/en |
| `--host` | str | `None` | 覆盖 WebSocket 主机 |
| `--port` | int | `None` | 覆盖 WebSocket 端口 |
| `--no-scheduler` | flag | `false` | 禁用任务调度器 |
| `--scheduler-interval` | float | `1.0` | 调度器检查间隔 |
| `--config`, `-c` | str | `None` | 指定配置文件 |
| `--check-memo` | flag | `false` | 检查到期任务并退出 |
| `--memo-add` | str | `None` | 添加任务（CLI） |
| `--memo-list` | flag | `false` | 列出任务 |
| `--memo-complete` | int | `None` | 完成指定任务 |

### 使用示例

```bash
# 启动服务器
python main.py

# 详细日志 + 指定端口
python main.py --verbose --port 9000

# 检查并执行到期任务（cron 场景）
python main.py --check-memo

# 添加任务
python main.py --memo-add "每日备份|执行数据库备份|+1h"

# 列出所有任务
python main.py --memo-list
```

---

## 十二、前端集成

### 12.1 前端文件

| 文件 | 说明 |
|------|------|
| `entry.html` | 简单入口页（重定向到 frontend.html） |
| `frontend.html` | 完整前端界面（对话/任务/设置） |

### 12.2 WebSocket 连接

```javascript
const ws = new WebSocket('ws://localhost:8765');

// 发送消息
ws.send(JSON.stringify({
    type: 'message',
    content: '你好'
}));

// 接收消息
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    switch (data.type) {
        case 'chunk':
            appendToChat(data.content);
            break;
        case 'complete':
            finishResponse();
            break;
        case 'error':
            showError(data.content);
            break;
    }
};
```

---

## 十三、部署

### 13.1 目录结构

```
/opt/micro_servers/aibox/
├── main.py
├── client.py
├── skills/
├── _run.sh          # core 拉起入口
├── _set.sh          # 首次安装
└── requirements.txt
```

### 13.2 core init.txt 配置

```bash
# aibox 由 core 拉起
bash /opt/micro_servers/aibox/_run.sh
```

### 13.3 systemd 单元（可选）

```ini
[Unit]
Description=AIBOX AI Agent
After=network.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/opt/micro_servers/aibox
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5
Environment=DEEPSEEK_API_KEY=xxx

[Install]
WantedBy=multi-user.target
```

---

## 十四、已知限制

| ID | 描述 | 严重等级 |
|----|------|---------|
| L1 | 单实例运行，不支持多进程 | 🟡 Medium |
| L2 | 记忆库无向量搜索，仅关键词匹配 | 🟡 Medium |
| L3 | 对话压缩可能丢失早期重要信息 | 🟢 Low |
| L4 | 工具执行无超时控制（部分工具） | 🟡 Medium |
| L5 | 日志无远程推送，仅本地文件 | 🟢 Low |
| L6 | 无用户认证，WebSocket 连接可被滥用 | 🔴 High |
| L7 | API Key 以明文存储在环境变量或文件中 | 🟡 Medium |

### L6 详细说明

WebSocket 服务器**无任何认证**，任何能连接到 `ws://localhost:8765` 的客户端都可以：
- 与 AI 对话（消耗 API 额度）
- 执行系统命令（`run_command`）
- 操作文件系统（`read_file`/`write_file`）
- 创建/删除任务

**建议**：
1. 仅绑定 `localhost`（默认配置已这样做）
2. 添加 API Token 认证
3. 限制 `run_command` 可执行的命令范围

---

## 十五、开发指南

### 15.1 添加新工具

1. 继承 `Tool` 基类
2. 实现 `get_name()`、`get_description()`、`get_parameters()`、`execute()`
3. 在 `DeepSeekChat._register_tools()` 中注册
4. 更新 README 文档

```python
class MyTool(Tool):
    def get_name(self) -> str:
        return "my_tool"
    
    def get_description(self) -> str:
        return "我的自定义工具"
    
    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "param": {"type": "string", "description": "参数说明"}
            },
            "required": ["param"]
        }
    
    def execute(self, param: str, **kwargs) -> str:
        return f"执行结果: {param}"
```

### 15.2 添加新命令

在 `WebSocketHandler.handle_command()` 中添加：

```python
elif command == "/mycommand":
    result = "命令执行结果"
    await self.send_message(websocket, {
        "type": "command_result",
        "content": result
    })
```

### 15.3 添加配置项

1. 在 `ConfigManager._get_default_config()` 中添加默认值
2. 在 README 文档中添加说明
3. 确保 `_merge_configs()` 能正确处理新增字段

---

## 十六、故障排查

### 16.1 常见问题

| 问题 | 排查步骤 |
|------|---------|
| API Key 无效 | 检查 `echo $DEEPSEEK_API_KEY` 或 `~/.cert/deepseek.key` |
| 技能不生效 | 确认技能文件在 `~/.aibox/skills/` 且有执行权限 |
| 任务不执行 | 检查调度器状态：`/tasks`；检查时间设置 |
| 对话压缩丢失信息 | 调整 `compress_message_threshold` 和 `compress_keep_recent` |
| WebSocket 断开 | 检查服务器进程：`ps aux | grep main.py`；查看日志 |

### 16.2 日志位置

```bash
# 查看实时日志
tail -f ~/.aibox/ai.log

# 查看最近错误
grep "ERROR" ~/.aibox/ai.log | tail -20
```

### 16.3 数据库检查

```bash
# 检查记忆库
sqlite3 ~/.aibox/memories.db "SELECT * FROM memories LIMIT 10;"

# 检查任务库
sqlite3 ~/.aibox/memos.db "SELECT * FROM memos WHERE status='pending';"
```

---

## 十七、版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v2.0 | 当前 | 新增 `ask_user`、`update_soul`、`create_skill`、`install_skill`、`set_proxy`/`get_proxy` 等工具；支持 Markdown 技能；对话压缩支持 Token 阈值 |
| v1.0 | — | 初始版本：WebSocket 服务器、文件/网页/记忆操作、任务调度、扩展技能、多语言 |

---

## 附录

### A. 配置文件示例

完整默认配置见 [aibox.py:1100-1180](aibox.py#L1100-L1180)

### B. 数据库 Schema

完整建表语句见：
- 记忆库：[aibox.py](aibox.py) 中 `MemoryManager._init_db()`
- 任务库：[aibox.py](aibox.py) 中 `MemoScheduler._init_db()`

### C. 参考文档

- [DeepSeek API 文档](https://platform.deepseek.com/docs)
- [websockets 库文档](https://websockets.readthedocs.io/)
- [SKILLS_FORMAT.md](SKILLS_FORMAT.md)

---

*本文档由代码审查工具生成，供开发团队参考。*
