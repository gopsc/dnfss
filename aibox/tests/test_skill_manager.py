"""subsystems.skill_manager 单元测试

覆盖：
  - SkillManager 加载 Markdown 技能
  - 加载可执行技能（--help）
  - search / get_skill_content / get_all / reload / count
每个测试用独立临时技能目录，互不污染。
"""
import os

import pytest

from subsystems import SkillManager
from common import constants


@pytest.fixture
def isolated_skill_dir(tmp_path, monkeypatch):
    """每个测试用独立技能目录 + monkeypatch constants.SKILLS_DIR。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(type(constants), "SKILLS_DIR",
                        property(lambda self: str(skills_dir)))
    return skills_dir


@pytest.fixture
def sm(isolated_skill_dir):
    """构造 SkillManager（依赖 isolated_skill_dir fixture 注入目录）。"""
    return SkillManager()


# ==================== 空目录 ====================

def test_empty_skills_dir(sm):
    assert sm.count() == 0
    assert sm.get_all() == []


# ==================== Markdown 技能 ====================

def test_loads_markdown_skill(isolated_skill_dir, sm):
    """Markdown 技能应被加载，简介取首行（# 标题去 # 前缀）。"""
    skill_file = isolated_skill_dir / "weather.md"
    skill_file.write_text("# 查询天气\n查询指定城市天气\n详细用法...", encoding="utf-8")

    sm.reload()
    assert sm.count() == 1

    s = sm.get_skill("weather")
    assert s is not None
    assert s["summary"] == "查询天气"
    assert s["is_markdown"] is True
    assert s["is_executable"] is False
    assert "查询指定城市天气" in s["content"]


def test_markdown_skill_content_retrieval(isolated_skill_dir, sm):
    """sm fixture 构造时目录为空，写入文件后需 reload 才能加载。"""
    (isolated_skill_dir / "notes.md").write_text("# 笔记技能\n笔记内容ABC", encoding="utf-8")
    sm.reload()
    content = sm.get_skill_content("notes")
    assert "笔记内容ABC" in content


def test_markdown_skill_empty_file_skipped(isolated_skill_dir, sm):
    """空文件应被跳过。"""
    (isolated_skill_dir / "empty.md").write_text("", encoding="utf-8")
    sm.reload()
    assert sm.count() == 0


def test_markdown_skill_no_title_uses_filename(isolated_skill_dir, sm):
    """无 # 标题时，首行普通文本作为简介。"""
    (isolated_skill_dir / "plain.md").write_text("这是普通首行\n第二行", encoding="utf-8")
    sm.reload()
    s = sm.get_skill("plain")
    assert s["summary"] == "这是普通首行"


# ==================== 隐藏/下划线文件跳过 ====================

def test_hidden_file_skipped(isolated_skill_dir, sm):
    (isolated_skill_dir / ".hidden.md").write_text("# 隐藏", encoding="utf-8")
    (isolated_skill_dir / "_internal.md").write_text("# 内部", encoding="utf-8")
    sm.reload()
    assert sm.count() == 0


# ==================== 搜索 ====================

def test_search_by_name(isolated_skill_dir, sm):
    (isolated_skill_dir / "weather.md").write_text("# 天气\n查天气", encoding="utf-8")
    (isolated_skill_dir / "calculator.md").write_text("# 计算器\n算数", encoding="utf-8")
    sm.reload()

    results = sm.search("weather")
    assert len(results) == 1
    assert results[0]["name"] == "weather"


def test_search_by_summary(isolated_skill_dir, sm):
    (isolated_skill_dir / "x.md").write_text("# 天气预报技能\n内容", encoding="utf-8")
    sm.reload()
    results = sm.search("天气")
    assert len(results) == 1


def test_search_by_content(isolated_skill_dir, sm):
    (isolated_skill_dir / "x.md").write_text("# 标题\n独特关键词XYZ123", encoding="utf-8")
    sm.reload()
    results = sm.search("XYZ123")
    assert len(results) == 1


def test_search_no_match(isolated_skill_dir, sm):
    (isolated_skill_dir / "x.md").write_text("# 标题\n内容", encoding="utf-8")
    sm.reload()
    assert sm.search("不存在的关键词") == []


def test_search_case_insensitive(isolated_skill_dir, sm):
    (isolated_skill_dir / "Weather.md").write_text("# Weather Skill\ncontent", encoding="utf-8")
    sm.reload()
    assert len(sm.search("weather")) == 1
    assert len(sm.search("WEATHER")) == 1


# ==================== get_all / count ====================

def test_get_all_returns_list(isolated_skill_dir, sm):
    (isolated_skill_dir / "a.md").write_text("# A\n内容A", encoding="utf-8")
    (isolated_skill_dir / "b.md").write_text("# B\n内容B", encoding="utf-8")
    sm.reload()

    all_skills = sm.get_all()
    assert len(all_skills) == 2
    assert all("name" in s and "summary" in s and "content" in s for s in all_skills)
    assert sm.count() == 2


# ==================== reload ====================

def test_reload_picks_up_new_file(isolated_skill_dir, sm):
    assert sm.count() == 0
    (isolated_skill_dir / "new.md").write_text("# 新技能\n内容", encoding="utf-8")
    n = sm.reload()
    assert n == 1
    assert sm.count() == 1


def test_reload_drops_deleted_file(isolated_skill_dir, sm):
    f = isolated_skill_dir / "temp.md"
    f.write_text("# 临时\n内容", encoding="utf-8")
    sm.reload()
    assert sm.count() == 1

    f.unlink()
    sm.reload()
    assert sm.count() == 0


# ==================== 不存在的技能 ====================

def test_get_nonexistent_skill_returns_none(sm):
    assert sm.get_skill("不存在") is None


def test_get_content_nonexistent_returns_none(sm):
    assert sm.get_skill_content("不存在") is None


# ==================== 可执行技能（跨平台：Windows 用 .bat，Unix 用 .sh） ====================

def test_loads_executable_skill(isolated_skill_dir, sm):
    """加载一个可执行技能文件（带 --help）。
    Windows 下用 .bat（SkillManager 的 _is_executable 识别 .bat），
    Unix 下用 .sh 并加可执行权限。
    """
    import os as _os
    if _os.name == 'nt':
        # Windows：用 .bat 文件
        skill_file = isolated_skill_dir / "greet.bat"
        skill_file.write_text(
            "@echo off\r\n"
            "echo 打招呼技能 - 输入名字打招呼\r\n",
            encoding="utf-8"
        )
    else:
        # Unix：用 .sh 并加可执行权限
        skill_file = isolated_skill_dir / "greet.sh"
        skill_file.write_text(
            "#!/bin/bash\n"
            "echo '打招呼技能 - 输入名字打招呼'\n",
            encoding="utf-8"
        )
        _os.chmod(str(skill_file), 0o755)

    sm.reload()
    s = sm.get_skill("greet")
    assert s is not None
    assert s["is_executable"] is True
    assert s["is_markdown"] is False
    assert "打招呼" in s["summary"]
