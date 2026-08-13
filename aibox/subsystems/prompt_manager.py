#!/usr/bin/env python3
"""
subsystems.prompt_manager - 提示词管理器

抽离自 aibox.py，负责从 prompts 目录加载系统提示词。
单例模式（__new__ + 类变量 _instance），多实例共享同一份 _prompts。

文件命名规范：
  - 00_IDENTITY.md : 用户身份信息
  - N_*.md/txt     : 基础技能提示词（N 为数字），按数字升序加载
  - 99_SOUL.md     : 智能体灵魂文件（角色定位、身份定义、性格等）
  - 100_STATUS.md  : 智能体状态记录

依赖：common (ConfigManager, constants) + 标准库
不反向依赖 aibox 任何符号。
"""

import os
import re
import shutil
from datetime import datetime
from typing import Dict, List, Optional

from common import ConfigManager


class PromptManager:
    """提示词管理器 - 负责从 prompts 目录加载系统提示词

    文件命名规范:
    - 00_IDENTITY.md: 用户身份信息
    - N_*.md/txt: 基础技能提示词（N 为数字，如 1_memory_search.md、10_task_handling.md），按数字升序加载
    - 99_SOUL.md: 智能体灵魂文件（角色定位、身份定义、性格等）
    - 100_STATUS.md: 智能体状态记录
    """

    _instance = None
    _prompts = []
    _prompts_dir = None

    # 核心文件
    IDENTITY_FILE = "00_IDENTITY.md"
    STATUS_FILE = "100_STATUS.md"
    SOUL_FILE = "99_SOUL.md"  # 智能体灵魂文件 - 所有角色定位内容

    # 基础技能文件名模式：N_xxx.md（N 为数字，如 1_memory_search.md、10_task_handling.md）
    _SKILL_FILE_RE = re.compile(r'^(\d+)_')

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._prompts:
            self._load_prompts()

    def _get_prompts_dir(self) -> str:
        config = ConfigManager()
        prompts_dir = config.get("system.prompts_dir", "~/.aibox/prompts")
        return os.path.expanduser(prompts_dir)

    def _load_prompts(self):
        self._prompts_dir = self._get_prompts_dir()
        os.makedirs(self._prompts_dir, exist_ok=True)
        self._prompts = []

        # 确保核心文件存在
        self._ensure_identity_file()
        self._ensure_status_file()
        self._ensure_soul_file()  # 智能体灵魂文件

        # 确保基础技能文件存在
        self._ensure_skill_files()

        prompt_files = []
        if os.path.exists(self._prompts_dir):
            # 按文件名排序，确保1_开头的技能文件在99_SOUL.md之前加载
            prompt_files = sorted([f for f in os.listdir(self._prompts_dir)
                                  if os.path.isfile(os.path.join(self._prompts_dir, f))
                                  and not f.startswith('.')])

            for filename in prompt_files:
                filepath = os.path.join(self._prompts_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:
                            self._prompts.append({
                                'file': filename,
                                'content': content
                            })
                except Exception as e:
                    print(f"⚠️ 读取提示词文件 {filename} 失败: {e}")

        essential_files = [self.IDENTITY_FILE, self.STATUS_FILE, self.SOUL_FILE]
        other_files = [f for f in prompt_files if f not in essential_files] if prompt_files else []

        if not self._prompts:
            self._create_default_prompts()

    def _get_template_dir(self) -> str:
        """获取仓库中的提示词模板目录（aibox/prompts/）"""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # subsystems/ 上一层是 aibox/，模板在其下的 prompts/
        aibox_dir = os.path.dirname(script_dir)
        return os.path.join(aibox_dir, 'prompts')

    def _ensure_from_template(self, filename: str):
        """从模板目录复制提示词文件到运行目录。

        模板目录为 aibox/prompts/，若其中存在同名文件则复制过去；
        若模板不存在则跳过（由调用方保证模板齐全）。
        已存在的运行时文件不会被覆盖。
        """
        target_path = os.path.join(self._prompts_dir, filename)
        if os.path.exists(target_path):
            return  # 已存在，不覆盖

        template_path = os.path.join(self._get_template_dir(), filename)
        if os.path.exists(template_path):
            try:
                shutil.copy2(template_path, target_path)
                print(f"✅ 已从模板创建: {filename}")
            except Exception as e:
                print(f"⚠️ 从模板复制 {filename} 失败: {e}")

    def _ensure_identity_file(self):
        """确保用户身份文件存在"""
        self._ensure_from_template(self.IDENTITY_FILE)

    def _ensure_status_file(self):
        """确保状态文件存在"""
        self._ensure_from_template(self.STATUS_FILE)

    def _ensure_soul_file(self):
        """确保智能体灵魂文件存在"""
        self._ensure_from_template(self.SOUL_FILE)

    def _ensure_skill_files(self):
        """确保基础技能文件存在 - 从模板目录复制所有 N_*.md 文件"""
        template_dir = self._get_template_dir()
        if not os.path.isdir(template_dir):
            return
        for filename in sorted(os.listdir(template_dir)):
            if self._SKILL_FILE_RE.match(filename):  # N_ 开头的文件
                self._ensure_from_template(filename)

    def update_identity(self, content: str) -> bool:
        """更新用户身份信息"""
        identity_path = os.path.join(self._prompts_dir, self.IDENTITY_FILE)
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated_content = f"""# 用户身份信息

{content}

【更新记录】
- 上次更新：{timestamp}
"""
            with open(identity_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)
            self.reload()
            return True
        except Exception as e:
            print(f"⚠️ 更新身份文件失败: {e}")
            return False

    def update_status(self, content: str) -> bool:
        """更新智能体状态"""
        status_path = os.path.join(self._prompts_dir, self.STATUS_FILE)
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated_content = f"""# 智能体状态记录

{content}

【更新记录】
- 上次更新：{timestamp}
"""
            with open(status_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)
            self.reload()
            return True
        except Exception as e:
            print(f"⚠️ 更新状态文件失败: {e}")
            return False

    def update_soul(self, content: str) -> bool:
        """更新智能体灵魂定义 - 所有角色定位内容"""
        soul_path = os.path.join(self._prompts_dir, self.SOUL_FILE)
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated_content = f"""# 智能体灵魂定义 - 我是谁

{content}

【更新记录】
- 上次更新：{timestamp}
"""
            with open(soul_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)
            self.reload()
            return True
        except Exception as e:
            print(f"⚠️ 更新灵魂文件失败: {e}")
            return False

    def get_identity(self) -> str:
        """获取用户身份信息"""
        identity_path = os.path.join(self._prompts_dir, self.IDENTITY_FILE)
        try:
            if os.path.exists(identity_path):
                with open(identity_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
        except Exception as e:
            print(f"⚠️ 读取身份文件失败: {e}")
        return "尚不清楚 - 无法读取身份信息"

    def get_status(self) -> str:
        """获取智能体状态"""
        status_path = os.path.join(self._prompts_dir, self.STATUS_FILE)
        try:
            if os.path.exists(status_path):
                with open(status_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
        except Exception as e:
            print(f"⚠️ 读取状态文件失败: {e}")
        return "这是第一次对话 - 无法读取状态信息"

    def get_soul(self) -> str:
        """获取智能体灵魂定义 - 角色定位核心"""
        soul_path = os.path.join(self._prompts_dir, self.SOUL_FILE)
        try:
            if os.path.exists(soul_path):
                with open(soul_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
        except Exception as e:
            print(f"⚠️ 读取灵魂文件失败: {e}")
        return """我是DeepSeek，一个智能AI助手。
请创建 99_SOUL.md 文件来定义我的完整身份、角色、性格和能力边界。"""

    def get_skill_prompts(self) -> List[Dict]:
        """获取所有基础技能提示词（以1_开头的文件）"""
        return [p for p in self._prompts if p['file'].startswith(self.SKILL_PREFIX)]

    def get_soul_prompt(self) -> Optional[Dict]:
        """获取灵魂提示词（99_SOUL.md）"""
        for p in self._prompts:
            if p['file'] == self.SOUL_FILE:
                return p
        return None

    def _create_default_prompts(self):
        """创建默认提示词（已由各 _ensure_* 方法处理）"""
        pass

    @classmethod
    def _skill_sort_key(cls, filename: str):
        """技能文件按前缀数字排序，非技能文件返回 None。"""
        m = cls._SKILL_FILE_RE.match(filename)
        return int(m.group(1)) if m else None

    def _prompt_sort_key(self, p):
        """排序规则：00_IDENTITY → 技能文件(按数字升序) → 99_SOUL → 100_STATUS → 其他"""
        file = p['file']
        if file == self.IDENTITY_FILE:
            return (0,)
        skill_num = self._skill_sort_key(file)
        if skill_num is not None:
            return (1, skill_num)
        if file == self.SOUL_FILE:
            return (2,)
        if file == self.STATUS_FILE:
            return (4,)
        return (3, file)  # 其他文件按名字排序

    def get_prompts(self, join_with: str = "\n\n") -> str:
        """获取所有提示词，按顺序拼接（identity → 技能文件 → soul → status）"""
        if not self._prompts:
            return ""
        sorted_prompts = sorted(self._prompts, key=self._prompt_sort_key)
        contents = [p['content'] for p in sorted_prompts]
        return join_with.join(contents)

    def get_sorted_prompts_list(self) -> List[Dict]:
        """获取按正确顺序排列的提示词列表（用于 system 消息构建）。"""
        return sorted(self._prompts, key=self._prompt_sort_key)

    def get_prompts_list(self) -> List[Dict]:
        """获取所有提示词列表（原始加载顺序，已弃用，请用 get_sorted_prompts_list）。"""
        return self._prompts.copy()

    def get_skill_prompts(self) -> List[Dict]:
        """获取所有基础技能提示词（N_ 开头的文件）。"""
        return [p for p in self._prompts if self._skill_sort_key(p['file']) is not None]

    def reload(self):
        """重新加载所有提示词"""
        self._prompts = []
        self._load_prompts()
        return len(self._prompts)

    def get_prompt_count(self) -> int:
        return len(self._prompts)

    def get_prompts_dir_path(self) -> str:
        return self._prompts_dir
