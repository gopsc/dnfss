"""聊天视图：用户/AI/系统消息 + AI 回复流式 Markdown 渲染。

服务端协议（aibox/aibox.py output_callback 原样透传 msg_type）：
  - chunk     增量片段（服务端 chunk_buffer 发送后即清零），直接 append
  - complete  收尾信号：chunk 流式是内容权威来源，complete 仅用于收尾；
              仅当流式完全丢帧（未建 widget 且无内容）时，用 full_text 补建
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
        self._ai_finalized = True  # 初始无活跃 AI 消息

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
        self._ai_finalized = False
        self._ai_widget = Markdown("", classes="msg-ai")
        await self.mount(self._ai_widget)
        self._anchor_bottom()

    async def append_ai(self, delta: str):
        """追加增量片段（服务端 chunk 为增量语义，直接拼接）。"""
        if not delta:
            return
        if self._ai_finalized:
            # 收尾后收到的延迟 chunk，丢弃以避免在上一条回复后追加脏数据
            return
        if self._ai_widget is None:
            await self.start_ai()
        self._ai_widget.append(delta)
        self._ai_text += delta
        # anchor 状态下容器会自动吸底；保险起见刷新后再滚一次
        self.call_after_refresh(self.scroll_end, animate=False)

    async def finish_ai(self, full_text: str = ""):
        """收尾一条 AI 消息。

        chunk 流式追加是内容权威来源，complete 只做收尾：
        - 已收尾则直接返回（防止 dialogue_ended + complete 双发导致重复挂载）
        - widget 存在且有流式内容：保持已渲染内容不变，仅清空引用
        - widget 存在但无内容（预建空框）且有 full_text：用 full_text 填充
        - widget 存在但无内容也无 full_text：移除空框
        - widget 不存在（流式丢帧/start_ai 未调用）且有 full_text：补建
        """
        if self._ai_finalized:
            return
        w = self._ai_widget
        self._ai_widget = None

        if w is not None:
            if self._ai_text:
                # 有流式内容，chunk 已完整渲染，直接保留，不做替换
                pass
            elif full_text:
                # 预建了空 widget 但没收到任何 chunk：用 full_text 填充
                w.update(full_text)
                self._ai_text = full_text
            else:
                # 预建后没有任何输出：移除空框
                await w.remove()
        else:
            # widget 不存在（start_ai 未调用），有 full_text 则补建
            if full_text:
                self._ai_text = full_text
                await self.mount(Markdown(full_text, classes="msg-ai"))
                self._anchor_bottom()

        self._ai_text = ""
        self._ai_finalized = True
        self.call_after_refresh(self.scroll_end, animate=False)

    async def reset(self):
        """清空聊天区（/clear）。"""
        self._ai_widget = None
        self._ai_text = ""
        self._ai_finalized = True
        await self.remove_children()
