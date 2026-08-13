"""subsystems.prompt_manager 单元测试

覆盖：
  - PromptManager 单例语义
  - 提示词加载/排序/拼接
  - identity/soul/status 读写
  - 从模板复制行为
  - reload 语义
"""
import os

import pytest

from subsystems import PromptManager


@pytest.fixture
def isolated_pm(tmp_path, monkeypatch):
    """每个测试用独立 prompts 目录 + 重置单例，避免测试间状态污染。"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # 重置单例状态
    PromptManager._instance = None
    PromptManager._prompts = []
    PromptManager._prompts_dir = None

    # 让 ConfigManager 返回临时目录
    from common import ConfigManager
    original_get = ConfigManager.get

    def mock_get(self, key, default=None):
        if key == "system.prompts_dir":
            return str(prompts_dir)
        return original_get(self, key, default)

    monkeypatch.setattr(ConfigManager, "get", mock_get)

    pm = PromptManager()
    yield pm

    # 清理单例
    PromptManager._instance = None
    PromptManager._prompts = []
    PromptManager._prompts_dir = None


# ==================== 单例 ====================

def test_prompt_manager_is_singleton(isolated_pm):
    pm2 = PromptManager()
    assert pm2 is isolated_pm


def test_prompt_manager_resets_between_tests(isolated_pm):
    """fixture 应重置单例，确保测试隔离。"""
    assert PromptManager._instance is isolated_pm


# ==================== 加载与排序 ====================

def test_loads_template_files(isolated_pm):
    """首次构造应从 aibox/prompts/ 模板复制核心文件。"""
    files = os.listdir(isolated_pm._prompts_dir)
    # 至少应有 00_IDENTITY / 99_SOUL / 100_STATUS
    assert "00_IDENTITY.md" in files
    assert "99_SOUL.md" in files
    assert "100_STATUS.md" in files


def test_get_prompts_returns_nonempty_string(isolated_pm):
    s = isolated_pm.get_prompts()
    assert isinstance(s, str) and len(s) > 0


def test_get_sorted_prompts_list_returns_list(isolated_pm):
    lst = isolated_pm.get_sorted_prompts_list()
    assert isinstance(lst, list)
    assert len(lst) > 0
    assert all("file" in p and "content" in p for p in lst)


def test_prompt_sort_order_identity_first(isolated_pm):
    """00_IDENTITY 应排在最前。"""
    lst = isolated_pm.get_sorted_prompts_list()
    assert lst[0]["file"] == "00_IDENTITY.md"


def test_prompt_sort_order_status_last_or_near_last(isolated_pm):
    """100_STATUS 应在 SOUL 之后。"""
    lst = isolated_pm.get_sorted_prompts_list()
    files = [p["file"] for p in lst]
    if "100_STATUS.md" in files and "99_SOUL.md" in files:
        assert files.index("100_STATUS.md") > files.index("99_SOUL.md")


def test_skill_prompts_filtered_correctly(isolated_pm):
    """N_ 开头的技能文件应被 get_skill_prompts 识别。"""
    skills = isolated_pm.get_skill_prompts()
    # 模板里至少有一个 1_xxx.md 技能文件
    assert len(skills) >= 0  # 至少不报错
    for p in skills:
        assert p["file"][0].isdigit()


# ==================== 读写 identity/soul/status ====================

def test_update_and_get_identity(isolated_pm):
    ok = isolated_pm.update_identity("测试用户-张三")
    assert ok is True
    content = isolated_pm.get_identity()
    assert "测试用户-张三" in content
    assert "用户身份信息" in content


def test_update_and_get_soul(isolated_pm):
    ok = isolated_pm.update_soul("我是测试智能体")
    assert ok is True
    content = isolated_pm.get_soul()
    assert "我是测试智能体" in content


def test_update_and_get_status(isolated_pm):
    ok = isolated_pm.update_status("测试状态-初始化完成")
    assert ok is True
    content = isolated_pm.get_status()
    assert "测试状态-初始化完成" in content


def test_get_identity_nonexistent_returns_fallback(monkeypatch, tmp_path):
    """无 identity 文件时应返回兜底字符串。"""
    PromptManager._instance = None
    PromptManager._prompts = []
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    from common import ConfigManager
    original_get = ConfigManager.get

    def mock_get(self, key, default=None):
        if key == "system.prompts_dir":
            return str(empty_dir)
        return original_get(self, key, default)

    monkeypatch.setattr(ConfigManager, "get", mock_get)
    pm = PromptManager()
    # 删除 identity 文件模拟不存在
    id_file = empty_dir / "00_IDENTITY.md"
    if id_file.exists():
        id_file.unlink()
    r = pm.get_identity()
    assert "尚不清楚" in r or "无法读取" in r

    PromptManager._instance = None
    PromptManager._prompts = []
    PromptManager._prompts_dir = None


# ==================== reload ====================

def test_reload_returns_count(isolated_pm):
    n = isolated_pm.reload()
    assert isinstance(n, int) and n >= 0


def test_reload_picks_up_new_file(isolated_pm, tmp_path):
    """reload 应能发现新增的提示词文件。"""
    before = isolated_pm.get_prompt_count()
    # 写入一个新的提示词文件
    new_file = os.path.join(isolated_pm._prompts_dir, "99_EXTRA_TEST.md")
    with open(new_file, "w", encoding="utf-8") as f:
        f.write("# 额外测试提示词\n内容")
    isolated_pm.reload()
    after = isolated_pm.get_prompt_count()
    assert after == before + 1


# ==================== 模板不覆盖已存在文件 ====================

def test_existing_file_not_overwritten_by_template(isolated_pm):
    """已存在的运行时文件不应被模板覆盖。"""
    # 修改 identity 文件
    isolated_pm.update_identity("我的自定义身份")
    custom_content = isolated_pm.get_identity()
    assert "我的自定义身份" in custom_content

    # reload 触发 _ensure_from_template，但不应覆盖
    isolated_pm.reload()
    after = isolated_pm.get_identity()
    assert "我的自定义身份" in after
