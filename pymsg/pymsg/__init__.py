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
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

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

    # ---- 发送单条消息（短连接，不等待回复）--------------------------------
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

    # ---- 请求-应答（短连接）------------------------------------------------
    def call(self, msg: str, *, timeout: "float | None" = None) -> str:
        """发送一条消息并阻塞等待单条回复（短连接：connect → send → recv → close）。

        适合请求-应答模式，如 RPC 调用。不会给消息添加 name 前缀。

        Args:
            msg:     要发送的原始字符串。
            timeout: 超时秒数，默认 10 秒。

        Returns:
            str: 服务器返回的字符串。

        Raises:
            ConnectionRefusedError: 目标服务不可达。
            TimeoutError:           等待响应超时。
            ConnectionError:        连接在收全数据前断开。
        """
        _tout = timeout if timeout is not None else 10.0
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.settimeout(_tout)
                s.connect((self.addr, self.port))
                send(s, msg)
                return recv(s, timeout=_tout)
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

    并发模式::

        server = MsgServer("0.0.0.0", 8003, workers=4)

    ``workers > 0`` 时回调提交到线程池并发执行（I/O 密集回调可获得真实
    并行度）。同一连接上的消息仍严格串行处理；``reply()`` / ``broadcast()``
    经每连接发送锁串行化，不会发生帧交错。
    """

    def __init__(self, addr: str, port: int, *, workers: int = 0) -> None:
        self.addr = addr
        self.port = port
        self._workers = workers
        self._running = False
        self._server_sock: "_socket.socket | None" = None
        self._clients: "dict" = {}          # socket -> (ip, port)
        self._send_locks: "dict" = {}       # socket -> Lock（防并发发送帧交错）
        self._pool: "ThreadPoolExecutor | None" = None
        self._lock = threading.Lock()       # 保护 _busy / _failed
        self._busy: "set" = set()           # 回调执行中的 socket（暂停 select 监听）
        self._failed: "set" = set()         # 回调失败的 socket（主循环统一移除）

    # ---- 启动（阻塞）----------------------------------------------------
    def start(self, on_message: "callable") -> None:
        """启动服务器，阻塞当前线程。

        Args:
            on_message: 回调 ``on_message(sock, addr, text)``
                - sock:  发送消息的客户端套接字
                - addr:  (ip, port) 元组
                - text:  解码后的字符串消息

        ``workers > 0`` 时回调提交到线程池并发执行；同一连接的消息保持串行
        （回调完成前该连接暂停 select 监听，恢复粒度为一个 select 周期，
        最坏约 100ms——对短连接 RPC 模式无影响）。
        """
        self._running = True
        servsock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        servsock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        servsock.bind((self.addr, self.port))
        servsock.listen(10)
        self._server_sock = servsock
        if self._workers > 0:
            self._pool = ThreadPoolExecutor(
                max_workers=self._workers,
                thread_name_prefix=f"pymsg-{self.port}",
            )

        socketlist = [servsock]
        try:
            while self._running:
                self._reap_failed(socketlist)
                with self._lock:
                    watched = [s for s in socketlist if s not in self._busy]
                try:
                    readable, _, exception = select.select(
                        watched, [], watched, 0.1
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
        finally:
            if self._pool is not None:
                self._pool.shutdown(wait=True)
                self._pool = None
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
                self._locked_send(client, msg)
            except (BrokenPipeError, ConnectionResetError, OSError):
                disconnected.append(client)
        for client in disconnected:
            self._clients.pop(client, None)
            self._send_locks.pop(client, None)
            try:
                client.close()
            except OSError:
                pass

    # ---- 应答 -----------------------------------------------------------
    def reply(self, sock: "_socket.socket", msg: str) -> None:
        """向指定客户端套接字发送一条应答消息（pymsg 协议）。

        用于请求-应答模式：服务器收到消息后，通过同一 socket 回复处理结果。
        经每连接发送锁串行化，``workers > 0`` 时可在回调线程安全调用。
        """
        self._locked_send(sock, msg)

    def _locked_send(self, sock: "_socket.socket", msg: str) -> None:
        """带每连接锁的发送：防止多线程向同一 socket 并发写入导致帧交错。"""
        lock = self._send_locks.get(sock)
        if lock is None:
            send(sock, msg)
            return
        with lock:
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
                self._send_locks[client_sock] = threading.Lock()
        except OSError:
            pass

    def _handle_client(self, s, socketlist, on_message):
        try:
            text = recv(s)
        except (ConnectionError, ConnectionResetError, OSError):
            self._remove_client(s, socketlist)
            return
        addr = self._clients.get(s, ("unknown", 0))
        if self._pool is None:
            # 同步模式（默认）：回调在主循环线程内执行，行为与旧版一致
            try:
                on_message(s, addr, text)
            except (ConnectionError, ConnectionResetError, OSError):
                self._remove_client(s, socketlist)
            return
        # 线程池模式：先标记 busy（暂停 select 监听该连接，保证同连接串行），
        # 再把回调提交到线程池执行
        with self._lock:
            self._busy.add(s)
        self._pool.submit(self._run_task, s, addr, text, on_message)

    def _run_task(self, s, addr, text, on_message):
        """工作线程：执行用户回调。

        出错时只打标记（``_failed``），实际的连接关闭与移除由主循环统一
        执行，避免 worker 线程与 accept/select 并发操作 fd。
        """
        try:
            on_message(s, addr, text)
        except (ConnectionError, ConnectionResetError, OSError):
            with self._lock:
                self._failed.add(s)
        except Exception:
            # 同步模式下此类异常会直接使 start() 崩溃；线程池模式无法传播，
            # 打印 traceback 并移除该连接，保证服务整体可用
            traceback.print_exc()
            with self._lock:
                self._failed.add(s)
        finally:
            with self._lock:
                self._busy.discard(s)

    def _reap_failed(self, socketlist):
        """主循环每轮统一移除回调失败的连接。"""
        with self._lock:
            failed, self._failed = self._failed, set()
        for s in failed:
            self._remove_client(s, socketlist)

    def _remove_client(self, s, socketlist):
        self._clients.pop(s, None)
        self._send_locks.pop(s, None)
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
        self._send_locks.clear()
