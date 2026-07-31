#!/usr/bin/env python3
"""BotOps — aibox 智能体的现代化 TUI 前端（Textual）。

复刻并增强 aibox/client.py：
  - AI 回复流式 Markdown 渲染（Markdown 部件增量追加，无闪烁）
  - 断线指数退避自动重连（/disconnect 抑制）
  - 任务事件独立侧栏（task_process / task_status / task_error）
  - Ctrl+C 中断生成、Ctrl+Q 退出、Ctrl+L 清屏

用法:
    python app.py [ws://host:port]        # 默认 ws://localhost:8765
"""
import sys

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, RichLog

from commands import CommandRegistry
from widgets.chat_view import ChatView
from widgets.status_bar import StatusBar
from ws_client import BotWSClient

DEFAULT_URI = "ws://localhost:8765"


class BotOpsApp(App):
    TITLE = "BotOps"
    SUB_TITLE = "aibox TUI"

    CSS = """
    #chat {
        width: 3fr;
        border: solid $primary;
    }
    #tasks {
        width: 1fr;
        border: solid $secondary;
    }
    .msg-user {
        color: cyan;
        padding: 0 1;
        margin-top: 1;
    }
    .msg-ai {
        padding: 0 1;
    }
    .msg-system {
        color: $text-muted;
        padding: 0 1;
    }
    .msg-error {
        color: red;
        padding: 0 1;
    }
    .msg-task {
        color: yellow;
        padding: 0 1;
    }
    .msg-reasoning {
        color: $text-muted;
        background: $surface;
        padding: 0 1;
        margin: 0 1;
        text-style: italic;
    }
    #status {
        height: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "中断", show=True),
        Binding("ctrl+q", "quit", "退出", show=True),
        Binding("ctrl+l", "clear", "清屏", show=True),
    ]

    def __init__(self, uri: str = DEFAULT_URI):
        super().__init__()
        self.ws = BotWSClient(uri)
        self.ws.on_message = self._on_ws_message
        self.ws.on_state = self._on_ws_state
        self.registry = CommandRegistry(self)

    # ── 布局 ─────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ChatView(id="chat")
            yield RichLog(id="tasks", max_lines=1000)
        yield Input(placeholder="输入消息，/help 查看命令，Enter 发送", id="input")
        yield StatusBar(id="status")
        yield Footer()

    async def on_mount(self):
        self.query_one("#input", Input).focus()
        self._refresh_status("")
        self.run_worker(self.connect_to(self.ws.uri))

    @property
    def chat(self) -> ChatView:
        return self.query_one("#chat", ChatView)

    @property
    def tasks(self) -> RichLog:
        return self.query_one("#tasks", RichLog)

    # ── 连接管理 ──────────────────────────────────────────────────────────
    async def connect_to(self, uri: str):
        if self.ws.connected:
            await self.ws.disconnect()
        self.ws.uri = uri
        await self.notify_system(f"正在连接 {uri} …")
        await self.ws.connect()

    async def disconnect(self):
        await self.ws.disconnect()

    async def send_interrupt(self):
        if await self.ws.send_interrupt():
            await self.notify_system("🛑 已发送中断信号")
        else:
            await self.notify_system("未连接到服务器", level="error")

    # ── UI 操作 ───────────────────────────────────────────────────────────
    async def notify_system(self, text: str, level: str = "info"):
        css = {"info": "msg-system", "error": "msg-error", "task": "msg-task"}.get(level, "msg-system")
        await self.chat.add_system(text, css)

    async def clear_chat(self):
        await self.chat.reset()

    def _refresh_status(self, extra: str):
        self.query_one("#status", StatusBar).set_state(
            self.ws.connected, self.ws.uri if self.ws.connected else "", extra)

    # ── 输入处理 ──────────────────────────────────────────────────────────
    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/") and await self.registry.execute(text):
            return
        if not self.ws.connected:
            await self.notify_system("未连接到服务器，先 /connect", level="error")
            return
        await self.chat.add_user(text)
        await self.chat.start_ai()          # 预建 AI 消息框，即时反馈
        if not await self.ws.send_message(text):
            await self.chat.finish_ai("")   # 发送失败：移除预建的空消息框
            await self.notify_system("❌ 发送失败：连接已断开，请 /connect 重连", level="error")

    # ── 快捷键 ────────────────────────────────────────────────────────────
    async def action_interrupt(self):
        await self.send_interrupt()

    async def action_clear(self):
        await self.clear_chat()

    def action_quit(self):
        self.exit()

    # ── aibox 协议消息路由（对应 client.py 的 _handle_message）─────────────
    async def _on_ws_message(self, data: dict):
        t = data.get("type", "")
        content = data.get("content", "")

        if t == "chunk":
            # 服务端 chunk 为增量片段（发送后即清零），直接拼接
            await self.chat.append_ai(content)
        elif t == "reasoning_start":
            # 开始深度思考，创建思考区域
            await self.chat.start_reasoning(content or "🤔 深度思考中...")
        elif t == "reasoning":
            # 思考内容增量
            stripped = content
            if stripped:
                await self.chat.append_reasoning(stripped)
        elif t == "reasoning_end":
            # 思考结束
            await self.chat.end_reasoning()
        elif t == "line":
            # 过程行（服务端 output_callback 原样透传）：工具调用提示 / 工具输出 / 前缀
            stripped = content.strip()
            if not stripped:
                pass
            elif stripped.startswith(("💬 助手", "💬 Assistant", "🤔 思考中", "🤔 Thinking", "🤔 深度思考中")):
                pass  # 助手前缀 / 思考提示：聊天区已有对应区域，无需展示
            else:
                # 🔧 调用工具 / 工具输出 / 🤔 分析工具结果 等 → 任务侧栏
                self.tasks.write(stripped)
        elif t == "complete":
            # complete 携带完整全文，用于对齐已显示内容
            await self.chat.finish_ai(content)
        elif t == "dialogue_ended":
            await self.chat.finish_ai(self.chat.ai_text)
            if content:
                await self.notify_system(f"✅ {content}")
        elif t == "task_added":
            await self.notify_system(f"➕ {content}")
        elif t == "dialogue_reset":
            if content:
                await self.notify_system(f"🔄 {content}")
        elif t == "pong":
            pass
        elif t == "task_process":
            sub = data.get("msg_type", "")
            title = data.get("task_title", "未知任务")
            if sub == "chunk":
                tail = str(data.get("content", ""))[-200:]
                if tail:
                    self.tasks.write(f"⚡ {title}: {tail}")
            elif sub == "complete":
                self.tasks.write(f"✅ 任务完成: {title}")
        elif t == "task_status":
            icon = {"executing": "⚙️", "complete": "✅", "failed": "❌",
                    "added": "➕", "batch_start": "🚀", "batch_complete": "🏁"
                    }.get(data.get("status", ""), "📋")
            self.tasks.write(
                f"{icon} [{data.get('task_id', '?')}] {data.get('title', '')}: "
                f"{data.get('message', content)}")
        elif t == "task_error":
            self.tasks.write(
                f"❌ [{data.get('task_id', '?')}] {data.get('task_title', '?')}: "
                f"{data.get('error', '未知错误')}")
        elif t == "interrupt_ack":
            await self.notify_system(f"🛑 {content}")
        elif t == "error":
            await self.notify_system(f"❌ {content}", level="error")
        elif t == "info":
            # 过滤掉对话开始这类隐式通知（用户发消息自然知道对话开始了）
            skip_keywords = ("Conversation started", "对话开始", "🎭")
            if not any(k in content for k in skip_keywords):
                await self.notify_system(f"ℹ️ {content}")
        elif t == "command_result":
            await self.notify_system(content)
        elif t == "connection_ack":
            await self.notify_system(f"✅ {content}")
        elif t == "notification":
            await self.notify_system(f"🔔 {content}")
        elif content:
            await self.chat.append_ai(content)

    # ── 连接状态回调 ──────────────────────────────────────────────────────
    async def _on_ws_state(self, state: str, detail: str):
        if state == "connected":
            self._refresh_status("")
            await self.notify_system(f"✅ 已连接到 {self.ws.uri}")
        elif state == "disconnected":
            self._refresh_status("")
            await self.notify_system(
                f"❌ {detail}（中断期间的服务端输出已丢失，如回复不完整请重发）")
        elif state == "reconnecting":
            self._refresh_status(detail)
        elif state == "error":
            self._refresh_status("")
            await self.notify_system(detail, level="error")


def main():
    uri = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URI
    BotOpsApp(uri).run()


if __name__ == "__main__":
    main()
