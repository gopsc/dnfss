#!/usr/bin/env python3
"""
tools.proxy_tools - 代理配置工具

抽离自 aibox.py，包含 2 个工具：
  - SetProxyTool : 设置 SOCKS5 代理（设置前验证连通性）
  - GetProxyTool : 查看当前代理配置

依赖：common (ConfigManager) + tools.base.Tool
不反向依赖 aibox 任何符号。
"""

import os
from typing import Dict, Optional, Tuple

import requests

from common import ConfigManager
from .base import Tool

# 探测端点：无内容的轻量响应，验证完整 SOCKS5 握手（含认证）与出网连通性
_PROBE_URL = "http://www.gstatic.com/generate_204"
# (连接超时, 读超时)：验证总耗时上限约 10s，避免阻塞工具调用过久
_PROBE_TIMEOUT = (4, 6)


class SetProxyTool(Tool):
    """设置 SOCKS5 代理的工具"""

    def get_name(self) -> str:
        return "set_proxy"

    def get_description(self) -> str:
        return """设置智能体的 SOCKS5 代理配置。
调用后所有 HTTP/HTTPS 请求（包括网页访问、API 调用）都会通过该代理。
启用代理时会先验证连通性（完整 SOCKS5 握手 + 探测请求），验证失败不保存配置；
代理暂未启动等场景可用 skip_check=true 跳过验证强制保存。
可随时修改，即时生效。"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "代理服务器地址，如 127.0.0.1"},
                "port": {"type": "integer", "description": "代理端口，如 1080"},
                "username": {"type": "string", "description": "用户名（可选）"},
                "password": {"type": "string", "description": "密码（可选，明文仅本次会话有效，不落盘）"},
                "password_env": {"type": "string", "description": "存有密码的环境变量名（可选，跨重启持久的推荐方式）"},
                "enabled": {"type": "boolean", "description": "是否启用代理，默认 true"},
                "save": {"type": "boolean", "description": "是否保存到配置文件（持久化），默认 true"},
                "skip_check": {"type": "boolean", "description": "跳过连通性验证强制保存（默认 false）", "default": False}
            },
            "required": ["host", "port"]
        }

    @staticmethod
    def _resolve_password(password: Optional[str], password_env: Optional[str]) -> Optional[str]:
        """对齐 ConfigManager._resolve_proxy_password：明文优先，否则查环境变量"""
        if password:
            return password
        if password_env:
            return os.environ.get(password_env) or None
        return None

    def _check_connectivity(self, host: str, port: int,
                            username: Optional[str], password: Optional[str]) -> Tuple[bool, str, str]:
        """验证代理连通性：走一次完整 SOCKS5 握手 + 探测请求。

        只做 TCP connect 验不了用户名/密码，必须发真实请求。
        返回 (ok, error_kind, detail)；error_kind 取值：
        auth_failed / timeout / connect_failed / probe_failed / error
        """
        if username and password:
            proxy_url = f"socks5://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"socks5://{host}:{port}"
        try:
            # trust_env=False：排除系统环境变量代理（http_proxy 等）干扰，只验证目标代理
            session = requests.Session()
            session.trust_env = False
            resp = session.get(
                _PROBE_URL,
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=_PROBE_TIMEOUT,
            )
            # generate_204 正常返回 204；部分代理/目标返回 200 也视为连通
            if resp.status_code in (200, 204):
                return True, "", f"探测成功 HTTP {resp.status_code}"
            return False, "probe_failed", f"探测端点返回 HTTP {resp.status_code}"
        except requests.exceptions.Timeout as e:
            # 含 ConnectTimeout（连接阶段超时）：异常类型与系统语言无关，优先于消息匹配
            return False, "timeout", str(e)
        except requests.exceptions.ConnectionError as e:
            # 连接层失败（拒绝/不可达/中断）。PySocks 依赖缺失与认证失败
            # 也会以 ConnectionError 形态冒出，按消息细分。
            msg = str(e)
            low = msg.lower()
            if "missing dependencies for socks" in low:
                return False, "missing_dependency", msg
            if "authentication" in low or "auth" in low:
                return False, "auth_failed", msg
            return False, "connect_failed", msg
        except Exception as e:
            # 兜底：仅按库自身（非本地化）消息关键词分类
            msg = str(e)
            low = msg.lower()
            if "missing dependencies for socks" in low:
                return False, "missing_dependency", msg
            if "authentication" in low or "auth" in low:
                return False, "auth_failed", msg
            if "timed out" in low or "timeout" in low:
                return False, "timeout", msg
            if "refused" in low or "unreachable" in low or "closed" in low:
                return False, "connect_failed", msg
            return False, "error", msg

    def execute(self, host: str, port: int, username: str = None, password: str = None,
                password_env: str = None, enabled: bool = True, save: bool = True,
                skip_check: bool = False, **kwargs) -> str:
        # 设置前连通性验证：坏地址/凭据在入口一次性暴露，避免静默落盘后在
        # 后续 web/API 调用中离场景报错、LLM 难以归因。skip_check 留给
        # "代理暂未启动但先写配置"的场景；禁用代理无需验证。
        check_detail = ""
        if enabled and not skip_check:
            pw = self._resolve_password(password, password_env)
            # 指定了 password_env 但环境变量为空：认证凭据缺失，提前给出精准提示
            if username and not password and password_env and pw is None:
                return self.format_result(False, "代理验证失败，未保存配置", {
                    "错误类型": "环境变量为空",
                    "详情": f"环境变量 {password_env} 未设置或为空",
                    "提示": "请先设置该环境变量，或改用 password 参数（仅本次会话有效）",
                })
            ok, kind, detail = self._check_connectivity(host, port, username, pw)
            if not ok:
                hints = {
                    "auth_failed": "用户名/密码错误，请核对凭据",
                    "timeout": "代理不可达（超时），请检查地址与网络",
                    "connect_failed": "连接被拒绝或不可达，请检查 host/port",
                    "missing_dependency": "缺少 SOCKS 支持库，请安装：pip install pysocks",
                }
                return self.format_result(False, "代理不可达，未保存配置", {
                    "错误类型": kind,
                    "详情": detail[:300],
                    "提示": hints.get(kind, "如确认配置无误可设置 skip_check=true 跳过验证"),
                })
            check_detail = detail

        config = ConfigManager()
        config.set_proxy(host, port, username, password, password_env, enabled, save)
        proxy_info = config.get_proxy_info()
        if proxy_info.get("password_env"):
            pw_source = "环境变量 " + proxy_info["password_env"]
        elif proxy_info.get("password"):
            pw_source = "仅本次会话（不落盘）"
        else:
            pw_source = "无"
        data = {
            "代理状态": "启用" if proxy_info["enabled"] else "禁用",
            "服务器": f"{proxy_info['host']}:{proxy_info['port']}",
            "认证": "有" if proxy_info.get("username") else "无",
            "密码来源": pw_source,
            "持久化": "已保存" if save else "仅内存"
        }
        if check_detail:
            data["连通性"] = check_detail
        return self.format_result(True, "代理设置成功", data)


class GetProxyTool(Tool):
    """查看当前代理配置"""

    def get_name(self) -> str:
        return "get_proxy"

    def get_description(self) -> str:
        return "查看当前 SOCKS5 代理配置信息"

    def get_parameters(self) -> Dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        config = ConfigManager()
        info = config.get_proxy_info()
        if info["enabled"]:
            data = {
                "代理状态": "启用",
                "服务器": f"{info['host']}:{info['port']}",
                "认证": "有" if info.get("username") else "无"
            }
            return self.format_result(True, "当前代理配置", data)
        else:
            return "❌ 当前代理未启用，所有请求走直连。"
