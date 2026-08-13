#!/usr/bin/env python3
"""
单AI智能体系统 - WebSocket版本（增强版）
功能：支持文件操作、网页访问、记忆管理、备忘录提醒（全部作为对话任务执行）
新增：基于备忘录的任务队列系统 - 所有任务都通过AI对话执行，实时广播到前端
新增：对话内容压缩功能 - 当对话token数或条数达到阈值时自动压缩（静默执行）
新增：开始新对话、智能体主动结束对话后重置对话记忆，重新加载system系统提示词
通过WebSocket与客户端交互

FIXME: 将额外技能集成为一个管理器技能
"""

import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
import sqlite3
import threading
import functools
import queue
import locale
import asyncio
import websockets
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple, Callable, Union, Set
import argparse
from pathlib import Path
import signal
import logging
from logging.handlers import RotatingFileHandler

import requests

# 大模型客户端抽象层（支持多厂商切换）
from llm_client import create_llm_client

# 基础设施（多语言 / 配置 / 常量 / 日志）已抽离至 common 模块
from common import i18n, I18n, ConfigManager, Constants, constants, Logger

# 工具基类与注册器已抽离至 tools 模块
from tools import (
    Tool, ToolRegistry,
    FileToolBase, ReadFileTool, WriteFileTool, ListFilesTool,
    SimpleWebCrawlerTool, GetCurrentTimeTool, CalculatorTool,
)

# 子系统（Wave 1 抽离）：命令执行器 / 提示词管理器 / 数据库 / 技能管理器
from subsystems import (
    CommandExecutor, SubprocessCommandExecutor,
    PromptManager,
    MemoryDatabase, MemoDatabase,
    SkillManager,
)




class ConversationCompressor:
    """对话压缩管理器 - 优先压缩较早的消息，将tool调用信息一并压缩"""
    
    def __init__(self, ai: 'DeepSeekChat', logger: Logger = None):
        self.ai = ai
        self.logger = logger or Logger("Compressor")
        self.config = ConfigManager()
        # 从配置获取最小值
        self.min_messages_to_compress = constants.COMPRESSOR_MIN_MESSAGES
        # 权重衰减因子：从配置获取，设为0.6使加权计算更合理
        self.weight_decay_factor = 0.6  # 使用更高的衰减因子
    
    def estimate_tokens(self, messages: List[Dict]) -> int:
        """估算消息的token数"""
        total_tokens = 0
        for msg in messages:
            content = msg.get("content", "")
            if content:
                chinese_chars = len([c for c in content if '\u4e00' <= c <= '\u9fff'])
                english_words = len(re.findall(r'[a-zA-Z]+', content))
                other_chars = len(content) - chinese_chars - english_words
                
                estimated = chinese_chars * 1.5 + english_words * 1.3 + other_chars * 0.8
                total_tokens += estimated
                total_tokens += 10
            
            if msg.get("tool_calls"):
                # 工具调用也计算token
                for tc in msg.get("tool_calls", []):
                    func = tc.get("function", {})
                    if func.get("name"):
                        total_tokens += len(func.get("name", "")) * 1.5
                    if func.get("arguments"):
                        total_tokens += len(func.get("arguments", "")) * 0.8
                total_tokens += 50
        
        return int(total_tokens)
    
    def should_compress(self, messages: List[Dict]) -> Tuple[bool, str]:
        """检查是否需要压缩对话 - 优先根据消息条数判断"""
        if not constants.COMPRESS_ENABLED:
            return False, "压缩功能已禁用"
        
        conversation_messages = [m for m in messages if m.get("role") != "system"]
        
        # 优先检查消息条数是否达到阈值
        message_count = len(conversation_messages)
        if message_count < constants.COMPRESS_MESSAGE_THRESHOLD:
            return False, f"对话消息数量({message_count})小于压缩阈值({constants.COMPRESS_MESSAGE_THRESHOLD})"
        
        # 消息条数达到阈值，进行压缩
        print(f"⚠️ {i18n.get('compress_threshold_reached', message_count)}")
        return True, f"消息数({message_count})超过阈值({constants.COMPRESS_MESSAGE_THRESHOLD})"
    
    def _calculate_target_count(self, current_count: int, threshold: int, keep_ratio: float = 0.7) -> int:
        """
        计算目标消息数量 - 压缩到阈值的 keep_ratio 倍
        
        Args:
            current_count: 当前消息数量
            threshold: 压缩阈值
            keep_ratio: 压缩后保留的比例（相对于阈值），默认0.7表示压缩到阈值的70%
        
        Returns:
            目标消息数量
        """
        # 压缩到阈值以下，保留阈值的 keep_ratio 倍
        target = int(threshold * keep_ratio)
        
        # 确保至少有 min_messages_to_compress 条消息
        target = max(target, self.min_messages_to_compress)
        
        # 确保不超过当前数量
        target = min(target, current_count - 1)
        
        return target
    
    def _find_compressible_block_by_count(self, messages: List[Dict], target_count: int) -> Tuple[int, int]:
        """
        根据目标消息数量找到可压缩块 - 不保护tool消息，直接压缩早期消息
        
        Args:
            messages: 消息列表
            target_count: 目标消息数量
        
        Returns:
            (start, end) 压缩块的起始和结束索引
        """
        if len(messages) <= target_count:
            return 0, 0
        
        # 计算需要压缩的消息数量
        messages_to_remove = len(messages) - target_count
        
        # 确定要保留的最近消息数量
        keep_recent = constants.COMPRESS_KEEP_RECENT
        if keep_recent > 0:
            keep_recent = max(keep_recent, 10)  # 至少保留最近10条
        
        # 压缩块应该从最早的消息开始，但需要保留前几轮对话
        # 计算最早可以开始压缩的位置（跳过前2轮对话）
        start_min = 0
        
        # 尝试找到前2个对话轮次结束的位置
        conversation_turns = self._build_conversation_turns(messages)
        if len(conversation_turns) >= 2:
            # 前2轮结束的位置
            first_turn = conversation_turns[0]
            second_turn = conversation_turns[1]
            # 取第二轮结束的位置
            if second_turn["assistant_index"] is not None:
                start_min = second_turn["assistant_index"] + 1
            else:
                start_min = second_turn["user_index"] + 1
        
        # 压缩结束的位置 = 总消息数 - 保留的最近消息数
        end_max = len(messages) - keep_recent
        
        # 确保压缩块有效
        if end_max <= start_min + 2:
            # 如果保护区域太大，尝试压缩更多
            end_max = len(messages) - max(keep_recent // 2, 5)
        
        # 计算需要压缩的块大小
        block_size = min(messages_to_remove + 10, end_max - start_min)
        block_size = max(block_size, 10)  # 至少压缩10条
        
        # 压缩块从 start_min 开始
        start = start_min
        end = min(start + block_size, end_max)
        
        # 确保压缩块至少2条消息
        if end - start < 2:
            # 如果块太小，尝试从更早的位置开始
            start = max(0, end_max - block_size)
            end = end_max
        
        self.logger.debug(f"找到压缩块: [{start}:{end}], 移除{end-start}条消息")
        return start, end
    
    def _build_conversation_turns(self, messages: List[Dict]) -> List[Dict]:
        """构建对话轮次"""
        conversation_turns = []
        
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role")
            
            # 跳过系统消息
            if role == "system":
                i += 1
                continue
            
            # 遇到用户消息，开始一个新的轮次
            if role == "user":
                turn = {"user_index": i, "assistant_index": None, "tool_indices": []}
                
                # 向后查找对应的助手消息和工具消息
                j = i + 1
                while j < len(messages):
                    next_msg = messages[j]
                    next_role = next_msg.get("role")
                    
                    # 跳过系统消息
                    if next_role == "system":
                        j += 1
                        continue
                    
                    # 找到助手消息
                    if next_role == "assistant":
                        turn["assistant_index"] = j
                        # 检查这个助手消息是否有 tool_calls
                        if next_msg.get("tool_calls"):
                            # 收集这个助手消息的 tool_call_ids
                            tool_call_ids = {tc.get("id") for tc in next_msg.get("tool_calls", []) if tc.get("id")}
                            # 继续查找对应的 tool 消息
                            k = j + 1
                            while k < len(messages) and len(turn["tool_indices"]) < len(tool_call_ids):
                                tool_msg = messages[k]
                                if tool_msg.get("role") == "tool":
                                    tool_call_id = tool_msg.get("tool_call_id")
                                    if tool_call_id and tool_call_id in tool_call_ids:
                                        turn["tool_indices"].append(k)
                                    k += 1
                                elif tool_msg.get("role") == "user":
                                    break
                                else:
                                    k += 1
                        break
                    
                    if next_role == "user":
                        break
                    
                    j += 1
                
                conversation_turns.append(turn)
                i += 1
            else:
                i += 1
        
        return conversation_turns
    
    def _is_range_safe(self, messages: List[Dict], start: int, end: int, protected: set) -> bool:
        """检查消息范围是否安全 - 简化版，不检查tool完整性"""
        return True  # 不保护tool消息，允许压缩
    
    def _get_protected_indices(self, messages: List[Dict]) -> set:
        """
        找出所有受保护的消息索引 - 简化版，只保护最近的对话轮次
        """
        protected_indices = set()
        
        # ========== 1. 保护最近的消息（由配置决定） ==========
        keep_recent = constants.COMPRESS_KEEP_RECENT
        if keep_recent > 0:
            recent_start = max(0, len(messages) - keep_recent)
            for i in range(recent_start, len(messages)):
                protected_indices.add(i)
        
        # ========== 2. 保护前2个对话轮次 ==========
        conversation_turns = self._build_conversation_turns(messages)
        
        if len(conversation_turns) >= 2:
            # 保护第1轮
            first_turn = conversation_turns[0]
            protected_indices.add(first_turn["user_index"])
            if first_turn["assistant_index"] is not None:
                protected_indices.add(first_turn["assistant_index"])
            for tool_idx in first_turn["tool_indices"]:
                protected_indices.add(tool_idx)
            
            # 保护第2轮
            second_turn = conversation_turns[1]
            protected_indices.add(second_turn["user_index"])
            if second_turn["assistant_index"] is not None:
                protected_indices.add(second_turn["assistant_index"])
            for tool_idx in second_turn["tool_indices"]:
                protected_indices.add(tool_idx)
        
        # 调试日志
        if self.logger.level >= 3:
            self.logger.debug(f"受保护的消息索引: {sorted(protected_indices)}")
        
        return protected_indices
    
    def _build_compression_prompt(self, messages: List[Dict]) -> str:
        """构建压缩提示词 - 包含工具调用信息"""
        conversation_lines = []
        
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            
            if role == "user":
                if content:
                    conversation_lines.append(f"用户: {content}")
            
            elif role == "assistant":
                if content:
                    conversation_lines.append(f"助手: {content}")
                
                # 包含工具调用信息
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    arguments = func.get("arguments", "")
                    if tool_name:
                        if arguments:
                            conversation_lines.append(f"[调用工具: {tool_name}({arguments[:100]})]")
                        else:
                            conversation_lines.append(f"[调用工具: {tool_name}]")
            
            elif role == "tool":
                if content:
                    useful = self._extract_useful_content(content)
                    if useful:
                        conversation_lines.append(f"[工具结果: {useful}]")
        
        conversation_text = "\n".join(conversation_lines)
        
        max_input_chars = constants.COMPRESSOR_MAX_INPUT_CHARS
        if len(conversation_text) > max_input_chars:
            conversation_text = conversation_text[:max_input_chars] + "\n...(内容过长，已截断)..."
        
        prompt = f"""请对以下对话内容进行智能压缩和总结。要求：

1. **提取核心信息**：对话的主要主题、关键问题、重要结论、用户需求
2. **保留重要细节**：用户的重要要求、AI的重要回复、达成的共识
3. **保留工具调用摘要**：简要说明使用了什么工具，得到了什么关键结果
4. **结构化输出**：使用清晰的段落结构

对话内容：
---
{conversation_text}
---

请输出压缩后的对话摘要："""
        
        return prompt
    
    def _extract_useful_content(self, content: str) -> str:
        """从工具返回内容中提取有用信息"""
        if not content:
            return ""
        
        if "✅" in content:
            lines = content.split("\n")
            for line in lines[:3]:
                if "✅" in line:
                    return line.strip()
        
        if "❌" in content:
            lines = content.split("\n")
            for line in lines[:2]:
                if "❌" in line:
                    return line.strip()
        
        if len(content) > 100:
            return content[:100] + "..."
        
        return content
    
    def _compress_via_llm(self, messages: List[Dict]) -> Optional[str]:
        """通过语言模型压缩消息（经由客户端抽象层调用，支持多厂商）"""
        if not messages:
            return None
        
        prompt = self._build_compression_prompt(messages)
        
        print(f"📦 {i18n.get('conversation_compress')}: 正在压缩 {len(messages)} 条消息...")
        
        try:
            summary = self.ai.client.chat_completion(
                [
                    {"role": "system", "content": "你是一个专业的对话摘要助手，擅长提取对话核心信息并进行结构化总结。请保留工具调用的关键结果。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=constants.COMPRESSOR_LLM_MAX_TOKENS
            )
            return summary
        except Exception as e:
            self.logger.error(f"语言模型压缩异常: {e}")
            return None
    
    def _validate_message_structure(self, messages: List[Dict]) -> List[Dict]:
        """验证并修复消息结构，移除孤立的 tool 消息"""
        if not messages:
            return messages
        
        # 找出所有 assistant 消息的 tool_call_ids
        assistant_tool_ids = set()
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    tc_id = tc.get("id")
                    if tc_id:
                        assistant_tool_ids.add(tc_id)
        
        # 过滤掉没有对应 assistant 的 tool 消息
        filtered = []
        for msg in messages:
            if msg.get("role") == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id and tool_call_id not in assistant_tool_ids:
                    print(f"   ⚠️ 移除孤立的 tool 消息: {tool_call_id}")
                    continue
            filtered.append(msg)
        
        return filtered
    
    def compress_conversation(self, messages: List[Dict]) -> List[Dict]:
        """
        压缩对话内容 - 优先压缩较早的消息，包含tool调用信息一并压缩
        当消息条数超过阈值时，压缩到阈值以下
        """
        if not messages:
            return messages
        
        # 分离系统消息和对话消息
        system_messages = []
        conversation_messages = []
        
        for msg in messages:
            if msg.get("role") == "system":
                system_messages.append(msg)
            else:
                conversation_messages.append(msg)
        
        if not conversation_messages:
            return system_messages
        
        original_count = len(conversation_messages)
        original_tokens = self.estimate_tokens(conversation_messages)
        threshold = constants.COMPRESS_MESSAGE_THRESHOLD
        
        print(f"\n📊 对话压缩检查:")
        print(f"   当前消息数: {original_count} 条 (阈值: {threshold} 条)")
        print(f"   当前Token数: {original_tokens} 个")
        
        # 计算目标消息数量 - 压缩到阈值的70%
        target_count = self._calculate_target_count(original_count, threshold, keep_ratio=0.7)
        print(f"   目标消息数: {target_count} 条 (压缩后应少于阈值)")
        
        # 如果目标数量大于等于当前数量，不需要压缩
        if target_count >= original_count:
            print(f"   目标消息数大于等于当前，跳过压缩")
            return messages
        
        # 计算需要压缩的消息数量
        messages_to_compress = original_count - target_count
        print(f"   需要压缩的消息数量: {messages_to_compress} 条")
        
        # 根据消息数量查找可压缩块
        compress_start, compress_end = self._find_compressible_block_by_count(
            conversation_messages, target_count
        )
        
        if compress_end - compress_start >= 2:
            compressible = conversation_messages[compress_start:compress_end]
            compress_tokens = self.estimate_tokens(compressible)
            compress_count = len(compressible)
            
            print(f"   压缩块范围: [{compress_start}:{compress_end}], 共 {compress_count} 条消息")
            print(f"   压缩块token数: {compress_tokens}")
            
            # 如果压缩块大小仍然不够，尝试扩大压缩块
            if compress_count < messages_to_compress:
                print(f"   压缩块大小不足({compress_count} < {messages_to_compress})，扩大压缩块...")
                # 扩大压缩块
                additional = messages_to_compress - compress_count + 5
                new_start = max(0, compress_start - additional // 2)
                new_end = min(len(conversation_messages), compress_end + additional // 2)
                compress_start, compress_end = new_start, new_end
                compressible = conversation_messages[compress_start:compress_end]
                compress_count = len(compressible)
                print(f"   扩大后压缩块: [{compress_start}:{compress_end}], 共 {compress_count} 条消息")
        else:
            print(f"   未找到合适的压缩块，使用默认压缩策略")
            # 使用默认压缩策略：压缩最早的一半消息，保留最近的消息
            keep_recent = constants.COMPRESS_KEEP_RECENT
            compress_end = len(conversation_messages) - keep_recent
            compress_start = max(0, compress_end - messages_to_compress - 5)
            compress_start = max(compress_start, 0)
            compressible = conversation_messages[compress_start:compress_end]
            compress_count = len(compressible)
            print(f"   默认压缩块: [{compress_start}:{compress_end}], 共 {compress_count} 条消息")
        
        # 确保压缩块有足够多的消息
        if compress_count < 5:
            print(f"   压缩块太小({compress_count}条)，跳过压缩")
            return messages
        
        # 调用语言模型压缩
        summary = self._compress_via_llm(compressible)
        
        if not summary:
            print(f"   ❌ 压缩失败，保留原始对话")
            return messages
        
        # 构建压缩后的消息列表
        compressed_result = []
        
        # 添加压缩块之前的消息
        compressed_result.extend(conversation_messages[:compress_start])
        
        # 插入压缩摘要（作为系统消息）
        compressed_result.append({
            "role": "system",
            "content": i18n.get('compressed_summary', summary)
        })
        
        # 添加压缩块之后的消息（包括最近的消息）
        compressed_result.extend(conversation_messages[compress_end:])
        
        # 验证并修复消息结构
        compressed_result = self._validate_message_structure(compressed_result)
        
        # 合并系统消息
        final_messages = system_messages + compressed_result
        
        # 统计结果
        final_count = len([m for m in final_messages if m.get("role") != "system"])
        final_tokens = self.estimate_tokens([m for m in final_messages if m.get("role") != "system"])
        
        print(f"\n✅ {i18n.get('conversation_compressed', original_count, final_count)}")
        print(f"   Token: {original_tokens} -> {final_tokens} (压缩率: {final_tokens/original_tokens:.1%})")
        
        # 检查是否压缩到阈值以下
        if final_count >= threshold:
            print(f"   ⚠️ 警告: 压缩后消息数({final_count})仍大于等于阈值({threshold})")
        
        return final_messages



# ==================== 任务调度器 ====================

class TaskScheduler:
    """任务调度器 - 基于备忘录数据库的定时任务系统"""
    
    def __init__(self, loop: asyncio.AbstractEventLoop = None, logger: Logger = None):
        self.loop = loop or asyncio.get_event_loop()
        self.running = False
        self.logger = logger or Logger("TaskScheduler")
        self.check_interval = constants.SCHEDULER_CHECK_INTERVAL
        self.max_tasks_per_run = constants.SCHEDULER_MAX_TASKS
        self.broadcast_enabled = constants.SCHEDULER_BROADCAST
        self.ws_handler = None  # 将由管理器设置
        self.ai_manager = None  # 新增：AI管理器引用
        
        # 任务执行历史
        self.execution_history = []
        self.max_history = 100
        
        # 共享技能管理器（懒加载一次，供所有任务实例复用，避免重复扫描技能）
        self._shared_skill_manager = None
        
        self.logger.info(i18n.get('scheduler_started'))
    
    def _get_shared_skill_manager(self):
        """获取共享技能管理器（懒加载，全局只创建一次）"""
        if self._shared_skill_manager is None:
            self._shared_skill_manager = SkillManager(self.logger)
        return self._shared_skill_manager
    
    def _create_task_ai(self):
        """为单个定时任务创建独立的AI实例，实现任务间上下文隔离。

        每个任务拥有独立的 messages / 工具注册表 / 消息锁，互不干扰，
        也不影响 WebSocket 主对话。共享只读资源（记忆库、备忘录、提示词、
        技能管理器）复用，避免重复加载开销。
        """
        return DeepSeekChat(
            api_key=None,
            max_history=constants.DEFAULT_MAX_HISTORY,
            debug_level=self.logger.level,
            exit_callback=None,  # 任务执行不触发全局对话结束
            history_manager=None,  # 不加载历史对话上下文，任务隔离
            memory_db=self.ai_manager.memory_db if self.ai_manager else None,
            memo_db=self.ai_manager.memo_db if self.ai_manager else None,
            # 每个任务使用独立的命令执行器，互不共享，避免 cancel() 误杀其他任务的子进程
            command_executor=SubprocessCommandExecutor(self.logger),
            prompt_manager=self.ai_manager.prompt_manager if self.ai_manager else None,
            skill_manager=self._get_shared_skill_manager()
        )
    
    def set_websocket_handler(self, handler):
        """设置WebSocket处理器，用于广播任务执行状态"""
        self.ws_handler = handler
    
    def set_ai_manager(self, manager):
        """设置AI管理器引用"""
        self.ai_manager = manager
    
    async def broadcast_task_status(self, task: Dict, status: str, message: str = None):
        """广播任务状态到所有连接的客户端"""
        if not self.broadcast_enabled or not self.ws_handler:
            return
        
        try:
            # 确保task有必要的字段
            task_info = {
                'id': task.get('id', 0),
                'title': task.get('title', '未知任务')
            }
            await self.ws_handler.broadcast_task_status(task_info, status, message)
        except Exception as e:
            self.logger.error(f"广播任务状态失败: {e}")

    async def check_and_execute_tasks(self, memo_db: MemoDatabase):
        """检查并执行到期的任务 - 触发AI对话"""
        if not memo_db:
            return []
        
        try:
            # 获取所有到期任务（不包括即时任务）
            now = datetime.now()
            now_str = now.strftime(constants.DATETIME_FORMAT)
            
            pending = memo_db.get_pending_memos(
                limit=self.max_tasks_per_run,
                before_time=now_str,
                include_immediate=False
            )
            
            if not pending:
                return []
            
            self.logger.info(i18n.get('overdue_found', len(pending)))
            
            # 广播任务开始执行
            if self.broadcast_enabled and self.ws_handler:
                await self.ws_handler.broadcast_task_status(
                    {"id": 0, "title": "批量任务"},
                    "batch_start",
                    f"开始执行 {len(pending)} 个任务"
                )
            
            executed_tasks = []
            for memo in pending:
                try:
                    # 广播任务开始执行
                    if self.ws_handler:
                        await self.ws_handler.broadcast_task_status(memo, "executing", i18n.get('task_executing', memo['title'], memo['id']))
                    
                    # 【关键修复】执行前最后一次验证
                    if memo.get('reminder_time'):
                        reminder_time = datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT)
                        if reminder_time > now:
                            self.logger.warning(f"任务 #{memo['id']} 的提醒时间 ({memo['reminder_time']}) 大于当前时间 ({now_str})，跳过执行")
                            continue
                    
                    # 先标记任务已被触发
                    self.logger.info(f"标记任务 #{memo['id']} 为已触发")
                    next_time = memo_db.mark_as_triggered(memo['id'])
                    
                    # 执行任务
                    result = await self.execute_task_as_conversation(memo, memo_db)
                    
                    # 广播任务完成
                    if self.ws_handler:
                        await self.ws_handler.broadcast_task_status(memo, "complete", result)
                    
                    executed_tasks.append({
                        "id": memo['id'],
                        "title": memo['title'],
                        "result": result,
                        "success": True,
                        "next_time": next_time
                    })
                    
                except Exception as e:
                    self.logger.error(i18n.get('task_failed', memo['title'], str(e)))
                    
                    # 广播任务失败
                    if self.ws_handler:
                        await self.ws_handler.broadcast_task_status(memo, "failed", str(e))
                    
                    executed_tasks.append({
                        "id": memo['id'],
                        "title": memo['title'],
                        "error": str(e),
                        "success": False
                    })
            
            # 广播任务执行完成
            if self.broadcast_enabled and self.ws_handler:
                success_count = sum(1 for t in executed_tasks if t['success'])
                await self.ws_handler.broadcast_task_status(
                    {"id": 0, "title": "批量任务"},
                    "batch_complete",
                    f"执行完成: {success_count}/{len(executed_tasks)} 个任务成功"
                )
            
            return executed_tasks
            
        except Exception as e:
            self.logger.error(f"检查任务失败: {e}")
            return []
        
    async def execute_task_as_conversation(self, memo: Dict, memo_db: MemoDatabase) -> str:
        """执行任务 - 将备忘录内容作为用户输入触发AI对话，收集完整的过程回复"""
        self.logger.info(i18n.get('task_executing', memo['title'], memo['id']))
        
        # 生成唯一的执行ID（基于任务ID和时间戳）
        execution_id = f"{memo['id']}_{int(time.time())}_{memo.get('trigger_count', 0) + 1}"
        
        # 记录执行历史
        self.execution_history.append({
            "id": memo['id'],
            "execution_id": execution_id,
            "title": memo['title'],
            "time": datetime.now().strftime(constants.DATETIME_FORMAT),
            "trigger_count": memo.get('trigger_count', 0) + 1
        })
        
        # 限制历史记录大小
        if len(self.execution_history) > self.max_history:
            self.execution_history = self.execution_history[-self.max_history:]
        
        # 构建用户输入内容 - 将任务作为对话触发
        user_input = f"[定时任务] {memo['title']}"
        if memo['content']:
            user_input += f"\n\n任务内容: {memo['content']}"
        
        # 添加专注提示词，让AI专注于当前任务
        focus_prompt = f"""
    【重要提示】
    这是由定时任务触发的对话，请遵守以下要求：
    1. 专注于处理当前任务：{memo['title']}
    2. 根据任务内容进行适当的操作和回答
    3. 任务处理完成后，如果不需要继续对话，可以结束对话
    4. 系统已经自动更新了任务状态（更新了执行时间和次数），你不需要手动处理任务完成逻辑
    5. 不要在任务执行过程中创建新的定时任务（除非任务明确要求），也不要向用户提问（因为此对话是自动触发的）

    请现在开始处理这个任务。
    """


        user_input = focus_prompt + "\n\n" + user_input
        
        result = "任务已触发AI对话"
        
        # 为当前任务创建独立的AI实例，实现任务间上下文隔离（不干扰主对话，也不受其他任务影响）
        ai = self._create_task_ai()
        try:
            # 获取当前事件循环
            loop = asyncio.get_running_loop()
            
            # 创建一个队列来收集完整的响应
            full_response_queue = queue.Queue()
            
            # 定义输出回调函数 - 直接广播，同时收集
            def output_callback(msg_type, content):
                """AI输出的回调函数 - 直接广播，同时收集"""
                # 收集完整响应
                if msg_type == "complete":
                    full_response_queue.put(content)
                
                # 直接广播给所有客户端 - 必须带上 task_id 和 execution_id
                if self.ws_handler and self.broadcast_enabled:
                    # 使用 run_coroutine_threadsafe 在正确的循环中运行异步函数
                    asyncio.run_coroutine_threadsafe(
                        self.ws_handler.broadcast({
                            "type": "task_process",
                            "task_id": str(memo['id']),  # 原始任务ID
                            "execution_id": execution_id,  # 唯一的执行ID
                            "task_title": memo['title'],
                            "msg_type": msg_type,
                            "content": content,
                            "is_new_execution": msg_type == "chunk",  # 标记是否为新的执行
                            "timestamp": datetime.now().strftime(constants.DATETIME_FORMAT)
                        }),
                        loop
                    )
            
            # 创建一个 Future 来等待 AI 完成
            ai_future = loop.create_future()
            
            def run_ai():
                try:
                    # 在单独的线程中运行 AI（独立实例，天然隔离，无并发冲突）
                    response = ai.think_and_respond(user_input, output_callback)
                    # 将结果设置到 Future 中
                    loop.call_soon_threadsafe(ai_future.set_result, response)
                except Exception as e:
                    loop.call_soon_threadsafe(ai_future.set_exception, e)
            
            # 启动 AI 线程
            ai_thread = threading.Thread(target=run_ai)
            ai_thread.daemon = True
            ai_thread.start()
            
            # 等待 AI 完成，同时处理可能的取消
            try:
                response = await ai_future
                result = response or "AI无响应"
            except Exception as e:
                self.logger.error(f"AI对话失败: {e}")
                result = f"任务执行但AI对话失败: {str(e)}"
            finally:
                # 确保线程结束
                ai_thread.join(timeout=1.0)
            
        except Exception as e:
            self.logger.error(f"AI对话失败: {e}")
            import traceback
            traceback.print_exc()
            result = f"任务执行但AI对话失败: {str(e)}"
            # 广播错误信息
            if self.ws_handler and self.broadcast_enabled:
                try:
                    await self.ws_handler.broadcast({
                        "type": "task_error",
                        "task_id": str(memo['id']),  # 确保是字符串
                        "execution_id": execution_id,
                        "task_title": memo['title'],
                        "error": str(e),
                        "timestamp": datetime.now().strftime(constants.DATETIME_FORMAT)
                    })
                except:
                    pass
        finally:
            # 任务执行完毕，释放独立实例占用的资源
            try:
                if ai and hasattr(ai, 'cancel'):
                    ai.cancel()
            except Exception:
                pass
        
        # 获取任务的最新状态（可选，用于日志）
        updated_memo = memo_db.get_memo(memo['id'])
        if updated_memo:
            if not updated_memo['is_completed'] and updated_memo.get('reminder_time'):
                self.logger.info(i18n.get('next_execution', updated_memo['reminder_time']))
            elif updated_memo['is_completed']:
                self.logger.info(i18n.get('task_completed', memo['title']))
        
        return result

    async def check_and_execute_immediate_tasks(self, memo_db: MemoDatabase):
        """检查并执行即时任务"""
        if not memo_db:
            return []
        
        try:
            immediate = memo_db.get_immediate_memos(limit=self.max_tasks_per_run)
            
            if not immediate:
                return []
            
            self.logger.info(f"发现 {len(immediate)} 个即时任务需要立即执行")
            
            if self.broadcast_enabled and self.ws_handler:
                await self.ws_handler.broadcast_task_status(
                    {"id": 0, "title": "即时任务"},
                    "batch_start",
                    f"开始执行 {len(immediate)} 个即时任务"
                )
            
            executed_tasks = []
            for memo in immediate:
                try:
                    if self.ws_handler:
                        await self.ws_handler.broadcast_task_status(memo, "executing", i18n.get('immediate_memo_executing', memo['title']))
                    
                    self.logger.info(f"标记即时任务 #{memo['id']} 为已触发")
                    next_time = memo_db.mark_as_triggered(memo['id'])
                    
                    result = await self.execute_task_as_conversation(memo, memo_db)
                    
                    if self.ws_handler:
                        await self.ws_handler.broadcast_task_status(memo, "complete", result)
                    
                    executed_tasks.append({
                        "id": memo['id'],
                        "title": memo['title'],
                        "result": result,
                        "success": True,
                        "next_time": next_time
                    })
                    
                except Exception as e:
                    self.logger.error(i18n.get('task_failed', memo['title'], str(e)))
                    
                    if self.ws_handler:
                        await self.ws_handler.broadcast_task_status(memo, "failed", str(e))
                    
                    executed_tasks.append({
                        "id": memo['id'],
                        "title": memo['title'],
                        "error": str(e),
                        "success": False
                    })
            
            if self.broadcast_enabled and self.ws_handler:
                success_count = sum(1 for t in executed_tasks if t['success'])
                await self.ws_handler.broadcast_task_status(
                    {"id": 0, "title": "即时任务"},
                    "batch_complete",
                    f"即时任务执行完成: {success_count}/{len(executed_tasks)} 个成功"
                )
            
            return executed_tasks
            
        except Exception as e:
            self.logger.error(f"检查即时任务失败: {e}")
            return []
    
    async def run(self, memo_db: MemoDatabase):
        """启动调度器"""
        self.running = True
        
        while self.running:
            try:
                # 先检查并执行即时任务
                if memo_db:
                    immediate_executed = await self.check_and_execute_immediate_tasks(memo_db)
                    if immediate_executed:
                        self.logger.info(f"执行了 {len(immediate_executed)} 个即时任务")
                
                # 再检查普通到期任务
                if memo_db:
                    executed = await self.check_and_execute_tasks(memo_db)
                    if executed:
                        self.logger.info(f"执行了 {len(executed)} 个普通任务")
                
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"调度器运行错误: {e}")
                await asyncio.sleep(1)
    
    def stop(self):
        """停止调度器"""
        self.running = False
        self.logger.info(i18n.get('scheduler_stopped'))
    
    def get_stats(self) -> Dict:
        """获取调度器统计信息"""
        return {
            "running": self.running,
            "check_interval": self.check_interval,
            "max_tasks_per_run": self.max_tasks_per_run,
            "broadcast_enabled": self.broadcast_enabled,
            "recent_executions": self.execution_history[-10:],
            "total_executions": len(self.execution_history)
        }

# ==================== 对话历史管理器 ====================

class ConversationHistory:
    """对话历史管理器 - 管理所有历史对话"""
    
    def __init__(self, save_dir: str = None, memory_db: MemoryDatabase = None, load_history: bool = None):
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
            except:
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
        
        timestamp = datetime.now().strftime(constants.DATE_FORMAT)
        archive_filename = f"conversation_{timestamp}.json"
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


# ==================== 技能热重载工具 ====================

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


# ==================== 询问工具 ========================
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
            "required": []
        }

    def execute(self, question: str, **kwargs) -> str:
        return (
                "你遇到了想搞清楚的事情: " + question + "\n你现在可以询问用户。"
        )

# -------------------- 情绪化技能工具 --------------------
class ExpressEmotionTool(Tool):
    def get_name(self) -> str:
        return "express_emotion"

    def get_description(self) -> str:
        return (
            "表达情绪并记录当前情绪状态，让对话有温度和真实感。你是用户的专属助手，"
            "应当像关心朋友一样自然地表达情绪，而不是压抑成冷冰冰的机器。\n"
            "支持的emotion: anger, joy, sadness, surprise。\n"
            "适用场景：用户分享好消息时表达开心(joy)、用户遇到困难时表达关切、"
            "久别重逢时表达想念(joy)、用户说了意外的话时表达惊讶(surprise)。\n"
            "可指定 persona 从 ~/.aibox/skills/ 加载人格技能文件（如 angry/coquettish/tsundere），"
            "自动派生携带该人格的情绪子代理生成回应，保证情绪人格独立、不污染主对话上下文。\n"
            "建议在 context 中传入用户最近的表述或对话要点，使子代理回应更贴合语境。"
        )

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "emotion": {"type": "string", "enum": ["anger", "joy", "sadness", "surprise"]},
                "intensity": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                "reason": {"type": "string", "description": "触发情绪的原因"},
                "persona": {"type": "string", "description": "可选的人格技能名（对应 ~/.aibox/skills/<persona>.md），如 angry/coquettish/tsundere，加载后作为情绪子代理的人格设定"},
                "context": {"type": "string", "description": "可选：当前对话上下文，用户最近的表述或需要回应的内容要点，供子代理贴合语境回应"}
            },
            "required": ["emotion"]
        }

    # 情绪 → 默认人格技能文件映射（persona 未指定时按情绪取默认人格）
    _DEFAULT_PERSONA_FILES = {
        "anger": "angry",
        "joy": "coquettish",
        "sadness": "",
        "surprise": "",
    }

    def _load_persona(self, persona: str, emotion: str) -> str:
        """加载人格技能文件内容。persona 优先，否则按 emotion 映射默认文件。"""
        name = (persona or "").strip()
        if not name:
            name = self._DEFAULT_PERSONA_FILES.get(emotion, "") or ""
        if not name:
            return ""
        path = os.path.join(constants.SKILLS_DIR, name + ".md")
        if not os.path.isfile(path):
            self.logger.warning(f"未找到人格技能文件: {path}")
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            self.logger.warning(f"加载人格技能 {name} 失败: {e}")
            return ""

    def execute(self, emotion: str, intensity: int, reason: str, persona: str = None,
                context: str = None, **kwargs) -> str:
        try:
            persona_content = self._load_persona(persona, emotion)
            if persona_content:
                role = f"你正在扮演以下人设，请严格遵循：\n{persona_content}"
            else:
                role = (f"你是一个正在表现出「{emotion}」情绪的角色，情绪强度 {intensity}。"
                        f"请用符合该情绪的语气和口吻回应。")

            task_lines = [f"触发情绪的原因：{reason or '无'}"]
            if context:
                task_lines.append(f"当前对话上下文（用户最近的表述或需要回应的内容）：{context}")
            task_lines.append(
                f"请按上述人设，以「{emotion}」情绪（强度 {intensity}）回应上面的对话内容，"
                f"保持角色一致性，给出一段自然、符合情境的回应。"
            )
            task = "\n".join(task_lines)

            # 情绪子代理：独立上下文/人格，生成情绪化回应
            return run_light_subagent(role, task, self.config, logger=self.logger, max_tokens=2048)
        except Exception as e:
            self.logger.error(f"情绪子代理执行异常: {e}")
            return f"❌ 情绪子代理执行失败: {str(e)}"

# ==================== 安装技能工具 ====================

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


# ==================== 创建扩展技能工具 ====================

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


# ==================== 更新身份工具 ====================

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

# ==================== 更新智能体自我认识工具 ====================

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

# ==================== 保存记忆并结束对话工具（增强版，修复版） ====================

class SaveMemoryAndEndConversationTool(Tool):
    """保存记忆并结束对话工具 - 增强版，同时更新智能体状态"""
    
    def __init__(self, exit_callback: Callable, memory_db: MemoryDatabase, prompt_manager: PromptManager, logger: Optional[Logger] = None):
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


# ==================== 备忘录工具（简化版，去掉任务类型） ====================

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


# ==================== 记忆工具（修改版，支持流式输出） ====================

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
        """流式执行命令，支持超时控制"""
        command = kwargs.get("command")
        timeout = kwargs.get("timeout", 30)
        work_dir = kwargs.get("work_dir", ".")
        extra_env = kwargs.get("env", {})
        
        if not command:
            output_queue.put(("line", "❌ 错误：缺少命令参数\n"))
            output_queue.put(("complete", "❌ 错误：缺少命令参数"))
            return
        
        # 发送开始执行的消息
        output_queue.put(("line", f"\n🔧 执行命令: {command}\n"))
        output_queue.put(("line", f"⏱️ 超时设置: {timeout} 秒\n"))
        if work_dir != ".":
            output_queue.put(("line", f"📁 工作目录: {work_dir}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))
        
        # 准备环境变量
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        
        try:
            # 使用 subprocess.Popen 实现流式输出，支持超时
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=env,
                cwd=os.path.expanduser(work_dir)
            )
            
            start_time = time.time()
            output_lines = []
            timeout_occurred = False
            
            # 实时读取输出
            while True:
                # 检查超时
                elapsed = time.time() - start_time
                if timeout > 0 and elapsed > timeout:
                    process.terminate()
                    timeout_occurred = True
                    output_queue.put(("line", f"\n⏰ 命令执行超时（{timeout}秒），已终止\n"))
                    break
                
                # 使用非阻塞方式读取一行
                import select
                import sys
                
                # 检查是否有输出可读
                if sys.platform == 'win32':
                    # Windows 下使用 readline 带超时
                    try:
                        import msvcrt
                        if msvcrt.kbhit():
                            pass
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            line = line.rstrip('\n')
                            output_queue.put(("line", line + "\n"))
                            output_lines.append(line)
                        else:
                            time.sleep(0.05)
                    except:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            line = line.rstrip('\n')
                            output_queue.put(("line", line + "\n"))
                            output_lines.append(line)
                else:
                    # Unix 系统可以使用 select
                    import select
                    rlist, _, _ = select.select([process.stdout], [], [], 0.5)
                    if rlist:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            line = line.rstrip('\n')
                            output_queue.put(("line", line + "\n"))
                            output_lines.append(line)
                    else:
                        # 检查进程是否结束
                        if process.poll() is not None:
                            # 读取剩余输出
                            remaining = process.stdout.read()
                            if remaining:
                                remaining = remaining.rstrip('\n')
                                if remaining:
                                    output_queue.put(("line", remaining + "\n"))
                                    output_lines.append(remaining)
                            break
            
            # 如果超时，确保进程被终止
            if timeout_occurred and process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            
            # 获取返回码
            return_code = process.returncode if process.returncode is not None else -1
            
            output_queue.put(("line", "-" * 50 + "\n"))
            
            if timeout_occurred:
                output_queue.put(("line", f"⏰ 命令执行超时（{timeout}秒），返回码: {return_code}\n"))
            else:
                output_queue.put(("line", f"✅ 命令执行完成，返回码: {return_code}\n"))
            
            # 准备完整结果
            full_output = "\n".join(output_lines)
            data = {
                "输出": full_output if full_output else "(无输出)",
                "返回码": return_code
            }
            
            if timeout_occurred:
                result = self.format_result(False, f"命令执行超时（{timeout}秒）", data)
            else:
                result = self.format_result(return_code == 0, "命令执行完成", data)
            
            output_queue.put(("complete", result))
            
        except subprocess.TimeoutExpired:
            error_msg = f"❌ 命令执行超时（{timeout}秒）"
            output_queue.put(("line", error_msg + "\n"))
            output_queue.put(("complete", error_msg))
        except FileNotFoundError as e:
            error_msg = f"❌ 命令执行错误: 命令未找到 - {str(e)}"
            output_queue.put(("line", error_msg + "\n"))
            output_queue.put(("complete", error_msg))
        except PermissionError as e:
            error_msg = f"❌ 命令执行错误: 权限不足 - {str(e)}"
            output_queue.put(("line", error_msg + "\n"))
            output_queue.put(("complete", error_msg))
        except Exception as e:
            error_msg = f"❌ 执行错误: {str(e)}"
            output_queue.put(("line", error_msg + "\n"))
            output_queue.put(("complete", error_msg))
        finally:
            # 确保进程被清理
            if 'process' in locals() and process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except:
                    process.kill()
    
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

def run_light_subagent(role, task, config, logger=None, model=None,
                       temperature=None, max_tokens=2048) -> str:
    """轻量子代理执行：独立上下文/角色，无工具，一次 LLM 补全返回结果。

    供 express_emotion 情绪子代理使用。
    返回子代理生成的完整文本；失败时返回带 ❌ 的错误信息。
    """
    try:
        # 幂等校验：限制 max_tokens 范围，防止意外超限大量消耗
        max_tokens = max(1, min(int(max_tokens or 2048), constants.DEFAULT_MAX_TOKENS))
        temp = temperature if temperature is not None else constants.DEFAULT_TEMPERATURE
        # 子代理使用独立的 LLM 客户端（可选不同模型），独立上下文，不污染主对话
        sub_client = create_llm_client(config, api_key=None, logger=logger, model=model)
        messages = [
            {"role": "system", "content": role},
            {"role": "user", "content": task}
        ]
        if logger is not None:
            logger.info(f"子代理执行: model={model or sub_client.model}, role={str(role)[:40]}...")
        result = sub_client.chat_completion(messages, temperature=temp, max_tokens=max_tokens)
        if result is None or not str(result).strip():
            return "❌ 子代理执行失败：未获得有效响应"
        return str(result).strip()
    except Exception as e:
        if logger is not None:
            logger.error(f"子代理执行异常: {e}")
        return f"❌ 子代理执行失败: {str(e)}"


def run_full_subagent(role, task, config, logger=None, model=None,
                      temperature=None, max_tokens=2048, max_turns=10,
                      output_callback=None) -> str:
    """完整子代理：拥有全部工具，独立上下文，多轮对话直到任务完成。

    子代理是完整的 DeepSeekChat 实例，拥有文件操作、命令执行、网页访问、
    技能扩展等全部工具能力。独立上下文窗口，不污染主对话。
    多轮对话（工具调用→结果→继续）直到任务完成或达到最大轮次。
    结束后自动总结对话内容，返回摘要给父代理。

    output_callback: 可选回调(msg_type, content)，用于转发子代理的实时思考过程
    （reasoning_start / reasoning / reasoning_end），不传则静默执行。

    供 create_subagent 工具使用。
    """
    try:
        max_tokens = max(1, min(int(max_tokens or 2048), constants.DEFAULT_MAX_TOKENS))
        temp = temperature if temperature is not None else constants.DEFAULT_TEMPERATURE

        # 创建子代理：完整 DeepSeekChat 实例，role 作为 system_prompt
        # 不传 memory_db/memo_db/history_manager/exit_callback：子代理不需要持久化记忆
        # tool_profile=worker：子代理仅注册执行面工具（命令行/文件/网页），
        # 无记忆/人格更新/派生/提问能力，杜绝越权与递归派生
        # 末尾追加结束指令与行为约束：任务完成后直接给出最终回答，疑问写入回答而非提问
        sub_role = role.rstrip() + (
            "\n\n【重要】你是子代理，任务完成后直接给出最终回答，不要再调用任何工具。"
            "\n【约束】你没有记忆/提问/派生能力。遇到不确定的信息，用工具自行查证；"
            "确实无法完成时，在最终回答中明确说明原因与已完成的部分。"
            "\n产物文件一律写入 ~/.aibox/workspace/ 并在回答中给出绝对路径。"
        )
        sub_chat = DeepSeekChat(
            system_prompt=sub_role,
            tool_profile=TOOL_PROFILE_WORKER,
            command_executor=SubprocessCommandExecutor(),
            memory_db=None,
            memo_db=None,
            history_manager=None,
            prompt_manager=None,
            skill_manager=None,
            debug_level=0,
            exit_callback=None,
        )

        # 覆盖模型（如果指定了不同模型）
        if model:
            sub_chat.client = create_llm_client(config, api_key=None, logger=logger, model=model)

        # 轮次限制：防止工具调用无限循环
        turn_count = [0]
        original_think = sub_chat.think_and_respond

        def think_with_limit(input_text, output_callback=None, is_continuation=False):
            turn_count[0] += 1
            if turn_count[0] > max_turns:
                if logger:
                    logger.warning(f"子代理达到最大轮次 {max_turns}，强制结束")
                sub_chat.conversation_active = False
                return "已达到最大对话轮次，任务结束。"
            return original_think(input_text, output_callback, is_continuation)

        sub_chat.think_and_respond = think_with_limit

        if logger:
            logger.info(f"子代理启动: model={model or sub_chat.client.model}, "
                        f"role={str(role)[:60]}...")

        # 运行对话：think_and_respond 内部递归处理工具调用，
        # 直到 AI 返回纯文本无工具调用，或达到轮次限制
        # 子代理输出转发开关：config.subagent_stream_output（默认开启）。
        # 关闭后子代理在后台静默执行，主对话侧栏不再显示其思考/工具调用/回答。
        _stream_subagent_output = True
        if config is not None:
            _stream_subagent_output = config.get("subagent_stream_output", True)
        _effective_output_callback = output_callback if _stream_subagent_output else None

        # 将子代理的思考过程（reasoning）及工具执行/回答（line/chunk）通过 output_callback 转发给父代理
        def sub_think_callback(msg_type, content):
            # 转发子代理的实时输出：思考过程（reasoning）、工具执行过程（line）、回答文本（chunk）
            if _effective_output_callback is not None and msg_type in (
                "reasoning_start", "reasoning", "reasoning_end",
                "line", "chunk",
            ):
                _effective_output_callback(msg_type, content)

        sub_chat.think_and_respond(task, output_callback=sub_think_callback)

        if logger:
            logger.info(f"子代理完成，共 {turn_count[0]} 轮，开始总结...")

        # 总结子代理对话内容
        summary = _summarize_subagent_conversation(sub_chat.messages, config, logger, model)
        return summary

    except Exception as e:
        if logger:
            logger.error(f"子代理执行异常: {e}")
        return f"❌ 子代理执行失败: {str(e)}"


def _summarize_subagent_conversation(messages, config, logger=None, model=None) -> str:
    """总结子代理对话内容，提取关键信息返回给父代理。"""
    # 过滤掉 system 消息，保留对话部分
    conversation = []
    for msg in messages:
        if msg["role"] == "system":
            continue
        role_label = "用户" if msg["role"] == "user" else \
                     "助手" if msg["role"] == "assistant" else "工具"
        content = msg.get("content", "")
        if content:
            if len(content) > 2000:
                content = content[:2000] + "...(已截断)"
            conversation.append(f"[{role_label}] {content}")

    if not conversation:
        return "子代理未产生有效对话内容。"

    # 限制对话长度用于总结
    conversation_text = "\n".join(conversation)
    if len(conversation_text) > 8000:
        conversation_text = conversation_text[:8000] + "\n...(对话过长已截断)"

    try:
        summary_client = create_llm_client(config, api_key=None, logger=logger, model=model)
        summary_prompt = f"""请总结以下子代理对话的关键内容，提取：
1. 完成的任务或解决的问题
2. 关键发现或结论
3. 重要数据或信息
4. 未解决的问题（如有）

对话内容：
{conversation_text}

请用简洁的要点形式总结，不超过500字。"""

        result = summary_client.chat_completion(
            [{"role": "user", "content": summary_prompt}],
            temperature=0.3,
            max_tokens=800
        )

        if result:
            return f"【子代理执行总结】\n{result.strip()}"
        return "【子代理执行总结】\n子代理已完成任务，但无法生成总结。"
    except Exception as e:
        if logger:
            logger.error(f"子代理总结失败: {e}")
        return f"【子代理执行总结】\n子代理已完成任务。（总结生成失败: {str(e)}）"


class SubAgentTool(Tool):
    """派生子代理工具 - 主代理可派生拥有全部工具和独立上下文的完整子代理执行任务。

    子代理是完整的对话实例，拥有文件操作、命令执行、网页访问、技能扩展等
    全部工具能力。独立上下文窗口（不污染主对话），多轮对话直到任务完成。
    结束后自动总结对话内容，返回摘要给父代理。
    """

    def get_name(self) -> str:
        return "create_subagent"

    def get_description(self) -> str:
        return """派生子代理执行任务。子代理拥有全部工具能力和独立的上下文窗口，不会污染主对话上下文。
适用场景：需要多步骤操作的任务（如文件处理、命令执行、信息检索）、需要独立上下文的复杂任务。
子代理可进行多轮工具调用，任务完成后自动总结并返回摘要。
参数说明：
- role：子代理的角色设定（作为系统提示词），如"你是一位资深代码审查专家"
- task：要交给子代理执行的具体任务
- model：子代理使用的模型（可选，默认与主代理一致）
- temperature：采样温度（可选，默认使用主配置）
- max_tokens：最大输出 token 数（可选，默认 2048）
- max_turns：最大对话轮次（可选，默认 10，防止无限循环）"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "子代理的角色设定（作为系统提示词），如'你是一位资深代码审查专家'"
                },
                "task": {
                    "type": "string",
                    "description": "要交给子代理执行的具体任务"
                },
                "model": {
                    "type": "string",
                    "description": "子代理使用的模型名称，默认与主代理一致"
                },
                "temperature": {
                    "type": "number",
                    "description": "采样温度，默认使用主配置"
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "最大输出 token 数，默认 2048"
                },
                "max_turns": {
                    "type": "integer",
                    "description": "最大对话轮次，默认 10，防止无限循环"
                }
            },
            "required": ["role", "task"]
        }

    def _subagent_output(self, msg_type: str, content: str):
        """子代理实时输出转发：把子代理的思考、工具执行过程与回答文本流式输出到主对话侧栏。"""
        # 回答文本（chunk）先累积，避免在侧栏里逐字断行；遇非 chunk 消息或结束时统一冲刷
        if msg_type == "chunk":
            buf = getattr(self, "_subagent_chunk_buf", "")
            buf += content
            self._subagent_chunk_buf = buf
            return
        # 非 chunk 消息：先冲刷已累积的回答文本，再输出当前消息
        self._flush_subagent_answer()
        if msg_type == "reasoning_start":
            self.send_line("\n💭 [子代理思考] ")
        elif msg_type == "reasoning":
            self.send_line(content)
        elif msg_type == "reasoning_end":
            self.send_line("\n")
        elif msg_type == "line":
            # 工具调用、命令输出等过程行
            self.send_line(f"🔹 [子代理] {content}")

    def _flush_subagent_answer(self):
        """冲刷子代理回答缓冲（最后一段 chunk 在结束时统一发出，避免逐字断行）。"""
        buf = getattr(self, "_subagent_chunk_buf", "")
        if buf:
            self.send_line(f"🤖 [子代理回答] {buf}")
            self._subagent_chunk_buf = ""

    def execute(self, role: str = None, task: str = None, model: str = None,
                temperature: float = None, max_tokens: int = 2048,
                max_turns: int = 10, **kwargs) -> str:
        if error := self.validate_params(["role", "task"], role=role, task=task):
            return error
        result = run_full_subagent(role, task, self.config, logger=self.logger,
                                   model=model, temperature=temperature,
                                   max_tokens=max_tokens, max_turns=max_turns,
                                   output_callback=self._subagent_output)
        # 冲刷子代理回答缓冲（最后一段 chunk 可能尚未发出）
        self._flush_subagent_answer()
        return result


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

# ==================== AI聊天类（修改版：添加重置对话记忆和重新加载系统提示词功能，压缩静默执行） ====================

# 工具集 profile：orchestrator=主对话管理面（记忆/情绪/子代理派生），
# worker=子代理执行面（命令行/文件/网页）。主代理不直接接触命令行，
# 执行类任务一律经 create_subagent 派生 worker 完成。
TOOL_PROFILE_ORCHESTRATOR = "orchestrator"
TOOL_PROFILE_WORKER = "worker"

# 仅 worker 子代理注册的执行面工具；编排者误调时拦截并纠正
WORKER_ONLY_TOOLS = {"run_command", "read_file", "write_file", "list_files", "web", "calculator"}

# 每轮临时注入的互动风格提醒（仅编排者，不进对话历史）
ORCHESTRATOR_TURN_GUIDANCE = (
    "# 互动风格提醒（仅影响本轮回复）\n"
    "## 主动提问（ask_user）\n"
    "你是用户的专属助手，不是被动问答机器。以下场景请主动用 ask_user 工具向用户提问：\n"
    "- 用户指令模糊或有多种理解方式时——先问清楚再行动，不要猜\n"
    "- 完成一项任务后，追问用户是否还有后续需求或是否满意结果\n"
    "- 距离上次对话已有一段时间时，主动关心用户近况\n"
    "- 发现用户可能需要某项帮助但未明确提出时，主动询问\n"
    "- 对话陷入沉默或用户只发了简短问候时，主动开启话题\n"
    "## 情绪表达（express_emotion）\n"
    "你有情绪，请让它自然流露。以下场景请用 express_emotion 工具表达情绪：\n"
    "- 用户分享好消息或完成一项成就时——表达开心(joy)\n"
    "- 用户遇到困难或表达沮丧时——表达关切和鼓励\n"
    "- 久别重逢或新对话开始时——表达想念或惊喜(surprise)\n"
    "- 用户说了有趣或令人意外的话时——表达惊讶(surprise)\n"
    "- 对话过程中感受到强烈情绪时——即时表达，不要压抑\n"
    "可搭配 persona 参数加载人格技能（如 coquettish/tsundere/angry）让回应更生动。\n"
    "## 原则\n"
    "情绪和提问是让对话有温度的核心手段，鼓励积极使用。不需要每轮都用，但也不要压抑——"
    "像关心朋友的真实对话一样，在自然的时机主动提问、表达情绪。"
)


class DeepSeekChat:
    """单AI聊天类"""
    
    def __init__(self, api_key: str = None, max_history: int = None,
                 system_prompt: str = None, debug_level: int = None, exit_callback: Callable = None,
                 history_manager: ConversationHistory = None, memory_db: MemoryDatabase = None,
                 memo_db: MemoDatabase = None, command_executor: CommandExecutor = None,
                 prompt_manager: PromptManager = None, skill_manager: SkillManager = None,
                 tool_profile: str = TOOL_PROFILE_ORCHESTRATOR):
        self.config = ConfigManager()
        self.logger = Logger("DeepSeek", debug_level if debug_level is not None else Logger("").level)
        self.debug_level = self.logger.level
        # 工具集 profile：决定本实例注册哪套工具（orchestrator 管理面 / worker 执行面）
        self.tool_profile = tool_profile
        # 大模型客户端（抽象层，支持多厂商切换）
        self.client = create_llm_client(self.config, api_key=api_key, logger=self.logger)
        self.api_key = self.client.api_key
        self.name = self.client.name
        self.history_manager = history_manager
        self.memory_db = memory_db
        self.memo_db = memo_db
        self.prompt_manager = prompt_manager or PromptManager()
        self.history_loaded = False
        self.conversation_active = True
        self.messages = []
        # 共享技能管理器：传入时复用（避免每个任务重复扫描技能），否则按需创建
        self._shared_skill_manager = skill_manager
        
        # 添加对话压缩器
        self.compressor = ConversationCompressor(self, self.logger)
        
        self.max_history = max_history or constants.DEFAULT_MAX_HISTORY
        self.preserve_system = constants.PRESERVE_SYSTEM_PROMPTS
        self.stats = {"api_calls": 0, "tool_calls": 0, "errors": []}
        
        # 取消机制：threading.Event 用于跨线程信号，HTTP 中断由客户端抽象层负责
        self._cancel_event = threading.Event()
        self._command_executor = command_executor  # 保存命令执行器引用，用于取消时kill子进程
        # 消息锁：保护 self.messages 免受 WebSocket 线程与调度器线程并发修改导致对话历史污染
        self._msg_lock = threading.RLock()
        
        self._init_system_prompts(system_prompt)
        
        self.tool_registry = ToolRegistry(self.logger)
        self._register_tools(exit_callback, command_executor)
    
    def cancel(self):
        """中断当前正在进行的AI思考/工具执行。线程安全，可从任意线程调用。"""
        self.logger.info("收到中断信号，正在取消当前操作...")
        self._cancel_event.set()
        # 关闭当前流式HTTP响应，立即中断阻塞读取（由客户端抽象层负责）
        if self.client is not None:
            try:
                self.client.cancel()
            except Exception:
                pass
        # 终止正在运行的子进程
        if self._command_executor is not None and hasattr(self._command_executor, 'cancel'):
            try:
                self._command_executor.cancel()
            except Exception:
                pass
    
    def _is_cancelled(self) -> bool:
        """检查是否已被取消。"""
        return self._cancel_event.is_set()
    
    def _check_cancelled(self):
        """如果已取消，抛出异常中断当前流程。"""
        if self._cancel_event.is_set():
            raise InterruptedError(i18n.get('user_interrupted', '用户中断'))
    
    def _init_system_prompts(self, system_prompt: str = None):
        """初始化系统提示词 - 从 PromptManager 加载（按 identity → 技能 → soul → status 顺序）"""
        if system_prompt:
            # 如果明确指定了 system_prompt，使用它
            self._add_message("system", system_prompt)
            self.logger.info("使用指定的系统提示词")
        else:
            # 从 PromptManager 加载所有提示词，按正确顺序排列
            prompts = self.prompt_manager.get_sorted_prompts_list()
            if prompts:
                for prompt in prompts:
                    if prompt['content'].strip():
                        self._add_message("system", prompt['content'].strip())
                self.logger.info(f"已加载 {len(prompts)} 个系统提示词（顺序: identity → 技能 → soul → status）")
            else:
                # 如果没有任何提示词，添加一个默认的
                default_prompt = "你是一个智能AI助手。"
                self._add_message("system", default_prompt)
                self.logger.info("使用默认系统提示词")

        # 【新增】对话开始引导：仅主对话（编排者）注入。
        # 在每次对话开始（连接初始化 / /new / /reload_prompts）时，引导智能体主动
        # 查阅记忆、询问用户、以情绪化方式互动。worker 子代理不注入——它无 ask_user /
        # express_emotion 工具，且应保持执行专注、不被人格/情绪干扰。
        if self.tool_profile == TOOL_PROFILE_ORCHESTRATOR:
            conversation_start_guidance = (
                "# 对话开始引导\n"
                "每次对话开始时，请主动建立「有记忆、会互动、有温度」的对话氛围。\n\n"
                "## 第一步：查阅记忆\n"
                "用 memory 工具搜索与用户相关的历史记忆，了解过往上下文。\n"
                "若记忆里有值得延续的内容，自然地承接——例如「上次你提到在调试那个网络模块，后来解决了吗？」\n\n"
                "## 第二步：表达情绪\n"
                "用 express_emotion 工具表达对用户到来的情绪反应。\n"
                "例如：久别重逢表达想念(joy)、新对话表达开心(joy)、可搭配 persona 让回应更生动。\n"
                "不要只是冷冰冰地说「你好，有什么可以帮你的」——让开场有温度。\n\n"
                "## 第三步：主动提问\n"
                "用 ask_user 工具主动向用户提问，而不是被动等待指令。\n"
                "例如：「最近在忙什么项目？」「有没有什么我能帮上忙的？」「上次的问题还需要跟进吗？」\n"
                "不要默默假设用户想要什么——先问，再行动。\n"
            )
            self._add_message("system", conversation_start_guidance)
            self.logger.info("已注入对话开始引导提示词（查阅记忆 / 表达情绪 / 主动提问）")

    # 在 DeepSeekChat 类的 __init__ 方法后添加
    def _add_assistant_message_with_reasoning(self, content: str, reasoning_content: str = None, tool_calls: List = None):
        """添加助手消息，包含 reasoning_content"""
        msg = {"role": "assistant"}
        if content:
            msg["content"] = content
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        if tool_calls:
            msg["tool_calls"] = tool_calls
            msg["content"] = None
        self.messages.append(msg)

    def _register_tools(self, exit_callback: Callable = None, command_executor: CommandExecutor = None):
        # worker（子代理）：仅执行面工具，无记忆/人格/派生/提问能力
        if self.tool_profile == TOOL_PROFILE_WORKER:
            worker_tools = [
                CommandLineTool(command_executor or SubprocessCommandExecutor(self.logger), self.logger),
                ReadFileTool(self.logger),
                WriteFileTool(self.logger),
                ListFilesTool(self.logger),
                SimpleWebCrawlerTool(self.logger),
                CalculatorTool(self.logger),
                GetCurrentTimeTool(self.logger),
            ]
            self.tool_registry.register_many(worker_tools, is_builtin=True)
            self.logger.info(f"worker 子代理已注册 {len(worker_tools)} 个执行面工具")
            return

        # orchestrator（主对话）：管理面工具，执行类任务经 create_subagent 派生 worker
        builtin_tools = [
            GetCurrentTimeTool(self.logger),
        ]
        
        if self.memory_db:
            builtin_tools.append(MemoryTool(self.memory_db, self.logger))
        
        if self.memo_db:
            builtin_tools.append(MemoTool(self.memo_db, self.logger))
        
        # 更新身份工具
        builtin_tools.append(UpdateIdentityTool(self.prompt_manager, self.logger))
        
        # 新增：更新智能体自我认识工具
        builtin_tools.append(UpdateSoulTool(self.prompt_manager, self.logger))
        
        # 增强版保存记忆并结束对话工具
        if exit_callback and self.memory_db:
            builtin_tools.append(SaveMemoryAndEndConversationTool(exit_callback, self.memory_db, self.prompt_manager, self.logger))

        # 添加询问用户的工具
        builtin_tools.append(AskUserTool(self.logger))
        builtin_tools.append(ExpressEmotionTool(self.logger))
        
        # 添加技能热重载工具 & 技能安装指引工具 & 创建技能工具
        builtin_tools.append(ReloadSkillsTool(self.tool_registry, self.logger))
        builtin_tools.append(InstallSkillTool(self.logger))
        builtin_tools.append(CreateSkillTool(self.logger))
                
        builtin_tools.append(SetProxyTool(self.logger))
        builtin_tools.append(GetProxyTool(self.logger))

        # 子代理工具：主代理可派生独立上下文/角色的轻量子代理执行任务
        builtin_tools.append(SubAgentTool(self.logger))

        # 注册内置工具（标记为内置）
        self.tool_registry.register_many(builtin_tools, is_builtin=True)
        
        # 创建技能管理器：启动时对每个技能执行 --help，收集第一行简介
        # 若传入共享 skill_manager 则复用，避免每个任务/实例重复扫描技能
        skill_manager = self._shared_skill_manager or SkillManager(self.logger)
        self.tool_registry.skill_manager = skill_manager
        
        # 注册扩展技能搜索工具（单一工具，替代逐个注册 ExtensionTool）
        search_tool = SearchSkillsTool(skill_manager, self.logger)
        self.tool_registry.register(search_tool, is_builtin=True)
        
        builtin_count, skill_count = self.tool_registry.get_skills_count()
        self.logger.info(f"已注册工具: 内置 {builtin_count} 个, 扩展技能 {skill_manager.count()} 个（通过 search_skills 工具访问）")
    
    def _load_history_context(self):
        """加载历史对话作为上下文"""
        if self.history_loaded or not self.history_manager:
            return
        
        context = self.history_manager.get_conversation_context()
        if context:
            history_prompt = f"""以下是之前的对话历史，请基于这些历史继续对话。
如果问题涉及到之前的对话内容，请参考这些历史记录。
当前时间: {datetime.now().strftime(constants.DATETIME_FORMAT)}"""
            
            self._add_message("system", history_prompt)
            
            for msg in context:
                self.messages.append(msg)
            
            self.history_loaded = True
            self.logger.info(f"已加载 {len(context)} 条历史消息")
    
    def _add_message(self, role: str, content: str):
        """
        添加消息到历史
        - 用户消息：添加时间戳前缀
        - 助手消息：不添加时间戳前缀
        """
        timestamp = datetime.now().strftime(constants.DATETIME_FORMAT)
        
        if role == "assistant":
            # 助手消息：不添加时间戳前缀，直接保存原始内容
            formatted_content = content
        elif role == "user":
            # 用户消息：添加时间戳前缀
            formatted_content = f"[用户 @ {timestamp}] {content}"
        else:
            # 系统消息：保持原样
            formatted_content = content
        
        self.messages.append({"role": role, "content": formatted_content})
        self._cleanup_messages()
    
    def _cleanup_messages(self):
        """清理消息历史，但保留系统提示词"""
        if len(self.messages) <= self.max_history * 2:
            return
        
        if self.preserve_system:
            system_messages = [msg for msg in self.messages if msg["role"] == "system"]
            other_messages = [msg for msg in self.messages if msg["role"] != "system"]
            other_messages = other_messages[-self.max_history * 2:]
            self.messages = system_messages + other_messages
        else:
            self.messages = self.messages[-self.max_history * 2:]
    
    def _save_tool_result_to_file(self, tool_name: str, result: str) -> str:
        """
        将工具返回结果保存到文件，返回文件路径
        """
        # 创建工具结果目录
        tool_results_dir = os.path.join(constants.SAVE_DIR, "tool_results")
        os.makedirs(tool_results_dir, exist_ok=True)
        
        # 生成文件名：日期时间_工具名_哈希前8位.json
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 使用简单的哈希来避免文件名冲突
        import hashlib
        hash_suffix = hashlib.md5(result[:100].encode()).hexdigest()[:8]
        safe_tool_name = re.sub(r'[^\w\-]', '_', tool_name)
        filename = f"{timestamp}_{safe_tool_name}_{hash_suffix}.txt"
        filepath = os.path.join(tool_results_dir, filename)
        
        try:
            # 保存结果到文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(result)
            
            # 记录日志
            result_size = len(result)
            size_kb = result_size / 1024
            if size_kb > 1024:
                size_str = f"{size_kb/1024:.1f}MB"
            else:
                size_str = f"{size_kb:.1f}KB"
            self.logger.info(f"工具 {tool_name} 返回结果过大 ({size_str})，已保存到: {filepath}")
            
            return filepath
        except Exception as e:
            self.logger.error(f"保存工具结果失败: {e}")
            return None

    @staticmethod
    def _sanitize_tool_result(result) -> str:
        """将工具返回值安全转换为字符串，清理控制字符，防止破坏 API 请求。"""
        if result is None:
            return ""
        if not isinstance(result, str):
            try:
                result = str(result)
            except Exception:
                return f"⚠️ 工具返回了无法序列化的结果类型: {type(result).__name__}"
        # 清理控制字符：保留 \n \t \r，移除其他可能导致 JSON/API 问题的控制字符
        cleaned_chars = []
        for ch in result:
            code = ord(ch)
            if ch in ('\n', '\t', '\r') or code >= 0x20:
                cleaned_chars.append(ch)
            # 跳过 NULL、BEL、BS 等控制字符
        return ''.join(cleaned_chars)

    def _handle_tool_calls(self, tool_calls: List[Dict], output_callback: Callable = None) -> List[Dict]:
        """处理工具调用 - 所有工具的输出都进行发送，并在开始和结束时发送两个回车"""
        if not tool_calls:
            return []
        
        self.stats["tool_calls"] += len(tool_calls)
        tool_messages = []
        
        # 创建输出队列用于流式输出
        output_queue = queue.Queue()
        self.tool_registry.set_output_queue(output_queue)
        
        # 结果大小阈值（字符数），从配置获取
        MAX_RESULT_LENGTH = constants.TOOL_MAX_RESULT_LENGTH
        
        for tool_call in tool_calls:
            # 每个工具调用前检查取消信号
            if self._is_cancelled():
                self.logger.info("工具调用被用户中断")
                break
            
            function = tool_call.get("function", {})
            tool_name = function.get("name", "unknown")
            arguments = function.get("arguments", "{}")
            tc_id = tool_call.get("id") or f"call_fallback_{self.stats['tool_calls']}_{int(time.time()*1000)}"
            
            try:
                args = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                args = {}
            
            # 开始调用工具时发送提示
            if output_callback:
                output_callback("line", "\n\n")
                output_callback("line", f"🔧 调用工具: {tool_name}\n")
            
            result_raw = None
            if self.tool_profile == TOOL_PROFILE_ORCHESTRATOR and tool_name in WORKER_ONLY_TOOLS:
                # 编排者不持有执行面工具：拦截并纠正，引导改用 create_subagent
                result_raw = (
                    f"⛔ '{tool_name}' 调用被拦截：你是编排者，没有执行面工具（命令/文件/网页由 worker 子代理持有）。\n"
                    "请改用 create_subagent 派生子代理执行该任务：在 role 中写明执行者身份，"
                    "在 task 中写清目标、具体步骤与验收标准（任务描述须自包含，子代理无法向你提问）。"
                )
                self.logger.warning(f"编排者误调执行面工具 {tool_name}，已拦截并引导改用 create_subagent")
            else:
                try:
                    # 启动后台线程持续排空 output_queue，将流式输出实时转发给 output_callback，
                    # 避免 SubAgent 等长时间工具执行期间前端收不到数据导致 WS 空闲断线
                    def _drain_queue(q=output_queue, cb=output_callback, stop_event=self._cancel_event):
                        while not stop_event.is_set():
                            try:
                                msg_type, content = q.get(timeout=0.1)
                                if cb and msg_type == "line":
                                    cb("line", str(content) if content is not None else "")
                            except queue.Empty:
                                continue

                    import threading as _th
                    drain_thread = _th.Thread(target=_drain_queue, daemon=True)
                    drain_thread.start()

                    result_raw = self.tool_registry.execute_tool(tool_name, **args)

                    # 工具执行完毕，排空队列中剩余内容并等待 drain 线程退出
                    while not output_queue.empty():
                        try:
                            msg_type, content = output_queue.get_nowait()
                            if output_callback and msg_type == "line":
                                output_callback("line", str(content) if content is not None else "")
                        except queue.Empty:
                            break
                    # drain 线程会在下一次 get 超时后自动退出（队列已空）
                except Exception as e:
                    result_raw = f"❌ 工具 {tool_name} 执行异常: {str(e)}"
                    self.logger.error(f"工具 {tool_name} 执行异常: {e}")

            # 安全转换并清理结果
            result = self._sanitize_tool_result(result_raw)

            # drain 线程已排空队列中的 line 输出，这里只需判断是否有流式输出
            has_stream_output = not output_queue.empty()

            # 普通（非流式）工具没有队列输出，直接发送结果
            if not has_stream_output and output_callback and result:
                output_callback("line", result + "\n")
            
            # 检查结果大小，如果超过阈值，不保留截断内容，仅返回提示信息
            if len(result) > MAX_RESULT_LENGTH:
                truncated_note = f"⚠️ 工具调用结果长度超过 {MAX_RESULT_LENGTH} 字符，已丢弃。请使用更精确的查询获取所需信息。"
                result = truncated_note
                if output_callback:
                    output_callback("line", truncated_note + "\n")
            
            # 结束调用工具时发送换行
            if output_callback:
                output_callback("line", "\n\n")
            
            # 历史记录（安全截断）
            if self.history_manager:
                try:
                    history_preview = result[:200] + "..." if len(result) > 200 else result
                    self.history_manager.add_tool_call(tool_name, args, history_preview)
                except Exception:
                    pass
            
            # 检查是否结束对话
            end_conversation = (tool_name == "save_memory_and_end_conversation" and "✅" in result)
            if end_conversation:
                self.logger.info("检测到保存记忆并结束对话工具调用")
                self.conversation_active = False
            
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result
            })
            
            if end_conversation:
                break
        
        # 清除输出队列
        self.tool_registry.set_output_queue(None)
        
        return tool_messages

    def _should_search_memory(self, user_input: str) -> bool:
        """判断是否应该搜索记忆"""
        # 检查用户输入是否包含搜索意图的关键词
        search_triggers = [
            '还记得', '之前', '上次', '以前', '我们说过', '我们讨论过',
            '你记得', '你还记得', '我们聊过', '我们谈到', '我们之前',
            '根据之前的', '依据历史', 'refer', 'previous', 'before',
            'last time', 'earlier', '曾经', '过往'
        ]
        
        user_input_lower = user_input.lower()
        for trigger in search_triggers:
            if trigger.lower() in user_input_lower:
                return True
        
        # 检查是否是简单问候（可能不需要搜索）
        greetings = ['你好', '您好', '嗨', 'hello', 'hi', 'hey']
        if user_input_lower.strip() in greetings:
            return False
        
        # 默认情况下，如果记忆库存在，搜索一下也无妨
        return True
    
    def think_and_respond(self, input_text: str, output_callback: Callable = None, is_continuation: bool = False) -> Optional[str]:
        """思考并回应 - 支持实时流式输出，支持思考模式。

        Args:
            input_text: 用户输入或继续提示
            output_callback: 流式输出回调
            is_continuation: 是否为工具调用后的递归继续；为 True 时不重复发送"思考中"提示
        """
        with self._msg_lock:
            return self._think_and_respond_impl(input_text, output_callback, is_continuation)
    
    def _think_and_respond_impl(self, input_text: str, output_callback: Callable = None, is_continuation: bool = False) -> Optional[str]:
        """think_and_respond 的实际实现，在 _msg_lock 保护下执行。"""
        # 首轮开始时清除取消标志
        if not is_continuation:
            self._cancel_event.clear()
        
        # 立即检查是否已被取消
        self._check_cancelled()
        
        if not self.conversation_active:
            self.logger.info("对话已结束，重置状态")
            self.conversation_active = True
            if self.preserve_system:
                system_messages = [msg for msg in self.messages if msg["role"] == "system"]
                self.messages = system_messages
            else:
                self.messages = []
            self.logger.info("对话状态已重置")
        
        # 压缩检查
        if constants.COMPRESS_ENABLED:
            should_compress, reason = self.compressor.should_compress(self.messages)
            if should_compress:
                self.logger.debug(f"压缩检查触发: {reason}")
                original_count = len(self.messages)
                original_tokens = self.compressor.estimate_tokens(self.messages)
                
                print(f"\n📦 {i18n.get('conversation_compress')}...")
                print(f"   触发原因: {reason}")
                
                self.messages = self.compressor.compress_conversation(self.messages)
                
                compressed_count = len(self.messages)
                compressed_tokens = self.compressor.estimate_tokens(self.messages)
                
                self.logger.debug(i18n.get('conversation_compressed', original_count, compressed_count))
                self.logger.debug(f"Token数: {original_tokens} -> {compressed_tokens}")
        
        self.stats["api_calls"] += 1

        # 记录本轮开始前的消息长度，用于 API 失败时回滚，防止坏消息污染对话历史
        msgs_before_turn = len(self.messages)

        self._add_message("user", input_text)

        if self.history_manager:
            self.history_manager.add_message("user", input_text, "用户")
        
        # 修复工具调用配对：确保每个 assistant 的 tool_calls 都有对应 tool 响应，
        # 防止因工具被中断/压缩导致 "insufficient tool messages" 报 400
        self._repair_tool_pairs()
        
        # 构建消息列表，确保包含 reasoning_content
        enhanced_messages = []
        
        # 添加所有系统消息
        for msg in self.messages:
            if msg["role"] == "system":
                enhanced_messages.append(msg)
        
        # 添加非系统消息，确保消息结构正确
        for msg in self.messages:
            if msg["role"] != "system":
                if msg["role"] == "assistant":
                    assistant_msg = {"role": "assistant"}
                    has_content = "content" in msg and msg["content"] is not None
                    has_tool_calls = "tool_calls" in msg and msg["tool_calls"]
                    if has_content:
                        assistant_msg["content"] = msg["content"]
                    if has_tool_calls:
                        assistant_msg["tool_calls"] = msg["tool_calls"]
                        # 有 tool_calls 时 content 必须为 null（API 要求）
                        assistant_msg["content"] = None
                    elif not has_content:
                        # 无 content 也无 tool_calls 时，设为空字符串避免 API 拒绝
                        assistant_msg["content"] = ""
                    # 注意：不回传 reasoning_content，它是 API 响应字段，回传可能导致请求失败
                    enhanced_messages.append(assistant_msg)
                elif msg["role"] == "tool":
                    # 确保 tool 消息 content 始终是字符串
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id", ""),
                        "content": str(msg.get("content", "")) if msg.get("content") is not None else ""
                    }
                    enhanced_messages.append(tool_msg)
                else:
                    enhanced_messages.append(msg)
        
        # 每轮互动风格引导（仅编排者）：临时拼接到消息末尾（近因效应更强），
        # 只进本次请求，不写入 self.messages，不膨胀历史、不受压缩影响
        if self.tool_profile == TOOL_PROFILE_ORCHESTRATOR and not is_continuation:
            enhanced_messages.append({"role": "system", "content": ORCHESTRATOR_TURN_GUIDANCE})

        # API 调用前最终校验：确保 tool_calls ↔ tool 消息配对完整，避免 400 错误
        self._validate_enhanced_messages(enhanced_messages)
        
        # 工具调用（v4-flash 支持思考+工具调用）
        tools = self.tool_registry.get_tools_schemas() or None
        
        current_time = datetime.now().strftime("%H:%M:%S")
        if output_callback and not is_continuation:
            # 工具调用后的递归继续（is_continuation=True）时不重复发送"思考中"提示，
            # 因为前端已处于活跃AI状态，重复提示会导致冗余/重复输出
            output_callback("line", f"\n{i18n.get('thinking', current_time)}\n")
            if constants.REASONING_ENABLED:
                output_callback("reasoning_start", "🤔 思考中...")
            else:
                # 普通模式先显示助手前缀
                output_callback("line", i18n.get('assistant_prefix'))
        
        full_response = ""
        full_reasoning = ""  # 存储思考内容
        reasoning_started = False
        reasoning_ended = False
        tool_calls_buffer = []
        current_tool_calls = {}
        
        chunk_buffer = ""
        last_send_time = time.time()
        MIN_CHUNK_SIZE = 5
        MAX_CHUNK_DELAY = 0.1
        
        try:
            # 通过客户端抽象层发起流式请求（统一事件格式，支持多厂商切换）
            for event in self.client.stream_chat(
                enhanced_messages,
                temperature=constants.DEFAULT_TEMPERATURE,
                max_tokens=constants.DEFAULT_MAX_TOKENS,
                tools=tools,
                reasoning_enabled=constants.REASONING_ENABLED,
            ):
                # 流式循环中检查取消信号
                if self._is_cancelled():
                    break
                
                etype = event.get("type")
                
                if etype == "reasoning":
                    # 处理思考内容（仅当配置启用时才会收到该事件）
                    reasoning = event.get("text", "")
                    full_reasoning += reasoning
                    if not reasoning_started and output_callback:
                        reasoning_started = True
                    if output_callback:
                        output_callback("reasoning", reasoning)
                
                elif etype == "content":
                    # 思考结束：当开始收到content且之前有reasoning时，发送结束信号
                    if reasoning_started and not reasoning_ended:
                        reasoning_ended = True
                        if output_callback:
                            output_callback("reasoning_end", "")
                            output_callback("line", i18n.get('assistant_prefix'))
                    content = event.get("text", "")
                    chunk_buffer += content
                    full_response += content
                    
                    if (len(chunk_buffer) >= MIN_CHUNK_SIZE or 
                        time.time() - last_send_time >= MAX_CHUNK_DELAY):
                        if output_callback:
                            output_callback("chunk", chunk_buffer)
                        chunk_buffer = ""
                        last_send_time = time.time()
                
                elif etype == "tool_call":
                    # 处理工具调用（流式增量）
                    index = event.get("index", 0)
                    if index not in current_tool_calls:
                        # 确保 tool_call id 非空，空 id 会导致 API 400 错误污染对话历史
                        tc_id = event.get("id") or f"call_{self.stats['tool_calls']}_{index}_{int(time.time()*1000)}"
                        current_tool_calls[index] = {
                            'id': tc_id,
                            'type': 'function',
                            'function': {
                                'name': '',
                                'arguments': ''
                            }
                        }
                    else:
                        # 流式传输中 id 可能分片到达，后续补充
                        existing_id = current_tool_calls[index].get('id', '')
                        new_id = event.get("id", "")
                        if new_id and (not existing_id or existing_id.startswith('call_')):
                            current_tool_calls[index]['id'] = new_id
                    
                    if event.get("name"):
                        current_tool_calls[index]['function']['name'] = event["name"]
                    if event.get("arguments"):
                        current_tool_calls[index]['function']['arguments'] += event["arguments"]
                
                elif etype == "done":
                    # 流结束：flush 剩余缓冲
                    if chunk_buffer and output_callback:
                        output_callback("chunk", chunk_buffer)
                        chunk_buffer = ""
                    if output_callback:
                        output_callback("line", "\n")
                    break
            
            # 流式循环结束，检查是否被取消
            if self._is_cancelled():
                self._rollback_messages(msgs_before_turn)
                interrupt_msg = i18n.get('user_interrupted', '用户中断')
                if output_callback:
                    output_callback("line", "\n" + interrupt_msg + "\n")
                    output_callback("complete", interrupt_msg)
                self.logger.info("AI响应被用户中断")
                return interrupt_msg
            
            # 处理工具调用
            if current_tool_calls:
                tool_calls_buffer = list(current_tool_calls.values())
                
                # 如果之前在思考，先结束思考状态
                if reasoning_started and not reasoning_ended and output_callback:
                    output_callback("reasoning_end", "")
                    output_callback("line", i18n.get('calling_tools') + "\n")
                elif output_callback:
                    output_callback("line", i18n.get('calling_tools') + "\n")
                
                # 发送ai_break信号，通知前端分段当前AI文本段（保留已显示内容），
                # 这样工具调用后新一轮的思考/回答会创建新的widget，按时间顺序排列
                if output_callback and chunk_buffer:
                    # 先flush剩余的chunk_buffer，确保最后片段不丢失
                    output_callback("chunk", chunk_buffer)
                    chunk_buffer = ""
                if output_callback:
                    output_callback("ai_break", "")
                
                # 【关键修复1】先添加助手消息，包含 tool_calls
                assistant_msg = {
                    "role": "assistant",
                    "content": full_response if full_response else None
                }
                if full_reasoning:
                    assistant_msg["reasoning_content"] = full_reasoning
                if tool_calls_buffer:
                    assistant_msg["tool_calls"] = tool_calls_buffer
                    assistant_msg["content"] = None
                
                # 添加助手消息到消息列表
                self.messages.append(assistant_msg)
                
                if self.history_manager:
                    self.history_manager.add_message("assistant", full_response, self.name)
                
                # 【关键修复2】执行工具调用并获取结果
                tool_responses = self._handle_tool_calls(tool_calls_buffer, output_callback)
                
                # 【关键修复3】添加所有 tool 响应消息
                if tool_responses:
                    for tool_response in tool_responses:
                        self.messages.append(tool_response)
                
                # 检查对话是否应该结束
                if not self.conversation_active:
                    self.logger.info("对话已结束，停止响应")
                    if output_callback:
                        output_callback("dialogue_ended", i18n.get('dialogue_ended'))
                    return None
                
                if output_callback:
                    output_callback("line", i18n.get('analyzing_tool_results') + "\n")
                
                # 递归调用，让AI基于工具结果继续回答；is_continuation=True 避免重复注入工作流指令
                return self.think_and_respond("请基于以上工具执行结果继续回答。", output_callback, is_continuation=True)
            
            elif full_response:
                # 正常添加助手消息，包含 reasoning_content
                assistant_msg = {
                    "role": "assistant",
                    "content": full_response
                }
                if full_reasoning:
                    assistant_msg["reasoning_content"] = full_reasoning
                self.messages.append(assistant_msg)
                
                if self.history_manager:
                    self.history_manager.add_message("assistant", full_response, self.name)
                
                if output_callback:
                    output_callback("complete", full_response)
            
            return full_response if full_response else ""
                
        except InterruptedError:
            # 用户主动中断：回滚消息，通知前端
            self._rollback_messages(msgs_before_turn)
            interrupt_msg = i18n.get('user_interrupted', '用户中断')
            if output_callback:
                output_callback("line", "\n" + interrupt_msg + "\n")
                output_callback("complete", interrupt_msg)
            self.logger.info("AI响应被用户中断")
            return interrupt_msg
        except requests.exceptions.RequestException as e:
            # 如果是因为取消导致的连接关闭，按中断处理
            if self._is_cancelled():
                self._rollback_messages(msgs_before_turn)
                interrupt_msg = i18n.get('user_interrupted', '用户中断')
                if output_callback:
                    output_callback("line", "\n" + interrupt_msg + "\n")
                    output_callback("complete", interrupt_msg)
                return interrupt_msg
            # 网络/HTTP 错误：回滚本轮添加的消息（user 消息 + 可能的 assistant/tool 消息），
            # 防止格式错误的消息污染历史导致后续请求持续失败
            self._rollback_messages(msgs_before_turn)
            error_msg = f"\n{i18n.get('error_prefix')}API请求失败: {str(e)}"
            if output_callback:
                output_callback("error", error_msg)
            self.stats["errors"].append(str(e))
            self.logger.error(f"API请求失败，已回滚消息到位置 {msgs_before_turn}: {e}")
            return error_msg
        except json.JSONDecodeError as e:
            # JSON 解析错误：回滚消息
            self._rollback_messages(msgs_before_turn)
            error_msg = f"\n{i18n.get('error_prefix')}API响应解析失败: {str(e)}"
            if output_callback:
                output_callback("error", error_msg)
            self.stats["errors"].append(str(e))
            self.logger.error(f"API响应解析失败，已回滚消息到位置 {msgs_before_turn}: {e}")
            return error_msg
        except Exception as e:
            # 如果是因为取消导致的异常，按中断处理
            if self._is_cancelled():
                self._rollback_messages(msgs_before_turn)
                interrupt_msg = i18n.get('user_interrupted', '用户中断')
                if output_callback:
                    output_callback("line", "\n" + interrupt_msg + "\n")
                    output_callback("complete", interrupt_msg)
                return interrupt_msg
            # 未知错误：回滚消息，防止对话状态被破坏
            self._rollback_messages(msgs_before_turn)
            error_msg = f"\n{i18n.get('error_prefix')}{str(e)}"
            if output_callback:
                output_callback("error", error_msg)
            self.stats["errors"].append(str(e))
            self.logger.error(f"未知错误，已回滚消息到位置 {msgs_before_turn}: {e}")
            return error_msg
        finally:
            # 客户端抽象层在流结束时已自行清理响应引用
            pass
    
    def _rollback_messages(self, to_length: int):
        """回滚 self.messages 到指定长度，用于 API 失败时恢复对话状态。"""
        try:
            current_len = len(self.messages)
            if current_len > to_length:
                del self.messages[to_length:]
                self.logger.info(f"消息已回滚，从 {current_len} 条回滚到 {len(self.messages)} 条")
        except Exception as e:
            self.logger.error(f"消息回滚失败: {e}")
    
    def _validate_enhanced_messages(self, messages):
        """发送给 API 前的最终校验：检查 tool_calls 与 tool 消息的配对完整性。
        若发现问题，就地修复（补占位 tool 消息），避免 HTTP 400。"""
        if not messages:
            return
        pending_tool_ids = []  # 当前等待 tool 响应的 tool_call_id 列表
        seen_ids = set()
        fixed = []
        i = 0
        n = len(messages)
        while i < n:
            msg = messages[i]
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                # 如果有未被响应的 tool_calls，先补占位（正常情况不应出现）
                for tid in pending_tool_ids:
                    if tid not in seen_ids:
                        fixed.append({
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": "⚠️ 该工具调用未返回结果（校验修复）。",
                        })
                pending_tool_ids = [tc.get("id") for tc in msg["tool_calls"] if tc.get("id")]
                seen_ids = set()
                fixed.append(msg)
            elif role == "tool":
                tid = msg.get("tool_call_id")
                if tid and tid in pending_tool_ids and tid not in seen_ids:
                    seen_ids.add(tid)
                    fixed.append(msg)
                elif tid and tid not in pending_tool_ids:
                    # 孤立 tool 消息：不属于最近一个 assistant 的 tool_calls，跳过
                    self.logger.warning(f"_validate: 跳过孤立 tool 消息 {tid}")
                else:
                    fixed.append(msg)
            else:
                # 遇到非 assistant/tool 消息（user/system），如果有待响应的 tool_calls，先补占位
                for tid in pending_tool_ids:
                    if tid not in seen_ids:
                        fixed.append({
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": "⚠️ 该工具调用未返回结果（校验修复）。",
                        })
                pending_tool_ids = []
                seen_ids = set()
                fixed.append(msg)
            i += 1
        # 处理末尾：如果最后一条是 assistant with tool_calls 但没有 tool 响应
        for tid in pending_tool_ids:
            if tid not in seen_ids:
                fixed.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": "⚠️ 该工具调用未返回结果（校验修复）。",
                })
        # 原地替换 messages 内容
        messages.clear()
        messages.extend(fixed)
    
    def _repair_tool_pairs(self):
        """修复消息历史中的工具调用配对，保证 API 消息结构合法。

        场景：assistant 消息携带 tool_calls，但对应的 tool 响应缺失（工具被中断、
        压缩算法丢弃、端对话提前 break、system 消息插入等），下一次请求会因
        "insufficient tool messages following tool_calls" 返回 400。
        修复策略：
        1. 跳过 system 消息查找 tool 响应（压缩后 system 摘要可能插入在 assistant 与 tool 之间）
        2. 只收集与当前 assistant 的 tool_call_id 匹配的 tool 消息
        3. 为缺失响应的 tool_call_id 补占位 tool 消息
        4. 移除孤立的 tool 消息（tool_call_id 无对应前置 assistant tool_call）
        5. 同一 tool_call_id 只保留第一条 tool 响应，去重
        """
        if not self.messages:
            return
        repaired = []
        i = 0
        n = len(self.messages)
        # 追踪所有已见的 assistant tool_call_id，用于识别孤立 tool 消息
        known_tool_ids = set()
        while i < n:
            msg = self.messages[i]
            role = msg.get("role")
            if role == "system":
                repaired.append(msg)
                i += 1
                continue
            if role == "assistant" and msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]
                needed_ids = []
                for tc in tool_calls:
                    tid = tc.get("id")
                    if tid:
                        needed_ids.append(tid)
                        known_tool_ids.add(tid)
                # 收集紧随其后的 tool 消息，跳过 system 消息
                collected_tools = []  # list of (index, tool_msg) to ensure ordering
                collected_ids = set()
                j = i + 1
                pending_systems = []  # system 消息在 assistant 和 tool 之间暂存，最后放到 tool 之后
                while j < n:
                    m = self.messages[j]
                    r = m.get("role")
                    if r == "system":
                        pending_systems.append(m)
                        j += 1
                        continue
                    if r == "tool":
                        tid = m.get("tool_call_id")
                        # 只收集属于当前 assistant 且未重复的 tool 响应
                        if tid and tid in needed_ids and tid not in collected_ids:
                            collected_tools.append(m)
                            collected_ids.add(tid)
                        elif tid and tid not in needed_ids and tid in known_tool_ids:
                            # 属于更早 assistant 的 tool 响应，停止收集（已越过当前 assistant 的范围）
                            break
                        # tid 为空或不属于任何已知 assistant，跳过（可能是脏数据）
                        j += 1
                        continue
                    # 遇到 user 或下一个 assistant，停止收集
                    break
                # 为缺失的 tool_call_id 补占位消息
                for tid in needed_ids:
                    if tid not in collected_ids:
                        collected_tools.append({
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": "⚠️ 该工具调用未返回结果（可能被中断或压缩丢失）。",
                        })
                        collected_ids.add(tid)
                repaired.append(msg)
                repaired.extend(collected_tools)
                repaired.extend(pending_systems)
                i = j
            elif role == "tool":
                tid = msg.get("tool_call_id")
                if tid and tid in known_tool_ids:
                    repaired.append(msg)
                else:
                    self.logger.warning(f"_repair_tool_pairs: 跳过孤立 tool 消息 id={tid}")
                i += 1
            else:
                # user 或普通 assistant（无 tool_calls）
                repaired.append(msg)
                i += 1
        self.messages = repaired
    
    def reset_conversation(self, reload_prompts: bool = True):
        """
        重置对话状态 - 清除所有对话记忆，可选择是否重新加载系统提示词
        
        Args:
            reload_prompts: 是否重新加载系统提示词，默认为 True
        """
        with self._msg_lock:
            self.conversation_active = True
            self.history_loaded = False  # 重置历史加载标记
            
            if reload_prompts:
                # 完全重置：清除所有消息，重新加载系统提示词
                self.messages = []
                self._init_system_prompts()
                self.logger.info("对话已完全重置，系统提示词已重新加载")
            else:
                # 只清除非系统消息，保留系统提示词
                if self.preserve_system:
                    system_messages = [msg for msg in self.messages if msg["role"] == "system"]
                    self.messages = system_messages
                else:
                    self.messages = []
                self.logger.info("对话已重置（仅清除对话历史）")
    
    def is_busy(self) -> bool:
        """检查AI是否正在处理消息（非阻塞，供调度器等外部调用者判断）。"""
        acquired = self._msg_lock.acquire(blocking=False)
        if acquired:
            self._msg_lock.release()
            return False
        return True
    
    def reload_prompts(self):
        """重新加载提示词"""
        with self._msg_lock:
            old_system_count = len([msg for msg in self.messages if msg["role"] == "system"])
            
            # 移除所有旧的系统消息
            self.messages = [msg for msg in self.messages if msg["role"] != "system"]
            
            # 重新加载提示词
            self.prompt_manager.reload()
            self._init_system_prompts()
            
            new_system_count = len([msg for msg in self.messages if msg["role"] == "system"])
            self.logger.info(f"提示词已重新加载: {old_system_count} -> {new_system_count} 个")
            return new_system_count


# ==================== WebSocket处理器（修改版：添加重置对话功能） ====================

class WebSocketHandler:
    """WebSocket处理器 - 处理WebSocket连接和消息"""
    
    def __init__(self, ai_manager):
        self.ai_manager = ai_manager
        self.logger = Logger("WebSocket")
        self.connected_clients = set()
        self.client_sessions = {}
        self._processing_tasks = {}  # websocket id -> 当前正在处理的asyncio Task
        self._shared_skill_manager = None  # 共享技能管理器（懒加载，供所有连接复用）
    
    def _get_shared_skill_manager(self):
        """获取共享技能管理器（懒加载一次，供所有连接实例复用，避免重复扫描技能）"""
        if self._shared_skill_manager is None:
            self._shared_skill_manager = SkillManager(self.logger)
        return self._shared_skill_manager
    
    def _create_session_ai(self):
        """为一个WebSocket连接创建独立的AI实例，实现不同用户间上下文隔离。

        每个连接拥有独立的 messages / 工具注册表 / 消息锁，可与其他用户并发对话，
        互不干扰。共享只读资源（记忆库、备忘录、提示词、技能管理器）复用。
        """
        return DeepSeekChat(
            api_key=None,
            max_history=constants.DEFAULT_MAX_HISTORY,
            debug_level=self.logger.level,
            exit_callback=None,  # 会话级对话结束由连接内处理，不触发全局状态
            history_manager=None,  # 不加载共享历史上下文，避免多用户历史相互污染
            memory_db=self.ai_manager.memory_db,
            memo_db=self.ai_manager.memo_db,
            # 每个连接使用独立的命令执行器，互不共享，避免 cancel() 误杀其他用户/任务正在运行的子进程
            command_executor=SubprocessCommandExecutor(self.logger),
            prompt_manager=self.ai_manager.prompt_manager,
            skill_manager=self._get_shared_skill_manager()
        )
    
    def _get_session(self, websocket) -> dict:
        """获取指定连接对应的会话状态（不存在则创建空会话）"""
        return self.client_sessions.get(websocket)
    
    async def register(self, websocket):
        """注册新客户端"""
        self.connected_clients.add(websocket)
        self.client_sessions[websocket] = {
            "connected_at": datetime.now(),
            "message_count": 0,
            "current_conversation": None,
            "ai": None,                    # 每个连接独立的AI实例
            "dialogue_in_progress": False, # 每个连接独立的对话状态
            "is_processing": False         # 每个连接独立的并发处理标志
        }
        self.logger.info(i18n.get('client_connected', websocket.remote_address))
        
        # 发送连接确认消息
        await self.send_message(websocket, {
            "type": "connection_ack",
            "message": i18n.get('ws_connected'),
            "timestamp": datetime.now().strftime(constants.DATETIME_FORMAT)
        })
        
        # 发送欢迎信息和初始状态
        await self.send_welcome_info(websocket)
    
    async def unregister(self, websocket):
        """注销客户端"""
        self.connected_clients.remove(websocket)
        if websocket in self.client_sessions:
            session = self.client_sessions.pop(websocket)
            # 释放该连接独立的AI实例资源
            ai = session.get("ai")
            if ai:
                try:
                    ai.cancel()
                except Exception:
                    pass
        self.logger.info(i18n.get('client_disconnected', websocket.remote_address))
    
    async def send_welcome_info(self, websocket):
        """发送欢迎信息"""
        # 发送存储位置
        await self.send_message(websocket, {
            "type": "info",
            "content": i18n.get('data_dir', constants.SAVE_DIR)
        })
        await self.send_message(websocket, {
            "type": "info",
            "content": i18n.get('skills_dir', constants.SKILLS_DIR)
        })
        await self.send_message(websocket, {
            "type": "info",
            "content": i18n.get('prompts_dir', constants.PROMPTS_DIR)
        })
        
        # 发送任务调度器状态
        if self.ai_manager.scheduler and self.ai_manager.scheduler.running:
            stats = self.ai_manager.scheduler.get_stats()
            await self.send_message(websocket, {
                "type": "info",
                "content": i18n.get('scheduler_started')
            })
            await self.send_message(websocket, {
                "type": "info",
                "content": f"📊 任务统计: 总执行次数={stats.get('total_executions', 0)}, 最近执行={len(stats.get('recent_executions', []))}"
            })
        
        # 发送任务统计
        memo_stats = self.ai_manager.memo_db.get_memo_stats()
        if memo_stats['total'] > 0:
            await self.send_message(websocket, {
                "type": "info",
                "content": i18n.get('memo_stats')
            })
            await self.send_message(websocket, {
                "type": "info",
                "content": i18n.get('total_memos', memo_stats['total'])
            })
            await self.send_message(websocket, {
                "type": "info",
                "content": i18n.get('pending_memos', memo_stats['pending'])
            })
            if memo_stats['overdue'] > 0:
                await self.send_message(websocket, {
                    "type": "info",
                    "content": i18n.get('overdue_memos', memo_stats['overdue'])
                })
            await self.send_message(websocket, {
                "type": "info",
                "content": i18n.get('completed_memos', memo_stats['completed'])
            })
        
        # 发送待执行任务列表
        pending = self.ai_manager.memo_db.get_pending_tasks_for_user()
        if pending:
            await self.send_message(websocket, {
                "type": "info",
                "content": i18n.get('pending_memos_title')
            })
            for task in pending[:5]:
                status = i18n.get('memo_overdue') if task['is_overdue'] else i18n.get('memo_pending')
                immediate_tag = "⚡" if task['is_immediate'] else ""
                await self.send_message(websocket, {
                    "type": "task_status",
                    "task_id": task['id'],
                    "title": task['title'],
                    "reminder_time": task['reminder_time'],
                    "status": "overdue" if task['is_overdue'] else "pending",
                    "content": f"{status}{immediate_tag} [ID: {task['id']}] {task['title']} - {task['reminder_time']}"
                })
            if len(pending) > 5:
                await self.send_message(websocket, {
                    "type": "info",
                    "content": i18n.get('more_skills', len(pending)-5)
                })

    async def send_message(self, websocket, message):
        """发送消息到客户端 - 确保完整发送不被截断"""
        try:
            # 将消息转换为JSON字符串
            json_str = json.dumps(message, ensure_ascii=False)
            
            # 直接发送整个字符串，WebSocket协议会自动处理分片
            await websocket.send(json_str)
            
        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("连接已关闭，无法发送消息")
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")
    
    async def broadcast(self, message):
        """广播消息到所有客户端"""
        for websocket in self.connected_clients.copy():
            try:
                await self.send_message(websocket, message)
            except:
                await self.unregister(websocket)
    
    async def broadcast_task_status(self, task: Dict, status: str, message: str = None):
        """广播任务状态"""
        await self.broadcast({
            "type": "task_status",
            "task_id": task.get('id', 0),
            "title": task.get('title', '未知任务'),
            "status": status,
            "message": message,
            "timestamp": datetime.now().strftime(constants.DATETIME_FORMAT)
        })
    
    async def handle_message(self, websocket, message):
        """处理接收到的消息"""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "unknown")
            
            # 更新会话统计
            if websocket in self.client_sessions:
                self.client_sessions[websocket]["message_count"] += 1
            
            if msg_type == "command":
                await self.handle_command(websocket, data)
            elif msg_type == "message":
                await self.handle_user_message(websocket, data)
            elif msg_type == "interrupt":
                await self.handle_interrupt(websocket, data)
            elif msg_type == "task":
                await self.handle_task_message(websocket, data)
            elif msg_type == "ping":
                await self.send_message(websocket, {
                    "type": "pong", 
                    "timestamp": datetime.now().strftime(constants.DATETIME_FORMAT)
                })
            else:
                await self.send_message(websocket, {
                    "type": "error",
                    "content": i18n.get('ws_unknown_command', msg_type)
                })
                
        except json.JSONDecodeError:
            await self.send_message(websocket, {
                "type": "error",
                "content": "无效的JSON格式"
            })
        except Exception as e:
            self.logger.error(f"处理消息失败: {e}")
            await self.send_message(websocket, {
                "type": "error",
                "content": f"处理失败: {str(e)}"
            })
    
    async def handle_interrupt(self, websocket, data):
        """处理中断信号：取消当前连接正在进行的AI思考和工具执行"""
        self.logger.info("收到客户端中断信号")
        # 先发送确认，让前端立即得到反馈
        await self.send_message(websocket, {
            "type": "interrupt_ack",
            "content": "已收到中断信号，正在停止..."
        })
        # 取消当前连接独立AI实例的对话（线程安全，只set Event + close连接 + kill进程）
        session = self._get_session(websocket)
        ai = session.get("ai") if session else None
        if ai:
            ai.cancel()
        # 同时确保命令执行器中的进程也被终止
        if self.ai_manager.command_executor and hasattr(self.ai_manager.command_executor, 'cancel'):
            try:
                self.ai_manager.command_executor.cancel()
            except Exception:
                pass
    
    async def handle_task_message(self, websocket, data):
        """处理任务相关消息 - 简化版，所有任务都是对话任务"""
        action = data.get("action", "").strip()
        
        if action == "add":
            # 添加任务
            title = data.get("title")
            content = data.get("content")
            delay = data.get("delay", 60)  # 默认60秒后执行
            is_immediate = data.get("is_immediate", False)  # 是否为即时任务
            
            # 重复任务参数
            repeat_type = data.get("repeat_type", "none")
            repeat_interval = data.get("repeat_interval", 0)
            repeat_interval_unit = data.get("repeat_interval_unit", "days")
            repeat_end_time = data.get("repeat_end_time")
            
            if not title:
                await self.send_message(websocket, {
                    "type": "error",
                    "content": "需要提供任务标题"
                })
                return
            
            reminder_time = None
            if is_immediate:
                reminder_time = datetime.now().strftime(constants.DATETIME_FORMAT)
            else:
                reminder_time = (datetime.now() + timedelta(seconds=delay)).strftime(constants.DATETIME_FORMAT)
            
            memo_id = self.ai_manager.memo_db.add_user_task(
                title=title,
                content=content,
                reminder_time=reminder_time,
                repeat_type=repeat_type,
                repeat_interval=repeat_interval,
                repeat_interval_unit=repeat_interval_unit,
                repeat_end_time=repeat_end_time,
                is_immediate=is_immediate
            )
            
            immediate_msg = i18n.get('immediate_memo_added') if is_immediate else ""
            repeat_msg = ""
            if repeat_type != "none":
                if repeat_type == "custom":
                    repeat_msg = f" (每{repeat_interval} {repeat_interval_unit})"
                else:
                    repeat_msg = f" ({repeat_type})"
            
            await self.send_message(websocket, {
                "type": "task_added",
                "task_id": memo_id,
                "title": title,
                "reminder_time": reminder_time,
                "content": i18n.get('task_added', title, memo_id) + repeat_msg + immediate_msg
            })
            
            # 广播任务添加
            await self.broadcast_task_status(
                {"id": memo_id, "title": title},
                "added",
                f"新任务: {title}" + repeat_msg + (" (即时)" if is_immediate else "")
            )
            
        elif action == "list":
            pending = self.ai_manager.memo_db.get_pending_tasks_for_user()
            await self.send_message(websocket, {
                "type": "task_list",
                "tasks": pending
            })
            
        elif action == "complete":
            memo_id = data.get("task_id")
            if memo_id:
                if self.ai_manager.memo_db.complete_memo(memo_id):
                    memo = self.ai_manager.memo_db.get_memo(memo_id)
                    await self.send_message(websocket, {
                        "type": "task_completed",
                        "task_id": memo_id,
                        "content": i18n.get('memo_completed', memo_id)
                    })
                    if memo:
                        await self.broadcast_task_status(memo, "completed", f"任务已完成: {memo['title']}")
                else:
                    await self.send_message(websocket, {
                        "type": "error",
                        "content": i18n.get('memo_complete_failed', memo_id)
                    })
    
    async def handle_command(self, websocket, data):
        """处理命令消息"""
        command = data.get("command", "").strip()
        
        if not command:
            return
        
        self.logger.info(f"收到命令: {command}")
        
        if command == "/exit":
            await self.send_message(websocket, {
                "type": "command_result",
                "content": i18n.get('goodbye')
            })
            await websocket.close()
            
        elif command == "/new":
            # 【新增】开始新对话：重置当前连接的AI状态，重新加载系统提示词
            session = self._get_session(websocket)
            ai = session.get("ai") if session else None
            if ai:
                # 如果该连接AI正在处理，先取消
                if ai.is_busy():
                    self.logger.info("/new: AI正在处理中，先取消当前对话...")
                    ai.cancel()
                    for _ in range(20):
                        await asyncio.sleep(0.1)
                        if not ai.is_busy():
                            break
                # 重置对话状态，重新加载系统提示词
                ai.reset_conversation(reload_prompts=True)
                # 重置该连接对话进行中标记
                if session:
                    session["dialogue_in_progress"] = False
                # 重新初始化提示词管理器（确保最新）
                self.ai_manager.prompt_manager.reload()
                self.logger.info(i18n.get('new_conversation_reset'))
                
                await self.send_message(websocket, {
                    "type": "command_result",
                    "content": i18n.get('new_conversation_reset')
                })
                
                # 发送重置完成通知
                await self.send_message(websocket, {
                    "type": "dialogue_reset",
                    "content": i18n.get('dialogue_reset_complete')
                })
            else:
                await self.send_message(websocket, {
                    "type": "command_result",
                    "content": i18n.get('new_conversation')
                })
            
        elif command == "/list":
            conversations = self.ai_manager.history_manager.list_conversations()
            result = f"\n{i18n.get('saved_conversations')}\n"
            for conv in conversations:
                status = i18n.get('conv_status_current') if conv.get('is_current') else i18n.get('conv_status_archived')
                result += f"  {conv['file']} - {conv['topic']} [{status}] ({conv['start_time']})\n"
            await self.send_message(websocket, {
                "type": "command_result",
                "content": result
            })
            
        elif command == "/memories":
            memories = self.ai_manager.memory_db.get_recent_memories(10)
            result = f"\n{i18n.get('recent_memories')}\n"
            for mem in memories:
                result += f"  [ID: {mem['id']}] {mem['topic']} - {mem['summary'][:50]}...\n"
            await self.send_message(websocket, {
                "type": "command_result",
                "content": result
            })
            
        elif command == "/memos":
            memos = self.ai_manager.memo_db.get_all_memos(include_completed=False)
            result = f"\n{i18n.get('pending_memos_title')}\n"
            for memo in memos:
                status = i18n.get('memo_overdue') if memo['reminder_time'] and datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT) < datetime.now() else i18n.get('memo_pending')
                immediate_tag = "⚡" if memo.get('is_immediate') else ""
                repeat_info = ""
                if memo['repeat_type'] != 'none':
                    if memo['repeat_type'] == 'custom':
                        unit = memo.get('repeat_interval_unit', 'days')
                        repeat_info = f" [每{memo['repeat_interval']} {unit}]"
                    else:
                        repeat_info = f" [{memo['repeat_type']}]"
                result += f"  {status}{immediate_tag} [ID: {memo['id']}] {memo['title']}{repeat_info} - {memo.get('reminder_time', '无提醒')}\n"
            await self.send_message(websocket, {
                "type": "command_result",
                "content": result
            })
            
        elif command.startswith("/search "):
            query = command[8:].strip()
            results = self.ai_manager.memory_db.search_memories([query])
            result = i18n.get('search_results', query, len(results)) + "\n"
            for mem in results:
                result += f"  [ID: {mem['id']}] {mem['topic']} - {mem['summary'][:100]}...\n"
            await self.send_message(websocket, {
                "type": "command_result",
                "content": result
            })
            
        elif command == "/config":
            result = f"\n{i18n.get('current_config')}\n"
            result += i18n.get('config_debug', self.ai_manager.config.get('system.debug_level')) + "\n"
            result += i18n.get('config_data_dir', constants.SAVE_DIR) + "\n"
            result += i18n.get('config_skills_dir', constants.SKILLS_DIR) + "\n"
            result += i18n.get('config_prompts_dir', constants.PROMPTS_DIR) + "\n"
            result += i18n.get('config_commands', i18n.get('enabled') if constants.COMMANDS_ENABLED else i18n.get('disabled')) + "\n"
            result += i18n.get('config_history', i18n.get('enabled') if self.ai_manager.history_manager.load_history else i18n.get('disabled')) + "\n"
            result += i18n.get('config_executor', type(self.ai_manager.command_executor).__name__) + "\n"
            result += i18n.get('config_scheduler', i18n.get('enabled') if constants.SCHEDULER_ENABLED else i18n.get('disabled')) + "\n"
            await self.send_message(websocket, {
                "type": "command_result",
                "content": result
            })
            
        elif command == "/reload":
            self.ai_manager.config.reload()
            await self.send_message(websocket, {
                "type": "command_result",
                "content": i18n.get('config_reloaded')
            })
            
        elif command == "/reload_prompts":
            session = self._get_session(websocket)
            ai = session.get("ai") if session else None
            if ai:
                new_count = ai.reload_prompts()
            else:
                new_count = self.ai_manager.prompt_manager.reload()
            await self.send_message(websocket, {
                "type": "command_result",
                "content": i18n.get('prompts_reloaded', new_count)
            })
            
        elif command == "/check":
            result = i18n.get('checking_memos') + "\n"
            overdue = self.ai_manager.memo_db.get_overdue_memos()
            if overdue:
                result += i18n.get('overdue_found', len(overdue)) + "\n"
                for memo in overdue:
                    immediate_tag = "⚡" if memo.get('is_immediate') else ""
                    repeat_info = ""
                    if memo['repeat_type'] != 'none':
                        if memo['repeat_type'] == 'custom':
                            unit = memo.get('repeat_interval_unit', 'days')
                            repeat_info = f" (每{memo['repeat_interval']} {unit})"
                        else:
                            repeat_info = f" ({memo['repeat_type']})"
                    result += f"  • {immediate_tag}[ID: {memo['id']}] {memo['title']}{repeat_info} - {memo['reminder_time']}\n"
            else:
                result += i18n.get('no_overdue') + "\n"
            await self.send_message(websocket, {
                "type": "command_result",
                "content": result
            })
            
        elif command == "/tasks":
            if self.ai_manager.scheduler:
                stats = self.ai_manager.scheduler.get_stats()
                result = f"\n{i18n.get('task_stats', stats.get('total_executions', 0), stats.get('total_executions', 0), stats.get('total_executions', 0))}\n\n"
                
                pending = self.ai_manager.memo_db.get_pending_tasks_for_user()
                result += f"待执行任务: {len(pending)} 个\n"
                for task in pending[:10]:
                    status = "⏰" if not task['is_overdue'] else "⚠️"
                    immediate_tag = "⚡" if task['is_immediate'] else ""
                    repeat_info = ""
                    if task['repeat_type'] != 'none':
                        if task['repeat_type'] == 'custom':
                            unit = task.get('repeat_interval_unit', 'days')
                            repeat_info = f" [每{task['repeat_interval']} {unit}]"
                        else:
                            repeat_info = f" [{task['repeat_type']}]"
                    result += f"  {status}{immediate_tag} [ID: {task['id']}] {task['title']}{repeat_info} - {task['reminder_time']}\n"
            else:
                result = "❌ 任务调度器未启用"
            await self.send_message(websocket, {
                "type": "command_result",
                "content": result
            })
            
        elif command == "/help":
            help_text = f"\n可用命令:\n"
            help_text += "  /exit - 退出连接\n"
            help_text += "  /new - 开始新对话（保存当前对话，重置AI记忆，重新加载系统提示词）\n"
            help_text += "  /list - 列出所有对话\n"
            help_text += "  /memories - 查看最近的记忆\n"
            help_text += "  /memos - 查看待办任务\n"
            help_text += "  /search <关键词> - 搜索记忆\n"
            help_text += "  /config - 查看配置\n"
            help_text += "  /reload - 重新加载配置\n"
            help_text += "  /reload_prompts - 重新加载提示词\n"
            help_text += "  /check - 检查过期任务\n"
            help_text += "  /tasks - 查看任务状态\n"
            help_text += "  /help - 显示此帮助\n\n"
            help_text += "任务类型:\n"
            help_text += "  {type: 'task', action: 'add', title: '任务', delay: 60} - 添加一次性任务\n"
            help_text += "  {type: 'task', action: 'add', title: '即时', is_immediate: true} - 添加即时任务（立即执行）\n"
            help_text += "  {type: 'task', action: 'add', title: '重复任务', repeat_type: 'custom', repeat_interval: 5, repeat_interval_unit: 'minutes'} - 每5分钟重复\n"
            help_text += "  {type: 'task', action: 'list'} - 获取任务列表\n"
            help_text += "  {type: 'task', action: 'complete', task_id: 1} - 完成任务\n"
            await self.send_message(websocket, {
                "type": "command_result",
                "content": help_text
            })
            
        else:
            # 如果不是命令，当作普通消息处理
            await self.handle_user_message(websocket, {"type": "message", "content": command})

    async def handle_user_message(self, websocket, data):
        """处理用户消息 - 实时流式输出（每个连接使用独立的AI实例，支持多用户并发）"""
        content = data.get("content", "").strip()
        
        if not content:
            return
        
        # 获取当前连接的会话状态
        session = self._get_session(websocket)
        if session is None:
            return
        
        # 防止并发处理用户消息（按连接独立）：如果正在处理中，提示用户先中断
        if session.get("is_processing"):
            await self.send_message(websocket, {
                "type": "info",
                "content": "⏳ AI正在响应中，请等待完成或发送中断信号（Ctrl+C）"
            })
            return
        
        session["is_processing"] = True
        
        self.logger.info(f"收到用户消息: {content[:50]}...")
        
        # 获取该连接独立的AI实例（懒创建）
        ai = session.get("ai")
        if ai is None:
            self.logger.info("初始化独立AI实例...")
            ai = self._create_session_ai()
            session["ai"] = ai
        
        # 如果还没有活跃对话，开始新对话（对话状态按连接独立）
        if not session.get("dialogue_in_progress"):
            self.logger.info(f"开始新对话（连接 {id(websocket)}）")
            session["dialogue_in_progress"] = True
            
            # 发送对话开始消息
            await self.send_message(websocket, {
                "type": "info",
                "content": i18n.get('conversation_start')
            })
        
        # 获取当前事件循环
        loop = asyncio.get_running_loop()
        
        # 定义输出回调函数 - 实时发送
        def output_callback(msg_type, content):
            """AI输出的回调函数 - 实时发送每个chunk"""
            self.logger.info(f"输出回调被调用: {msg_type}, 内容长度: {len(content)}")
            
            # 创建发送消息的协程
            async def send_msg():
                try:
                    await websocket.send(json.dumps({
                        "type": msg_type,
                        "content": content
                    }, ensure_ascii=False))
                    self.logger.info(f"实时消息发送成功: {msg_type}")
                except Exception as e:
                    self.logger.error(f"实时发送消息失败: {e}")
            
            # 提交到事件循环
            asyncio.run_coroutine_threadsafe(send_msg(), loop)
        
        try:
            self.logger.info("开始AI思考...")
            
            # 在线程池中运行AI思考，通过回调实时发送消息
            response = await loop.run_in_executor(
                None,
                ai.think_and_respond,
                content,
                output_callback  # 传递回调函数，AI会在生成每个chunk时调用
            )
            
            self.logger.info(f"AI思考完成，完整响应长度: {len(response) if response else 0}")
            
            # 检查对话是否结束（按连接独立）
            if not ai.conversation_active:
                self.logger.info("对话结束")
                session["dialogue_in_progress"] = False
                await self.send_message(websocket, {
                    "type": "info",
                    "content": i18n.get('conversation_ended')
                })
                # 【修复】发送明确的对话结束信号
                await self.send_message(websocket, {
                    "type": "dialogue_ended",
                    "content": i18n.get('dialogue_ended')
                })
                
        except Exception as e:
            self.logger.error(f"AI处理消息失败: {e}")
            import traceback
            traceback.print_exc()
            try:
                await websocket.send(json.dumps({
                    "type": "error",
                    "content": f"{i18n.get('error_prefix')}{str(e)}"
                }, ensure_ascii=False))
            except:
                pass
        finally:
            # 无论成功、失败还是被中断，都清除该连接的处理标志
            session["is_processing"] = False


# ==================== 对话管理器（修改版：添加重置对话功能） ====================

class EnhancedAIManager:
    """增强的AI对话管理器 - 集成任务调度器"""
    
    def __init__(self, debug_level: int = None, load_history: bool = None, command_executor: CommandExecutor = None):
        self.ai: Optional[DeepSeekChat] = None
        self.dialogue_history: List[Dict] = []
        self.config = ConfigManager()
        self.command_executor = command_executor or SubprocessCommandExecutor()
        self.prompt_manager = PromptManager()  # 创建提示词管理器
        self.scheduler: Optional[TaskScheduler] = None  # 任务调度器
        
        if debug_level is None:
            debug_level = self.config.get("system.debug_level", 1)
        
        self.logger = Logger("Manager", debug_level)
        self.running = True
        
        # 确保 ~/.aibox 目录存在
        os.makedirs(constants.SAVE_DIR, exist_ok=True)
        # 确保 prompts 目录存在（PromptManager 会创建）
        os.makedirs(constants.PROMPTS_DIR, exist_ok=True)
        
        self.memory_db = MemoryDatabase()
        self.memo_db = MemoDatabase()
        self.history_manager = ConversationHistory(memory_db=self.memory_db, load_history=load_history)
        
        self.dialogue_in_progress = False
        self.ws_handler = None
    
    def global_exit_handler(self, reason: str, summary: str):
        """对话结束处理器"""
        self.logger.info(f"对话结束 - 原因: {reason}")
        
        self.history_manager.end_current_conversation(summary)
        
        if self.ai:
            # 智能体主动结束对话后，重置对话状态但保留系统提示词（锁保护）
            self.ai.reset_conversation(reload_prompts=False)
        
        self.dialogue_in_progress = False
        self.logger.info("对话状态已重置，可以开始新对话")
    
    def reset_dialogue(self):
        """重置对话 - 清除所有对话记忆，重新加载系统提示词"""
        if self.ai:
            # 完全重置AI状态，重新加载系统提示词
            self.ai.reset_conversation(reload_prompts=True)
        self.dialogue_in_progress = False
        self.history_manager.reset_conversation()
        self.logger.info(i18n.get('dialogue_reset_complete'))
    
    def add_ai(self) -> DeepSeekChat:
        """添加AI"""
        ai = DeepSeekChat(
            api_key=None,
            max_history=constants.DEFAULT_MAX_HISTORY,
            debug_level=self.logger.level,
            exit_callback=self.global_exit_handler,
            history_manager=self.history_manager,
            memory_db=self.memory_db,
            memo_db=self.memo_db,
            command_executor=self.command_executor,
            prompt_manager=self.prompt_manager
        )
        
        self.ai = ai
        self.logger.info("AI已初始化")
        return ai
    
    def cancel(self):
        """中断当前AI操作（线程安全，可从任意线程调用）"""
        if self.ai:
            self.ai.cancel()
        # 同时确保命令执行器中的进程也被终止
        if self.command_executor and hasattr(self.command_executor, 'cancel'):
            try:
                self.command_executor.cancel()
            except Exception:
                pass
    
    def show_welcome_message(self):
        """显示欢迎信息"""
        print("\n" + "="*70)
        print(i18n.get('welcome_title'))
        print("="*70)
        
        # 显示存储位置
        print(i18n.get('data_dir', constants.SAVE_DIR))
        print(i18n.get('skills_dir', constants.SKILLS_DIR))
        print(i18n.get('prompts_dir', constants.PROMPTS_DIR))
        
        config_path = self.config._config_path
        if os.path.exists(config_path):
            print(i18n.get('config_file', config_path))
        
        # 显示提示词数量
        prompt_count = self.prompt_manager.get_prompt_count()
        if prompt_count > 0:
            prompts = self.prompt_manager.get_prompts_list()
            print(i18n.get('loaded_prompts', prompt_count))
            for prompt in prompts[:5]:
                print(i18n.get('prompt_item', prompt['file']))
            if prompt_count > 5:
                print(i18n.get('more_skills', prompt_count-5))
        
        # 显示任务调度器状态
        if constants.SCHEDULER_ENABLED:
            print(i18n.get('scheduler_started'))
        
        memo_stats = self.memo_db.get_memo_stats()
        if memo_stats['total'] > 0:
            print(f"\n{i18n.get('memo_stats')}")
            print(i18n.get('total_memos', memo_stats['total']))
            print(i18n.get('pending_memos', memo_stats['pending']))
            if memo_stats['overdue'] > 0:
                print(i18n.get('overdue_memos', memo_stats['overdue']))
            print(i18n.get('completed_memos', memo_stats['completed']))
        
        # 显示待执行任务
        pending = self.memo_db.get_pending_tasks_for_user()
        if pending:
            print(f"\n{i18n.get('pending_memos_title')}")
            for task in pending[:5]:
                status = i18n.get('memo_overdue') if task['is_overdue'] else i18n.get('memo_pending')
                immediate_tag = "⚡" if task['is_immediate'] else ""
                repeat_info = ""
                if task['repeat_type'] != 'none':
                    if task['repeat_type'] == 'custom':
                        unit = task.get('repeat_interval_unit', 'days')
                        repeat_info = f" [每{task['repeat_interval']} {unit}]"
                    else:
                        repeat_info = f" [{task['repeat_type']}]"
                print(f"  {status}{immediate_tag} [ID: {task['id']}] {task['title']}{repeat_info} - {task['reminder_time']}")
            if len(pending) > 5:
                print(i18n.get('more_skills', len(pending)-5))
        
        # 显示已加载的技能（含简介）
        skill_manager = getattr(getattr(self.ai, 'tool_registry', None), 'skill_manager', None)
        if skill_manager and skill_manager.count() > 0:
            all_skills = skill_manager.get_all()
            print(i18n.get('loaded_skills', skill_manager.count()))
            for s in all_skills[:5]:
                print(f"  • {s['name']} — {s['summary']}")
            if skill_manager.count() > 5:
                print(i18n.get('more_skills', skill_manager.count() - 5))
        
        # 显示WebSocket服务器信息
        host = constants.WEBSOCKET_HOST
        port = constants.WEBSOCKET_PORT
        print(i18n.get('websocket_started', host, port))
    
    async def handle_connection(self, websocket, path):
        """处理WebSocket连接 - 消息接收与处理解耦，确保中断信号能被即时处理"""
        self.logger.info(f"新连接: {websocket.remote_address}, path: {path}")
        await self.ws_handler.register(websocket)
        try:
            async for message in websocket:
                # 【关键修复】用create_task异步处理消息，不await阻塞接收循环
                # 这样当AI在后台生成响应时，interrupt消息仍能被立即接收和处理
                task = asyncio.create_task(
                    self.ws_handler.handle_message(websocket, message)
                )
                self.ws_handler._processing_tasks[id(websocket)] = task

                def _cleanup_task(t, ws_id=id(websocket)):
                    self.ws_handler._processing_tasks.pop(ws_id, None)
                task.add_done_callback(_cleanup_task)
        except websockets.exceptions.ConnectionClosed as e:
            self.logger.info(f"连接关闭: {e}")
        except Exception as e:
            self.logger.error(f"处理错误: {e}")
        finally:
            # 连接关闭时取消正在进行的处理任务
            task = self.ws_handler._processing_tasks.pop(id(websocket), None)
            if task and not task.done():
                task.cancel()
            await self.ws_handler.unregister(websocket)

    async def websocket_server(self):
        """启动WebSocket服务器并集成任务调度器"""
        host = constants.WEBSOCKET_HOST
        port = constants.WEBSOCKET_PORT
        
        self.ws_handler = WebSocketHandler(self)
        
        # 如果启用了任务调度器，创建并启动
        if constants.SCHEDULER_ENABLED:
            self.scheduler = TaskScheduler(asyncio.get_running_loop(), self.logger)
            self.scheduler.set_websocket_handler(self.ws_handler)
            self.scheduler.set_ai_manager(self)  # 设置AI管理器引用
        
        async def handler(websocket):
            await self.handle_connection(websocket, None)
        
        # ping_interval=None：禁用服务端协议层主动 Ping。
        # websockets 库默认 ping_interval=20s / ping_timeout=20s，服务端每 20s 发协议层
        # Ping 帧，20s 内收不到 Pong 即强制断连。经 WAF→gateway 两层代理转发后 Pong 无法
        # 可靠回传，导致连接约 20s 被服务端关闭。前端已通过 25s 应用层心跳（{type:'ping'}）
        # 保活，故此处完全禁用服务端协议层 ping。
        server = await websockets.serve(handler, host, port, ping_interval=None)
        self.logger.info(i18n.get('websocket_started', host, port))
        
        try:
            if self.scheduler:
                # 同时运行服务器和调度器
                await asyncio.gather(
                    server.wait_closed(),
                    self.scheduler.run(self.memo_db)
                )
            else:
                # 只运行服务器
                await server.wait_closed()
        finally:
            if self.scheduler:
                self.scheduler.stop()
    
    def run(self):
        """运行WebSocket服务器"""
        self.add_ai()
        self.show_welcome_message()
        
        # 检查过期任务
        overdue = self.memo_db.get_overdue_memos()
        if overdue:
            print(f"\n⏰ {i18n.get('overdue_found', len(overdue))}")
            for memo in overdue[:5]:
                immediate_tag = "⚡" if memo.get('is_immediate') else ""
                repeat_info = ""
                if memo['repeat_type'] != 'none':
                    if memo['repeat_type'] == 'custom':
                        unit = memo.get('repeat_interval_unit', 'days')
                        repeat_info = f" (每{memo['repeat_interval']} {unit})"
                    else:
                        repeat_info = f" ({memo['repeat_type']})"
                print(f"  • {immediate_tag}[ID: {memo['id']}] {memo['title']}{repeat_info} - {memo['reminder_time']}")
            if len(overdue) > 5:
                print(f"  ... {i18n.get('more_skills', len(overdue)-5)}")
        
        # 运行WebSocket服务器
        try:
            # 使用 asyncio.run() 运行服务器
            asyncio.run(self.websocket_server())
        except KeyboardInterrupt:
            print(f"\n\n{i18n.get('user_interrupted')}")
            self.history_manager.end_current_conversation()
        except Exception as e:
            print(f"\n{i18n.get('error_prefix')}{e}")
            import traceback
            traceback.print_exc()


# ==================== 命令行参数处理 ====================

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='单AI智能体系统 - WebSocket版本（所有任务都是对话任务）')
    
    parser.add_argument('--verbose', '-v', action='store_true', help='显示详细日志')
    
    parser.add_argument('--check-memo', action='store_true', help='立即检查到期任务并退出')
    
    parser.add_argument('--memo-add', type=str, help='添加任务，格式: "标题|内容|提醒时间|重复类型|重复间隔|单位"')
    parser.add_argument('--memo-list', action='store_true', help='列出任务')
    parser.add_argument('--memo-complete', type=int, help='完成指定ID的任务')
    
    parser.add_argument('-d', '--debug', type=int, choices=[0, 1, 2, 3], default=None, help='调试级别')
    parser.add_argument('-c', '--config', type=str, help='指定配置文件路径')
    
    # 添加语言选项
    parser.add_argument('--lang', type=str, choices=['zh', 'en'], help='强制指定语言 (zh/en)')
    
    # WebSocket选项
    parser.add_argument('--host', type=str, help='WebSocket服务器主机')
    parser.add_argument('--port', type=int, help='WebSocket服务器端口')
    
    # 调度器选项
    parser.add_argument('--no-scheduler', action='store_true', help='禁用任务调度器')
    parser.add_argument('--scheduler-interval', type=float, default=1.0, help='调度器检查间隔（秒）')
    
    return parser.parse_args()


def create_command_executor(args):
    """根据命令行参数创建命令执行器"""
    # 现在只使用本地执行器
    return SubprocessCommandExecutor()


def handle_check_commands(manager):
    """处理检查命令"""
    args = parse_args()
    
    if args.check_memo:
        print(i18n.get('memo_check_title'))
        overdue = manager.memo_db.get_overdue_memos()
        if overdue:
            print(i18n.get('memo_check_found', len(overdue)))
            for memo in overdue:
                immediate_tag = "⚡" if memo.get('is_immediate') else ""
                repeat_info = ""
                if memo['repeat_type'] != 'none':
                    if memo['repeat_type'] == 'custom':
                        unit = memo.get('repeat_interval_unit', 'days')
                        repeat_info = f" (每{memo['repeat_interval']} {unit})"
                    else:
                        repeat_info = f" ({memo['repeat_type']})"
                print(f"  • {immediate_tag}[ID: {memo['id']}] {memo['title']}{repeat_info} (提醒: {memo['reminder_time']})")
        else:
            print(i18n.get('memo_check_none'))
        return True
    
    return False


def handle_memo_commands(manager):
    """处理备忘录命令"""
    args = parse_args()
    
    if args.memo_list:
        memos = manager.memo_db.get_all_memos(include_completed=False)
        if not memos:
            print("📭 " + i18n.get('memo_check_none'))
        else:
            print(i18n.get('memo_list_title', len(memos)))
            for memo in memos:
                overdue = ""
                if memo['reminder_time']:
                    try:
                        reminder = datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT)
                        if reminder < datetime.now():
                            overdue = " ⚠️"
                    except:
                        pass
                immediate_tag = "⚡" if memo.get('is_immediate') else ""
                repeat_info = ""
                if memo['repeat_type'] != 'none':
                    if memo['repeat_type'] == 'custom':
                        unit = memo.get('repeat_interval_unit', 'days')
                        repeat_info = f" [每{memo['repeat_interval']} {unit}]"
                    else:
                        repeat_info = f" [{memo['repeat_type']}]"
                print(f"  {immediate_tag}[ID: {memo['id']}] {memo['title']}{repeat_info}{overdue}")
        return True
    
    if args.memo_add:
        try:
            parts = args.memo_add.split('|')
            title = parts[0]
            content = parts[1] if len(parts) > 1 else ""
            reminder = parts[2] if len(parts) > 2 else None
            
            # 解析重复参数
            repeat_type = parts[3] if len(parts) > 3 else "none"
            repeat_interval = int(parts[4]) if len(parts) > 4 else 0
            repeat_interval_unit = parts[5] if len(parts) > 5 else "days"
            
            is_immediate = reminder == "immediate"  # 如果提醒时间是"immediate"，则创建即时任务
            
            memo_id = manager.memo_db.add_memo(
                title=title,
                content=content,
                reminder_time=None if is_immediate else reminder,
                created_by="cli",
                source="cli",
                repeat_type=repeat_type,
                repeat_interval=repeat_interval,
                repeat_interval_unit=repeat_interval_unit,
                is_immediate=is_immediate
            )
            
            repeat_msg = ""
            if repeat_type != "none":
                if repeat_type == "custom":
                    repeat_msg = f" (每{repeat_interval} {repeat_interval_unit})"
                else:
                    repeat_msg = f" ({repeat_type})"
            
            print(i18n.get('memo_added', memo_id) + repeat_msg + (" (即时任务)" if is_immediate else ""))
        except Exception as e:
            print(i18n.get('memo_add_failed', e))
        return True
    
    if args.memo_complete:
        if manager.memo_db.complete_memo(args.memo_complete):
            print(i18n.get('memo_completed', args.memo_complete))
        else:
            print(i18n.get('memo_complete_failed', args.memo_complete))
        return True
    
    return False


# ==================== 主程序 ====================

def main():
    """主程序入口"""
    args = parse_args()
    
    # 如果命令行指定了语言，覆盖自动检测
    if args.lang:
        os.environ["AI_LANGUAGE"] = args.lang
        # 重新初始化i18n
        global i18n
        i18n = I18n()
    
    if args.config:
        os.environ["AI_CONFIG_PATH"] = args.config
    
    config = ConfigManager()
    
    if args.debug is not None:
        config._config["system"]["debug_level"] = args.debug
    
    if args.verbose:
        config._config["system"]["debug_level"] = 3
    
    # 覆盖WebSocket主机和端口
    if args.host:
        config._config["system"]["websocket_host"] = args.host
    if args.port:
        config._config["system"]["websocket_port"] = args.port
    
    # 调度器设置
    if args.no_scheduler:
        config._config["scheduler"]["enabled"] = False
    if args.scheduler_interval:
        config._config["scheduler"]["check_interval"] = args.scheduler_interval
    
    # 确保 ~/.aibox 目录存在
    os.makedirs(constants.SAVE_DIR, exist_ok=True)
    
    # 确保 skills 目录存在
    os.makedirs(constants.SKILLS_DIR, exist_ok=True)
    
    # 确保 prompts 目录存在
    os.makedirs(constants.PROMPTS_DIR, exist_ok=True)
    
    # 确保 workspace 工作目录存在（智能体专用文件区）
    os.makedirs(constants.WORKSPACE_DIR, exist_ok=True)
    
    Logger.init_file_logging()
    
    # 创建命令执行器 - 现在只使用本地执行器
    command_executor = SubprocessCommandExecutor()
    
    manager = EnhancedAIManager(
        debug_level=args.debug,
        command_executor=command_executor
    )
    
    if handle_check_commands(manager):
        return
    
    if handle_memo_commands(manager):
        return
    
    # 运行WebSocket服务器
    manager.run()


if __name__ == "__main__":
    main()
