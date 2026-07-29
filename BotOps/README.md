# BotOps — aibox 智能体 TUI 前端

复刻并增强 [aibox/client.py](../aibox/client.py)，基于 [Textual](https://textual.textualize.io/) 的现代化终端 UI，连接 aibox 微服务（WebSocket，默认 `ws://localhost:8765`）。

## 相比 client.py 的增强

| 能力 | client.py | BotOps |
|------|-----------|--------|
| AI 回复渲染 | 纯文本流式 / 一次性静态渲染 | **Markdown 增量流式渲染**（无闪烁） |
| 终端管理 | 手搓光标/提示符 | Textual 部件框架（多面板布局） |
| 任务事件 | 混在对话流里 | **独立侧栏**（task_process / task_status / task_error） |
| 自动重连 | ✅（已移植） | ✅ 指数退避 2s→30s，`/disconnect` 抑制 |
| 快捷键 | Ctrl+C 中断 | Ctrl+C 中断 / Ctrl+Q 退出 / Ctrl+L 清屏 |

## 安装与运行

```bash
cd BotOps
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python app.py                      # 默认 ws://localhost:8765
python app.py ws://your-host:8765  # 指定 aibox 地址
```

## 命令

- 本地命令（不发给服务器）：`/connect` `/disconnect` `/interrupt` `/clear` `/status` `/help` `/quit`
- 其余 `/` 命令透传给 aibox 服务器（与 client.py 行为一致）

## 结构

```
BotOps/
├── app.py            # Textual App：布局 + 消息路由 + 快捷键
├── ws_client.py      # WebSocket 协议层（自动重连）
├── commands.py       # 斜杠命令注册表
└── widgets/
    ├── chat_view.py  # 聊天区：AI 回复 Markdown 增量流式渲染
    └── status_bar.py # 底部状态栏（连接状态）
```

## 协议说明

与 aibox 服务端协议完全兼容（详见 aibox/client.py 注释）：
- 发送：`{"type":"message","content":...}` / `{"type":"command","command":"/..."}` / `{"type":"interrupt",...}`
- 接收：`chunk`（**增量片段**，服务端 `chunk_buffer` 发送后即清零，前端直接拼接）/ `complete`（**完整全文**，用于对齐已显示内容）/ `line`（过程行：🔧 工具调用提示、工具输出、💬 助手前缀，路由到任务侧栏）/ `reasoning`（思考流，仅服务端 debug_level≥3 发送）/ `dialogue_ended` / `dialogue_reset` / `task_added` / `task_process` / `task_status` / `task_error` / `interrupt_ack` / `error` / `info` / `command_result` / `connection_ack` / `notification` / `pong`
