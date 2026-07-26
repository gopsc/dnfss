"""pymsg - Length-prefixed variable-length socket message framing.

Protocol: [4-byte big-endian uint32 length][UTF-8 payload]

Usage:
    import socket
    from pymsg import send, recv

    # sender
    send(sock, "hello world")

    # receiver
    msg = recv(sock)

    # with timeout (seconds)
    send(sock, "hello", timeout=5.0)
    msg = recv(sock, timeout=5.0)
"""

import contextlib
import select
import socket as _socket
import struct

__all__ = ["send", "recv", "TimeoutError", "MsgClient", "MsgServer"]

_HEADER = struct.Struct("!I")  # 4 bytes, big-endian unsigned int


class TimeoutError(Exception):
    """Raised when a send or recv operation exceeds its timeout."""


@contextlib.contextmanager
def _timeout(sock: "_socket.socket", seconds: "float | None"):
    """Temporarily set the socket timeout, restoring it on exit."""
    if seconds is None:
        yield
        return
    original = sock.gettimeout()
    sock.settimeout(seconds)
    try:
        yield
    finally:
        sock.settimeout(original)


def send(
    sock: "_socket.socket",
    data: str,
    *,
    timeout: "float | None" = None,
) -> None:
    """Send a string over a socket using length-prefixed framing.

    Args:
        sock: A connected socket object.
        data: The string message to send.
        timeout: Optional timeout in seconds. Raises TimeoutError on expiry.
    """
    payload = data.encode("utf-8")
    header = _HEADER.pack(len(payload))
    with _timeout(sock, timeout):
        try:
            sock.sendall(header + payload)
        except _socket.timeout:
            raise TimeoutError(
                f"send timed out after {timeout}s"
            ) from None


def recv(
    sock: "_socket.socket",
    *,
    timeout: "float | None" = None,
) -> str:
    """Receive a length-prefixed string message from a socket.

    Args:
        sock: A connected socket object.
        timeout: Optional timeout in seconds. Raises TimeoutError on expiry.

    Returns:
        The decoded string message.

    Raises:
        ConnectionError: If the connection is closed before a complete
            message is received.
        TimeoutError: If the operation exceeds the timeout.
    """
    with _timeout(sock, timeout):
        try:
            header = _recv_exact(sock, _HEADER.size)
            (length,) = _HEADER.unpack(header)
            payload = _recv_exact(sock, length)
            return payload.decode("utf-8")
        except _socket.timeout:
            raise TimeoutError(
                f"recv timed out after {timeout}s"
            ) from None


def _recv_exact(sock: "_socket.socket", n: int) -> bytes:
    """Read exactly n bytes from sock, raising on premature close."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(
                f"connection closed: expected {n} bytes, got {len(buf)}"
            )
        buf.extend(chunk)
    return bytes(buf)


# ---------------------------------------------------------------------------
# 高层类：封装基于 pymsg 的客户端 / 服务器
# ---------------------------------------------------------------------------

class MsgClient:
    """TCP 聊天客户端：封装套接字创建、连接、发送、监听与关闭。

    ``listen()`` 在调用线程内阻塞运行，可另开线程调用 ``stop()`` 来终止。

    Usage::

        def on_msg(text: str) -> None:
            print(f"收到: {text}")

        c = MsgClient("localhost", 8003, "Alice")
        c.connect()
        c.send("hello")           # 发送单条消息
        c.listen(on_msg)          # 阻塞当前线程，持续回调
    """

    def __init__(self, addr: str, port: int, name: str) -> None:
        self.addr = addr
        self.port = port
        self.name = name
        self._sock: "_socket.socket | None" = None
        self._running = False

    # ---- 连接 ------------------------------------------------------------
    def connect(self) -> None:
        """创建 TCP 套接字并连接到服务器。"""
        self._sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._sock.connect((self.addr, self.port))

    # ---- 发送单条消息（短连接）--------------------------------------------
    def send(self, msg: str) -> None:
        """发送一条消息到服务器（使用短连接，pymsg 协议）。"""
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.settimeout(3)
                s.connect((self.addr, self.port))
                send(s, f"{self.name}: {msg}")
        except ConnectionRefusedError:
            raise ConnectionRefusedError(
                f"Could not connect to {self.addr}:{self.port}"
            )

    # ---- 持续监听（阻塞当前线程，回调模式）-------------------------------
    def listen(self, on_message: "callable") -> None:
        """阻塞当前线程，持续接收服务器消息并回调。

        Args:
            on_message: 回调 ``on_message(text: str) -> None``。
        """
        if self._sock is None:
            raise RuntimeError("必须先调用 connect()")
        self._running = True
        socklist = [self._sock]
        while self._running:
            try:
                readable, _, exception = select.select(
                    socklist, [], socklist, 0.1
                )
                if readable:
                    try:
                        text = recv(self._sock)
                        on_message(text)
                    except ConnectionError:
                        break
                if exception:
                    break
            except (ConnectionResetError, OSError):
                break

    # ---- 关闭 ------------------------------------------------------------
    def stop(self) -> None:
        """设置停止标志并断开 TCP 连接。

        可在另一个线程中调用，使 ``listen()`` 退出阻塞。
        """
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class MsgServer:
    """基于 pymsg 长度前缀协议的多客户端 TCP 服务器。

    收到消息时调用回调函数 —— 转发、过滤等逻辑全部在回调中实现。

    Usage::

        def on_msg(sender_sock, addr, text):
            # 转发给所有人（排除发送者）
            server.broadcast(f"[{addr}] {text}", except_sock=sender_sock)

        server = MsgServer("0.0.0.0", 8003)
        server.start(on_msg)     # 阻塞当前线程

        # 在另一线程中调用 server.stop() 可优雅退出
    """

    def __init__(self, addr: str, port: int) -> None:
        self.addr = addr
        self.port = port
        self._running = False
        self._server_sock: "_socket.socket | None" = None
        self._clients: "dict" = {}          # socket -> (ip, port)

    # ---- 启动（阻塞）----------------------------------------------------
    def start(self, on_message: "callable") -> None:
        """启动服务器，阻塞当前线程。

        Args:
            on_message: 回调 ``on_message(sock, addr, text)``
                - sock:  发送消息的客户端套接字
                - addr:  (ip, port) 元组
                - text:  解码后的字符串消息
        """
        self._running = True
        servsock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        servsock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        servsock.bind((self.addr, self.port))
        servsock.listen(10)
        self._server_sock = servsock

        socketlist = [servsock]
        while self._running:
            try:
                readable, _, exception = select.select(
                    socketlist, [], socketlist, 0.1
                )
            except (ValueError, OSError):
                break

            for s in exception:
                self._remove_client(s, socketlist)

            for s in readable:
                if s is servsock:
                    self._accept_client(servsock, socketlist)
                else:
                    self._handle_client(s, socketlist, on_message)

        self._shutdown(servsock, socketlist)

    # ---- 广播 -----------------------------------------------------------
    def broadcast(
        self, msg: str, except_sock: "_socket.socket | None" = None
    ) -> None:
        """向所有已连接客户端广播消息（pymsg 协议）。

        Args:
            msg:         要发送的字符串。
            except_sock: 排除的套接字（通常为发送者），不向其发送。
        """
        disconnected = []
        for client in list(self._clients):
            if client is except_sock:
                continue
            try:
                send(client, msg)
            except (BrokenPipeError, ConnectionResetError, OSError):
                disconnected.append(client)
        for client in disconnected:
            self._clients.pop(client, None)
            try:
                client.close()
            except OSError:
                pass

    # ---- 应答 -----------------------------------------------------------
    def reply(self, sock: "_socket.socket", msg: str) -> None:
        """向指定客户端套接字发送一条应答消息（pymsg 协议）。

        用于请求-应答模式：服务器收到消息后，通过同一 socket 回复处理结果。
        """
        send(sock, msg)

    # ---- 停止 -----------------------------------------------------------
    def stop(self) -> None:
        """设置停止标志并关闭监听套接字（可从另一线程调用）。"""
        self._running = False
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass

    # ---- 内部方法 -------------------------------------------------------
    def _accept_client(self, servsock, socketlist):
        try:
            client_sock, client_addr = servsock.accept()
            if client_sock.fileno() != -1:
                socketlist.append(client_sock)
                self._clients[client_sock] = client_addr
        except OSError:
            pass

    def _handle_client(self, s, socketlist, on_message):
        try:
            text = recv(s)
            on_message(s, self._clients.get(s, ("unknown", 0)), text)
        except (ConnectionError, ConnectionResetError, OSError):
            self._remove_client(s, socketlist)

    def _remove_client(self, s, socketlist):
        self._clients.pop(s, None)
        if s in socketlist:
            socketlist.remove(s)
        try:
            s.close()
        except OSError:
            pass

    def _shutdown(self, servsock, socketlist):
        for s in socketlist:
            try:
                s.close()
            except OSError:
                pass
        self._clients.clear()
