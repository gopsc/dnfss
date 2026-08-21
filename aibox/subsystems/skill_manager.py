#!/usr/bin/env python3
"""
subsystems.skill_manager - 扩展技能管理器

抽离自 aibox.py，收集并管理 ~/.aibox/skills/ 目录下的技能文件。

支持两种技能格式：
  1. 可执行脚本/程序：执行 --help，第一行为简介
  2. Markdown 文件 (.md)：第一行为简介（# 标题或普通文本），其余为技能内容

依赖：common (constants, Logger) + 标准库
不反向依赖 aibox 任何符号。
"""

import os
import subprocess
from typing import Dict, List, Optional, Tuple

from common import constants, Logger


class SkillManager:
    """扩展技能管理器 - 收集并管理 ~/.aibox/skills/ 目录下的技能文件。

    支持两种技能格式：
    1. 可执行脚本/程序：执行 --help，第一行为简介
    2. Markdown 文件 (.md)：第一行为简介（# 标题或普通文本），其余为技能内容
    """

    # J16（审计 2026-08-19）：--help 输出上限——内容会进系统提示词
    MAX_HELP_LENGTH = 2000

    def __init__(self, logger: Optional[Logger] = None):
        self.logger = logger or Logger("SkillManager")
        # name -> {path, file, summary, content, is_markdown}
        self.skills: Dict[str, Dict] = {}
        self._load_skills()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _run_help(self, skill_path: str) -> str:
        """执行 skill_path --help 并返回完整输出（stdout 优先，否则 stderr）。

        J16（审计 2026-08-19）：输出截断到 MAX_HELP_LENGTH——技能 --help
        输出会进系统提示词，异常/恶意脚本可撑爆上下文。
        """
        try:
            result = subprocess.run(
                [skill_path, "--help"],
                capture_output=True,
                text=True,
                timeout=10,
                env=os.environ.copy()
            )
            output = result.stdout.strip()
            if not output:
                output = result.stderr.strip()
            if len(output) > self.MAX_HELP_LENGTH:
                output = output[:self.MAX_HELP_LENGTH] + "\n…（已截断）"
            return output
        except Exception as e:
            self.logger.warning(f"运行 {skill_path} --help 失败: {e}")
            return ""

    def _read_markdown_skill(self, skill_path: str) -> Tuple[str, str]:
        """读取 Markdown 技能文件，返回 (简介, 完整内容)"""
        try:
            with open(skill_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            if not content:
                return "（空文件）", ""

            # 提取第一行非空内容作为简介
            lines = content.splitlines()
            summary = ""
            for line in lines:
                stripped = line.strip()
                if stripped:
                    # 如果是 Markdown 标题，去掉 # 前缀
                    if stripped.startswith('#'):
                        summary = stripped.lstrip('#').strip()
                    else:
                        summary = stripped
                    break

            if not summary:
                summary = os.path.basename(skill_path).replace('.md', '')

            return summary, content

        except Exception as e:
            self.logger.warning(f"读取 Markdown 技能文件失败 {skill_path}: {e}")
            return "（读取失败）", ""

    @staticmethod
    def _first_nonempty_line(text: str) -> str:
        """返回文本中第一行非空内容。"""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    def _is_markdown_file(self, filename: str) -> bool:
        """判断是否为 Markdown 文件"""
        return filename.lower().endswith('.md')

    def _is_executable(self, filepath: str) -> bool:
        """检查文件是否可执行"""
        if os.name == 'nt':  # Windows
            # Windows 下检查扩展名
            executable_extensions = {'.exe', '.bat', '.cmd', '.ps1', '.py', '.sh'}
            ext = os.path.splitext(filepath)[1].lower()
            return ext in executable_extensions
        else:  # Unix/Linux/Mac
            return os.access(filepath, os.X_OK)

    # ------------------------------------------------------------------
    # 加载 / 重载
    # ------------------------------------------------------------------

    def _load_skills(self):
        """扫描技能目录，加载所有技能文件（可执行文件或 Markdown 文件）。

        T26（审计 2026-08-21）：本地构建 new_skills 完成后一次性原子替换，
        对齐 PromptManager M6——此前先 self.skills = {} 清空再逐个重建，
        reload 期间（每个可执行技能 --help 最多 10s）并发读者会看到空/半截
        技能表（技能"消失"再"逐渐出现"）。"""
        skills_dir = constants.SKILLS_DIR
        new_skills = {}

        if not os.path.exists(skills_dir):
            self.logger.info(f"技能目录不存在: {skills_dir}")
            self.skills = new_skills
            return

        skill_files = [
            f for f in os.listdir(skills_dir)
            if os.path.isfile(os.path.join(skills_dir, f))
            and not f.startswith('_')
            and not f.startswith('.')
        ]

        # J16（审计 2026-08-19）：同名键冲突（foo 可执行文件与 foo.md）此前
        # 由 listdir 顺序决定谁覆盖谁，行为不确定。改为可执行优先 + 确定性：
        # 先加载可执行技能，Markdown 遇到已占用的同名键时跳过并告警
        executable_files = [
            f for f in skill_files
            if not self._is_markdown_file(f)
        ]
        markdown_files = [f for f in skill_files if self._is_markdown_file(f)]

        for skill_file in executable_files + markdown_files:
            skill_path = os.path.join(skills_dir, skill_file)
            skill_name = os.path.splitext(skill_file)[0]
            is_markdown = self._is_markdown_file(skill_file)

            if is_markdown:
                if skill_name in new_skills:
                    self.logger.warning(
                        f"Markdown 技能 '{skill_file}' 与同名可执行技能冲突，已跳过"
                        f"（可执行技能优先）")
                    continue
                # Markdown 格式技能：直接读取内容
                summary, content = self._read_markdown_skill(skill_path)
                if content:
                    new_skills[skill_name] = {
                        "path": skill_path,
                        "file": skill_file,
                        "summary": summary,
                        "content": content,
                        "is_markdown": True,
                        "is_executable": False
                    }
                    self.logger.info(f"已加载 Markdown 技能: {skill_name} — {summary}")
                else:
                    self.logger.warning(f"Markdown 技能文件为空（跳过）: {skill_file}")

            else:
                # 可执行文件格式技能：执行 --help 获取简介
                if not self._is_executable(skill_path):
                    self.logger.warning(f"技能文件不可执行（跳过）: {skill_file}")
                    continue

                help_output = self._run_help(skill_path)
                summary = self._first_nonempty_line(help_output) or f"扩展技能: {skill_name}"

                new_skills[skill_name] = {
                    "path": skill_path,
                    "file": skill_file,
                    "summary": summary,
                    "content": help_output,  # 完整的 --help 输出作为内容
                    "is_markdown": False,
                    "is_executable": True
                }
                self.logger.info(f"已加载可执行技能: {skill_name} — {summary}")

        self.skills = new_skills
        self.logger.info(f"共加载 {len(new_skills)} 个技能（{len([s for s in new_skills.values() if s['is_markdown']])} 个 Markdown 技能）")

    def reload(self) -> int:
        """重新扫描技能目录，返回加载的技能数量。"""
        self._load_skills()
        return len(self.skills)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def search(self, keyword: str) -> List[Dict]:
        """在技能名称、简介和内容中搜索关键字，返回匹配项列表。"""
        kw = keyword.lower()
        results = []
        for name, info in self.skills.items():
            match = (
                kw in name.lower() or
                kw in info["summary"].lower() or
                kw in info.get("content", "").lower()
            )
            if match:
                results.append({
                    "name": name,
                    "path": info["path"],
                    "summary": info["summary"],
                    "content": info.get("content", ""),
                    "is_markdown": info.get("is_markdown", False),
                    "is_executable": info.get("is_executable", False)
                })
        return results

    def get_skill_content(self, skill_name: str) -> Optional[str]:
        """获取技能的内容（Markdown 技能返回完整内容，可执行技能返回 --help 输出）"""
        info = self.skills.get(skill_name)
        if info:
            return info.get("content")
        return None

    def get_skill(self, skill_name: str) -> Optional[Dict]:
        """获取单个技能的完整信息"""
        return self.skills.get(skill_name)

    def get_all(self) -> List[Dict]:
        """返回所有技能的列表（含名称、路径、简介和内容）。"""
        return [
            {
                "name": name,
                "path": info["path"],
                "summary": info["summary"],
                "content": info.get("content", ""),
                "is_markdown": info.get("is_markdown", False),
                "is_executable": info.get("is_executable", False)
            }
            for name, info in self.skills.items()
        ]

    def count(self) -> int:
        return len(self.skills)
