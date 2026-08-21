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
from concurrent.futures import ThreadPoolExecutor
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
    # B 批工具
    CommandLineTool,
    SearchSkillsTool, ReloadSkillsTool, InstallSkillTool, CreateSkillTool,
    MemoryTool, MemoTool,
    UpdateIdentityTool, UpdateSoulTool, SaveMemoryAndEndConversationTool,
    SetProxyTool, GetProxyTool,
    AskUserTool,
    # 后台进程管理工具（bg_run/bg_write/bg_list/bg_output/bg_kill，worker 执行面）
    BgRunTool, BgWriteTool, BgListTool, BgOutputTool, BgKillTool,
)

# 子系统（Wave 1 + Wave 2 抽离）：命令执行器 / 提示词管理器 / 数据库 / 技能管理器 / 对话历史 / 对话压缩
from subsystems import (
    CommandExecutor, SubprocessCommandExecutor,
    PromptManager,
    MemoryDatabase, MemoDatabase,
    SkillManager,
    ConversationHistory,
    ConversationCompressor,
)



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
            skill_manager=self._get_shared_skill_manager(),
            # H3：CLI --model 为进程级覆盖，定时任务实例同样生效
            model=getattr(self.ai_manager, "model", None) if self.ai_manager else None,
            # T22：精简 profile——无 update_identity/update_soul/set_proxy，
            # 被污染的任务内容无法在无人值守时段改写人格/代理
            tool_profile=TOOL_PROFILE_TASK,
            # T22：后台进程属主隔离——任务 AI 与会话 AI 的 bg 进程互不可杀
            owner_id=f"task-{id(self):x}"
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
                    if self.ws_handler and self.broadcast_enabled:
                        await self.ws_handler.broadcast_task_status(memo, "executing", i18n.get('task_executing', memo['title'], memo['id']))
                    
                    # 【关键修复】执行前最后一次验证
                    # J1（审计 2026-08-19）：格式异常按到期执行（Q5 语义），
                    # 不再抛 ValueError 致任务既不触发也不完成、每秒重试热循环
                    if memo.get('reminder_time'):
                        try:
                            reminder_time = datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT)
                        except (ValueError, TypeError) as e:
                            self.logger.warning(
                                f"任务 #{memo['id']} 提醒时间格式异常，按到期执行: {e}")
                            reminder_time = None
                        if reminder_time is not None and reminder_time > now:
                            self.logger.warning(f"任务 #{memo['id']} 的提醒时间 ({memo['reminder_time']}) 大于当前时间 ({now_str})，跳过执行")
                            continue
                    
                    # 先标记任务已被触发
                    self.logger.info(f"标记任务 #{memo['id']} 为已触发")
                    next_time = memo_db.mark_as_triggered(memo['id'])
                    
                    # 执行任务
                    result = await self.execute_task_as_conversation(memo, memo_db)
                    
                    # 广播任务完成
                    if self.ws_handler and self.broadcast_enabled:
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
                    if self.ws_handler and self.broadcast_enabled:
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
            
            # 定义输出回调函数 - 直接广播
            def output_callback(msg_type, content):
                """AI输出的回调函数 - 直接广播"""
                # J15（审计 2026-08-19）：full_response_queue 收集后从未被消费，
                # 属死代码，已移除；广播路径保留
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

            def _set_result_if_pending(response):
                # T10：wait_for 硬超时会 cancel future，之后 run_ai 线程迟到的
                # set_result/set_exception 会抛 InvalidStateError 打进事件循环
                if not ai_future.done():
                    ai_future.set_result(response)

            def _set_exception_if_pending(exc):
                if not ai_future.done():
                    ai_future.set_exception(exc)

            def run_ai():
                try:
                    # 在单独的线程中运行 AI（独立实例，天然隔离，无并发冲突）
                    response = ai.think_and_respond(user_input, output_callback)
                    # 将结果设置到 Future 中
                    loop.call_soon_threadsafe(_set_result_if_pending, response)
                except Exception as e:
                    loop.call_soon_threadsafe(_set_exception_if_pending, e)
            
            # 启动 AI 线程
            ai_thread = threading.Thread(target=run_ai)
            ai_thread.daemon = True
            ai_thread.start()
            
            # M3（审计 2026-08-18）：任务级超时兜底。此前 await ai_future
            # 无任何超时/取消路径——AI think 卡死（LLM 挂起、子进程不可杀）
            # 时该任务永不返回，调度器 run 循环顺序 await 被阻塞，后续全部
            # 定时/即时任务不再执行且任务已 mark_as_triggered 不会重试。
            # 到点 call_later 触发 ai.cancel()（置取消事件 + 关在途 HTTP 流），
            # worker 线程自然收尾，调度循环继续。
            task_timeout = float(ConfigManager().get("scheduler.task_timeout", 600))
            timed_out = threading.Event()

            def _timeout_cancel():
                timed_out.set()
                try:
                    ai.cancel()
                except Exception:
                    pass

            timeout_handle = loop.call_later(task_timeout, _timeout_cancel)
            try:
                # T10（审计 2026-08-21）：wait_for 硬超时兜底——软取消（ai.cancel
                # 三层：取消事件/关在途 HTTP 流/杀子进程）够不到的卡死路径
                # （Windows 代理连接黑洞、requests 读超时被服务器滴流输出
                # 规避、kill 失败的进程）会让 run_ai 永不回调，await 永久挂起
                # 停摆整个调度循环且任务已 mark_as_triggered 不会重试。
                # task_timeout 到点先软取消，+30s 宽限后硬超时放行调度循环
                # （对齐 --once 的 _hard_exit 兜底语义；AI 线程为 daemon，
                # 迟到的回调经 done() 检查安全跳过）
                response = await asyncio.wait_for(
                    ai_future, timeout=task_timeout + 30)
                result = response or "AI无响应"
            except asyncio.TimeoutError:
                self.logger.error(
                    f"任务执行硬超时（{task_timeout + 30:.0f}秒，软取消未生效），"
                    f"放弃等待，调度循环继续")
                result = f"任务执行硬超时（{task_timeout + 30:.0f}秒），已放弃等待"
            except Exception as e:
                if timed_out.is_set():
                    self.logger.error(f"任务执行超时（{task_timeout}秒），AI 已取消")
                    result = f"任务执行超时（{task_timeout}秒），AI 已取消"
                else:
                    self.logger.error(f"AI对话失败: {e}")
                    result = f"任务执行但AI对话失败: {str(e)}"
            finally:
                timeout_handle.cancel()
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
                    if self.ws_handler and self.broadcast_enabled:
                        await self.ws_handler.broadcast_task_status(memo, "executing", i18n.get('immediate_memo_executing', memo['title']))
                    
                    self.logger.info(f"标记即时任务 #{memo['id']} 为已触发")
                    next_time = memo_db.mark_as_triggered(memo['id'])
                    
                    result = await self.execute_task_as_conversation(memo, memo_db)
                    
                    if self.ws_handler and self.broadcast_enabled:
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
                    
                    if self.ws_handler and self.broadcast_enabled:
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

# ConversationHistory 已抽离至 subsystems/conversation_history.py（Wave 2）


class ExpressEmotionTool(Tool):
    def __init__(self, logger=None, cancel_event: threading.Event = None):
        super().__init__(logger)
        # I 批（审计 2026-08-18）：父代理取消事件注入——情绪子代理此前
        # 用独立 client 且不可中断，父级 cancel 后仍阻塞至请求自然结束
        self.cancel_event = cancel_event

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
        # N12 + L4（审计 2026-08-18）：persona 为 LLM 可控输入。abspath 前缀
        # 检查拦截 ../ 越界，但符号链接可绕过（skills 目录内软链指向任意文件
        # 即被读取注入）；realpath 解析软链后再做前缀校验。
        skills_root = os.path.normcase(os.path.abspath(constants.SKILLS_DIR))
        resolved = os.path.normcase(os.path.realpath(path))
        if not resolved.startswith(skills_root + os.sep):
            self.logger.warning(f"人格技能名非法（越界路径）: {name}")
            return ""
        path = resolved
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
            return run_light_subagent(role, task, self.config, logger=self.logger,
                                      max_tokens=2048, cancel_event=self.cancel_event)
        except Exception as e:
            self.logger.error(f"情绪子代理执行异常: {e}")
            return f"❌ 情绪子代理执行失败: {str(e)}"

# ==================== 安装技能工具 ====================





def run_light_subagent(role, task, config, logger=None, model=None,
                       temperature=None, max_tokens=2048,
                       cancel_event: threading.Event = None) -> str:
    """轻量子代理执行：独立上下文/角色，无工具，一次 LLM 补全返回结果。

    供 express_emotion 情绪子代理使用。
    返回子代理生成的完整文本；失败时返回带 ❌ 的错误信息。

    cancel_event（I 批，审计 2026-08-18）：父代理取消事件。此前本函数用
    非流式 chat_completion，父级 cancel 只能等请求自然结束（最长 timeout，
    期间持锁阻塞）；改走 stream_chat 后由观察者线程转发 sub_client.cancel()
    强关在途 HTTP 流，用户中断可即时终止。
    """
    if cancel_event is not None and cancel_event.is_set():
        return "❌ 子代理已因父级取消而终止"
    try:
        # 幂等校验：限制 max_tokens 范围，防止意外超限大量消耗
        max_tokens = max(1, min(int(max_tokens or 2048), constants.DEFAULT_MAX_TOKENS))
        # 子代理使用独立的 LLM 客户端（可选不同模型），独立上下文，不污染主对话
        sub_client = create_llm_client(config, api_key=None, logger=logger, model=model)
        # L3：调用方 temperature > 档案级 default_temperature > 全局默认
        client_temp = getattr(sub_client, "default_temperature", None)
        temp = temperature if temperature is not None else (
            client_temp if client_temp is not None else constants.DEFAULT_TEMPERATURE)
        messages = [
            {"role": "system", "content": role},
            {"role": "user", "content": task}
        ]
        if logger is not None:
            logger.info(f"子代理执行: model={model or sub_client.model}, role={str(role)[:40]}...")

        _watch_done = threading.Event()
        if cancel_event is not None:
            def _watch_parent_cancel():
                while not _watch_done.wait(timeout=0.2):
                    if cancel_event.is_set():
                        try:
                            sub_client.cancel()
                        except Exception:
                            pass
                        return

            threading.Thread(target=_watch_parent_cancel, daemon=True,
                             name="light-subagent-cancel-watch").start()

        # I 批：改走流式（可被 cancel 强关在途 HTTP），拼接 content 事件
        parts = []
        try:
            for event in sub_client.stream_chat(messages, temperature=temp,
                                                max_tokens=max_tokens):
                if event.get("type") == "content":
                    parts.append(event.get("text") or "")
        finally:
            _watch_done.set()

        result = "".join(parts)
        if not str(result).strip():
            return "❌ 子代理执行失败：未获得有效响应"
        return str(result).strip()
    except Exception as e:
        if logger is not None:
            logger.error(f"子代理执行异常: {e}")
        return f"❌ 子代理执行失败: {str(e)}"


# 子代理结果标记：供 _handle_tool_calls 检测工具失败并剥离
_SUBAGENT_TOOL_FAILED_MARKER = "[SUBAGENT_TOOL_FAILED]\n"

def run_full_subagent(role, task, config, logger=None, model=None,
                      temperature=None, max_tokens=2048, max_turns=10,
                      output_callback=None, outcome: dict = None,
                      cancel_event=None) -> str:
    """完整子代理：拥有全部工具，独立上下文，多轮对话直到任务完成。

    子代理是完整的 DeepSeekChat 实例，拥有文件操作、命令执行、网页访问、
    技能扩展等全部工具能力。独立上下文窗口，不污染主对话。
    多轮对话（工具调用→结果→继续）直到任务完成或达到最大轮次。
    结束后自动总结对话内容，返回摘要给父代理。

    output_callback: 可选回调(msg_type, content)，用于转发子代理的实时思考过程
    （reasoning_start / reasoning / reasoning_end），不传则静默执行。

    cancel_event: 父代理取消事件（N5）。传入时子代理共享该事件并起观察者
    线程转发深度中断——父级取消（用户中断 / --once 超时）可即时终止子代理。

    供 create_subagent 工具使用。
    """
    _watch_done = threading.Event()  # N5：取消观察者线程退出信号（finally 置位）
    try:
        max_tokens = max(1, min(int(max_tokens or 2048), constants.DEFAULT_MAX_TOKENS))
        # max_turns 钳制：0/负数此前致首轮即拦截（只剩收尾报告、无任何执行）；
        # 0/空回退默认值的惯例对齐 max_tokens，下限 1 保证至少执行一个任务轮
        max_turns = max(1, int(max_turns or 10))
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

        # N5（审计 2026-08-18）：父代理取消传播。子代理此前持完全独立的
        # _cancel_event/executor/HTTP client，父级 cancel 或 --once 超时杀不到
        # 它，需等子代理自行结束。现共享父取消事件（子代理在 think 前置、
        # 工具调用间、流式事件间的既有检查点立即感知），并由观察者线程转发
        # 深度中断（sub_chat.cancel() 强关在途 HTTP 流并 kill 其子进程）。
        if cancel_event is not None:
            sub_chat._cancel_event = cancel_event
            # H1（审计 2026-08-18）：共享父事件后禁止子代理首轮 clear——
            # _think_and_respond_impl 首轮无条件 clear() 会吞掉父级已置位的
            # 取消信号（用户中断 / --once 超时恰落在子代理生成窗口时被清掉，
            # 观察者线程随之失效，子代理跑完全程）。置位后立即检查一次，
            # 父级已取消则不再启动子代理。
            sub_chat._shared_cancel_event = True
            if cancel_event.is_set():
                return "❌ 子代理已因父级取消而终止"

            def _watch_parent_cancel():
                while not _watch_done.wait(timeout=0.2):
                    if cancel_event.is_set():
                        sub_chat.cancel()
                        return

            threading.Thread(target=_watch_parent_cancel, daemon=True,
                             name="subagent-cancel-watch").start()

        # 覆盖模型（如果指定了不同模型）
        # M1：经 set_client 统一替换——同步刷新 compressor.llm_client 与
        # api_key/name 快照，避免压缩链仍用默认模型发请求
        if model:
            sub_chat.set_client(create_llm_client(config, api_key=None, logger=logger, model=model))

        # 轮次限制：防止工具调用无限循环
        # 注意：超限时不置 conversation_active=False——那会触发 think_and_respond
        # 的对话重置逻辑，清空子代理上下文，导致后续收尾报告丢失全部过程信息
        turn_count = [0]
        original_think = sub_chat.think_and_respond

        def think_with_limit(input_text, output_callback=None, is_continuation=False):
            turn_count[0] += 1
            if turn_count[0] > max_turns:
                if logger:
                    logger.warning(f"子代理达到最大轮次 {max_turns}，强制结束")
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
            logger.info(f"子代理完成，共 {turn_count[0]} 轮，请求子代理生成最终报告...")

        # 收尾报告由子代理自己生成：它拥有全部上下文（工具输出、中间结论），
        # 比外部摘要器（只能看到截断后的对话转录）更完整，也不会丢最终回答。
        # 走 original_think 绕过轮次限制：即使任务因 max_turns 被截断也能收尾。
        if _effective_output_callback is not None:
            _effective_output_callback("line", "\n📝 [子代理收尾] 生成最终报告...\n")
        wrap_up_prompt = (
            "【收尾】请不要再调用任何工具，基于以上完整执行过程，输出最终报告，包含：\n"
            "1. 任务完成情况与结论\n"
            "2. 关键过程：做了什么、重要命令/文件/数据\n"
            "3. 产物清单（如有，附绝对路径）\n"
            "4. 未解决的问题（如有）\n"
            "这份报告将原样返回给派生你的主代理，请自包含、力求完整。"
        )
        report = None
        try:
            report = original_think(wrap_up_prompt, output_callback=sub_think_callback,
                                    is_continuation=True)
        except Exception as e:
            if logger:
                logger.error(f"子代理收尾报告生成异常: {e}")

        # 透传子代理工具失败状态（供 SubAgentTool → orchestrator stats 汇总）
        if outcome is not None:
            outcome["tool_failed"] = sub_chat.stats.get("tool_failed", False)

        if report and str(report).strip():
            return str(report).strip()

        # 兜底：收尾失败时取最后一条有内容的助手消息
        for msg in reversed(sub_chat.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return f"【子代理最终回答】\n{msg['content']}"
        return "子代理未产生有效对话内容。"

    except Exception as e:
        if logger:
            logger.error(f"子代理执行异常: {e}")
        return f"❌ 子代理执行失败: {str(e)}"
    finally:
        # N5：通知取消观察者线程退出（子代理已结束，无论成败）
        _watch_done.set()


class SubAgentTool(Tool):
    """派生子代理工具 - 主代理可派生拥有全部工具和独立上下文的完整子代理执行任务。

    子代理是完整的对话实例，拥有文件操作、命令执行、网页访问、技能扩展等
    全部工具能力。独立上下文窗口（不污染主对话），多轮对话直到任务完成。
    结束后由子代理基于完整执行过程生成最终报告，返回给父代理。
    """

    def __init__(self, logger=None, cancel_event=None):
        super().__init__(logger)
        # N5：父代理（DeepSeekChat）取消事件——父级被取消（用户中断/--once
        # 超时）时传播给 run_full_subagent，即时终止子代理
        self.cancel_event = cancel_event

    def get_name(self) -> str:
        return "create_subagent"

    def _model_names_hint(self) -> str:
        """M2：从配置读取可用模型档案名，注入工具描述供 LLM 选择。

        此前 LLM 不知道有哪些合法档案名，幻觉出的名字走裸名语义
        （默认端点 + 错误模型名），运行时 API 400。models 节为空时
        返回空串，工具描述保持原样（零破坏）。
        """
        try:
            models = self.config.get("models") or {}
            if not isinstance(models, dict) or not models:
                return ""
            default_name = self.config.get("api.default_model")
            names = [f"{n}（默认）" if n == default_name else str(n) for n in models]
            return f"可用模型档案：{', '.join(names)}"
        except Exception:
            return ""

    def get_description(self) -> str:
        hint = self._model_names_hint()
        model_line = "- model：子代理使用的模型（可选，默认与主代理一致）"
        if hint:
            model_line += f"。{hint}，请从中选择，不要编造其他名称"
        return f"""派生子代理执行任务。子代理拥有全部工具能力和独立的上下文窗口，不会污染主对话上下文。
适用场景：需要多步骤操作的任务（如文件处理、命令执行、信息检索）、需要独立上下文的复杂任务。
子代理可进行多轮工具调用，任务完成后由子代理基于完整执行过程生成最终报告（结论/关键过程/产物清单/未解决问题）返回。
参数说明：
- role：子代理的角色设定（作为系统提示词），如"你是一位资深代码审查专家"
- task：要交给子代理执行的具体任务
{model_line}
- temperature：采样温度（可选，默认使用主配置）
- max_tokens：最大输出 token 数（可选，默认 2048）
- max_turns：最大对话轮次（可选，默认 10，防止无限循环）"""

    def get_parameters(self) -> Dict:
        model_desc = "子代理使用的模型名称，默认与主代理一致"
        hint = self._model_names_hint()
        if hint:
            model_desc += f"。{hint}，请从中选择"
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
                    "description": model_desc
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
        outcome: dict = {}
        result = run_full_subagent(role, task, self.config, logger=self.logger,
                                   model=model, temperature=temperature,
                                   max_tokens=max_tokens, max_turns=max_turns,
                                   output_callback=self._subagent_output,
                                   outcome=outcome,
                                   cancel_event=self.cancel_event)
        # 冲刷子代理回答缓冲（最后一段 chunk 可能尚未发出）
        self._flush_subagent_answer()
        # 子代理内部工具失败 → 打上标记，由 _handle_tool_calls 检测并置 orchestrator stats
        if outcome.get("tool_failed"):
            result = _SUBAGENT_TOOL_FAILED_MARKER + result
        return result


TOOL_PROFILE_ORCHESTRATOR = "orchestrator"
TOOL_PROFILE_WORKER = "worker"
# T22（审计 2026-08-21）：定时任务精简 profile——任务内容可源自 LLM 生成物
# （备忘录标题/正文可能被网页内容污染），无人值守时段不持有改写全局人格
# 提示词（update_identity/update_soul）与全局网络面（set_proxy）的工具
TOOL_PROFILE_TASK = "task"

# 仅 worker 子代理注册的执行面工具；编排者误调时拦截并纠正
WORKER_ONLY_TOOLS = {"run_command", "read_file", "write_file", "list_files", "web", "calculator",
                     "bg_run", "bg_write", "bg_list", "bg_output", "bg_kill"}

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
                 tool_profile: str = TOOL_PROFILE_ORCHESTRATOR, enable_memory: bool = True,
                 model: str = None, temperature: float = None,
                 owner_id: str = None):
        self.config = ConfigManager()
        self.logger = Logger("DeepSeek", debug_level if debug_level is not None else Logger("").level)
        self.debug_level = self.logger.level
        # 工具集 profile：决定本实例注册哪套工具（orchestrator 管理面 / worker 执行面）
        self.tool_profile = tool_profile
        # T22：后台进程属主标识——bg_* 工具携带，ProcessManager 据此隔离
        # kill/write 权限（会话/任务/子代理互不可操作他人进程，观察面共享）
        self.owner_id = owner_id
        # 大模型客户端（抽象层，支持多厂商切换）；model 显式传入时覆盖配置默认
        self.client = create_llm_client(self.config, api_key=api_key, logger=self.logger, model=model)
        self.api_key = self.client.api_key
        self.name = self.client.name
        # temperature 显式传入时覆盖 constants 默认（一次性 CLI 可指定更确定性的采样）
        # L3：CLI/调用方 temperature > 档案级 default_temperature > 全局默认
        self.temperature = temperature if temperature is not None else (
            self.client.default_temperature
            if getattr(self.client, "default_temperature", None) is not None
            else constants.DEFAULT_TEMPERATURE)
        self.history_manager = history_manager
        # enable_memory=False 时置空，注册层（_register_tools）依此跳过 MemoryTool，
        # 使记忆工具 schema 不出现在请求中，LLM 无从调用——干净禁用而非运行期拦截
        self.memory_db = memory_db if enable_memory else None
        self.memo_db = memo_db
        self.prompt_manager = prompt_manager or PromptManager()
        self.history_loaded = False
        self.conversation_active = True
        self.messages = []
        # 共享技能管理器：传入时复用（避免每个任务重复扫描技能），否则按需创建
        self._shared_skill_manager = skill_manager
        
        # 添加对话压缩器（注入 llm 客户端而非 AI 实例，避免循环引用）
        self.compressor = ConversationCompressor(self.client, self.logger)
        
        self.max_history = max_history or constants.DEFAULT_MAX_HISTORY
        self.preserve_system = constants.PRESERVE_SYSTEM_PROMPTS
        self.stats = {"api_calls": 0, "tool_calls": 0, "errors": [], "tool_failed": False}
        
        # 取消机制：threading.Event 用于跨线程信号，HTTP 中断由客户端抽象层负责
        self._cancel_event = threading.Event()
        self._command_executor = command_executor  # 保存命令执行器引用，用于取消时kill子进程
        # 消息锁：保护 self.messages 免受 WebSocket 线程与调度器线程并发修改导致对话历史污染
        self._msg_lock = threading.RLock()

        self._init_system_prompts(system_prompt)

        self.tool_registry = ToolRegistry(self.logger)
        self._register_tools(exit_callback, command_executor)

    def set_client(self, client) -> None:
        """M1：统一替换 LLM 客户端（换模型/换档案），同步刷新派生引用。

        直接赋值 self.client 会让 ConversationCompressor 等持有旧 client 的
        组件继续用默认模型发请求（压缩链与实际对话模型不一致）。本方法
        一并更新 compressor.llm_client 与 api_key/name 快照。
        """
        self.client = client
        self.api_key = client.api_key
        self.name = client.name
        self.compressor.llm_client = client
    
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
    
    def _reasoning_enabled(self) -> bool:
        """思考模式开关：client.reasoning_enabled 非 None 时优先（档案级构造参数），
        否则回退全局配置；getattr 兼容未实现该契约的自定义 client"""
        val = getattr(self.client, "reasoning_enabled", None)
        return val if val is not None else constants.REASONING_ENABLED

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
                # 后台进程管理（注册表全局共享；T22：owner 隔离写/杀权限，
                # 观察面 list/output 仍全局可见）
                BgRunTool(self.logger, owner=self.owner_id),
                BgWriteTool(self.logger, owner=self.owner_id),
                BgListTool(self.logger),
                BgOutputTool(self.logger),
                BgKillTool(self.logger, owner=self.owner_id),
            ]
            self.tool_registry.register_many(worker_tools, is_builtin=True)
            self.logger.info(f"worker 子代理已注册 {len(worker_tools)} 个执行面工具")
            return

        # orchestrator（主对话）：管理面工具，执行类任务经 create_subagent 派生 worker
        # T22：task profile 为 orchestrator 子集——剔除改写全局人格提示词与
        # 全局网络面的工具（定时任务无人值守，任务内容可能被污染）
        is_task_profile = self.tool_profile == TOOL_PROFILE_TASK
        builtin_tools = [
            GetCurrentTimeTool(self.logger),
        ]

        if self.memory_db:
            builtin_tools.append(MemoryTool(self.memory_db, self.logger))

        if self.memo_db:
            builtin_tools.append(MemoTool(self.memo_db, self.logger))

        if not is_task_profile:
            # 更新身份工具
            builtin_tools.append(UpdateIdentityTool(self.prompt_manager, self.logger))

            # 新增：更新智能体自我认识工具
            builtin_tools.append(UpdateSoulTool(self.prompt_manager, self.logger))
        
        # 增强版保存记忆并结束对话工具
        if exit_callback and self.memory_db:
            builtin_tools.append(SaveMemoryAndEndConversationTool(exit_callback, self.memory_db, self.prompt_manager, self.logger))

        # 添加询问用户的工具
        # I 批：ExpressEmotionTool 注入父取消事件（情绪子代理可被中断）
        builtin_tools.append(AskUserTool(self.logger))
        builtin_tools.append(ExpressEmotionTool(self.logger, cancel_event=self._cancel_event))
        
        # 添加技能热重载工具 & 技能安装指引工具 & 创建技能工具
        builtin_tools.append(ReloadSkillsTool(self.tool_registry, self.logger))
        builtin_tools.append(InstallSkillTool(self.logger))
        builtin_tools.append(CreateSkillTool(self.logger))
                
        if not is_task_profile:
            # T22：set_proxy 是全局持久化网络面，任务 AI 不持有
            builtin_tools.append(SetProxyTool(self.logger))
        builtin_tools.append(GetProxyTool(self.logger))

        # 子代理工具：主代理可派生独立上下文/角色的轻量子代理执行任务
        # cancel_event 注入（N5）：父级取消传播到子代理
        builtin_tools.append(SubAgentTool(self.logger, cancel_event=self._cancel_event))

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
        
        # N2 修复（审计 2026-08-18）：drain 线程改为整个工具批次仅创建一个，
        # 由独立 stop 事件控制生命周期，批次结束（正常/异常/中断）在 finally
        # 中统一置位、join 并排空残余。此前每次工具调用都启动一个仅靠 cancel
        # 事件退出的常驻线程（注释声称"队列空自动退出"，循环条件却从未检查
        # 队列是否为空），闭包长期持有 output_callback（含 websocket 引用）
        # 阻碍 GC，常驻服务下线程与内存持续累积。
        drain_stop = threading.Event()
        # T12：drain 线程与工具调用后的排空循环并发消费同一队列，complete
        # 标记可能被任一方取走——用可变容器共享，两路见到都置位，供
        # has_stream_output 判定流式工具（流式已逐行转发，勿重复整发结果）
        saw_complete = [False]

        def _drain_queue(q=output_queue, cb=output_callback, stop_event=drain_stop,
                         flag=saw_complete):
            """持续排空 output_queue 并转发 line 输出（长时间工具执行期间防 WS 空闲断线）"""
            while not stop_event.is_set():
                try:
                    msg_type, content = q.get(timeout=0.1)
                    if msg_type == "complete":
                        flag[0] = True
                    elif cb and msg_type == "line":
                        cb("line", str(content) if content is not None else "")
                except queue.Empty:
                    continue

        drain_thread = threading.Thread(target=_drain_queue, daemon=True,
                                        name="tool-output-drain")
        drain_thread.start()

        try:
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
                if self.tool_profile in (TOOL_PROFILE_ORCHESTRATOR, TOOL_PROFILE_TASK) and tool_name in WORKER_ONLY_TOOLS:
                    # 编排者/任务 AI 不持有执行面工具：拦截并纠正，引导改用 create_subagent
                    result_raw = (
                        f"⛔ '{tool_name}' 调用被拦截：你是编排者，没有执行面工具（命令/文件/网页由 worker 子代理持有）。\n"
                        "请改用 create_subagent 派生子代理执行该任务：在 role 中写明执行者身份，"
                        "在 task 中写清目标、具体步骤与验收标准（任务描述须自包含，子代理无法向你提问）。"
                    )
                    self.logger.warning(f"编排者误调执行面工具 {tool_name}，已拦截并引导改用 create_subagent")
                else:
                    # T12：每个工具调用前重置流式标志（drain 线程与下方排空
                    # 循环并发消费，任一方见到 complete 都会置位）
                    saw_complete[0] = False
                    try:
                        result_raw = self.tool_registry.execute_tool(tool_name, **args)
                        # 排空队列中剩余标记（complete 标记供 has_stream_output 判定、
                        # 残余 line 补发）——与 drain 线程并发消费安全（每项仅被消费一次）
                        while not output_queue.empty():
                            try:
                                msg_type, content = output_queue.get_nowait()
                                if msg_type == "complete":
                                    saw_complete[0] = True
                                elif output_callback and msg_type == "line":
                                    output_callback("line", str(content) if content is not None else "")
                            except queue.Empty:
                                break
                    except Exception as e:
                        result_raw = f"❌ 工具 {tool_name} 执行异常: {str(e)}"
                        self.logger.error(f"工具 {tool_name} 执行异常: {e}")
                        self.stats["tool_failed"] = True

                # 子代理内部工具失败透传：SubAgentTool 在结果前缀打标记，此处检测并置 stats
                if tool_name == "create_subagent" and result_raw and \
                        str(result_raw).startswith(_SUBAGENT_TOOL_FAILED_MARKER):
                    self.stats["tool_failed"] = True
                    result_raw = str(result_raw)[len(_SUBAGENT_TOOL_FAILED_MARKER):]

                # 安全转换并清理结果
                result = self._sanitize_tool_result(result_raw)

                # T12：流式工具的 line 输出已由 drain 线程逐行转发（registry 侧
                # N8 预算已生效），勿再把完整 result 整发——此前排空后判定
                # output_queue.empty() 恒为 False，导致重复推送 + 超长结果绕过预算
                has_stream_output = saw_complete[0]

                # 长度预算前置：超长结果先替换为提示，再决定是否推送，
                # 避免全量 result 经非流式直发路径绕过 N8 输出限制（T12）
                if len(result) > MAX_RESULT_LENGTH:
                    result = f"⚠️ 工具调用结果长度超过 {MAX_RESULT_LENGTH} 字符，已丢弃。请使用更精确的查询获取所需信息。"

                # 普通（非流式）工具没有队列输出，直接发送（已截断的）结果
                if not has_stream_output and output_callback and result:
                    output_callback("line", result + "\n")

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
        finally:
            # 停止 drain 线程并回收（线程 0.1s 轮询粒度内感知置位）
            drain_stop.set()
            drain_thread.join(timeout=2)
            # 排空残余输出（drain 停止后无并发消费者，残余 line 补发）
            while True:
                try:
                    msg_type, content = output_queue.get_nowait()
                    if output_callback and msg_type == "line":
                        output_callback("line", str(content) if content is not None else "")
                except queue.Empty:
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
        # 首轮开始时清除取消标志（H1：共享父事件的子代理不清除——清除会
        # 吞掉父级已置位的取消信号，使中断/--once 超时失效）
        if not is_continuation and not getattr(self, "_shared_cancel_event", False):
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

                # 压缩提示走 output_callback（line 类型），由回调端决定去向：
                # WebSocket 模式 → 前端过程区；CLI once 模式 → stderr（不污染 stdout 管道）
                if output_callback:
                    output_callback("line", f"\n📦 {i18n.get('conversation_compress')}...\n   触发原因: {reason}\n")
                else:
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
            if self._reasoning_enabled():
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
                temperature=self.temperature,
                max_tokens=constants.DEFAULT_MAX_TOKENS,
                tools=tools,
                reasoning_enabled=self._reasoning_enabled(),
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
        self._processing_tasks = {}  # websocket -> {asyncio.Task}（N14：集合登记，支持并发多任务）
        self._shared_skill_manager = None  # 共享技能管理器（懒加载，供所有连接复用）
        # I 批（审计 2026-08-18）：/new、/reload_prompts 等命令专用线程池——
        # 此前 run_in_executor(None) 与 AI think 共用默认池，繁忙时 reset 任务
        # 排队等锁，多连接并发 /new 可耗尽默认池（阻塞新消息处理）
        self._cmd_executor = ThreadPoolExecutor(max_workers=2,
                                                thread_name_prefix="ws-cmd")
        # J7（审计 2026-08-19）：broadcast 单客户端发送超时（半开 TCP 防卡死）
        self.broadcast_send_timeout = 5.0

    def shutdown(self):
        """I 批：释放命令专用线程池（服务退出时调用）"""
        if self._cmd_executor is not None:
            self._cmd_executor.shutdown(wait=False)
            self._cmd_executor = None
    
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
            skill_manager=self._get_shared_skill_manager(),
            # H3：CLI --model 进程级覆盖传导到每个会话实例
            model=getattr(self.ai_manager, "model", None)
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
        # N13：discard 替代 remove——并发注销/重复注销时 remove 抛 KeyError
        # 会逃逸到调用方（如 broadcast 的 except 分支），discard 幂等
        self.connected_clients.discard(websocket)
        # R3（审计 2026-08-20）：取消该连接仍在进行的处理任务并主动关闭
        # websocket。broadcast 超时路径（J7）也会走到这里——ping 禁用下
        # 半开 TCP 的接收循环对断开无感知，不 close 的话连接对象/协程/
        # 任务登记泄漏直至进程重启；与 handle_connection finally 对齐
        pending = self._processing_tasks.pop(websocket, set())
        for t in pending:
            if not t.done():
                t.cancel()
        if websocket in self.client_sessions:
            session = self.client_sessions.pop(websocket)
            # 释放该连接独立的AI实例资源
            ai = session.get("ai")
            if ai:
                try:
                    ai.cancel()
                except Exception:
                    pass
        # close 幂等（已关闭连接重复调用无副作用）；mock/异常对象兜底
        try:
            await websocket.close()
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
        """广播消息到所有客户端。

        J7（审计 2026-08-19）：此前串行 await 且 send_message 吞掉全部异常，
        broadcast 的 except→unregister 为死代码——ping 禁用（ping_interval=None）
        下半开 TCP 无法探测，backpressure 下 websocket.send 可无限期阻塞，
        卡死广播与调度器循环。现改为每客户端并发发送 + wait_for 超时，
        超时即 unregister 回收（discard 幂等）。
        """
        clients = list(self.connected_clients)
        if not clients:
            return

        async def _send_one(websocket):
            try:
                await asyncio.wait_for(
                    self.send_message(websocket, message),
                    timeout=self.broadcast_send_timeout)
            except Exception:
                # 超时/发送异常：回收该连接（取消事件被 wait_for 传播，
                # CancelledError 为 BaseException 不在此列，不影响任务取消语义）
                await self.unregister(websocket)

        await asyncio.gather(*(_send_one(ws) for ws in clients),
                             return_exceptions=True)
    
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
        # 取消当前连接独立AI实例的对话（线程安全，只set Event + close连接 + kill进程）；
        # ai.cancel() 已同时终止该会话自身 executor 中的子进程。
        # R2（审计 2026-08-20）：移除对 ai_manager.command_executor（CLI 主 AI
        # 执行器）的全局取消——会话隔离架构下单实例时代的遗留，WS 用户中断
        # 会误杀终端用户正在执行的命令；对会话自身亦冗余
        session = self._get_session(websocket)
        ai = session.get("ai") if session else None
        if ai:
            ai.cancel()
    
    async def handle_task_message(self, websocket, data):
        """处理任务相关消息 - 简化版，所有任务都是对话任务"""
        # W1（审计 2026-08-20）：字段类型规整——非 str 此前在 .strip() 抛
        # AttributeError，落顶层兜底以笼统"处理失败"收场
        action = data.get("action", "")
        if not isinstance(action, str):
            action = ""
        action = action.strip()
        
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
            
            # W1（审计 2026-08-20）：title 传 dict 等类型此前直达 sqlite 抛
            # InterfaceError（J1 except ValueError 拦不住）
            if not isinstance(title, str) or not title.strip():
                await self.send_message(websocket, {
                    "type": "error",
                    "content": "需要提供任务标题"
                })
                return
            if content is not None and not isinstance(content, str):
                await self.send_message(websocket, {
                    "type": "error",
                    "content": "任务内容必须是字符串"
                })
                return

            # W2（审计 2026-08-20）：delay 无校验——非数字 TypeError、负数立即
            # 执行、10^18 OverflowError。float 转换 + 钳制 [0, 10 年]
            try:
                delay = float(delay)
            except (TypeError, ValueError):
                await self.send_message(websocket, {
                    "type": "error",
                    "content": f"任务延迟必须是数字（秒）: {delay!r}"
                })
                return
            delay = min(max(delay, 0.0), 10 * 365 * 86400)

            reminder_time = None
            if is_immediate:
                reminder_time = datetime.now().strftime(constants.DATETIME_FORMAT)
            else:
                reminder_time = (datetime.now() + timedelta(seconds=delay)).strftime(constants.DATETIME_FORMAT)
            
            # J1（审计 2026-08-19）：add_memo 入库校验抛 ValueError（畸形时间/
            # 重复间隔等），此处转为友好错误消息，不让 handle_message 顶层
            # 兜底以笼统的"处理失败"收场
            try:
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
            except ValueError as e:
                await self.send_message(websocket, {
                    "type": "error",
                    "content": f"任务添加失败: {e}"
                })
                return
            
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
            else:
                # W3（审计 2026-08-20）：缺 task_id 此前静默无响应
                await self.send_message(websocket, {
                    "type": "error",
                    "content": i18n.get('memo_complete_missing_id')
                })
        else:
            # J15（审计 2026-08-19）：未知 action 此前静默吞掉，客户端无任何反馈
            await self.send_message(websocket, {
                "type": "error",
                "content": f"未知的任务操作: {action}"
            })
    
    async def handle_command(self, websocket, data):
        """处理命令消息"""
        command = data.get("command", "")
        if not isinstance(command, str):  # W1（审计 2026-08-20）：非 str 字段规整
            return
        command = command.strip()
        
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
                # J11（审计 2026-08-19）：取消后旧回合可能仍在收尾（回发内容），
                # 且新消息的 think 线程可能与 reset 无序抢 _msg_lock（RLock 无
                # 公平性）——等待该连接处理标志清零后再排队 reset，避免新回合
                # 在旧上下文上执行后被 reset 抹掉（消息"消失"）
                for _ in range(20):
                    if not session or not session.get("is_processing"):
                        break
                    await asyncio.sleep(0.1)
                # 重置对话状态，重新加载系统提示词
                # N3：reset_conversation 内部同步获取 _msg_lock（繁忙对话持锁
                # 可达数十秒），直接调用会冻结整个事件循环（所有连接的心跳/
                # 消息收发全部停摆）。移入线程池执行，事件循环仅等待结果。
                loop = asyncio.get_running_loop()

                def _do_reset():
                    ai.reset_conversation(reload_prompts=True)
                    # 重新初始化提示词管理器（确保最新）
                    self.ai_manager.prompt_manager.reload()

                await loop.run_in_executor(self._cmd_executor, _do_reset)
                # 重置该连接对话进行中标记
                if session:
                    session["dialogue_in_progress"] = False
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
                # R1（审计 2026-08-20）：畸形 reminder_time 此前裸 strptime 抛
                # ValueError → /memos 整体落入顶层兜底"处理失败"（J1 加固漏点）。
                # 解析失败按"待执行"展示，对齐 CLI --memo-list 的既有模式
                status = i18n.get('memo_pending')
                if memo['reminder_time']:
                    try:
                        if datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT) < datetime.now():
                            status = i18n.get('memo_overdue')
                    except (ValueError, TypeError):
                        pass
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
            # W4（审计 2026-08-20）：空关键词此前 LIKE %% 全表兜底刷屏，回用法提示
            if not query:
                await self.send_message(websocket, {
                    "type": "command_result",
                    "content": i18n.get('search_usage')
                })
                return
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
            # W5（审计 2026-08-20）：reload 经 _load_config 重建 _config，会静默
            # 丢弃 CLI 进程级覆盖（--debug/--port/--no-scheduler 等）——此处重放
            # main() 暂存在 config._cli_overrides 的覆盖
            overrides = getattr(self.ai_manager.config, "_cli_overrides", None)
            note = ""
            if overrides:
                for section, kv in overrides.items():
                    target = self.ai_manager.config._config.get(section)
                    if isinstance(target, dict):
                        target.update(kv)
                note = i18n.get('config_reloaded_cli_kept')
            await self.send_message(websocket, {
                "type": "command_result",
                "content": i18n.get('config_reloaded') + note
            })
            
        elif command == "/reload_prompts":
            session = self._get_session(websocket)
            ai = session.get("ai") if session else None
            # M1（审计 2026-08-18）：reload_prompts 内部同步获取 _msg_lock
            # （繁忙对话持锁可达数十秒），直接调用会冻结整个事件循环——
            # N3 只修了 /new，这里同类遗漏。移入线程池执行。
            loop = asyncio.get_running_loop()

            def _do_reload_prompts():
                if ai:
                    return ai.reload_prompts()
                return self.ai_manager.prompt_manager.reload()

            new_count = await loop.run_in_executor(self._cmd_executor, _do_reload_prompts)
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
        content = data.get("content", "")
        if not isinstance(content, str):  # W1（审计 2026-08-20）：非 str 字段规整
            return
        content = content.strip()
        
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
            self.logger.debug(f"输出回调被调用: {msg_type}, 内容长度: {len(content)}")
            
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
    
    def __init__(self, debug_level: int = None, load_history: bool = None, command_executor: CommandExecutor = None,
                 model: str = None):
        self.ai: Optional[DeepSeekChat] = None
        self.dialogue_history: List[Dict] = []
        self.config = ConfigManager()
        self.command_executor = command_executor or SubprocessCommandExecutor()
        self.prompt_manager = PromptManager()  # 创建提示词管理器
        self.scheduler: Optional[TaskScheduler] = None  # 任务调度器
        # H3：CLI --model 进程级覆盖（档案名或裸模型名），传导到本进程创建的所有 AI 实例
        # （主对话 add_ai / WebSocket 会话 / 定时任务），此前服务模式该参数被静默忽略
        self.model = model

        if debug_level is None:
            debug_level = self.config.get("system.debug_level", 1)

        self.logger = Logger("Manager", debug_level)
        if model:
            self.logger.info(f"CLI 模型覆盖已启用: {model}")
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
            prompt_manager=self.prompt_manager,
            model=self.model  # H3：--model 进程级覆盖
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
        try:
            # J1（审计 2026-08-19）：register 移入 try——此前在 try 外，握手期
            # 异常（如任务展示路径解析失败）逃逸时 finally 不执行，会话登记
            # 永久泄漏；unregister 为 discard 幂等，提前失败也会被正常清理
            await self.ws_handler.register(websocket)
            async for message in websocket:
                # 【关键修复】用create_task异步处理消息，不await阻塞接收循环
                # 这样当AI在后台生成响应时，interrupt消息仍能被立即接收和处理
                task = asyncio.create_task(
                    self.ws_handler.handle_message(websocket, message)
                )
                # N14：按连接登记任务集合。此前按 ws id 覆盖式登记，同连接并发
                # 多任务时先完成任务的 done_callback 会误 pop 掉后续任务的登记
                tasks = self.ws_handler._processing_tasks.setdefault(websocket, set())
                tasks.add(task)

                def _cleanup_task(t, ws=websocket):
                    pending = self.ws_handler._processing_tasks.get(ws)
                    if pending is not None:
                        pending.discard(t)
                task.add_done_callback(_cleanup_task)
        except websockets.exceptions.ConnectionClosed as e:
            self.logger.info(f"连接关闭: {e}")
        except Exception as e:
            self.logger.error(f"处理错误: {e}")
        finally:
            # 连接关闭时取消该连接所有仍在进行的处理任务（N14：此前仅最后一个）
            pending = self.ws_handler._processing_tasks.pop(websocket, set())
            for t in pending:
                if not t.done():
                    t.cancel()
            await self.ws_handler.unregister(websocket)

    async def websocket_server(self):
        """启动WebSocket服务器并集成任务调度器"""
        host = constants.WEBSOCKET_HOST
        port = constants.WEBSOCKET_PORT
        
        self.ws_handler = WebSocketHandler(self)
        # M2（审计 2026-08-18）：技能扫描对每个可执行技能串行跑 --help
        # 子进程（各 ≤10s），懒加载于首条消息/首个任务时在事件循环线程
        # 执行会冻结整个服务。启动前预热，把扫描成本移到连接建立之前。
        self.ws_handler._get_shared_skill_manager()
        
        # 如果启用了任务调度器，创建并启动
        if constants.SCHEDULER_ENABLED:
            self.scheduler = TaskScheduler(asyncio.get_running_loop(), self.logger)
            self.scheduler.set_websocket_handler(self.ws_handler)
            self.scheduler.set_ai_manager(self)  # 设置AI管理器引用
            self.scheduler._get_shared_skill_manager()
        
        async def handler(websocket):
            await self.handle_connection(websocket, None)
        
        # ping_interval=None：禁用服务端协议层主动 Ping。
        # websockets 库默认 ping_interval=20s / ping_timeout=20s，服务端每 20s 发协议层
        # Ping 帧，20s 内收不到 Pong 即强制断连。经 WAF→gateway 两层代理转发后 Pong 无法
        # 可靠回传，导致连接约 20s 被服务端关闭。前端已通过 25s 应用层心跳（{type:'ping'}）
        # 保活，故此处完全禁用服务端协议层 ping。
        # T1（审计 2026-08-21，产品决策：仅告警不加鉴权）：绑定非回环地址时
        # WebSocket 端点（经工具链可达任意命令执行）即暴露给网络内任意
        # 客户端，启动时显著告警提醒确认网关/WAF 接入与网络边界
        _h = (host or "").strip().lower()
        if _h not in ("", "localhost", "127.0.0.1", "::1", "[::1]"):
            for _ in range(3):
                self.logger.warning(
                    f"⚠️ WebSocket 绑定在 {host}:{port}（非回环地址）：任何能建立 "
                    f"TCP 连接的客户端都可使用本智能体（含命令执行能力），"
                    f"请确认已置于可信网络或有网关/WAF 认证保护")
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
            # I 批：释放命令专用线程池
            self.ws_handler.shutdown()
    
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
            # W6（审计 2026-08-20）：启动/运行失败此前打印堆栈后以退出码 0
            # 收场，supervisor/脚本误判成功
            sys.exit(1)


# ==================== 命令行参数处理 ====================

def _port_number(value):
    """W6（审计 2026-08-20）：--port argparse 类型校验，非法值启动前即报错"""
    try:
        iv = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"端口必须是整数: {value!r}")
    if not (1 <= iv <= 65535):
        raise argparse.ArgumentTypeError(f"端口必须在 1~65535 之间: {iv}")
    return iv


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
    parser.add_argument('--port', type=_port_number, help='WebSocket服务器端口')
    
    # 调度器选项
    parser.add_argument('--no-scheduler', action='store_true', help='禁用任务调度器')
    parser.add_argument('--scheduler-interval', type=float, default=1.0, help='调度器检查间隔（秒）')

    # 一次性对话模式（CLI 单次调用：执行 QUERY 后退出，不启动 WebSocket）
    parser.add_argument('--once', type=str, metavar='QUERY', help='一次性对话：执行 QUERY 并输出结果后退出')
    parser.add_argument('--quiet', action='store_true', help='静默模式：仅 stdout 输出回答正文，过程行/思考全静默（适合管道）')
    parser.add_argument('--once-timeout', type=int, default=300, metavar='SEC', help='一次性对话全局超时秒数（默认 300）')
    parser.add_argument('--no-save-memory', action='store_true', help='一次性对话不注册记忆工具，禁止写入记忆数据库')
    parser.add_argument('--show-reasoning', action='store_true', help='一次性对话在 stderr 显示思考过程流（默认不显示）')
    parser.add_argument('--worker-only', action='store_true', help='一次性对话以 worker 纯执行面模式运行（7 种执行工具，无记忆/人格/派生）')
    parser.add_argument('--model', type=str, metavar='NAME', help='覆盖配置中的模型：档案名（models 节，如 kimi）或裸模型名（同端点换模型）；歧义时可用 model:NAME 强制裸名 / profile:NAME 强制档案。服务模式与 --once 均生效（进程级覆盖）')
    parser.add_argument('--temperature', type=float, metavar='T', help='覆盖默认采样温度（0.0~2.0，越低越确定）')

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
            # W12（审计 2026-08-20）："|x" 空标题此前直接入库；"a|b|" 尾部空段
            # 以 "" 传入绕过 J1 时间校验——空串规整为 None，空标题由 db 层拦
            title = parts[0].strip()
            content = parts[1] if len(parts) > 1 else ""
            reminder = (parts[2].strip() or None) if len(parts) > 2 else None
            
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


def handle_once_mode(manager, args) -> bool:
    """一次性对话模式：命中 --once 则执行单次 AI 对话并以退出码结束进程。

    设计要点：
    - stdout 仅输出 AI 回答正文（chunk 流式），line/reasoning 过程行走 stderr，管道安全
    - 退出码：0 成功 / 2 API 或链路失败 / 3 用户中断(Ctrl+C) / 4 全局超时
    - 复用三层中断机制（Event + HTTP 关闭 + 子进程 kill）：SIGINT 与超时 Timer 均走 cancel()
    - 状态隔离：不加载对话历史、不挂全局退出回调（与 TaskScheduler 任务实例同款隔离）
    """
    if not args.once:
        return False

    # worker 模式注入精简 system prompt：纯执行面，不加载人格/身份/技能提示词
    # （与 run_full_subagent 的子代理语义对齐：执行专注、结果导向、产物落盘 workspace）
    worker_system_prompt = None
    if args.worker_only:
        worker_system_prompt = (
            "你是一个任务执行器。你只有执行工具（命令行/文件/网页/计算/时间），"
            "没有记忆、人格、派生、提问能力。\n"
            "规则：\n"
            "1. 直接执行任务，不要提问、不要寒暄、不要情绪表达。\n"
            "2. 任务完成后立即给出最终回答，不要继续调用工具。\n"
            "3. 产物文件一律写入 ~/.aibox/workspace/ 并在回答中给出绝对路径。\n"
            "4. 无法完成时，在最终回答中明确说明原因与已完成的部分。"
        )

    # 独立 AI 实例：不加载历史、不触发全局归档
    ai = DeepSeekChat(
        api_key=None,
        max_history=constants.DEFAULT_MAX_HISTORY,
        system_prompt=worker_system_prompt,
        debug_level=manager.logger.level,
        exit_callback=None,          # AI 主动结束对话时不触发全局归档/重置
        history_manager=None,        # 单次模式不加载/不写对话历史
        memory_db=manager.memory_db,
        memo_db=manager.memo_db,
        command_executor=manager.command_executor,
        prompt_manager=manager.prompt_manager,
        enable_memory=not args.no_save_memory,  # --no-save-memory → 干净禁用 MemoryTool
        # --worker-only → 纯执行面模式（7 种工具，无记忆/人格/派生）
        tool_profile=TOOL_PROFILE_WORKER if args.worker_only else TOOL_PROFILE_ORCHESTRATOR,
        model=args.model,                # --model 覆盖配置模型
        temperature=args.temperature,    # --temperature 覆盖默认采样温度
    )
    manager.ai = ai  # 挂到 manager 上，cancel() 可达

    state = {"exit_code": 0, "interrupted": False, "timed_out": False}

    def cli_callback(msg_type, content):
        """AI 流式输出回调：chunk→stdout（权威内容），其余→stderr（过程元信息）。"""
        try:
            if msg_type == "chunk":
                sys.stdout.write(content)
                sys.stdout.flush()
            elif msg_type == "complete":
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif msg_type == "error":
                sys.stderr.write(str(content) + "\n")
                sys.stderr.flush()
                state["exit_code"] = 2
            elif msg_type == "line":
                if not args.quiet:
                    sys.stderr.write(str(content))
                    sys.stderr.flush()
            elif msg_type in ("reasoning", "reasoning_start", "reasoning_end"):
                # thinking 流默认静默，仅 --show-reasoning 显式开启（且非 --quiet）
                if args.show_reasoning and not args.quiet:
                    sys.stderr.write(str(content))
                    sys.stderr.flush()
            # ai_break / dialogue_ended：CLI 无 widget 概念，忽略
        except (BrokenPipeError, OSError):
            # 下游管道提前关闭（如 | head -1）：终止 AI，避免无效消耗
            ai.cancel()

    # Ctrl+C → 三层取消（与 WebSocket interrupt 路径等价），随后以退出码 3 结束
    def _sigint_handler(sig, frame):
        state["interrupted"] = True
        manager.cancel()
    signal.signal(signal.SIGINT, _sigint_handler)

    # 全局超时：守护 Timer 到点 cancel，随后以退出码 4 结束
    def _on_timeout():
        state["timed_out"] = True
        manager.cancel()
        # I 批（F9，审计 2026-08-18）：软超时兜底——cancel 后若 AI 卡死在
        # 不可中断路径（HTTP 请求空窗、忽略信号的子进程），think 可能永不
        # 返回、进程挂起（退出码 4 永远到不了）。宽限一个超时周期后强杀进程，
        # 保证 --once 有硬性结束语义
        def _hard_exit():
            print(f"\n⏰ 一次性对话强制超时（{args.once_timeout * 2} 秒），已终止",
                  file=sys.stderr)
            sys.stderr.flush()
            os._exit(4)

        hard_timer = threading.Timer(max(1, args.once_timeout), _hard_exit)
        hard_timer.daemon = True
        hard_timer.start()
    timer = threading.Timer(max(1, args.once_timeout), _on_timeout)
    timer.daemon = True
    timer.start()

    try:
        response = ai.think_and_respond(args.once, cli_callback)
        # 未走 error 回调但仍无有效回答时，视为链路失败
        if state["exit_code"] == 0 and not state["interrupted"] and not state["timed_out"]:
            if response is None or (isinstance(response, str) and not response.strip()):
                state["exit_code"] = 2
    except Exception as e:
        # J15（审计 2026-08-19）：except KeyboardInterrupt 为死代码——SIGINT
        # 已由自定义 handler 接管（_sigint_handler），KeyboardInterrupt 永不抛出
        print(f"{i18n.get('error_prefix')}{e}", file=sys.stderr)
        state["exit_code"] = 2
    finally:
        timer.cancel()
        try:
            ai.cancel()  # 兜底清理可能残留的子进程
        except Exception:
            pass

    if state["timed_out"]:
        print(f"\n⏰ 一次性对话超时（{args.once_timeout} 秒）", file=sys.stderr)
        sys.exit(4)
    if state["interrupted"]:
        print(f"\n{i18n.get('user_interrupted')}", file=sys.stderr)
        sys.exit(3)
    # 工具执行链路失败（run_command 非零码或工具异常）→ 退出码 1
    if state["exit_code"] == 0 and ai.stats.get("tool_failed"):
        state["exit_code"] = 1
    sys.exit(state["exit_code"])


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

    # W8（审计 2026-08-20）：--temperature 入口钳制，防 99/-1 直达 API 400
    if args.temperature is not None:
        args.temperature = min(2.0, max(0.0, args.temperature))

    # W11（审计 2026-08-20）：config.json 的 system/scheduler 节被写成非 dict
    # 时，下方 CLI 覆盖赋值 TypeError 崩启动——回退默认节
    _section_defaults = config._get_default_config()
    for _section in ("system", "scheduler"):
        if not isinstance(config._config.get(_section), dict):
            config._config[_section] = _section_defaults.get(_section, {})

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
    # W7（审计 2026-08-20）：负值直达 asyncio.sleep(负) 空转热循环，0 被 falsy
    # 静默忽略——统一钳制下限 0.1s
    config._config["scheduler"]["check_interval"] = max(0.1, args.scheduler_interval)

    # W5（审计 2026-08-20）：暂存 CLI 进程级覆盖，WS /reload 后重放（否则
    # config.reload() 重建 _config 会静默丢弃）
    _cli_overrides = {"system": {}, "scheduler": {}}
    if args.debug is not None:
        _cli_overrides["system"]["debug_level"] = args.debug
    if args.verbose:
        _cli_overrides["system"]["debug_level"] = 3
    if args.host:
        _cli_overrides["system"]["websocket_host"] = args.host
    if args.port:
        _cli_overrides["system"]["websocket_port"] = args.port
    if args.no_scheduler:
        _cli_overrides["scheduler"]["enabled"] = False
    _cli_overrides["scheduler"]["check_interval"] = config._config["scheduler"]["check_interval"]
    config._cli_overrides = {k: v for k, v in _cli_overrides.items() if v}
    
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
        command_executor=command_executor,
        model=args.model  # H3：--model 服务模式同样生效（进程级覆盖）
    )
    
    if handle_check_commands(manager):
        return

    if handle_memo_commands(manager):
        return

    # 一次性对话模式（--once）：执行后内部 sys.exit，不启动 WebSocket
    if handle_once_mode(manager, args):
        return

    # 运行WebSocket服务器
    manager.run()


if __name__ == "__main__":
    main()
