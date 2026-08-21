#!/usr/bin/env python3
"""
common.constants - aibox 系统常量

抽离自 aibox.py 的 Constants 类与 constants 实例。仅依赖 common.config
（类体内 _config = ConfigManager()，运行期实例化时读取 i18n），不反向依赖
aibox 任何符号，可安全独立导入。
"""

import os
import json

from .config import ConfigManager
# ==================== 常量定义 ====================


class Constants:
    """系统常量 - 从配置管理器获取"""
    
    _config = ConfigManager()
    
    @classmethod
    def _get_config(cls):
        return cls._config
    
    @property
    def DATETIME_FORMAT(cls):
        return cls._get_config().get("system.datetime_format", "%Y-%m-%d %H:%M:%S")
    
    @property
    def DATE_FORMAT(cls):
        return cls._get_config().get("system.date_format", "%Y%m%d_%H%M%S")
    
    @property
    def DEEPSEEK_API_URL(cls):
        return cls._get_config().get("api.deepseek_api_url")
    
    @property
    def DEEPSEEK_MODEL(cls):
        return cls._get_config().get("api.deepseek_model", "deepseek-v4-flash")
    
    @property
    def REASONING_ENABLED(cls):
        return cls._get_config().get("api.reasoning_enabled", False)
    
    @property
    def DEFAULT_TIMEOUT(cls):
        return cls._get_config().get("api.timeout", 30)
    
    @property
    def DEFAULT_MAX_HISTORY(cls):
        return cls._get_config().get("context.max_history_messages", 500)
    
    @property
    def DEFAULT_TEMPERATURE(cls):
        return cls._get_config().get("api.temperature", 0.7)
    
    @property
    def DEFAULT_MAX_TOKENS(cls):
        return cls._get_config().get("context.max_tokens", 8000)
    
    @property
    def MAX_FILE_SIZE(cls):
        return cls._get_config().get("files.max_file_size", 10485710)
    
    @property
    def SAVE_DIR(cls):
        return os.path.expanduser(cls._get_config().get("system.save_dir", "~/.aibox"))
    
    @property
    def SKILLS_DIR(cls):
        return os.path.expanduser(cls._get_config().get("system.skills_dir", "~/.aibox/skills"))
    
    @property
    def WORKSPACE_DIR(cls):
        return os.path.expanduser(cls._get_config().get("system.workspace_dir", "~/.aibox/workspace"))
    
    @property
    def PROMPTS_DIR(cls):
        return os.path.expanduser(cls._get_config().get("system.prompts_dir", "~/.aibox/prompts"))
    
    @property
    def MEMORY_DB(cls):
        return cls._get_config().get("memory.db_name", "memories.db")
    
    @property
    def CURRENT_CONVERSATION_FILE(cls):
        return cls._get_config().get("conversation.current_file", "current_conversation.json")
    
    @property
    def HISTORY_FILE(cls):
        return cls._get_config().get("conversation.history_file", "conversation_history.json")
    
    @property
    def ARCHIVE_DIR(cls):
        return cls._get_config().get("conversation.archive_dir", "archive")
    
    @property
    def USER_PREFIX(cls):
        return "[用户]"
    
    @property
    def SYSTEM_PREFIX(cls):
        return "[系统]"
    
    @property
    def ASSISTANT_PREFIX(cls):
        return "[助手]"
    
    @property
    def MAX_HISTORY_CONTEXT(cls):
        return cls._get_config().get("context.max_context_messages", 50)
    
    @property
    def MAX_HISTORY_CONVERSATIONS(cls):
        return cls._get_config().get("context.max_history_conversations", 5)
    
    @property
    def PRESERVE_SYSTEM_PROMPTS(cls):
        return cls._get_config().get("context.preserve_system_prompts", True)
    
    @property
    def WEB_TIMEOUT(cls):
        return cls._get_config().get("web.timeout", 10)
    
    @property
    def WEB_DELAY(cls):
        return cls._get_config().get("web.delay", 0)
    
    @property
    def WEB_USER_AGENT(cls):
        return cls._get_config().get("web.user_agent", 
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    @property
    def WEB_MAX_DOWNLOAD_SIZE(cls):
        return cls._get_config().get("web.max_download_size", 104857600)
    
    @property
    def MEMORY_MAX_SEARCH_RESULTS(cls):
        return cls._get_config().get("memory.max_search_results", 100)
    
    @property
    def MEMORY_DEFAULT_IMPORTANCE(cls):
        return cls._get_config().get("memory.default_importance", 2)
    
    @property
    def MEMO_DEFAULT_REMINDER_MINUTES(cls):
        return cls._get_config().get("memo.default_reminder_minutes", 60)
    
    @property
    def MEMO_MAX_PER_CHECK(cls):
        return cls._get_config().get("memo.max_memos_per_check", 10)
    
    @property
    def LOG_FILE(cls):
        return os.path.expanduser(cls._get_config().get("system.log_file", "~/.aibox/ai.log"))
    
    @property
    def LOG_MAX_SIZE(cls):
        return cls._get_config().get("system.log_max_size", 10485760)
    
    @property
    def LOG_BACKUP_COUNT(cls):
        return cls._get_config().get("system.log_backup_count", 5)
    
    @property
    def COMMANDS_ENABLED(cls):
        return cls._get_config().get("commands.enabled", True)
    
    @property
    def WEBSOCKET_HOST(cls):
        return cls._get_config().get("system.websocket_host", "localhost")
    
    @property
    def WEBSOCKET_PORT(cls):
        return cls._get_config().get("system.websocket_port", 8765)
    
    @property
    def SCHEDULER_ENABLED(cls):
        return cls._get_config().get("scheduler.enabled", True)
    
    @property
    def SCHEDULER_CHECK_INTERVAL(cls):
        return cls._get_config().get("scheduler.check_interval", 1.0)
    
    @property
    def SCHEDULER_MAX_TASKS(cls):
        return cls._get_config().get("scheduler.max_tasks_per_run", 10)
    
    @property
    def SCHEDULER_BROADCAST(cls):
        return cls._get_config().get("scheduler.broadcast_enabled", True)
    
    @property
    def MEMO_ALLOW_USER_TASKS(cls):
        return cls._get_config().get("memo.allow_user_tasks", True)
    
    @property
    def COMPRESS_ENABLED(cls):
        return cls._get_config().get("context.compress_enabled", True)
    
    @property
    def COMPRESS_MESSAGE_THRESHOLD(cls):
        return cls._get_config().get("context.compress_message_threshold", 100)
    
    @property
    def COMPRESS_TOKEN_THRESHOLD(cls):
        return cls._get_config().get("context.compress_token_threshold", 4000)
    
    @property
    def COMPRESS_RATIO(cls):
        return cls._get_config().get("context.compress_ratio", 0.4)
    
    @property
    def COMPRESS_KEEP_RECENT(cls):
        return cls._get_config().get("context.compress_keep_recent", 20)
    
    @property
    def TOOL_MAX_RESULT_LENGTH(cls):
        return cls._get_config().get("tools.max_result_length", 100000)  # 0.1MB 字符限制
    
    @property
    def COMPRESSOR_MIN_MESSAGES(cls):
        return cls._get_config().get("compressor.min_messages_to_compress", 5)
    
    @property
    def COMPRESSOR_WEIGHT_DECAY_FACTOR(cls):
        return cls._get_config().get("compressor.weight_decay_factor", 0.01)
    
    @property
    def COMPRESSOR_MAX_INPUT_CHARS(cls):
        return cls._get_config().get("compressor.max_input_chars", 6000)
    
    @property
    def COMPRESSOR_LLM_MAX_TOKENS(cls):
        return cls._get_config().get("compressor.llm_max_tokens", 1000)

    # 后台进程管理（bg_run/bg_list/bg_output/bg_kill）
    @property
    def BG_PROCESSES_ENABLED(cls):
        return cls._get_config().get("background_processes.enabled", True)

    @property
    def BG_MAX_PROCESSES(cls):
        return cls._get_config().get("background_processes.max_processes", 16)

    @property
    def BG_MAX_ENTRIES(cls):
        return cls._get_config().get("background_processes.max_entries", 32)

    @property
    def BG_BUFFER_PER_PROCESS(cls):
        return cls._get_config().get("background_processes.buffer_per_process", 262144)

    @property
    def BG_MAX_LIFETIME(cls):
        return cls._get_config().get("background_processes.max_lifetime", 0)

    @property
    def BG_MAX_WRITE_BYTES(cls):
        return cls._get_config().get("background_processes.max_write_bytes", 65536)


# 创建常量实例
constants = Constants()
