"""聊天视图：用户/AI/系统消息 + AI 回复流式 Markdown 渲染。

服务端协议（aibox/main.py output_callback 原样透传 msg_type）：
  - chunk    增量片段（服务端 chunk_buffer 发送后即清零），直接 append
  - complete 完整全文，用于对齐：与已显示文本不一致时整体替换（防丢帧/乱序）
"""
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static


class ChatView(VerticalScroll):
    """可滚动聊天区。AI 回复用单个 Markdown 部件增量 append，实现流式渲染。

    滚动策略：每条新消息挂载后调用 anchor() 锚定底部——之后内容增长
    （流式 append）容器会自动保持吸底；用户上翻时 Textual 自动脱锚，
    下一条新消息再重新锚定。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ai_widget: Markdown | None = None
        self._ai_text = ""

    @property
    def ai_text(self) -> str:
        return self._ai_text

    def _anchor_bottom(self):
        """锚定吸底（需在布局刷新后执行，否则滚到的是旧位置）。"""
        self.call_after_refresh(self.anchor)

    async def add_user(self, text: str):
        await self.mount(Static(f"👤 你: {text}", classes="msg-user", markup=False))
        self._anchor_bottom()

    async def add_system(self, text: str, css_class: str = "msg-system"):
        await self.mount(Static(text, classes=css_class, markup=False))
        self._anchor_bottom()

    async def start_ai(self):
        """预建一条 AI 消息（发送用户消息后即可调用，给出即时反馈）。"""
        if self._ai_widget is not None:
            return
        self._ai_text = ""
        self._ai_widget = Markdown("", classes="msg-ai")
        await self.mount(self._ai_widget)
        self._anchor_bottom()

    async def append_ai(self, delta: str):
        """追加增量片段（服务端 chunk 为增量语义，直接拼接）。"""
        if not delta:
            return
        if self._ai_widget is None:
            await self.start_ai()
        self._ai_widget.append(delta)
        self._ai_text += delta
        # anchor 状态下容器会自动吸底；保险起见刷新后再滚一次
        self.call_after_refresh(self.scroll_end, animate=False)

    async def finish_ai(self, full_text: str = ""):
        """收尾一条 AI 消息。

        - full_text 为 complete 携带的完整全文：与已显示不一致时整体替换对齐；
        - full_text 为空且未收到任何内容（如发送失败）：移除预建的空消息框。
        """
        w = self._ai_widget
        self._ai_widget = None
        if w is None:
            # 流式期间丢帧，只在 complete 收到全文：补建完整消息
            if full_text:
                self._ai_text = full_text
                await self.mount(Markdown(full_text, classes="msg-ai"))
                self._anchor_bottom()
            return
        if full_text and full_text != self._ai_text:
            # 已显示内容与全文不一致（丢字/乱序/丢帧），整体替换对齐
            await w.remove()
            self._ai_text = full_text
            await self.mount(Markdown(full_text, classes="msg-ai"))
        elif not full_text and not self._ai_text:
            # 预建后没有任何输出：移除空框
            await w.remove()
        self._ai_text = ""
        self.call_after_refresh(self.scroll_end, animate=False)

    async def reset(self):
        """清空聊天区（/clear）。"""
        self._ai_widget = None
        self._ai_text = ""
        await self.remove_children()
