#!/usr/bin/env python3
"""
tools.registry - 工具注册器

抽离自 aibox.py 的 ToolRegistry 类。仅依赖 common 与标准库，
不反向依赖 aibox 任何符号（skill_manager 为前向引用，运行期注入）。
"""

import queue
import threading
import time
from typing import Dict, List, Optional, Tuple

from common import Logger, constants, i18n

from .base import Tool

# 工具参数名命中这些关键词时，debug 日志里打码（防密码/密钥进日志文件）
_SENSITIVE_PARAM_KEYS = {"password", "api_key", "token", "secret", "authorization"}


def _sanitize_kwargs(kwargs: Dict) -> Dict:
    """复制一份参数并把敏感键的值替换为 ***（仅用于日志输出）"""
    return {
        k: ("***" if str(k).lower() in _SENSITIVE_PARAM_KEYS else v)
        for k, v in kwargs.items()
    }


class ToolRegistry:
    """工具注册器 - 支持技能热重载"""
    
    def __init__(self, logger: Optional[Logger] = None):
        self._tools: Dict[str, Tool] = {}
        self._builtin_tools: Dict[str, Tool] = {}  # 内置工具单独存储
        self.logger = logger or Logger("ToolRegistry")
        self.current_output_queue = None  # 用于流式输出的队列
        self.skill_manager: Optional['SkillManager'] = None  # 技能管理器引用
        # 单工具流式执行总上限（工具自身秒级超时之外的兜底；测试可调小）
        self.stream_tool_timeout = 3600
    
    def register(self, tool: Tool, is_builtin: bool = False):
        """注册工具，可以标记是否为内置工具"""
        self._tools[tool.get_name()] = tool
        if is_builtin:
            self._builtin_tools[tool.get_name()] = tool
        self.logger.info(f"工具已注册: {tool.get_name()} {'(内置)' if is_builtin else ''}")
    
    def register_many(self, tools: List[Tool], is_builtin: bool = False):
        """批量注册工具"""
        for tool in tools:
            self.register(tool, is_builtin)
    
    def get_tool(self, name: str) -> Optional[Tool]:
        """获取工具"""
        return self._tools.get(name)
    
    def get_all_tools(self) -> List[Tool]:
        """获取所有工具"""
        return list(self._tools.values())
    
    def get_tools_schemas(self) -> List[Dict]:
        """获取所有工具的OpenAI函数调用格式"""
        return [tool.to_function_call_format() for tool in self._tools.values()]
    
    def set_output_queue(self, queue: queue.Queue):
        """设置输出队列"""
        self.current_output_queue = queue
        # 同时设置所有工具的队列
        for tool in self._tools.values():
            tool.output_queue = queue
    
    def execute_tool(self, name: str, **kwargs) -> str:
        """执行工具"""
        tool = self.get_tool(name)
        if not tool:
            return f"❌ 错误：找不到工具 '{name}'"
        
        self.logger.debug(f"工具调用: {name}({_sanitize_kwargs(kwargs)})")
        start_time = time.time()
        
        try:
            # 检查是否有队列可用，并且工具支持流式输出
            if self.current_output_queue and hasattr(tool, 'execute_streaming'):
                # 设置工具的流式输出属性
                tool.streaming_output = True
                tool.output_queue = self.current_output_queue
                
                # 创建队列来收集流式输出
                output_queue = queue.Queue()
                
                # 执行流式输出
                thread = threading.Thread(
                    target=tool.execute_streaming,
                    args=(output_queue,),
                    kwargs=kwargs
                )
                thread.daemon = True
                thread.start()

                # M2 修复（审计 2026-08-17）：转发循环加总超时。
                # 此前 while thread.is_alive() 在工具内部卡死（如子进程
                # 等待 stdin）时永不退出；超时后放弃等待，避免本调用
                # 线程被连带挂起。
                # N6 修复（审计 2026-08-18）：超时路径此前直接 return，
                # 跳过下方清理——daemon 线程继续持有 tool.output_queue
                # 实例属性，属性被后续调用覆盖到新会话队列后，僵尸输出
                # 会混入后续对话流。现在统一在出口清理。
                tool_timeout = self.stream_tool_timeout
                deadline = time.time() + tool_timeout
                final_result = None
                timed_out = False
                # N8：流式转发同样受 TOOL_MAX_RESULT_LENGTH 约束——此前仅非流式
                # 返回值受限，超长内容（如记忆全文）经流式通道可完整推到前端
                line_budget = constants.TOOL_MAX_RESULT_LENGTH
                forwarded = 0
                truncation_noted = False
                while thread.is_alive() or not output_queue.empty():
                    if time.time() > deadline:
                        self.logger.warning(
                            f"工具 {name} 流式执行超过 {tool_timeout}s 总上限，放弃等待")
                        timed_out = True
                        break
                    try:
                        msg_type, content = output_queue.get(timeout=0.1)
                        if msg_type == "line":
                            piece = str(content) if content is not None else ""
                            if forwarded >= line_budget:
                                # 预算耗尽：停止转发，仅提示一次
                                if not truncation_noted:
                                    truncation_noted = True
                                    if self.current_output_queue:
                                        self.current_output_queue.put(
                                            ("line", f"\n⚠️ 流式输出超过 {line_budget} 字符上限，已停止转发剩余内容\n"))
                                continue
                            remaining = line_budget - forwarded
                            if len(piece) > remaining:
                                piece = piece[:remaining] + \
                                    f"\n⚠️ 流式输出超过 {line_budget} 字符上限，已截断"
                                forwarded = line_budget
                            else:
                                forwarded += len(piece)
                            # 发送到当前队列（会通过WebSocket发送）
                            if self.current_output_queue:
                                self.current_output_queue.put(("line", piece))
                        elif msg_type == "complete":
                            final_result = content
                            # 也发送完整结果
                            if self.current_output_queue:
                                self.current_output_queue.put(("complete", content))
                    except queue.Empty:
                        continue

                if not timed_out:
                    thread.join(timeout=5)
                    if thread.is_alive():
                        timed_out = True

                # 统一清理（正常与超时路径都必须执行）：断开实例属性，
                # 僵尸 daemon 线程的 send_line 落到 None 上不再外写
                tool.streaming_output = False
                tool.output_queue = None

                if timed_out:
                    result = f"❌ 工具执行超时（>{tool_timeout}s，已放弃等待）"
                else:
                    result = final_result or ""
            else:
                # 普通执行
                result = tool.execute(**kwargs)
            
            elapsed = time.time() - start_time
            self.logger.debug(f"工具结果 ({elapsed:.2f}s): {result[:100]}...")
            return result
        except TypeError as e:
            return f"❌ 工具执行错误: 参数错误 - {str(e)}"
        except Exception as e:
            return f"❌ 工具执行错误: {str(e)}"
    
    def reload_skills(self, skills_dir: str = None) -> int:
        """
        重新加载所有扩展技能（热重载）。
        委托给 SkillManager 重新扫描目录并执行 --help 收集简介。
        """
        self.logger.info("开始重新加载扩展技能...")
        if self.skill_manager is not None:
            count = self.skill_manager.reload()
            self.logger.info(i18n.get('skills_reloaded', count))
            return count
        self.logger.warning("skill_manager 未初始化，无法重载技能")
        return 0
    
    def get_skills_count(self) -> Tuple[int, int]:
        """获取内置工具和扩展技能的数量"""
        builtin_count = len(self._builtin_tools)
        skill_count = len(self._tools) - builtin_count
        return builtin_count, skill_count
