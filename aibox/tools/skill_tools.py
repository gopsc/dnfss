#!/usr/bin/env python3
"""
tools.skill_tools - 技能相关工具

抽离自 aibox.py，包含 4 个技能管理工具：
  - SearchSkillsTool   : 按关键字搜索扩展技能
  - ReloadSkillsTool   : 热重载技能
  - InstallSkillTool   : 返回技能安装指引
  - CreateSkillTool    : 返回创建技能的指令提示词

依赖：common (constants, i18n, Logger) + tools.base.Tool + tools.base.ToolRegistry
       + subsystems.skill_manager.SkillManager
不反向依赖 aibox 任何符号。
"""

import os
import queue
import re
from typing import Dict, Optional

from common import constants, i18n, Logger
from subsystems import SkillManager
from .base import Tool
from .registry import ToolRegistry


class SearchSkillsTool(Tool):
    """扩展技能搜索工具 - 按关键字搜索 ~/.aibox/skills/ 下的技能文件。

    支持两种技能格式：
    1. 可执行脚本/程序：通过 --help 获取简介和完整使用说明
    2. Markdown 文件 (.md)：第一行为简介，其余为技能内容

    工作流程：
    1. 用关键字调用本工具，获取匹配技能的名称和简介列表
    2. 对于 Markdown 技能，可以直接读取 content 字段获取完整技能内容
    3. 对于可执行技能，通过 create_subagent 派生子代理执行 "技能名 --help" 查看完整命令手册
    4. 根据内容，使用相应方式调用技能
    """

    def __init__(self, skill_manager: SkillManager, logger: Optional[Logger] = None):
        super().__init__(logger)
        self.skill_manager = skill_manager

    def get_name(self) -> str:
        return "search_skills"

    def get_description(self) -> str:
        return (
            "扩展技能搜索工具。\n\n"
            "用关键字搜索 ~/.aibox/skills/ 目录下的技能文件。\n"
            "支持两种技能格式：\n"
            "  1. 可执行脚本/程序：通过 --help 获取简介和完整使用说明\n"
            "  2. Markdown 文件 (.md)：第一行为简介，其余为技能内容\n\n"
            "使用方法：\n"
            "1. 调用本工具搜索关键字，获取匹配技能列表（空字符串列出全部）。\n"
            "2. 对于 Markdown 技能：设置 show_content=true 读取返回的内容获取完整技能说明。\n"
            "3. 对于可执行技能：用 create_subagent 派生子代理执行 \"技能名 --help\" 获取完整命令手册，再按手册调用该技能。\n"
            "4. 按照内容说明，使用相应方式调用该技能（执行类操作一律经 create_subagent 派生子代理完成）。"
        )

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": (
                        "搜索关键字，在技能名称、简介和内容中进行匹配。"
                        "传入空字符串可列出全部可用技能。"
                    )
                },
                "show_content": {
                    "type": "boolean",
                    "description": (
                        "是否在结果中显示技能内容。"
                        "对于 Markdown 技能，这会影响返回结果的详细程度。"
                        "默认 False（只显示名称和简介）。"
                    ),
                    "default": False
                }
            },
            "required": ["keyword"]
        }

    def execute(self, keyword: str = "", show_content: bool = False, **kwargs) -> str:
        kw = keyword.strip()
        if kw:
            results = self.skill_manager.search(kw)
            header = f"🔍 搜索 '{kw}' 的结果（共 {len(results)} 个匹配技能）："
            empty_msg = (
                f"❌ 未找到与 '{kw}' 相关的技能。\n"
                "提示：传入空字符串可列出所有可用技能。"
            )
        else:
            results = self.skill_manager.get_all()
            header = f"🔧 所有可用技能（共 {len(results)} 个）："
            empty_msg = "❌ ~/.aibox/skills/ 目录下暂无可用技能。"

        if not results:
            return empty_msg

        lines = [header]
        for item in results:
            skill_type = "📄 Markdown" if item.get('is_markdown') else "⚙️ 可执行"
            lines.append(f"\n  {skill_type} • {item['name']}")
            lines.append(f"    简介: {item['summary']}")

            if show_content and item.get('content'):
                content = item['content']
                if item.get('is_markdown'):
                    preview = content[:500]
                    if len(content) > 500:
                        preview += "...\n    （内容较长，已截断，请直接读取技能文件获取完整内容）"
                    lines.append(f"    内容预览:\n    {preview}")
                else:
                    preview = content[:200] if content else "(无帮助输出)"
                    if len(content) > 200:
                        preview += "..."
                    lines.append(f"    帮助预览: {preview}")
            else:
                if item.get('is_markdown'):
                    lines.append(f"    💡 提示: 设置 show_content=true 可查看技能内容")
                else:
                    lines.append(f"    💡 提示: 用 create_subagent 派生子代理执行 \"{item['name']} --help\" 查看完整手册")

        lines.append("")
        lines.append("📖 技能调用方式:")
        lines.append("  • Markdown 技能: 设置 show_content=true 读取完整技能说明")
        lines.append("  • 可执行技能: 用 create_subagent 派生子代理执行（先 '技能名 --help' 查手册，再按手册调用）")
        return "\n".join(lines)

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行搜索技能"""
        keyword = kwargs.get("keyword", "")
        show_content = kwargs.get("show_content", False)

        output_queue.put(("line", f"\n🔍 搜索技能: {keyword if keyword else '(全部)'}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 直接传递 kwargs，避免重复参数
        result = self.execute(**kwargs)

        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


class ReloadSkillsTool(Tool):
    """技能热重载工具 - 重新加载所有扩展技能"""

    def __init__(self, tool_registry: ToolRegistry, logger: Optional[Logger] = None):
        super().__init__(logger)
        self.tool_registry = tool_registry

    def get_name(self) -> str:
        return "reload_skills"

    def get_description(self) -> str:
        return """技能热重载工具 - 重新加载所有扩展技能
当你添加、修改或删除了 ~/.aibox/skills/ 目录下的技能文件后，
调用此工具可以立即生效，无需重启系统。"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "确认重新加载技能",
                    "default": True
                }
            }
        }

    def execute(self, confirm: bool = True, **kwargs) -> str:
        """执行技能热重载"""
        if not confirm:
            return "❌ 技能重载已取消，如需重载请设置 confirm=true"

        self.logger.info("开始执行技能热重载...")

        try:
            count = self.tool_registry.reload_skills()

            # 从 SkillManager 获取更新后的技能列表（含简介）
            skill_manager = self.tool_registry.skill_manager
            if skill_manager:
                all_skills = skill_manager.get_all()
                skill_lines = "\n".join(
                    f"  • {s['name']} — {s['summary']}"
                    for s in all_skills[:10]
                )
                data = {
                    "重新加载的技能数量": count,
                    "技能目录": constants.SKILLS_DIR,
                    "技能列表（含简介）": skill_lines or "（无技能）",
                }
                if count > 10:
                    data["提示"] = f"还有 {count - 10} 个技能未显示"
            else:
                data = {
                    "重新加载的技能数量": count,
                    "技能目录": constants.SKILLS_DIR,
                }

            return self.format_result(True, i18n.get('skills_reloaded', count), data)

        except Exception as e:
            self.logger.error(f"技能热重载失败: {e}")
            return i18n.get('skills_reload_failed', str(e))

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行技能热重载"""
        output_queue.put(("line", "\n🔄 执行技能热重载...\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        result = self.execute(**kwargs)

        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


class InstallSkillTool(Tool):
    """技能安装指引工具 - 返回如何将可执行文件/脚本安装为扩展技能的说明"""

    def get_name(self) -> str:
        return "install_skill"

    def get_description(self) -> str:
        return (
            "技能安装指引工具。\n"
            "返回将 Markdown 技能文件安装为扩展技能的完整步骤说明，"
            "包括文件检查、格式校验和部署方法（目标文件名统一带 .md 扩展名）。"
        )

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": (
                        "待安装的技能文件路径（可选）。"
                        "若提供，说明中会带入该路径作为示例；"
                        "不提供则返回通用说明。"
                    )
                }
            },
            "required": []
        }

    def execute(self, skill_path: str = "", **kwargs) -> str:
        skills_dir = constants.SKILLS_DIR

        # 如果给了具体路径，在说明里带入，否则用占位符
        src = skill_path.strip() if skill_path else "/path/to/your_skill.md"
        # 去掉扩展名作为技能名称，再统一补 .md 作为目标文件名
        base = os.path.splitext(os.path.basename(src))[0] if src else "your_skill"
        dst = os.path.join(skills_dir, base + ".md")

        guide = f"""📦 扩展技能安装指引
{"=" * 55}

要将一个 Markdown 技能文件安装为扩展技能，请按以下步骤操作：

─────────────────────────────────────────────────────
步骤 1：确认文件存在且格式正确
─────────────────────────────────────────────────────
调用 create_subagent 派生子代理执行以下命令，确认文件存在：

  ls -l {src}

技能文件格式要求：
  • 文件名为纯文本 Markdown，第一行是一级标题（# 技能名），作为技能摘要
  • 其余内容为技能的执行说明、使用方法和示例
  • 文件应带 .md 扩展名（系统以 .md 文件作为技能加载）

─────────────────────────────────────────────────────
步骤 2：复制到技能目录并统一为 .md 扩展名
─────────────────────────────────────────────────────
技能目录：{skills_dir}

调用 create_subagent 派生子代理执行复制命令（目标文件名统一补 .md 扩展名）：

  cp {src} {dst}

⚠️  注意：目标文件名必须带 .md 扩展名，
    系统以 .md 文件作为技能加载，且技能名称取文件名（不含 .md）。

─────────────────────────────────────────────────────
步骤 3：让系统识别新技能
─────────────────────────────────────────────────────
复制完成后，调用热重载工具使其立即生效：

  reload_skills confirm=true

之后可用 search_skills 验证技能已加载：

  search_skills keyword="{base}"

{"=" * 55}
完成以上步骤后，即可通过该技能执行相应操作。"""

        return guide


class CreateSkillTool(Tool):
    """创建扩展技能工具 - 返回提示词，驱动智能体自行编写并部署 Markdown 技能文件。

    工具本身不调用 LLM，只输出一段结构化的指令提示词。
    智能体读到提示词后，会自行：
      1. 编写满足格式要求的 Markdown 技能文件
      2. 经 create_subagent 派生子代理写入 ~/.aibox/skills/<skill_name>.md
      3. 调用 reload_skills 热重载
    """

    def get_name(self) -> str:
        return "create_skill"

    def get_description(self) -> str:
        return (
            "创建扩展技能工具。\n"
            "返回一段指令提示词，驱动你自行编写并部署一个 Markdown 技能文件（带 .md 扩展名）。\n\n"
            "技能文件格式要求：\n"
            "  • 文件名必须带 .md 扩展名（如 fetch-weather.md、calc-bmi.md）\n"
            "  • 第一行为一级标题（# 技能名），作为该技能的一句话简介\n"
            "  • 其余内容为技能的执行说明、使用方法和示例，供智能体参考执行"
        )

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "技能名称（部署后的文件名，不含扩展名；系统会自动补齐 .md）。"
                        "只能包含字母、数字、连字符和下划线，例如：fetch-weather、calc-bmi。"
                    )
                },
                "description": {
                    "type": "string",
                    "description": (
                        "对该技能的详细需求描述：它应该做什么、接受哪些输入参数、"
                        "返回什么输出、有哪些典型使用场景。描述越详细，生成质量越高。"
                    )
                }
            },
            "required": ["skill_name", "description"]
        }

    def execute(self, skill_name: str = "", description: str = "", **kwargs) -> str:
        # ---- 参数校验 ----
        if not skill_name or not skill_name.strip():
            return "❌ 错误：skill_name 不能为空"
        if not description or not description.strip():
            return "❌ 错误：description 不能为空"
        if not re.match(r'^[A-Za-z0-9_\-]+$', skill_name.strip()):
            return "❌ 错误：skill_name 只能包含字母、数字、连字符(-)和下划线(_)"

        skill_name = skill_name.strip()
        skills_dir = constants.SKILLS_DIR
        # 技能文件名统一带 .md 扩展名
        skill_path = os.path.join(skills_dir, skill_name + ".md")

        # ---- 返回给智能体的指令提示词 ----
        prompt = f"""【任务】创建扩展技能：{skill_name}

请按照以下步骤，自行完成该扩展技能的编写和部署：

─────────────────────────────────────────────────
步骤 1：编写 Markdown 技能文件
─────────────────────────────────────────────────
根据以下需求，编写一个 Markdown 技能文件：

需求描述：
{description.strip()}

技能文件必须满足的格式约定：
1. 第一行为一级标题 `# {skill_name}`，用于对该技能功能做一句话简介（系统用第一行作为技能摘要）
2. 其余内容清晰说明该技能的用途、使用方式、注意事项和典型示例
3. 内容完整可执行（即按内容操作即可完成该技能要完成的事）

─────────────────────────────────────────────────
步骤 2：写入技能目录
─────────────────────────────────────────────────
调用 create_subagent 派生子代理，将你编写的完整内容写入（文件名必须带 .md 扩展名）：
  {skill_path}
（在 task 中附上完整文件内容，并要求子代理写入后用 head -5 验证首行格式）

─────────────────────────────────────────────────
步骤 3：热重载
─────────────────────────────────────────────────
调用 reload_skills 工具使新技能立即生效：
  reload_skills confirm=true

─────────────────────────────────────────────────
完成后请告知用户技能已创建成功，并展示技能的第一行简介。"""

        return prompt
