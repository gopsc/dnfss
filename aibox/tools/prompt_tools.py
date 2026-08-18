#!/usr/bin/env python3
"""
tools.prompt_tools - 提示词相关工具

抽离自 aibox.py，包含 3 个工具：
  - UpdateIdentityTool               : 更新用户身份信息（00_IDENTITY.md）
  - UpdateSoulTool                    : 更新智能体自我认识（99_SOUL.md）
  - SaveMemoryAndEndConversationTool  : 保存记忆并结束对话（同时更新状态）

依赖：common (i18n, Logger) + tools.base.Tool
       + subsystems.db.MemoryDatabase + subsystems.prompt_manager.PromptManager
不反向依赖 aibox 任何符号。
"""

import queue
from typing import Callable, Dict, Optional

from common import i18n, Logger
from subsystems import MemoryDatabase, PromptManager
from .base import Tool


class UpdateIdentityTool(Tool):
    """更新用户身份工具 - 修改 00_IDENTITY.md 文件"""

    def __init__(self, prompt_manager: PromptManager, logger: Optional[Logger] = None):
        super().__init__(logger)
        self.prompt_manager = prompt_manager

    def get_name(self) -> str:
        return "update_identity"

    def get_description(self) -> str:
        return """更新用户身份信息 - 修改 00_IDENTITY.md 文件
当你了解到用户的身份信息时，调用此工具更新身份文件。
如果文件不存在，会自动创建。"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "用户身份信息内容（Markdown格式），包含用户的身份、特点、偏好等信息"
                },
                "confirm": {
                    "type": "boolean",
                    "description": "确认更新身份信息",
                    "default": True
                }
            },
            "required": ["content"]
        }

    def execute(self, content: str = None, confirm: bool = True, **kwargs) -> str:
        if not content:
            return "❌ 需要提供身份信息内容"

        # N9：持久化注入面约束——内容超限拒绝写入（管理端另有兜底）
        if len(content) > PromptManager.MAX_CONTENT_LENGTH:
            return (f"❌ 内容过长（{len(content)} > {PromptManager.MAX_CONTENT_LENGTH} 字符），"
                    f"身份文件作为系统提示词加载，请精简后重试")

        if not confirm:
            return "❌ 身份更新已取消，如需更新请设置 confirm=true"

        self.logger.info("开始更新用户身份信息...")

        if self.prompt_manager.update_identity(content):
            return self.format_result(True, i18n.get('identity_updated', self.prompt_manager.IDENTITY_FILE), {
                "文件": self.prompt_manager.IDENTITY_FILE,
                "路径": self.prompt_manager.get_prompts_dir_path()
            })
        else:
            return self.format_result(False, "身份更新失败", {
                "文件": self.prompt_manager.IDENTITY_FILE,
                "路径": self.prompt_manager.get_prompts_dir_path()
            })

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行更新身份"""
        output_queue.put(("line", "\n👤 更新用户身份信息...\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        result = self.execute(**kwargs)

        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


class UpdateSoulTool(Tool):
    """更新智能体自我认识工具 - 修改 99_SOUL.md 文件"""

    def __init__(self, prompt_manager: PromptManager, logger: Optional[Logger] = None):
        super().__init__(logger)
        self.prompt_manager = prompt_manager

    def get_name(self) -> str:
        return "update_soul"

    def get_description(self) -> str:
        return """更新智能体的自我认识定义 - 修改 99_SOUL.md 文件
当你需要重新定义自己的身份、角色、性格、能力边界时，调用此工具更新自我认识文件。
如果文件不存在，会自动创建默认定义。"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "智能体自我认识定义内容（Markdown格式），包含：身份定义、角色描述、性格特点、能力边界、行为准则等"
                },
                "confirm": {
                    "type": "boolean",
                    "description": "确认更新自我认识定义",
                    "default": True
                }
            },
            "required": ["content"]
        }

    def execute(self, content: str = None, confirm: bool = True, **kwargs) -> str:
        if not content:
            return "❌ 需要提供智能体自我认识定义内容"

        # N9：持久化注入面约束——内容超限拒绝写入（管理端另有兜底）
        if len(content) > PromptManager.MAX_CONTENT_LENGTH:
            return (f"❌ 内容过长（{len(content)} > {PromptManager.MAX_CONTENT_LENGTH} 字符），"
                    f"灵魂文件作为系统提示词加载，请精简后重试")

        if not confirm:
            return "❌ 自我认识更新已取消，如需更新请设置 confirm=true"

        self.logger.info("开始更新智能体自我认识定义...")

        if self.prompt_manager.update_soul(content):
            return self.format_result(True, "智能体自我认识已更新", {
                "文件": self.prompt_manager.SOUL_FILE,
                "路径": self.prompt_manager.get_prompts_dir_path()
            })
        else:
            return self.format_result(False, "自我认识更新失败", {
                "文件": self.prompt_manager.SOUL_FILE,
                "路径": self.prompt_manager.get_prompts_dir_path()
            })

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行更新自我认识"""
        output_queue.put(("line", "\n🧠 更新智能体自我认识...\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        result = self.execute(**kwargs)

        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


class SaveMemoryAndEndConversationTool(Tool):
    """保存记忆并结束对话工具 - 增强版，同时更新智能体状态"""

    def __init__(self, exit_callback: Callable, memory_db: MemoryDatabase,
                 prompt_manager: PromptManager, logger: Optional[Logger] = None):
        super().__init__(logger)
        self.exit_callback = exit_callback
        self.memory_db = memory_db
        self.prompt_manager = prompt_manager

    def get_name(self) -> str:
        return "save_memory_and_end_conversation"

    def get_description(self) -> str:
        return """保存记忆并结束当前对话 - 必须提供详细的对话总结和当前状态！
使用此工具时，AI会：
1. 将本次对话的重要内容保存到记忆库
2. 提供完整的对话总结
3. 描述自己现在的状态、事情的走向等，保存到 100_STATUS.md
4. 礼貌地结束对话"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "详细的对话总结（至少50字），包含：讨论的主题、关键问题、重要结论、达成的共识等"
                },
                "topic": {
                    "type": "string",
                    "description": "对话主题（用于记忆分类，默认自动提取）"
                },
                "reason": {
                    "type": "string",
                    "description": "结束对话的原因",
                    "default": "话题讨论完成"
                },
                "current_status": {
                    "type": "string",
                    "description": "当前的智能体状态描述，包含：当前进展、下一步计划、待处理事项、整体走向等"
                },
                "next_goals": {
                    "type": "string",
                    "description": "接下来的目标或计划（可选）"
                }
            },
            "required": ["summary", "current_status"]
        }

    def execute(self, summary: str = None, topic: str = None, reason: str = "话题讨论完成",
                current_status: str = None, next_goals: str = None, **kwargs) -> str:
        if summary is None or len(summary) < 50:
            return "❌ 请提供更详细的对话总结（至少50字），以便保存到记忆库"

        if current_status is None:
            return "❌ 请提供当前状态描述，以便更新 100_STATUS.md"

        self.logger.info(f"AI保存记忆并结束对话: {reason}")

        # 保存到记忆库
        memory_result = ""
        if self.memory_db and topic:
            memory_id = self.memory_db.add_memory(
                topic=topic,
                summary=summary,
                importance=3  # 提高重要性，因为是对话总结
            )
            self.logger.info(f"对话记忆已保存，ID: {memory_id}")
            memory_result = f"记忆已保存，ID: {memory_id}"

        # 构建状态内容
        status_content = f"【当前状态】\n{current_status}\n\n"
        if next_goals:
            status_content += f"【下一步计划】\n{next_goals}\n\n"
        status_content += f"【对话总结】\n{summary}"

        # N9：状态文件作为系统提示词加载，超长拒绝写入（约束注入规模）
        if len(status_content) > PromptManager.MAX_CONTENT_LENGTH:
            return (f"❌ 状态内容过长（{len(status_content)} > "
                    f"{PromptManager.MAX_CONTENT_LENGTH} 字符），请精简 current_status/summary 后重试")

        # 更新状态文件
        status_updated = self.prompt_manager.update_status(status_content)

        if self.exit_callback:
            self.exit_callback(reason, summary)

        data = {
            "原因": reason,
            "总结预览": summary[:100] + "..." if len(summary) > 100 else summary,
            "状态更新": "✅ 已更新" if status_updated else "❌ 更新失败",
            "记忆已保存": True
        }

        if memory_result:
            data["记忆"] = memory_result

        return self.format_result(True, "对话记忆已保存，状态已更新，对话结束", data)

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行结束对话"""
        output_queue.put(("line", "\n💾 保存记忆并结束对话\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        result = self.execute(**kwargs)

        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))
