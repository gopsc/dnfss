#!/usr/bin/env python3
"""
tools.db_tools - 数据库相关工具

抽离自 aibox.py，包含 2 个数据库工具：
  - MemoryTool : 记忆管理（保存/搜索/查看/删除，支持分页）
  - MemoTool   : 任务管理（创建/查看/完成/删除/搜索/统计）

依赖：common (constants, Logger) + tools.base.Tool
       + subsystems.db.MemoryDatabase/MemoDatabase
不反向依赖 aibox 任何符号。
"""

import queue
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from common import constants, Logger
from subsystems import MemoryDatabase, MemoDatabase
from .base import Tool


# ==================== 记忆工具 ====================

class MemoryTool(Tool):
    """记忆工具 - 用于存储、检索和删除对话记忆（支持流式输出，支持分页）"""

    def __init__(self, memory_db: MemoryDatabase, logger: Optional[Logger] = None):
        super().__init__(logger)
        self.memory_db = memory_db

    def get_name(self) -> str:
        return "memory"

    def get_description(self) -> str:
        return """记忆管理工具 - 存储、检索和删除重要的对话信息
支持：保存总结、多关键词搜索记忆（支持分页）、查看最近的记忆、删除无用记忆"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["save", "search", "recent", "get", "delete"]
                },
                "summary": {"type": "string", "description": "要保存的总结内容（用于save操作）"},
                "topic": {"type": "string", "description": "对话主题（用于save操作）"},
                "content": {"type": "string", "description": "详细内容（可选，用于save操作）"},
                "keywords": {
                    "type": "array", "items": {"type": "string"},
                    "description": "搜索关键词列表"
                },
                "query": {"type": "string", "description": "搜索关键词（单个关键词）"},
                "match_all": {"type": "boolean", "default": False},
                "memory_id": {"type": "integer"},
                "memory_ids": {"type": "array", "items": {"type": "integer"}},
                "importance": {"type": "integer", "default": 2},
                "limit": {"type": "integer", "default": 10, "description": "每页条数（默认10）"},
                "page": {"type": "integer", "default": 1, "description": "页码（从1开始）"},
                "confirm": {"type": "boolean", "default": False}
            },
            "required": ["action"]
        }

    def execute(self, action: str, **kwargs) -> str:
        try:
            if action == "save":
                return self._save_memory(kwargs)
            elif action == "search":
                return self._search_memories(kwargs)
            elif action == "recent":
                return self._get_recent_memories(kwargs)
            elif action == "get":
                return self._get_memory_by_id(kwargs)
            elif action == "delete":
                return self._delete_memory(kwargs)
            else:
                return f"❌ 未知操作: {action}"
        except Exception as e:
            return f"❌ 记忆操作错误: {str(e)}"

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行记忆操作"""
        action = kwargs.get("action")

        # 发送开始执行的消息
        output_queue.put(("line", f"\n🧠 执行记忆操作: {action}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 执行操作
        result = self.execute(**kwargs)

        # 发送结果
        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))

    def _save_memory(self, kwargs: Dict) -> str:
        summary = kwargs.get("summary")
        if not summary:
            return "❌ 需要提供总结内容"

        topic = kwargs.get("topic", "未命名对话")
        content = kwargs.get("content")
        importance = kwargs.get("importance", constants.MEMORY_DEFAULT_IMPORTANCE)

        memory_id = self.memory_db.add_memory(
            topic=topic,
            summary=summary,
            content=content,
            importance=importance
        )

        return self.format_result(True, "记忆已保存", {
            "记忆ID": memory_id,
            "主题": topic,
            "总结": summary[:100] + "..." if len(summary) > 100 else summary
        })

    def _search_memories(self, kwargs: Dict) -> str:
        """搜索记忆 - 支持分页"""
        keywords = kwargs.get("keywords", [])
        query = kwargs.get("query", "")
        match_all = kwargs.get("match_all", False)
        page = kwargs.get("page", 1)
        page_size = kwargs.get("limit", 10)  # 使用 limit 作为 page_size

        # 确保页码和每页条数有效
        page = max(1, page)
        page_size = max(1, min(50, page_size))  # 每页最多50条

        # 处理关键词
        if keywords and isinstance(keywords, list):
            pass
        elif query:
            keywords = query.split()
        else:
            return "❌ 需要提供搜索关键词（keywords 或 query）"

        if not keywords:
            return "❌ 关键词列表为空"

        # 先获取所有结果（不分页，用于计算总数）
        all_results = self.memory_db.search_memories(keywords, match_all, limit=None)

        if not all_results:
            match_mode = "所有" if match_all else "任一"
            return f"❌ 未找到包含 {match_mode} 关键词 {keywords} 的记忆"

        # 计算分页信息
        total_count = len(all_results)
        total_pages = (total_count + page_size - 1) // page_size

        # 检查页码是否有效
        if page > total_pages and total_pages > 0:
            return f"❌ 页码 {page} 超出范围，共有 {total_pages} 页"

        # 计算当前页的起止索引
        start_idx = (page - 1) * page_size
        end_idx = min(start_idx + page_size, total_count)

        # 获取当前页的结果
        page_results = all_results[start_idx:end_idx]

        # 构建输出
        match_mode = "所有关键词" if match_all else "任一关键词"
        output = [
            f"🔍 搜索 {match_mode} {keywords} 找到 {total_count} 条记忆",
            f"📄 第 {page}/{total_pages} 页 (每页 {page_size} 条)",
            "-" * 40
        ]

        for i, mem in enumerate(page_results, 1):
            # 全局序号
            global_idx = start_idx + i
            output.append(f"\n{global_idx}. [ID: {mem['id']}] [{mem['end_time']}] {mem['topic']}")
            output.append(f"   总结: {mem['summary'][:200]}")
            if mem.get('tags'):
                output.append(f"   标签: {', '.join(mem['tags'])}")
            if mem.get('importance'):
                output.append(f"   重要性: {'⭐' * mem['importance']} ({mem['importance']}/5)")

        # 添加翻页提示
        if total_pages > 1:
            output.append(f"\n📖 翻页提示: 使用 page 参数查看更多结果")
            output.append(f"   例如: memory search query='{query}' page={page + 1 if page < total_pages else page} limit={page_size}")
            if page < total_pages:
                output.append(f"   ➡️ 下一页: page={page + 1}")
            if page > 1:
                output.append(f"   ⬅️ 上一页: page={page - 1}")

        return "\n".join(output)

    def _get_recent_memories(self, kwargs: Dict) -> str:
        """获取最近的记忆 - 支持分页"""
        page = kwargs.get("page", 1)
        page_size = kwargs.get("limit", 10)

        # 确保页码和每页条数有效
        page = max(1, page)
        page_size = max(1, min(50, page_size))

        # 先获取所有结果
        all_results = self.memory_db.get_recent_memories(limit=None)

        if not all_results:
            return "📭 没有找到记忆"

        # 计算分页信息
        total_count = len(all_results)
        total_pages = (total_count + page_size - 1) // page_size

        # 检查页码是否有效
        if page > total_pages and total_pages > 0:
            return f"❌ 页码 {page} 超出范围，共有 {total_pages} 页"

        # 计算当前页的起止索引
        start_idx = (page - 1) * page_size
        end_idx = min(start_idx + page_size, total_count)

        # 获取当前页的结果
        page_results = all_results[start_idx:end_idx]

        output = [f"📚 最近 {total_count} 条记忆 (第 {page}/{total_pages} 页，每页 {page_size} 条):"]
        output.append("-" * 40)

        for i, mem in enumerate(page_results, 1):
            global_idx = start_idx + i
            output.append(f"\n{global_idx}. [ID: {mem['id']}] [{mem['end_time']}] {mem['topic']}")
            output.append(f"   总结: {mem['summary'][:100]}")

        # 添加翻页提示
        if total_pages > 1:
            output.append(f"\n📖 翻页提示: 使用 page 参数查看更多结果")
            output.append(f"   例如: memory recent page={page + 1 if page < total_pages else page} limit={page_size}")

        return "\n".join(output)

    def _get_memory_by_id(self, kwargs: Dict) -> str:
        """根据ID获取记忆"""
        memory_id = kwargs.get("memory_id")
        if not memory_id:
            return "❌ 需要提供记忆ID"

        memory = self.memory_db.get_memory_by_id(memory_id)
        if not memory:
            return f"❌ 未找到ID为 {memory_id} 的记忆"

        output = [
            f"📌 记忆 #{memory_id}",
            f"主题: {memory['topic']}",
            f"时间: {memory['start_time']} - {memory['end_time']}",
            f"总结: {memory['summary']}",
        ]

        if memory.get('content'):
            output.append(f"详细内容:\n{memory['content']}")

        if memory.get('tags'):
            output.append(f"标签: {', '.join(memory['tags'])}")

        if memory.get('importance'):
            output.append(f"重要性: {'⭐' * memory['importance']} ({memory['importance']}/5)")

        return "\n".join(output)

    def _delete_memory(self, kwargs: Dict) -> str:
        """删除记忆"""
        confirm = kwargs.get("confirm", False)
        if not confirm:
            return "❌ 需要设置 confirm=True 来确认删除操作"

        memory_id = kwargs.get("memory_id")
        memory_ids = kwargs.get("memory_ids", [])

        deleted_count = 0

        if memory_id:
            if self.memory_db.delete_memory(memory_id):
                deleted_count = 1
        elif memory_ids:
            deleted_count = self.memory_db.delete_memories_by_ids(memory_ids)
        else:
            return "❌ 需要提供 memory_id 或 memory_ids"

        if deleted_count == 0:
            return f"❌ 未找到要删除的记忆"

        return self.format_result(True, f"成功删除 {deleted_count} 条记忆", {"删除数量": deleted_count})


# ==================== 备忘录工具 ====================

class MemoTool(Tool):
    """备忘录工具 - 用于管理任务（所有任务都是对话任务）"""

    def __init__(self, memo_db: MemoDatabase, logger: Optional[Logger] = None):
        super().__init__(logger)
        self.memo_db = memo_db

    def get_name(self) -> str:
        return "memo"

    def get_description(self) -> str:
        return """任务管理工具 - 创建、查看、完成任务
支持：创建任务（支持一次性/重复、即时任务）、查看待办、标记完成、删除任务、搜索任务"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["add", "list", "get", "complete", "delete", "search", "stats"]
                },
                "title": {
                    "type": "string",
                    "description": "任务标题（用于add操作）"
                },
                "content": {
                    "type": "string",
                    "description": "任务内容（可选，用于add操作）"
                },
                "reminder_time": {
                    "type": "string",
                    "description": "执行时间（格式：YYYY-MM-DD HH:MM:SS，或相对时间如 '+1h', '+30m', 'tomorrow 9am'）"
                },
                "reminder_minutes": {
                    "type": "integer",
                    "description": "延迟分钟数（从现在开始，用于快速设置任务）"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "标签列表"
                },
                "priority": {
                    "type": "integer",
                    "description": "优先级 1-5（默认1）",
                    "default": 1
                },
                "repeat_type": {
                    "type": "string",
                    "description": "重复类型（none=一次性, daily=每天, weekly=每周, monthly=每月, custom=自定义）",
                    "enum": ["none", "daily", "weekly", "monthly", "custom"],
                    "default": "none"
                },
                "repeat_interval": {
                    "type": "integer",
                    "description": "自定义重复间隔，当repeat_type='custom'时使用",
                    "default": 0
                },
                "repeat_interval_unit": {
                    "type": "string",
                    "description": "重复间隔单位（minutes=分钟, hours=小时, days=天），当repeat_type='custom'时使用",
                    "enum": ["minutes", "hours", "days"],
                    "default": "days"
                },
                "repeat_end_time": {
                    "type": "string",
                    "description": "重复结束时间，None表示永久重复"
                },
                "is_immediate": {
                    "type": "boolean",
                    "description": "是否为即时任务（立即执行）",
                    "default": False
                },
                "memo_id": {
                    "type": "integer",
                    "description": "任务ID（用于get、complete、delete操作）"
                },
                "memo_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "任务ID列表（用于批量删除）"
                },
                "force_complete": {
                    "type": "boolean",
                    "description": "强制完成（忽略重复规则）",
                    "default": False
                },
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词（用于search操作）"
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "是否包含已完成的任务（默认False）",
                    "default": False
                },
                "limit": {
                    "type": "integer",
                    "description": "返回数量限制（默认20）",
                    "default": 20
                },
                "confirm": {
                    "type": "boolean",
                    "description": "确认删除操作",
                    "default": False
                }
            },
            "required": ["action"]
        }

    def _parse_reminder_time(self, reminder_input: Any) -> Optional[str]:
        """解析提醒时间输入"""
        if not reminder_input:
            return None

        if isinstance(reminder_input, int):
            reminder_time = datetime.now() + timedelta(minutes=reminder_input)
            return reminder_time.strftime(constants.DATETIME_FORMAT)

        if isinstance(reminder_input, str):
            reminder_input = reminder_input.strip()

            if reminder_input.startswith('+'):
                match = re.match(r'\+(\d+)([mhd])', reminder_input)
                if match:
                    value, unit = int(match.group(1)), match.group(2)
                    if unit == 'm':
                        delta = timedelta(minutes=value)
                    elif unit == 'h':
                        delta = timedelta(hours=value)
                    elif unit == 'd':
                        delta = timedelta(days=value)
                    else:
                        return None

                    reminder_time = datetime.now() + delta
                    return reminder_time.strftime(constants.DATETIME_FORMAT)

            try:
                reminder_time = datetime.strptime(reminder_input, constants.DATETIME_FORMAT)
                return reminder_time.strftime(constants.DATETIME_FORMAT)
            except ValueError:
                pass

            now = datetime.now()
            reminder_input_lower = reminder_input.lower()

            if 'tomorrow' in reminder_input_lower:
                tomorrow = now + timedelta(days=1)
                time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', reminder_input_lower)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2)) if time_match.group(2) else 0
                    ampm = time_match.group(3)

                    if ampm:
                        if ampm == 'pm' and hour < 12:
                            hour += 12
                        elif ampm == 'am' and hour == 12:
                            hour = 0

                    reminder_time = tomorrow.replace(hour=hour, minute=minute, second=0)
                else:
                    reminder_time = tomorrow.replace(hour=9, minute=0, second=0)

                return reminder_time.strftime(constants.DATETIME_FORMAT)

        return None

    def execute(self, action: str, **kwargs) -> str:
        """执行备忘录操作"""
        try:
            if action == "add":
                return self._add_memo(kwargs)
            elif action == "list":
                return self._list_memos(kwargs)
            elif action == "get":
                return self._get_memo(kwargs)
            elif action == "complete":
                return self._complete_memo(kwargs)
            elif action == "delete":
                return self._delete_memo(kwargs)
            elif action == "search":
                return self._search_memos(kwargs)
            elif action == "stats":
                return self._get_stats(kwargs)
            else:
                return f"❌ 未知操作: {action}"
        except Exception as e:
            return f"❌ 任务操作错误: {str(e)}"

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行备忘录操作"""
        action = kwargs.get("action")

        # 发送开始执行的消息
        output_queue.put(("line", f"\n📝 执行备忘录操作: {action}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 执行操作
        result = self.execute(**kwargs)

        # 发送结果
        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))

    def _add_memo(self, kwargs: Dict) -> str:
        """添加任务 - 所有任务都是对话任务，不需要task_type"""
        title = kwargs.get("title")
        if not title:
            return "❌ 需要提供任务标题"

        content = kwargs.get("content")
        reminder_minutes = kwargs.get("reminder_minutes")
        reminder_time_input = kwargs.get("reminder_time")
        tags = kwargs.get("tags", [])
        priority = kwargs.get("priority", 1)
        repeat_type = kwargs.get("repeat_type", "none")
        repeat_interval = kwargs.get("repeat_interval", 0)
        repeat_interval_unit = kwargs.get("repeat_interval_unit", "days")
        repeat_end_time = kwargs.get("repeat_end_time")
        is_immediate = kwargs.get("is_immediate", False)

        if repeat_type not in ["none", "daily", "weekly", "monthly", "custom"]:
            return f"❌ 无效的重复类型: {repeat_type}"

        if repeat_type == "custom" and repeat_interval <= 0:
            return "❌ 自定义重复需要设置有效的 repeat_interval (>0)"

        reminder_time = None
        if is_immediate:
            # 即时任务：设置为当前时间，确保立即执行
            reminder_time = datetime.now().strftime(constants.DATETIME_FORMAT)
        elif reminder_minutes:
            reminder_time = self._parse_reminder_time(reminder_minutes)
        elif reminder_time_input:
            reminder_time = self._parse_reminder_time(reminder_time_input)
        else:
            # 默认30分钟后
            reminder_time = self._parse_reminder_time(30)

        memo_id = self.memo_db.add_memo(
            title=title,
            content=content,
            reminder_time=reminder_time,
            tags=tags,
            priority=priority,
            created_by="AI",
            source="user",
            repeat_type=repeat_type,
            repeat_interval=repeat_interval,
            repeat_interval_unit=repeat_interval_unit,
            repeat_end_time=repeat_end_time,
            is_immediate=is_immediate
        )

        data = {
            "任务ID": memo_id,
            "标题": title,
        }

        if is_immediate:
            data["任务类型"] = "即时任务"
        else:
            if reminder_time:
                data["执行时间"] = reminder_time

        if repeat_type != "none":
            if repeat_type == "custom":
                repeat_info = f"重复类型: 每{repeat_interval} {repeat_interval_unit}"
            else:
                repeat_map = {
                    'daily': '每天',
                    'weekly': '每周',
                    'monthly': '每月'
                }
                repeat_info = f"重复类型: {repeat_map.get(repeat_type, repeat_type)}"

            if repeat_end_time:
                repeat_info += f", 结束于: {repeat_end_time}"
            else:
                repeat_info += ", 永久重复"
            data["重复信息"] = repeat_info

        return self.format_result(True, "任务已创建", data)

    def _list_memos(self, kwargs: Dict) -> str:
        """列出任务"""
        include_completed = kwargs.get("include_completed", False)
        limit = kwargs.get("limit", 20)

        memos = self.memo_db.get_all_memos(include_completed, limit)

        if not memos:
            return "📭 没有找到任务"

        status = "所有" if include_completed else "待执行"
        output = [f"📋 {status}任务 (共 {len(memos)} 个):"]

        now = datetime.now()

        for i, memo in enumerate(memos, 1):
            status_icon = "✅" if memo['is_completed'] else "⏰"
            priority_icon = "🔴" * memo['priority']
            source_tag = f"[{memo.get('source', '未知')}]" if memo.get('source') else ""

            immediate_tag = "⚡" if memo.get('is_immediate') else ""

            repeat_tag = ""
            if memo['repeat_type'] != 'none':
                if memo['repeat_type'] == 'custom':
                    unit = memo.get('repeat_interval_unit', 'days')
                    unit_map = {'minutes': '分钟', 'hours': '小时', 'days': '天'}
                    repeat_tag = f" [每{memo['repeat_interval']}{unit_map.get(unit, unit)}]"
                else:
                    repeat_map = {
                        'daily': ' [每天]',
                        'weekly': ' [每周]',
                        'monthly': ' [每月]'
                    }
                    repeat_tag = repeat_map.get(memo['repeat_type'], ' [重复]')

            output.append(f"\n{i}. {status_icon}{immediate_tag} [ID: {memo['id']}] {source_tag}{repeat_tag} {memo['title']} {priority_icon}")

            if memo['reminder_time']:
                try:
                    reminder = datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT)
                    if not memo['is_completed'] and reminder < now:
                        output.append(f"   ⚠️ 已过期: {memo['reminder_time']}")
                    else:
                        output.append(f"   ⏱️ 执行: {memo['reminder_time']}")
                except:
                    output.append(f"   ⏱️ 执行: {memo['reminder_time']}")

            if memo.get('trigger_count', 0) > 0:
                output.append(f"   🔄 已执行 {memo['trigger_count']} 次")

            if memo['content']:
                preview = memo['content'][:100] + "..." if len(memo['content']) > 100 else memo['content']
                output.append(f"   📝 {preview}")

            if memo['tags']:
                output.append(f"   🏷️ {', '.join(memo['tags'])}")

        return "\n".join(output)

    def _get_memo(self, kwargs: Dict) -> str:
        """获取单个任务"""
        memo_id = kwargs.get("memo_id")
        if not memo_id:
            return "❌ 需要提供任务ID"

        memo = self.memo_db.get_memo(memo_id)
        if not memo:
            return f"❌ 未找到ID为 {memo_id} 的任务"

        status = "✅ 已完成" if memo['is_completed'] else "⏰ 待执行"
        if memo['is_completed']:
            status += f" (完成于: {memo['completed_at']})"

        immediate_tag = "⚡ 即时任务" if memo.get('is_immediate') else ""

        output = [
            f"📌 任务 #{memo_id} {immediate_tag}",
            f"📋 状态: {status}",
            f"📝 标题: {memo['title']}",
        ]

        if memo.get('source'):
            output.append(f"📎 来源: {memo['source']}")

        if memo['repeat_type'] != 'none':
            if memo['repeat_type'] == 'custom':
                unit_map = {'minutes': '分钟', 'hours': '小时', 'days': '天'}
                unit = memo.get('repeat_interval_unit', 'days')
                repeat_info = f"🔄 重复: 每{memo['repeat_interval']} {unit_map.get(unit, unit)}"
            else:
                repeat_map = {
                    'daily': '每天',
                    'weekly': '每周',
                    'monthly': '每月'
                }
                repeat_info = f"🔄 重复: {repeat_map.get(memo['repeat_type'], memo['repeat_type'])}"

            if memo['repeat_end_time']:
                repeat_info += f", 结束于: {memo['repeat_end_time']}"
            else:
                repeat_info += ", 永久重复"
            output.append(repeat_info)

            if memo.get('trigger_count', 0) > 0:
                output.append(f"   📊 已执行 {memo['trigger_count']} 次")
            if memo.get('last_triggered_at'):
                output.append(f"   ⏱️ 上次执行: {memo['last_triggered_at']}")

        if memo['content']:
            output.append(f"📄 内容:\n{memo['content']}")

        if memo['reminder_time']:
            try:
                now = datetime.now()
                reminder = datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT)
                if not memo['is_completed'] and reminder < now:
                    output.append(f"⚠️ 执行时间 (已过期): {memo['reminder_time']}")
                else:
                    output.append(f"⏰ 执行时间: {memo['reminder_time']}")
            except:
                output.append(f"⏰ 执行时间: {memo['reminder_time']}")

        if memo['tags']:
            output.append(f"🏷️ 标签: {', '.join(memo['tags'])}")

        output.append(f"🔢 优先级: {'🔴' * memo['priority']} ({memo['priority']}/5)")
        output.append(f"📅 创建时间: {memo['created_at']}")
        output.append(f"👤 创建者: {memo['created_by'] or '系统'}")

        return "\n".join(output)

    def _complete_memo(self, kwargs: Dict) -> str:
        """完成任务"""
        memo_id = kwargs.get("memo_id")
        if not memo_id:
            return "❌ 需要提供任务ID"

        force_complete = kwargs.get("force_complete", False)

        memo = self.memo_db.get_memo(memo_id)
        if not memo:
            return f"❌ 未找到ID为 {memo_id} 的任务"

        if memo['is_completed'] and not force_complete:
            return f"ℹ️ 任务 #{memo_id} 已经完成了"

        if self.memo_db.complete_memo(memo_id, force_complete):
            memo = self.memo_db.get_memo(memo_id)
            data = {
                "任务ID": memo_id,
                "标题": memo['title']
            }

            if memo['repeat_type'] != 'none' and not memo['is_completed']:
                data["下一次执行"] = memo['reminder_time']
                data["已执行次数"] = memo.get('trigger_count', 0)
                return self.format_result(True, "任务已处理，已更新下一次执行时间", data)
            else:
                return self.format_result(True, "任务已完成", data)
        else:
            return f"❌ 无法完成任务 #{memo_id}"

    def _delete_memo(self, kwargs: Dict) -> str:
        """删除任务"""
        confirm = kwargs.get("confirm", False)
        if not confirm:
            return "❌ 需要设置 confirm=True 来确认删除操作"

        memo_id = kwargs.get("memo_id")
        if memo_id:
            if self.memo_db.complete_memo(memo_id, force_complete=True):
                return self.format_result(True, f"任务 #{memo_id} 已删除")
            else:
                return f"❌ 无法删除任务 #{memo_id}"
        else:
            return "❌ 需要提供 memo_id"

    def _search_memos(self, kwargs: Dict) -> str:
        """搜索任务"""
        keyword = kwargs.get("keyword")
        if not keyword:
            return "❌ 需要提供搜索关键词"

        include_completed = kwargs.get("include_completed", False)
        limit = kwargs.get("limit", 20)

        all_memos = self.memo_db.get_all_memos(include_completed, limit=100)

        results = []
        keyword_lower = keyword.lower()
        for memo in all_memos:
            if (keyword_lower in memo['title'].lower() or
                (memo['content'] and keyword_lower in memo['content'].lower())):
                results.append(memo)

        if not results:
            return f"❌ 未找到包含 '{keyword}' 的任务"

        output = [f"🔍 搜索 '{keyword}' 找到 {len(results)} 个任务:"]
        now = datetime.now()

        for i, memo in enumerate(results[:limit], 1):
            status_icon = "✅" if memo['is_completed'] else "⏰"
            immediate_tag = "⚡" if memo.get('is_immediate') else ""
            output.append(f"\n{i}. {status_icon}{immediate_tag} [ID: {memo['id']}] {memo['title']}")
            if memo['reminder_time']:
                try:
                    reminder = datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT)
                    if not memo['is_completed'] and reminder < now:
                        output.append(f"   ⚠️ 已过期: {memo['reminder_time']}")
                    else:
                        output.append(f"   ⏱️ {memo['reminder_time']}")
                except:
                    output.append(f"   ⏱️ {memo['reminder_time']}")

        return "\n".join(output)

    def _get_stats(self, kwargs: Dict) -> str:
        """获取任务统计信息"""
        stats = self.memo_db.get_memo_stats()

        output = [
            "📊 任务统计",
            f"  总任务: {stats['total']} 个",
            f"  待执行: {stats['pending']} 个",
            f"  已过期: {stats['overdue']} 个",
            f"  已完成: {stats['completed']} 个",
            f"  即时任务: {stats['immediate']} 个"
        ]

        if stats['overdue'] > 0:
            output.append(f"\n⚠️ 有 {stats['overdue']} 个任务已过期，将自动执行")

        return "\n".join(output)
