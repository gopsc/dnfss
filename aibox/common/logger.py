#!/usr/bin/env python3
"""
common.logger - aibox 统一日志系统

抽离自 aibox.py 的 Logger 类。依赖 common.constants（日志文件路径与大小限制）
与 common.config，不反向依赖 aibox 任何符号，可安全独立导入。
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

from .constants import constants
from .config import ConfigManager
# ==================== 日志系统 ====================

class Logger:
    """统一日志系统 - 支持文件和终端输出"""
    
    LEVELS = {
        "DEBUG": 3,
        "INFO": 2,
        "WARNING": 1,
        "ERROR": 0
    }
    
    _file_handler = None
    _log_initialized = False
    
    @classmethod
    def init_file_logging(cls, log_file: str = None, max_size: int = None, backup_count: int = None):
        """初始化文件日志"""
        if cls._log_initialized:
            return
        
        if log_file is None:
            log_file = constants.LOG_FILE
        if max_size is None:
            max_size = constants.LOG_MAX_SIZE
        if backup_count is None:
            backup_count = constants.LOG_BACKUP_COUNT
        
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        
        cls._file_handler = RotatingFileHandler(
            log_file, 
            maxBytes=max_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        cls._file_handler.setFormatter(formatter)
        
        cls._log_initialized = True
    
    def __init__(self, name: str = "System", level: int = None):
        self.name = name
        config = ConfigManager()
        if level is None:
            level = config.get("system.debug_level", 1)
        self.level = level
        
        self.py_logger = logging.getLogger(f"AI.{name}")
        self.py_logger.setLevel(logging.DEBUG)
        
        if Logger._file_handler and not self.py_logger.handlers:
            self.py_logger.addHandler(Logger._file_handler)
    
    def debug(self, msg: str):
        if self.level >= 3:
            self._log("🐛", "DEBUG", msg)
            self.py_logger.debug(msg)
    
    def info(self, msg: str):
        if self.level >= 2:
            self._log("📋", "INFO", msg)
            self.py_logger.info(msg)
    
    def warning(self, msg: str):
        if self.level >= 1:
            self._log("⚠️", "WARNING", msg)
            self.py_logger.warning(msg)
    
    def error(self, msg: str):
        if self.level >= 0:
            self._log("❌", "ERROR", msg)
            self.py_logger.error(msg)
    
    def _log(self, emoji: str, level: str, msg: str):
        """输出到终端"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{emoji} [{timestamp}] [{self.name}] {msg}", flush=True)

