"""BotOps WebSocket 协议层（提炼自 aibox/client.py，增加状态回调与自动重连）。

协议：JSON 消息。
  发送：{"type": "message", "content": ...}
        {"type": "command", "command": "/..."}
        {"type": "interrupt", "content": ...}
  接收：chunk / line / reasoning / complete / dialogue_ended / dialogue_reset /
        task_process / task_status / task_error / task_added / interrupt_ack /
        error / info / command_result / connection_ack / notification / pong

说明：服务端 chunk 为**增量片段**（output_callback 发送 chunk_buffer 后即清零），
      complete 为完整全文（可用于对齐）；line 为过程行（工具提示/输出/前缀）。
"""
import asyncio
import json
from typing import Awaitable, Callable, Optional

import websockets

MessageHandler = Callable[[dict], Awaitable[None]]
StateHandler = Callable[[str, str], Awaitable[None]]   # (state, detail)


class BotWSClient:
    """aibox 协议 WebSocket 客户端：断线指数退避自动重连，手动断开则抑制。"""

    def __init__(self, uri: str):
        self.uri = uri
        self.ws = None
        self.connected = False
        self._manual_disconnect = False
        self._listen_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self.on_message: Optional[MessageHandler] = None
        self.on_state: Optional[StateHandler] = None

    async def _emit_state(self, state: str, detail: str = ""):
        if self.on_state:
            await self.on_state(state, detail)

    async def connect(self) -> bool:
        """连接并开始监听；成功返回 True。"""
        await self._close_ws()
        try:
            self.ws = await websockets.connect(self.uri)
        except Exception as e:
            await self._emit_state("error", f"连接失败: {e}")
            return False
        self.connected = True
        self._manual_disconnect = False
        self._listen_task = asyncio.create_task(self._listen())
        await self._emit_state("connected", self.uri)
        return True

    async def disconnect(self):
        """手动断开（抑制自动重连）。"""
        self._manual_disconnect = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self.connected = False
        await self._close_ws()
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        await self._emit_state("disconnected", "已手动断开")

    async def _close_ws(self):
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

    async def send_message(self, text: str) -> bool:
        """发送用户消息（/ 开头按 command 发送，与 aibox 协议一致）。"""
        if not self.connected or not self.ws:
            return False
        data = ({"type": "command", "command": text} if text.startswith("/")
                else {"type": "message", "content": text})
        try:
            await self.ws.send(json.dumps(data, ensure_ascii=False))
            return True
        except Exception as e:
            await self._emit_state("error", f"发送失败: {e}")
            return False

    async def send_interrupt(self) -> bool:
        """发送中断信号（对应 client.py 的 Ctrl+C 行为）。"""
        if not self.connected or not self.ws:
            return False
        try:
            await self.ws.send(json.dumps(
                {"type": "interrupt", "content": "用户按下了 Ctrl+C 中断当前操作"},
                ensure_ascii=False))
            return True
        except Exception:
            return False

    async def _listen(self):
        """接收循环：断线时在 finally 中触发自动重连。"""
        try:
            async for raw in self.ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"type": "chunk", "content": raw}
                if self.on_message:
                    await self.on_message(data)
        except websockets.exceptions.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._emit_state("error", f"接收错误: {e}")
        finally:
            was_connected = self.connected
            self.connected = False
            if was_connected:
                await self._emit_state("disconnected", "连接已断开")
                self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self._manual_disconnect:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self):
        """指数退避 2s→30s 封顶，直到成功或手动断开。"""
        delay = 2
        while not self._manual_disconnect and not self.connected:
            await self._emit_state("reconnecting", f"{delay}s 后自动重连 {self.uri} …")
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if self._manual_disconnect:
                return
            try:
                self.ws = await websockets.connect(self.uri)
            except Exception:
                delay = min(delay * 2, 30)
                continue
            self.connected = True
            self._listen_task = asyncio.create_task(self._listen())
            await self._emit_state("connected", self.uri)
            return
