"""
i18n - 多语言支持子模块

仅依赖标准库 (os / sys / locale)，是 common 包的最底层，不依赖包内其他模块。
"""

import os
import sys
import locale

class I18n:
    """多语言支持类 - 单例模式"""
    
    _instance = None
    _lang = None
    _strings = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._strings:
            self._detect_language()
            self._load_strings()

    def _detect_language(self):
        # 首先尝试从环境变量获取（Linux/Mac）
        lang = os.environ.get('LANG') or os.environ.get('LC_ALL')
        if lang:
            if lang.startswith('en_'):
                self._lang = 'en'
            else:
                self._lang = 'zh'
        else:
            # Windows: 使用 locale.getlocale()（无需 setlocale）
            try:
                sys_locale = locale.getlocale()[0]
                if sys_locale and sys_locale.startswith('en_'):
                    self._lang = 'en'
                else:
                    self._lang = 'zh'
            except:
                self._lang = 'zh'
        # 强制覆盖
        force_lang = os.environ.get("AI_LANGUAGE")
        if force_lang in ['en', 'zh']:
            self._lang = force_lang
    
    def _load_strings(self):
        """加载多语言字符串"""
        self._strings = {
            'zh': {
                # 欢迎信息
                'welcome_title': "🤖 单AI智能体系统 - WebSocket服务器已启动",
                'data_dir': "📁 数据存储目录: {}",
                'skills_dir': "📁 技能目录: {}",
                'prompts_dir': "📁 提示词目录: {}",
                'config_file': "📋 配置文件: {}",
                'memo_stats': "📋 任务统计:",
                'total_memos': "  📝 总任务: {} 个",
                'pending_memos': "  ⏰ 待执行: {} 个",
                'overdue_memos': "  ⚠️ 已过期: {} 个",
                'completed_memos': "  ✅ 已完成: {} 个",
                'loaded_skills': "🔧 已加载扩展技能 ({} 个):",
                'skill_item': "  • {}",
                'more_skills': "  ... 还有 {} 个",
                'loaded_prompts': "📝 已加载提示词 ({} 个):",
                'prompt_item': "  • {}",
                'websocket_started': "🌐 WebSocket服务器已启动在 ws://{}:{}",
                'client_connected': "🔌 客户端已连接: {}",
                'client_disconnected': "🔌 客户端断开连接: {}",
                
                # 任务调度器相关
                'scheduler_started': "⏰ 任务调度器已启动",
                'scheduler_stopped': "⏰ 任务调度器已停止",
                'task_added': "📋 已添加任务: {} (ID: {})",
                'task_removed': "📋 已移除任务: {}",
                'task_executing': "⚙️ 正在执行任务: {} (ID: {})",
                'task_executed': "✅ 任务执行成功: {}",
                'task_failed': "❌ 任务执行失败: {} - {}",
                'task_stats': "📊 任务统计: 总数={}, 待执行={}, 已过期={}, 已完成={}",
                'check_memos_task': "检查过期任务",
                'broadcast_tasks': "📢 广播: {} 个任务已到期",
                'next_execution': "⏱️ 下次执行: {}",
                'task_completed': "✅ 任务已完成: {}",
                
                # 命令处理
                'goodbye': "👋 再见！",
                'new_conversation': "🔄 开始新对话（历史已保存）",
                'new_conversation_reset': "🔄 开始新对话（历史已保存，对话记忆已重置）",
                'saved_conversations': "📚 已保存的对话:",
                'conv_status_current': "当前",
                'conv_status_archived': "归档",
                'recent_memories': "📚 最近的记忆:",
                'pending_memos_title': "📋 待执行的任务:",
                'memo_overdue': "⚠️",
                'memo_pending': "⏰",
                'search_results': "🔍 搜索 '{}' 找到 {} 条记忆:",
                'search_usage': "用法: /search 关键词",
                'current_config': "⚙️  当前配置:",
                'config_debug': "  调试级别: {}",
                'config_data_dir': "  数据目录: {}",
                'config_skills_dir': "  技能目录: {}",
                'config_prompts_dir': "  提示词目录: {}",
                'config_commands': "  命令执行: {}",
                'config_history': "  加载历史: {}",
                'config_executor': "  命令执行器: {}",
                'config_scheduler': "  任务调度器: {}",
                'enabled': "启用",
                'disabled': "禁用",
                'config_reloaded': "✅ 配置已重新加载",
                'config_reloaded_cli_kept': "（命令行进程级覆盖已保留）",
                'prompts_reloaded': "✅ 提示词已重新加载 ({} 个)",
                'checking_memos': "🔍 检查任务...",
                'overdue_found': "发现 {} 个到期任务:",
                'no_overdue': "没有到期的任务",
                'memo_tool_usage': "📝 任务工具使用:",
                'memo_usage_in_dialogue': "  在对话中可以直接使用 memo 工具:",
                'memo_add_example': "  - memo add title=\"任务标题\" content=\"任务内容\" reminder_time=\"+1h\" repeat_type=\"none\"",
                'memo_list_example': "  - memo list",
                'memo_complete_example': "  - memo complete memo_id=1",
                'memory_habit': "🧠 记忆习惯:",
                'memory_habit_desc': "  AI会自动检查相关记忆，保持对话连贯性",
                'memory_save_desc': "  对话结束时，AI会自动保存重要记忆",
                'command_stream': "⚙️ 命令执行实时输出:",
                'command_stream_desc': "  run_command 工具会自动实时显示命令输出",
                'command_stream_disable': "  可以通过 stream=false 参数关闭实时输出",
                'skills_extension': "🔧 扩展技能:",
                'skills_desc': "  将Python脚本放入 ~/.aibox/skills/ 目录",
                'skills_interface_desc': "  可执行文件需支持 --help 选项，第一行为简介，其余为完整使用说明",
                'skills_reloaded': "✅ 技能已重新加载 ({} 个)",
                'skills_reload_failed': "❌ 技能重载失败: {}",
                'skills_reload_success': "✅ 技能热重载成功，当前共有 {} 个扩展技能",
                
                # 对话相关
                'input_prompt': "💬 请输入问题: ",
                'thinking': "🤔 思考中... [{}]",
                'assistant_prefix': "   💬 助手: ",
                'calling_tools': "\n   🔧 需要调用工具...",
                'analyzing_tool_results': "\n   🤔 分析工具结果...",
                'conversation_start': "🎭 对话开始",
                'conversation_ended': "✅ AI保存记忆并结束了对话",
                'conversation_stats': "\n📊 对话结束，总轮数: {}",
                'user_interrupted': "\n\n👋 用户中断",
                'error_prefix': "❌ 错误: ",
                
                # 工具相关
                'command_output_title': "\n" + "=" * 50 + "\n📟 命令实时输出:\n" + "=" * 50,
                'command_output_end': "\n" + "=" * 50,
                
                # 备忘录检查
                'memo_check_title': "⏰ 检查到期任务...",
                'memo_check_found': "发现 {} 个到期任务:",
                'memo_check_none': "没有到期的任务",
                'memo_list_title': "📋 待执行任务 (共 {} 个):",
                'memo_added': "✅ 任务已添加，ID: {}",
                'memo_add_failed': "❌ 添加失败: {}",
                'memo_completed': "✅ 任务 #{} 已完成",
                'memo_complete_failed': "❌ 无法完成任务 #{}",
                'memo_complete_missing_id': "❌ 缺少 task_id，无法完成任务",
                
                # 即时任务相关
                'immediate_memo_added': "⚡ 已添加为即时任务并立即检查",
                'immediate_memo_executing': "⚡ 正在执行即时任务: {}",
                
                # 新对话
                'new_dialogue_start': "🎭 开始新对话: {}",
                
                # 记忆相关
                'memory_check': "习惯性检查相关记忆...",
                'memory_found': "找到 {} 条相关记忆",
                
                # 配置文件
                'config_loaded': "📋 已加载配置文件: {}",
                'config_not_found': "📋 配置文件不存在: {}，使用默认配置",
                'config_created': "📋 已创建默认配置文件: {}",
                
                # WebSocket消息类型
                'ws_connected': "✅ 已连接到AI服务器",
                'ws_disconnected': "❌ 与服务器断开连接",
                'ws_error': "⚠️ WebSocket错误: {}",
                'ws_unknown_command': "❌ 未知命令: {}",
                'ws_command_processed': "✅ 命令已处理",
                
                # 消息类型
                'msg_type_chunk': "chunk",
                'msg_type_complete': "complete",
                'msg_type_error': "error",
                'msg_type_command_result': "command_result",
                'msg_type_connection_ack': "connection_ack",
                'msg_type_notification': "notification",
                'msg_type_task_status': "task_status",
                'msg_type_task_executing': "task_executing",
                'msg_type_task_complete': "task_complete",
                
                # 新增提示词
                'memory_search_prompt': "【重要习惯】在每次回答用户问题前，请先使用 memory search 工具搜索记忆库中与当前话题相关的记忆，确保回答的连贯性和准确性。",
                'identity_updated': "✅ 用户身份已更新，ID: {}",
                'status_updated': "✅ 智能体状态已更新",
                
                # 对话结束相关
                'dialogue_ended': "🔚 对话已结束，请开始新对话",
                'dialogue_force_reset': "🔄 对话状态已重置",
                'dialogue_reset_complete': "✅ 对话状态已完全重置，系统提示词已重新加载",
                
                # 对话压缩相关（静默执行，不输出信息）
                'conversation_compress': "📦 对话内容压缩",
                'conversation_compressed': "✅ 对话已压缩: 从 {} 条消息压缩为 {} 条",
                'compress_threshold_reached': "⚠️ 对话已达到压缩阈值 ({} 条消息)，正在压缩...",
                'compress_token_threshold_reached': "⚠️ 对话token数已达到 {}，正在压缩...",
                'compress_failed': "❌ 对话压缩失败: {}",
                'compressed_summary': "【对话摘要】{}",
                'last_n_messages': "【最近消息】{}"
            },
            'en': {
                # Welcome messages
                'welcome_title': "🤖 Single AI Agent System - WebSocket Server Started",
                'data_dir': "📁 Data directory: {}",
                'skills_dir': "📁 Skills directory: {}",
                'prompts_dir': "📁 Prompts directory: {}",
                'config_file': "📋 Configuration file: {}",
                'memo_stats': "📋 Task statistics:",
                'total_memos': "  📝 Total tasks: {}",
                'pending_memos': "  ⏰ Pending: {}",
                'overdue_memos': "  ⚠️ Overdue: {}",
                'completed_memos': "  ✅ Completed: {}",
                'loaded_skills': "🔧 Loaded extension skills ({}):",
                'skill_item': "  • {}",
                'more_skills': "  ... {} more",
                'loaded_prompts': "📝 Loaded prompts ({}):",
                'prompt_item': "  • {}",
                'websocket_started': "🌐 WebSocket server started at ws://{}:{}",
                'client_connected': "🔌 Client connected: {}",
                'client_disconnected': "🔌 Client disconnected: {}",
                
                # Scheduler related
                'scheduler_started': "⏰ Task scheduler started",
                'scheduler_stopped': "⏰ Task scheduler stopped",
                'task_added': "📋 Task added: {} (ID: {})",
                'task_removed': "📋 Task removed: {}",
                'task_executing': "⚙️ Executing task: {} (ID: {})",
                'task_executed': "✅ Task executed successfully: {}",
                'task_failed': "❌ Task execution failed: {} - {}",
                'task_stats': "📊 Task stats: total={}, pending={}, overdue={}, completed={}",
                'check_memos_task': "Check overdue tasks",
                'broadcast_tasks': "📢 Broadcast: {} tasks overdue",
                'next_execution': "⏱️ Next execution: {}",
                'task_completed': "✅ Task completed: {}",
                
                # Command processing
                'goodbye': "👋 Goodbye!",
                'new_conversation': "🔄 Starting new conversation (history saved)",
                'new_conversation_reset': "🔄 Starting new conversation (history saved, conversation memory reset)",
                'saved_conversations': "📚 Saved conversations:",
                'conv_status_current': "current",
                'conv_status_archived': "archived",
                'recent_memories': "📚 Recent memories:",
                'pending_memos_title': "📋 Pending tasks:",
                'memo_overdue': "⚠️",
                'memo_pending': "⏰",
                'search_results': "🔍 Searching '{}' found {} memories:",
                'search_usage': "Usage: /search <keyword>",
                'current_config': "⚙️  Current configuration:",
                'config_debug': "  Debug level: {}",
                'config_data_dir': "  Data directory: {}",
                'config_skills_dir': "  Skills directory: {}",
                'config_prompts_dir': "  Prompts directory: {}",
                'config_commands': "  Command execution: {}",
                'config_history': "  Load history: {}",
                'config_executor': "  Command executor: {}",
                'config_scheduler': "  Task scheduler: {}",
                'enabled': "enabled",
                'disabled': "disabled",
                'config_reloaded': "✅ Configuration reloaded",
                'config_reloaded_cli_kept': " (CLI process-level overrides preserved)",
                'prompts_reloaded': "✅ Prompts reloaded ({})",
                'checking_memos': "🔍 Checking tasks...",
                'overdue_found': "Found {} overdue tasks:",
                'no_overdue': "No overdue tasks",
                'memo_tool_usage': "📝 Task tool usage:",
                'memo_usage_in_dialogue': "  You can use memo tool directly in dialogue:",
                'memo_add_example': "  - memo add title=\"Task\" content=\"Content\" reminder_time=\"+1h\" repeat_type=\"none\"",
                'memo_list_example': "  - memo list",
                'memo_complete_example': "  - memo complete memo_id=1",
                'memory_habit': "🧠 Memory habits:",
                'memory_habit_desc': "  AI automatically checks relevant memories for conversation continuity",
                'memory_save_desc': "  AI automatically saves important memories when conversation ends",
                'command_stream': "⚙️ Command execution real-time output:",
                'command_stream_desc': "  run_command tool automatically displays command output in real-time",
                'command_stream_disable': "  You can disable real-time output with stream=false parameter",
                'skills_extension': "🔧 Extension skills:",
                'skills_desc': "  Put Python scripts in ~/.aibox/skills/ directory",
                'skills_interface_desc': "  Executables must support --help; first line is the summary, rest is the full manual",
                'skills_reloaded': "✅ Skills reloaded ({})",
                'skills_reload_failed': "❌ Skills reload failed: {}",
                'skills_reload_success': "✅ Skills hot-reloaded successfully, now {} extension skills available",
                
                # Dialogue related
                'input_prompt': "💬 Please enter your question: ",
                'thinking': "🤔 Thinking... [{}]",
                'assistant_prefix': "   💬 Assistant: ",
                'calling_tools': "\n   🔧 Need to call tools...",
                'analyzing_tool_results': "\n   🤔 Analyzing tool results...",
                'conversation_start': "🎭 Conversation started",
                'conversation_ended': "✅ AI saved memory and ended conversation",
                'conversation_stats': "\n📊 Conversation ended, total turns: {}",
                'user_interrupted': "\n\n👋 User interrupted",
                'error_prefix': "❌ Error: ",
                
                # Tool related
                'command_output_title': "\n" + "=" * 50 + "\n📟 Command real-time output:\n" + "=" * 50,
                'command_output_end': "\n" + "=" * 50,
                
                # Memo check
                'memo_check_title': "⏰ Checking overdue tasks...",
                'memo_check_found': "Found {} overdue tasks:",
                'memo_check_none': "No overdue tasks",
                'memo_list_title': "📋 Pending tasks (total {}):",
                'memo_added': "✅ Task added, ID: {}",
                'memo_add_failed': "❌ Failed to add: {}",
                'memo_completed': "✅ Task #{} completed",
                'memo_complete_failed': "❌ Cannot complete task #{}",
                'memo_complete_missing_id': "❌ Missing task_id, cannot complete task",
                
                # Immediate task related
                'immediate_memo_added': "⚡ Added as immediate task and checked now",
                'immediate_memo_executing': "⚡ Executing immediate task: {}",
                
                # New dialogue
                'new_dialogue_start': "🎭 Starting new dialogue: {}",
                
                # Memory related
                'memory_check': "Habitually checking relevant memories...",
                'memory_found': "Found {} relevant memories",
                
                # Configuration file
                'config_loaded': "📋 Configuration file loaded: {}",
                'config_not_found': "📋 Configuration file not found: {}, using default configuration",
                'config_created': "📋 Default configuration file created: {}",
                
                # WebSocket message types
                'ws_connected': "✅ Connected to AI server",
                'ws_disconnected': "❌ Disconnected from server",
                'ws_error': "⚠️ WebSocket error: {}",
                'ws_unknown_command': "❌ Unknown command: {}",
                'ws_command_processed': "✅ Command processed",
                
                # Message types
                'msg_type_chunk': "chunk",
                'msg_type_complete': "complete",
                'msg_type_error': "error",
                'msg_type_command_result': "command_result",
                'msg_type_connection_ack': "connection_ack",
                'msg_type_notification': "notification",
                'msg_type_task_status': "task_status",
                'msg_type_task_executing': "task_executing",
                'msg_type_task_complete': "task_complete",
                
                # New prompts
                'memory_search_prompt': "【Important Habit】Before answering each user question, please use the memory search tool to search the memory database for information related to the current topic, ensuring coherence and accuracy of responses.",
                'identity_updated': "✅ User identity updated, ID: {}",
                'status_updated': "✅ Agent status updated",
                
                # Dialogue end related
                'dialogue_ended': "🔚 Dialogue ended, please start a new one",
                'dialogue_force_reset': "🔄 Dialogue state reset",
                'dialogue_reset_complete': "✅ Dialogue state fully reset, system prompts reloaded",
                
                # Conversation compression related (silent execution)
                'conversation_compress': "📦 Conversation compression",
                'conversation_compressed': "✅ Conversation compressed: from {} messages to {}",
                'compress_threshold_reached': "⚠️ Conversation reached compression threshold ({} messages), compressing...",
                'compress_token_threshold_reached': "⚠️ Conversation token count reached {}, compressing...",
                'compress_failed': "❌ Conversation compression failed: {}",
                'compressed_summary': "【Conversation Summary】{}",
                'last_n_messages': "【Recent Messages】{}"
            }
        }
    
    def get(self, key: str, *args) -> str:
        """获取指定语言的字符串，支持格式化参数"""
        if key in self._strings[self._lang]:
            text = self._strings[self._lang][key]
            if args:
                return text.format(*args)
            return text
        if key in self._strings['zh']:
            text = self._strings['zh'][key]
            if args:
                return text.format(*args)
            return text
        return key
    
    @property
    def lang(self) -> str:
        return self._lang



# 创建全局多语言实例
i18n = I18n()
