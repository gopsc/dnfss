"""日志客户端 — 委托到 pymsg.logc 统一库。"""
from pymsg.logc import LogClient, set_log_level  # noqa: F401

logs = LogClient(service_name="redirect80")
