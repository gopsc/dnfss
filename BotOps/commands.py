"""斜杠命令注册表：/connect /disconnect /interrupt /clear /status /help /quit。

本地命令在此拦截处理；未注册的 / 命令透传给 aibox 服务器（与 client.py 行为一致）。
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import BotOpsApp


class CommandRegistry:
    def __init__(self, app: "BotOpsApp"):
        self.app = app
        self._commands = {
            "/connect": ("连接服务器：/connect ws://host:port", self._connect),
            "/disconnect": ("断开连接（不自动重连）", self._disconnect),
            "/interrupt": ("中断当前生成（或按 Ctrl+C）", self._interrupt),
            "/clear": ("清空聊天区", self._clear),
            "/status": ("显示连接状态", self._status),
            "/help": ("显示帮助", self._help),
            "/quit": ("退出 BotOps", self._quit),
        }

    async def execute(self, line: str) -> bool:
        """是本地命令则执行并返回 True；否则返回 False（调用方应透传服务器）。"""
        cmd = line.split()[0].lower()
        entry = self._commands.get(cmd)
        if entry is None:
            return False
        await entry[1](line)
        return True

    async def _connect(self, line: str):
        parts = line.split()
        if len(parts) < 2:
            await self.app.notify_system("用法: /connect ws://host:port", level="error")
            return
        await self.app.connect_to(parts[1])

    async def _disconnect(self, _line: str):
        await self.app.disconnect()

    async def _interrupt(self, _line: str):
        await self.app.send_interrupt()

    async def _clear(self, _line: str):
        await self.app.clear_chat()

    async def _status(self, _line: str):
        if self.app.ws.connected:
            await self.app.notify_system(f"✅ 已连接: {self.app.ws.uri}")
        else:
            await self.app.notify_system("○ 未连接")

    async def _help(self, _line: str):
        lines = ["可用命令:"] + [f"  {k:<14} {v[0]}" for k, v in self._commands.items()]
        lines.append("  其余 / 命令将透传给 aibox 服务器")
        await self.app.notify_system("\n".join(lines))

    async def _quit(self, _line: str):
        await self.app.ws.disconnect()
        self.app.exit()
