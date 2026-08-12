#!/usr/bin/env python3
"""
tools.base - 工具抽象基类

抽离自 aibox.py 的 Tool 基类。仅依赖 common 与标准库，
不反向依赖 aibox 任何符号。
"""

import os
import queue
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from common import ConfigManager, Logger, constants


class Tool(ABC):
    """工具基类"""
    
    def __init__(self, logger: Optional[Logger] = None):
        self.logger = logger or Logger(self.__class__.__name__)
        self.config = ConfigManager()
        self.streaming_output = False  # 标记是否正在流式输出
        self.output_queue = None  # 添加输出队列属性
    
    @abstractmethod
    def get_name(self) -> str:
        """返回工具名称"""
        pass
    
    @abstractmethod
    def get_description(self) -> str:
        """返回工具描述"""
        pass
    
    @abstractmethod
    def get_parameters(self) -> Dict:
        """返回工具参数定义（JSON Schema格式）"""
        pass
    
    @abstractmethod
    def execute(self, **kwargs) -> str:
        """执行工具并返回结果"""
        pass
    
    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行工具，通过队列输出结果"""
        self.output_queue = output_queue  # 保存队列引用
        self.streaming_output = True
        try:
            # 执行具体的工具逻辑，子类可以重写这个方法
            result = self.execute(**kwargs)
            # 如果结果不为空，发送到队列
            if result:
                # 对于非流式输出，直接发送完整结果
                output_queue.put(("complete", result))
        finally:
            self.streaming_output = False
            self.output_queue = None
    
    def validate_params(self, required: List[str], **kwargs) -> Optional[str]:
        """验证必需参数"""
        missing = [p for p in required if p not in kwargs or kwargs[p] is None]
        if missing:
            return f"❌ 错误：缺少必需参数: {', '.join(missing)}"
        return None
    
    def to_function_call_format(self) -> Dict:
        """转换为OpenAI函数调用格式"""
        return {
            "type": "function",
            "function": {
                "name": self.get_name(),
                "description": self.get_description(),
                "parameters": self.get_parameters()
            }
        }
    
    def format_result(self, success: bool, message: str, data: Dict = None) -> str:
        """统一结果格式化"""
        result = []
        emoji = "✅" if success else "❌"
        result.append(f"{emoji} {message}")
        
        if data:
            for key, value in data.items():
                if isinstance(value, str) and "\n" in value:
                    result.append(f"  {key}:")
                    for line in value.split("\n"):
                        result.append(f"    {line}")
                else:
                    result.append(f"  {key}: {value}")
        
        return "\n".join(result)
    
    def check_path_safety(self, filepath: str) -> Tuple[bool, str, str]:
        """检查路径安全性（简化版）"""
        try:
            abs_path = os.path.abspath(os.path.expanduser(filepath))
            
            # 检查文件大小
            if os.path.exists(abs_path) and os.path.isfile(abs_path):
                file_size = os.path.getsize(abs_path)
                max_size = constants.MAX_FILE_SIZE
                if file_size > max_size:
                    return False, abs_path, f"❌ 文件太大 ({file_size/1024/1024:.1f}MB)"
            
            return True, abs_path, ""
        except Exception as e:
            return False, filepath, f"❌ 路径检查错误: {str(e)}"
    
    def send_line(self, line: str):
        """发送一行输出到队列（如果存在）"""
        if self.output_queue:
            self.output_queue.put(("line", line))
    
    def send_complete(self, result: str):
        """发送完成结果到队列（如果存在）"""
        if self.output_queue:
            self.output_queue.put(("complete", result))
