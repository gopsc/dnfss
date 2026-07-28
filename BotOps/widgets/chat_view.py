"""聊天视图：用户/AI/系统消息 + AI 回复流式 Markdown 渲染。"""
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static


def _delta(prev: str, new: str) -> str:
    """计算累计文本的增量（服务端 chunk 为累计全文；前缀失配时取最长公共前缀后的部分）。"""
    if new.startswith(prev):
        return new[len(prev):]
    i = 0
    for a, b in zip(prev, new):
        if a != b:
            break
        i += 1
    return new[i:]


class ChatView(VerticalScroll):
    """可滚动聊天区。AI 回复用单个 Markdown 部件增量 append，实现流式渲染。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ai_widget: Markdown | None = None
        self._ai_prev = ""

    @property
    def ai_text(self) -> str:
        return self._ai_prev

    async def add_user(self, text: str):
        await self.mount(Static(f"👤 你: {text}", classes="msg-user", markup=False))

    async def add_system(self, text: str, css_class: str = "msg-system"):
        await self.mount(Static(text, classes=css_class, markup=False))

    async def start_ai(self):
        """预建一条 AI 消息（发送用户消息后即可调用，给出即时反馈）。"""
        if self._ai_widget is not None:
            return
        self._ai_prev = ""
        self._ai_widget = Markdown("", classes="msg-ai")
        await self.mount(self._ai_widget)
        self.scroll_end(animate=False)

    async def append_ai(self, full_text: str):
        """以累计全文增量渲染（服务端 chunk 为累计文本）。"""
        if self._ai_widget is None:
            await self.start_ai()
        d = _delta(self._ai_prev, full_text)
        if d:
            self._ai_widget.append(d)
            self._ai_prev = full_text
            self.scroll_end(animate=False)

    async def finish_ai(self, full_text: str = ""):
        """收尾：用最终全文对齐一次（防中途丢帧），随后关闭本条 AI 消息。"""
        if self._ai_widget is None and full_text:
            await self.start_ai()
        if full_text and full_text != self._ai_prev:
            await self.append_ai(full_text)
        self._ai_widget = None
        self._ai_prev = ""
        self.scroll_end(animate=False)

    async def reset(self):
        """清空聊天区（/clear）。"""
        self._ai_widget = None
        self._ai_prev = ""
        await self.remove_children()
