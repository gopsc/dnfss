#!/usr/bin/env python3
"""
tools.command_tool - 命令行执行工具

抽离自 aibox.py，使用抽象的 CommandExecutor 实现命令执行（流式 + 超时控制）。
工具本身不持有 subprocess 逻辑，而是委托给 SubprocessCommandExecutor。

H1 修复（审计 2026-08-17）：此前本类内置一套重复的 Popen 读取循环，
完全绕过注入的 executor，导致 1MB 输出上限、cancel() 三层中断、
超时机制三项保护同时失效。现改为纯委托，行为与 executor 完全一致。

依赖：common (ConfigManager, constants) + tools.base.Tool + subsystems.executors
不反向依赖 aibox 任何符号。
"""

import queue
from typing import Dict

from common import ConfigManager
from subsystems import CommandExecutor, SubprocessCommandExecutor
from .base import Tool


class CommandLineTool(Tool):
    """命令行执行工具 - 使用抽象的命令执行器（支持流式输出和超时控制）"""

    def __init__(self, executor: CommandExecutor = None, logger=None):
        super().__init__(logger)
        self.executor = executor or SubprocessCommandExecutor(logger)

    def get_name(self) -> str:
        return "run_command"

    def get_description(self) -> str:
        return """执行任意系统命令，支持实时输出显示和超时控制"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的命令"
                },
                "timeout": {
                    "type": "integer",
                    "default": 30,
                    "description": "命令执行超时时间（秒），超时后将终止命令执行。默认30秒"
                },
                "work_dir": {
                    "type": "string",
                    "default": ".",
                    "description": "命令执行的工作目录"
                },
                "env": {
                    "type": "object",
                    "description": "额外的环境变量"
                }
            },
            "required": ["command"]
        }

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行命令：纯委托给 executor（1MB 上限/超时/cancel 全部生效）"""
        command = kwargs.get("command")

        if not command:
            output_queue.put(("line", "❌ 错误：缺少命令参数\n"))
            output_queue.put(("complete", "❌ 错误：缺少命令参数"))
            return

        self.executor.execute_streaming(
            command,
            output_queue,
            timeout=kwargs.get("timeout", 30),
            work_dir=kwargs.get("work_dir", "."),
            env=kwargs.get("env"),
        )

    def execute(self, command: str, timeout: int = 30,
                work_dir: str = ".", env: Dict = None, **kwargs) -> str:
        """执行命令 - 仅支持流式执行（命令工具强制流式输出，无 stream 参数）"""
        if error := self.validate_params(["command"], command=command):
            return error

        # 如果正在流式输出，直接返回空（实际输出通过队列）
        if self.streaming_output and self.output_queue:
            return ""

        # 命令工具固定走流式执行：通过临时队列收集完整结果
        output_queue = queue.Queue()
        self.execute_streaming(
            output_queue,
            command=command,
            timeout=timeout,
            work_dir=work_dir,
            env=env
        )

        # 收集流式执行的最终结果（complete 消息）
        final_result = ""
        while not output_queue.empty():
            try:
                msg_type, content = output_queue.get_nowait()
                if msg_type == "complete":
                    final_result = content
            except queue.Empty:
                break
        return final_result
