#!/usr/bin/env python3
"""
common.config - aibox 配置管理器

抽离自 aibox.py 的 ConfigManager。仅依赖 common.i18n（在运行期方法内部使用），
不反向依赖 aibox 任何符号，可安全独立导入。
"""

import os
import json
from typing import Dict, Optional

from .i18n import i18n
class ConfigManager:
    """配置管理器 - 单例模式，负责加载和保存配置"""
    
    _instance = None
    _config = None
    _config_path = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._config is None:
            self._load_config()
    
    def _get_default_config_path(self) -> str:
        env_config = os.environ.get("AI_CONFIG_PATH")
        if env_config:
            return env_config
        return os.path.expanduser("~/.aibox/config.json")
    
    def _get_default_config(self) -> Dict:
        return {

            # 系统参数
            "system": {
                "save_dir": "~/.aibox",                     # 储存目录
                "datetime_format": "%Y-%m-%d %H:%M:%S",     # 时间格式
                "date_format": "%Y%m%d_%H%M%S",             # 另一个时间格式
                "debug_level": 1,                           # 调试等级
                "load_history_by_default": True,            # 默认加载历史记录
                "log_file": "~/.aibox/ai.log",              # 日志文件
                "log_max_size": 10485760,                   # 日志最大大小
                "log_backup_count": 5,                      # 日志备份数量
                "skills_dir": "~/.aibox/skills",            # 技能目录
                "prompts_dir": "~/.aibox/prompts",          # 提示词目录
                "workspace_dir": "~/.aibox/workspace",      # 工作空间目录
                "websocket_host": "localhost",              # websocket监听地址
                "websocket_port": 8765,                     # websocket监听端口
            },

            # 调度程序
            "scheduler": {
                "enabled": True,            # 开启调度
                "check_interval": 1.0,      # 检查间隔
                "max_tasks_per_run": 10,    # 每次运行最大任务量
                "broadcast_enabled": True   # 开启广播
            },


            # 大模型调用接口
            "api": {
                "deepseek_api_url": "https://api.deepseek.com/chat/completions",    # deepseek API URL
                "deepseek_model": "deepseek-v4-flash",      # 模型名字
                "reasoning_enabled": False,                 # 开启思维链
                "api_key_env": "DEEPSEEK_API_KEY",          # API-KEY所在的环境变量
                "timeout": 120,                             # 调用大模型请求超时时间
                "temperature": 0.7                          # 大模型对话温度（类似思维发散程度）
            },

            # 大模型上下文
            "context": {
                "max_history_messages": 500,            # 最大历史消息数量
                "max_context_messages": 120,            # 最大内容消息数量（？）
                "max_tokens": 8192,                     # 最大输出token量
                "max_history_conversations": 5,         # 最大历史对话
                "preserve_system_prompts": True,        # 开启前置系统提示词
                # 对话压缩配置
                "compress_enabled": True,               # 开启压缩
                "compress_message_threshold": 200,      # 压缩消息条数阈值
                "compress_token_threshold": 120000,     # 压缩消息词元阈值
                "compress_ratio": 0.2,                  # 压缩率
                "compress_keep_recent": 16              # 压缩保留最近的几条
            },


            # 定时任务参数
            "memo": {
                "default_reminder_minutes": 60,
                "max_memos_per_check": 10,      # 每次检查定时任务数量
                "allow_user_tasks": True        # 允许用户任务
            },

            # 文件
            "files": {
                "max_file_size": 10485710   # 最大文件大小
            },

            # 指令
            "commands": {
                "enabled": True     # 开启指令
            },

            # 网页
            "web": {
                "timeout": 10,  # 超时时间
                "delay": 0,     # 延时
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "respect_robots": False,        # 
                "max_download_size": 10485760  # 最大下载大小
            },

            # 记忆
            "memory": {
                "db_name": "memories.db",   # 数据库名
                "max_search_results": 100,  # 最大搜索结果
                "default_importance": 2     # ？
            },

            # 对话
            "conversation": {
                "current_file": "current_conversation.json",    # 当前对话文件
                "history_file": "conversation_history.json",    # 历史对话文件
                "archive_dir": "archive"                        # ？
            },

            # 工具
            "tools": {
                "max_result_length": 10000,         # 结果最大长度
                "save_result_to_file": False,       # 保存结果到文件
                "tool_results_dir": "tool_results"  # 工具结果存储目录
            },

            # 压缩器
            "compressor": {
                "min_messages_to_compress": 5,  # 最小压缩数量
                "weight_decay_factor": 0.01,    # ？
                "max_input_chars": 6000,        # 最大输入字符
                "llm_max_tokens": 1000          # 大模型最大输出词元
            }
        }
    
    def _load_config(self):
        config_path = self._get_default_config_path()
        self._config_path = config_path
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                default_config = self._get_default_config()
                self._config = self._merge_configs(default_config, user_config)
                print(i18n.get('config_loaded', config_path))
            except Exception as e:
                print(f"⚠️ {i18n.get('error_prefix')}{e}，使用默认配置")
                self._config = self._get_default_config()
        else:
            print(i18n.get('config_not_found', config_path))
            self._config = self._get_default_config()
            self._save_default_config(config_path)
    
    def _merge_configs(self, default: Dict, user: Dict) -> Dict:
        result = default.copy()
        for key, value in user.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            else:
                result[key] = value
        return result
    
    def _save_default_config(self, config_path: str):
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self._get_default_config(), f, ensure_ascii=False, indent=2)
            print(i18n.get('config_created', config_path))
        except:
            pass
    
    def get(self, key_path: str, default=None):
        keys = key_path.split('.')
        value = self._config
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
    
    def reload(self):
        self._load_config()


    def get_proxy(self) -> Optional[Dict[str, str]]:
        """获取当前代理配置（返回 requests 可用的 proxies 字典）"""
        proxy_conf = self.get("proxy.socks5")
        if not proxy_conf or not proxy_conf.get("enabled", False):
            return None
        host = proxy_conf.get("host", "127.0.0.1")
        port = proxy_conf.get("port", 1080)
        username = proxy_conf.get("username")
        password = proxy_conf.get("password")
        if username and password:
            proxy_url = f"socks5://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"socks5://{host}:{port}"
        return {"http": proxy_url, "https": proxy_url}

    def set_proxy(self, host: str, port: int, username: str = None, password: str = None, enabled: bool = True, save: bool = True):
        """动态设置代理配置（内存中更新，并可选择写入配置文件）"""
        # 确保 proxy 节点存在
        if "proxy" not in self._config:
            self._config["proxy"] = {}
        self._config["proxy"]["socks5"] = {
            "enabled": enabled,
            "host": host,
            "port": port,
            "username": username,
            "password": password
        }
        if save:
            self._save_config()
        return True

    def _save_config(self):
        """将当前配置保存到配置文件（覆盖）"""
        config_path = self._config_path
        if config_path:
            try:
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(self._config, f, ensure_ascii=False, indent=2)
                print(f"✅ 配置已保存到 {config_path}")
            except Exception as e:
                print(f"⚠️ 保存配置失败: {e}")

    def get_proxy_info(self) -> Dict:
        """获取代理配置的详细信息（用于显示）"""
        proxy_conf = self.get("proxy.socks5", {})
        return {
            "enabled": proxy_conf.get("enabled", False),
            "host": proxy_conf.get("host", ""),
            "port": proxy_conf.get("port", 0),
            "username": proxy_conf.get("username"),
            "password": proxy_conf.get("password") is not None  # 只显示是否存在
        }
