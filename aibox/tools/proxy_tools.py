#!/usr/bin/env python3
"""
tools.proxy_tools - 代理配置工具

抽离自 aibox.py，包含 2 个工具：
  - SetProxyTool : 设置 SOCKS5 代理
  - GetProxyTool : 查看当前代理配置

依赖：common (ConfigManager) + tools.base.Tool
不反向依赖 aibox 任何符号。
"""

from typing import Dict

from common import ConfigManager
from .base import Tool


class SetProxyTool(Tool):
    """设置 SOCKS5 代理的工具"""

    def get_name(self) -> str:
        return "set_proxy"

    def get_description(self) -> str:
        return """设置智能体的 SOCKS5 代理配置。
调用后所有 HTTP/HTTPS 请求（包括网页访问、API 调用）都会通过该代理。
可随时修改，即时生效。"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "代理服务器地址，如 127.0.0.1"},
                "port": {"type": "integer", "description": "代理端口，如 1080"},
                "username": {"type": "string", "description": "用户名（可选）"},
                "password": {"type": "string", "description": "密码（可选）"},
                "enabled": {"type": "boolean", "description": "是否启用代理，默认 true"},
                "save": {"type": "boolean", "description": "是否保存到配置文件（持久化），默认 true"}
            },
            "required": ["host", "port"]
        }

    def execute(self, host: str, port: int, username: str = None, password: str = None,
                enabled: bool = True, save: bool = True, **kwargs) -> str:
        config = ConfigManager()
        config.set_proxy(host, port, username, password, enabled, save)
        proxy_info = config.get_proxy_info()
        data = {
            "代理状态": "启用" if proxy_info["enabled"] else "禁用",
            "服务器": f"{proxy_info['host']}:{proxy_info['port']}",
            "认证": "有" if proxy_info.get("username") else "无",
            "持久化": "已保存" if save else "仅内存"
        }
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
