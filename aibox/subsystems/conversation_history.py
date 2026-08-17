#!/usr/bin/env python3
"""
subsystems.conversation_history - 对话历史管理器

抽离自 aibox.py，管理当前对话与归档历史，提供上下文加载能力。

职责：
  - 维护当前对话（current_conversation.json）
  - 归档历史对话（archive/*.json）+ 历史索引（conversation_history.json）
  - 加载历史消息作为 AI 上下文
  - 归档/结束时可选写入 MemoryDatabase

依赖：common (constants, ConfigManager, Logger) + subsystems.db (MemoryDatabase) + 标准库
不反向依赖 aibox 任何符号。
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from common import ConfigManager, Logger, constants

if TYPE_CHECKING:
    # 仅用于类型注解，避免运行时循环导入；MemoryDatabase 来自同包 subsystems.db
    from subsystems.db import MemoryDatabase


class ConversationHistory:
    """对话历史管理器 - 管理所有历史对话"""

    def __init__(self, save_dir: str = None, memory_db: "MemoryDatabase" = None, load_history: bool = None):
        if save_dir is None:
            save_dir = constants.SAVE_DIR

        self.save_dir = Path(save_dir)
        self.archive_dir = self.save_dir / constants.ARCHIVE_DIR
        self.history_file = self.save_dir / constants.HISTORY_FILE
        self.memory_db = memory_db

        config = ConfigManager()
        if load_history is None:
            load_history = config.get("system.load_history_by_default", True)
        self.load_history = load_history

        # 确保目录存在
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        self.logger = Logger("HistoryManager")
        self.history_index = self._load_history_index()
        self.current_conversation = None
        self.current_file = self.save_dir / constants.CURRENT_CONVERSATION_FILE

        self._load_current_conversation()

    def _load_history_index(self) -> Dict:
        """加载历史索引文件"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                # N16：收窄为 Exception——裸 except 连 KeyboardInterrupt/SystemExit
                # 也吞掉；损坏时重置为空索引并告警
                self.logger.warning(f"历史索引文件损坏，已重置: {e}")
                return {"conversations": [], "last_id": 0}
        return {"conversations": [], "last_id": 0}

    def _save_history_index(self):
        """保存历史索引"""
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(self.history_index, f, ensure_ascii=False, indent=2)

    def _load_current_conversation(self):
        """加载当前的对话（如果有）"""
        if self.current_file.exists():
            try:
                with open(self.current_file, 'r', encoding='utf-8') as f:
                    self.current_conversation = json.load(f)
                    self.logger.info(f"已加载当前对话: {self.current_conversation.get('topic', '未命名')}")
            except Exception as e:
                self.logger.error(f"加载当前对话失败: {e}")

    def save_current_conversation(self):
        """保存当前对话"""
        if self.current_conversation:
            with open(self.current_file, 'w', encoding='utf-8') as f:
                json.dump(self.current_conversation, f, ensure_ascii=False, indent=2)

    def start_new_conversation(self, topic: str = None) -> Dict:
        """开始新对话"""
        if self.current_conversation:
            self.archive_current_conversation()

        conv_id = str(int(time.time()))
        self.current_conversation = {
            "id": conv_id,
            "start_time": datetime.now().strftime(constants.DATETIME_FORMAT),
            "end_time": None,
            "topic": topic or "新对话",
            "messages": [],
            "tool_calls": [],
            "stats": {
                "user_messages": 0,
                "ai_messages": 0,
                "tool_calls": 0
            }
        }

        self.save_current_conversation()
        self.logger.info(f"开始新对话: {topic}")
        return self.current_conversation

    def archive_current_conversation(self, final_summary: str = None):
        """将当前对话归档到历史"""
        if not self.current_conversation:
            return None

        if self.current_conversation.get("end_time") is None:
            self.current_conversation["end_time"] = datetime.now().strftime(constants.DATETIME_FORMAT)

        if final_summary and self.memory_db:
            messages = self.current_conversation.get("messages", [])
            content_preview = "\n".join([f"{m['speaker']}: {m['content'][:200]}"
                                        for m in messages[-10:] if m.get('speaker')])

            self.memory_db.add_memory(
                topic=self.current_conversation["topic"],
                summary=final_summary,
                content=content_preview,
                start_time=self.current_conversation["start_time"],
                end_time=self.current_conversation["end_time"],
                importance=2
            )

        # N16：归档文件名加序号防同秒覆盖——DATE_FORMAT 精度为秒，
        # 同一秒内两次归档（如测试/快速重置）会静默覆盖前一份
        timestamp = datetime.now().strftime(constants.DATE_FORMAT)
        archive_filename = f"conversation_{timestamp}.json"
        counter = 1
        while (self.archive_dir / archive_filename).exists():
            archive_filename = f"conversation_{timestamp}_{counter}.json"
            counter += 1
        archive_path = self.archive_dir / archive_filename

        with open(archive_path, 'w', encoding='utf-8') as f:
            json.dump(self.current_conversation, f, ensure_ascii=False, indent=2)

        self.history_index["conversations"].append({
            "id": self.current_conversation["id"],
            "file": archive_filename,
            "start_time": self.current_conversation["start_time"],
            "end_time": self.current_conversation["end_time"],
            "topic": self.current_conversation["topic"],
            "messages": len(self.current_conversation["messages"]),
            "tool_calls": len(self.current_conversation["tool_calls"])
        })

        self._save_history_index()
        self.logger.info(f"对话已归档: {archive_filename}")

        self.current_conversation = None
        if self.current_file.exists():
            self.current_file.unlink()

        return str(archive_path)

    def load_all_history(self, max_messages: int = None) -> List[Dict]:
        """加载所有历史对话作为上下文"""
        if max_messages is None:
            max_messages = constants.MAX_HISTORY_CONTEXT

        if not self.load_history:
            return []

        all_messages = []

        if self.current_conversation:
            current_msgs = self.current_conversation.get("messages", [])
            for msg in current_msgs[-max_messages//2:]:
                if not msg.get("is_history"):
                    all_messages.append({
                        "timestamp": msg["timestamp"],
                        "role": msg["role"],
                        "speaker": msg.get("speaker"),
                        "content": msg["content"],
                        "from_current": True
                    })

        history_files = sorted(self.archive_dir.glob("*.json"), reverse=True)[:constants.MAX_HISTORY_CONVERSATIONS]

        for hist_file in history_files:
            try:
                with open(hist_file, 'r', encoding='utf-8') as f:
                    conv = json.load(f)
                    messages = conv.get("messages", [])
                    for msg in messages[-max_messages//len(history_files):]:
                        if not msg.get("is_history"):
                            all_messages.append({
                                "timestamp": msg["timestamp"],
                                "role": msg["role"],
                                "speaker": msg.get("speaker"),
                                "content": msg["content"],
                                "from_file": hist_file.name
                            })
            except Exception as e:
                self.logger.error(f"加载历史文件 {hist_file} 失败: {e}")

        all_messages.sort(key=lambda x: x["timestamp"])

        if len(all_messages) > max_messages:
            all_messages = all_messages[-max_messages:]

        self.logger.info(f"已加载 {len(all_messages)} 条历史消息")
        return all_messages

    def get_conversation_context(self, max_messages: int = None) -> List[Dict]:
        """获取适合作为AI上下文的对话历史"""
        if max_messages is None:
            max_messages = constants.MAX_HISTORY_CONTEXT

        if not self.load_history:
            return []

        history_messages = self.load_all_history(max_messages)

        context = []
        for msg in history_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                continue

            if role == "assistant":
                speaker = msg.get("speaker", "")
                context.append({
                    "role": "assistant",
                    "content": f"[{speaker}] {content}"
                })
            else:
                context.append({
                    "role": "user",
                    "content": content
                })

        return context

    def add_message(self, role: str, content: str, speaker: str = None):
        """添加消息到当前对话"""
        if not self.current_conversation:
            self.start_new_conversation()

        message = {
            "timestamp": datetime.now().strftime(constants.DATETIME_FORMAT),
            "role": role,
            "speaker": speaker,
            "content": content
        }

        self.current_conversation["messages"].append(message)

        if role == "user":
            self.current_conversation["stats"]["user_messages"] += 1
        elif role == "assistant":
            self.current_conversation["stats"]["ai_messages"] += 1

        self.save_current_conversation()

    def add_tool_call(self, tool_name: str, args: Dict, result: str):
        """记录工具调用"""
        if not self.current_conversation:
            return

        tool_call = {
            "timestamp": datetime.now().strftime(constants.DATETIME_FORMAT),
            "tool": tool_name,
            "args": args,
            "result_preview": result[:200] + "..." if len(result) > 200 else result
        }

        self.current_conversation["tool_calls"].append(tool_call)
        self.current_conversation["stats"]["tool_calls"] += 1
        self.save_current_conversation()

    def end_current_conversation(self, summary: str = None):
        """结束当前对话"""
        if self.current_conversation:
            self.current_conversation["end_time"] = datetime.now().strftime(constants.DATETIME_FORMAT)

            if summary and self.memory_db:
                messages = self.current_conversation.get("messages", [])
                content_preview = "\n".join([f"{m['speaker']}: {m['content'][:200]}"
                                            for m in messages[-10:] if m.get('speaker')])

                self.memory_db.add_memory(
                    topic=self.current_conversation["topic"],
                    summary=summary,
                    content=content_preview,
                    start_time=self.current_conversation["start_time"],
                    end_time=self.current_conversation["end_time"],
                    importance=2
                )

            self.save_current_conversation()
            self.logger.info("当前对话已保存")

    def reset_conversation(self):
        """重置当前对话状态"""
        if self.current_conversation:
            self.current_conversation["end_time"] = datetime.now().strftime(constants.DATETIME_FORMAT)
            self.save_current_conversation()
        self.current_conversation = None

    def list_conversations(self) -> List[Dict]:
        """列出所有对话（包括当前和归档）"""
        conversations = []

        if self.current_conversation:
            conversations.append({
                "file": constants.CURRENT_CONVERSATION_FILE,
                "is_current": True,
                "id": self.current_conversation.get("id"),
                "start_time": self.current_conversation.get("start_time"),
                "end_time": self.current_conversation.get("end_time"),
                "topic": self.current_conversation.get("topic"),
                "messages": len(self.current_conversation.get("messages", [])),
                "tool_calls": len(self.current_conversation.get("tool_calls", []))
            })

        for conv in self.history_index.get("conversations", []):
            conversations.append({
                "file": conv["file"],
                "is_current": False,
                "id": conv["id"],
                "start_time": conv["start_time"],
                "end_time": conv["end_time"],
                "topic": conv["topic"],
                "messages": conv["messages"],
                "tool_calls": conv["tool_calls"]
            })

        conversations.sort(key=lambda x: x["start_time"] if x["start_time"] else "", reverse=True)
        return conversations

    def load_conversation(self, file_name: str) -> Optional[Dict]:
        """加载指定的对话"""
        if file_name == constants.CURRENT_CONVERSATION_FILE and self.current_conversation:
            return self.current_conversation

        file_path = self.archive_dir / file_name
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
