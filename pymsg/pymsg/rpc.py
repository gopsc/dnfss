"""pymsg.rpc — pymsg 线协议之上的 JSON RPC 约定层。

约定（与 C++/Go 客户端字节级兼容）::

    请求:  {"_route": "<路由名>", ...业务字段}
    响应:  {"success": true,  "data": ...}
          {"success": false, "error": "..."}

把各服务（db / smtp / captcha）重复的 ``_dispatch`` / ``_reply`` 服务端
分发逻辑，以及各调用方（dnfs / log_client）重复的 ``_route`` 注入 +
JSON 编解码客户端逻辑，收敛为 ``RpcServer`` / ``RpcClient`` 两个类。

异常分层::

    RpcError               — 远端返回的业务错误（success=false）或非法响应
    pymsg.TimeoutError     — 网络层超时（来自 MsgClient）
    ConnectionRefusedError — 目标不可达（来自 MsgClient）
    ConnectionError        — 连接中断（来自 MsgClient）
"""

import json

from . import MsgClient, MsgServer

__all__ = ["RpcClient", "RpcServer", "RpcError"]


class RpcError(Exception):
    """远端返回 ``success=false`` 的业务错误，或响应不符合 RPC 约定。"""


class RpcClient:
    """JSON RPC 客户端（短连接，每次调用独立 TCP 连接）。

    Usage::

        rpc = RpcClient("127.0.0.1", 8005, "dnfs")
        data = rpc.call("execute", {"action": "select", "table": "Users"})

        # 启动时阻塞等待服务就绪（连接被拒绝时自动重试）
        rpc = RpcClient("127.0.0.1", 8010, "static", retry_on_refused=True)
    """

    def __init__(self, addr: str, port: int, name: str = "rpc",
                 timeout: float = 10.0,
                 *, retry_on_refused: bool = False,
                 retry_interval: float = 1.0) -> None:
        self._client = MsgClient(addr, port, name)
        self._timeout = timeout
        self._retry_on_refused = retry_on_refused
        self._retry_interval = retry_interval

    def call(self, route: str, payload: "dict | None" = None, *,
             timeout: "float | None" = None,
             retry_on_refused: "bool | None" = None,
             retry_interval: "float | None" = None):
        """调用指定路由，返回响应中的 ``data`` 字段。

        Args:
            route:   路由名（注入为 ``_route`` 字段）。
            payload: 业务字段 dict（``_route`` 键会被覆盖）。
            timeout: 本次调用的超时秒数，缺省用构造时的值。
            retry_on_refused: 本次调用是否在连接被拒绝时阻塞重试，
                              None 表示使用构造时的值（默认 False）。
            retry_interval:  本次调用的重试间隔，None 表示使用构造时的值。

        Returns:
            服务端返回的 ``data``（可能为 None）。

        Raises:
            RpcError:              服务端返回 success=false，或响应非法。
            pymsg.TimeoutError:    等待响应超时。
            ConnectionRefusedError: 目标服务不可达且未开启重试。
        """
        req = dict(payload or {})
        req["_route"] = route
        _retry = self._retry_on_refused if retry_on_refused is None else retry_on_refused
        _interval = self._retry_interval if retry_interval is None else retry_interval
        resp = self._client.call(
            json.dumps(req, ensure_ascii=False),
            timeout=self._timeout if timeout is None else timeout,
            retry_on_refused=_retry,
            retry_interval=_interval,
        )
        try:
            obj = json.loads(resp)
        except json.JSONDecodeError as e:
            raise RpcError(f"响应不是合法 JSON: {e}") from None
        if not isinstance(obj, dict) or "success" not in obj:
            raise RpcError(f"非法 RPC 响应: {resp[:200]!r}")
        if not obj["success"]:
            raise RpcError(obj.get("error", "未知错误"))
        return obj.get("data")


class RpcServer:
    """JSON RPC 服务端：基于 MsgServer，自带路由分发与响应包装。

    handler 签名为 ``handler(req: dict) -> data``：

    - 返回值放入 ``{"success": true, "data": ...}`` 回复；
    - 抛出的异常自动包装为 ``{"success": false, "error": str(e)}``，
      服务不会因此崩溃。

    Usage::

        rpc = RpcServer("127.0.0.1", 8005, workers=4)

        @rpc.route("execute")
        def _execute(req):
            return {"rows": []}

        rpc.start()   # 阻塞当前线程
    """

    def __init__(self, addr: str, port: int, *, workers: int = 0,
                 json_default: "callable | None" = None,
                 on_error: "callable | None" = None) -> None:
        """
        Args:
            addr:         监听地址。
            port:         监听端口。
            workers:      回调并发线程数（透传 MsgServer，0=同步串行）。
            json_default: 响应 JSON 序列化的兜底函数（如日期转 ISO），
                          透传 ``json.dumps(default=...)``。
            on_error:     可选钩子 ``on_error(route, exc)``，handler 抛异常
                          时调用（用于记日志）；None 表示静默。
        """
        self._server = MsgServer(addr, port, workers=workers)
        self._routes: "dict" = {}
        self._json_default = json_default
        self._on_error = on_error

    # ── 路由注册 -------------------------------------------------------
    def route(self, name: str):
        """装饰器：注册路由处理函数。"""
        def deco(fn):
            self._routes[name] = fn
            return fn
        return deco

    def add_route(self, name: str, fn: "callable") -> None:
        """直接注册路由处理函数（等价于 route 装饰器）。"""
        self._routes[name] = fn

    # ── 启动 / 停止 ----------------------------------------------------
    def start(self) -> None:
        """启动服务（阻塞当前线程）。"""
        self._server.start(self._dispatch)

    def stop(self) -> None:
        """从另一线程停止服务。"""
        self._server.stop()

    # ── 分发与响应包装 --------------------------------------------------
    def _dispatch(self, sock, addr, text):
        try:
            req = json.loads(text)
        except json.JSONDecodeError as e:
            self._reply(sock, False, error=f"JSON 解析失败: {e}")
            return

        route = req.pop("_route", "")
        handler = self._routes.get(route)
        if handler is None:
            self._reply(sock, False, error=f"未知路由: {route}")
            return

        try:
            result = handler(req)
            self._reply(sock, True, data=result)
        except Exception as e:
            if self._on_error is not None:
                try:
                    self._on_error(route, e)
                except Exception:
                    pass
            self._reply(sock, False, error=str(e))

    def _reply(self, sock, success, data=None, error=None):
        resp = {"success": success}
        if data is not None:
            resp["data"] = data
        if error is not None:
            resp["error"] = error
        self._server.reply(sock, json.dumps(
            resp, ensure_ascii=False, default=self._json_default))
