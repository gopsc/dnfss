#!/usr/bin/env python3
"""
tools.misc_tools - 杂项工具

抽离自 aibox.py，包含不依赖任何子系统的独立工具：
  - AskUserTool : 主动向用户提问

依赖：tools.base.Tool
不反向依赖 aibox 任何符号。
"""

from typing import Dict

from .base import Tool


class AskUserTool(Tool):
    """智能体遇到了麻烦，主动向用户问一个问题"""

    def get_name(self) -> str:
        return "ask_user"

    def get_description(self) -> str:
        return (
            "主动向用户提问的工具。你是用户的专属助手，应当主动关心用户、澄清需求、确认意图，"
            "而不是被动等待指令。适用场景：指令模糊需要澄清、任务完成后追问后续需求、"
            "关心用户近况、主动发现用户可能需要的帮助。请积极使用此工具与用户互动。"
        )

    def get_parameters(self) -> Dict:
        ''' TODO: 调用子代理生成提示词 '''
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "你现在遇到一个问题，你想询问用户，现在提供你的问题。"
                    )
                }
            },
            # L8：此前 required 为空，LLM 不传 question 时 str + None 抛 TypeError
            "required": ["question"]
        }

    def execute(self, question: str = None, **kwargs) -> str:
        if not question:
            return "❌ 需要提供问题内容（question 参数）"
        return (
                "你遇到了想搞清楚的事情: " + question + "\n你现在可以询问用户。"
        )
