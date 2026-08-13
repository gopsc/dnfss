"""tools.basic_tools 单元测试

覆盖 A 批抽离的 7 个工具类：
  - FileToolBase           : 路径安全检查
  - ReadFileTool           : 读文件指引
  - WriteFileTool          : 写文件指引
  - ListFilesTool          : 列目录指引
  - SimpleWebCrawlerTool   : 网页访问指引
  - GetCurrentTimeTool     : 时间获取
  - CalculatorTool         : 数学计算

设计原则：
  - 不依赖网络/文件系统副作用（除 FileToolBase 路径检查用临时文件）
  - 不依赖 DeepSeek API、websockets 等外部服务
  - 验证工具接口契约（name/description/parameters/execute 返回类型）
  - 验证命令行指引类工具的核心语义（返回包含正确命令关键词 + run_command 提示）
"""

import os
import queue
import tempfile
from datetime import datetime

import pytest

from tools import (
    FileToolBase,
    ReadFileTool,
    WriteFileTool,
    ListFilesTool,
    SimpleWebCrawlerTool,
    GetCurrentTimeTool,
    CalculatorTool,
)


# ==================== 通用接口契约测试 ====================

# (工具类, 期望 get_name 返回值, 必填参数键列表)
TOOL_CASES = [
    (ReadFileTool, "read_file", ["filepath"]),
    (WriteFileTool, "write_file", ["filepath", "content"]),
    (ListFilesTool, "list_files", []),
    (SimpleWebCrawlerTool, "web", ["action", "url"]),
    (GetCurrentTimeTool, "get_current_time", []),
    (CalculatorTool, "calculator", ["a", "b", "operation"]),
]


@pytest.mark.parametrize("tool_cls,expected_name,required_keys", TOOL_CASES)
def test_tool_interface_contract(tool_cls, expected_name, required_keys):
    """所有工具必须满足接口契约：name/描述非空、parameters 是合法 schema、必填字段在 properties 中。"""
    tool = tool_cls()
    assert tool.get_name() == expected_name

    desc = tool.get_description()
    assert isinstance(desc, str) and len(desc) > 0

    params = tool.get_parameters()
    assert isinstance(params, dict)
    assert params.get("type") == "object"
    props = params.get("properties", {})
    assert isinstance(props, dict) and len(props) > 0
    for k in required_keys:
        assert k in props, f"必填参数 {k} 未出现在 properties 中"
    # required 中声明的字段必须在 properties 中
    for k in params.get("required", []):
        assert k in props


# ==================== GetCurrentTimeTool ====================

def test_get_current_time_full_format():
    tool = GetCurrentTimeTool()
    r = tool.execute(format="full")
    assert "当前时间" in r
    # 应包含 YYYY-MM-DD HH:MM:SS 形式
    assert datetime.now().strftime("%Y-%m-%d") in r


def test_get_current_time_date_format():
    tool = GetCurrentTimeTool()
    r = tool.execute(format="date")
    assert datetime.now().strftime("%Y-%m-%d") in r


def test_get_current_time_time_format():
    tool = GetCurrentTimeTool()
    r = tool.execute(format="time")
    assert datetime.now().strftime("%H:%M") in r


def test_get_current_time_streaming_emits_complete():
    tool = GetCurrentTimeTool()
    q: queue.Queue = queue.Queue()
    tool.execute_streaming(q, format="date")
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    # 至少应有一个 complete 事件
    types = [m[0] for m in msgs]
    assert "complete" in types
    # complete 事件内容非空
    complete_msgs = [m[1] for m in msgs if m[0] == "complete"]
    assert complete_msgs and len(complete_msgs[0]) > 0


# ==================== CalculatorTool ====================

def test_calculator_add():
    r = CalculatorTool().execute(a=3, b=4, operation="add")
    assert "7" in r and "计算成功" in r


def test_calculator_subtract():
    r = CalculatorTool().execute(a=10, b=4, operation="subtract")
    assert "6" in r


def test_calculator_multiply():
    r = CalculatorTool().execute(a=6, b=7, operation="multiply")
    assert "42" in r


def test_calculator_divide():
    r = CalculatorTool().execute(a=20, b=4, operation="divide")
    assert "5" in r


def test_calculator_divide_by_zero():
    r = CalculatorTool().execute(a=1, b=0, operation="divide")
    assert "除数不能为0" in r


def test_calculator_unknown_operation():
    r = CalculatorTool().execute(a=1, b=1, operation="power")
    assert "未知运算" in r


# ==================== ReadFileTool ====================
#
# 说明：使用 pytest 的 tmp_path fixture 创建临时文件作为 read_file 入参，
# 不依赖任何相对路径或 cwd——早期版本用 filepath="aibox.py" 假设从
# aibox/ 目录运行 pytest，从仓库根运行会因路径漂移失败（2026-08-14 修复）。

def test_read_file_head_returns_command_prompt(tmp_path):
    """head 模式应返回含 head 命令 + run_command 指引的 markdown。"""
    f = tmp_path / "sample.txt"
    f.write_text("line1\nline2\n")
    r = ReadFileTool().execute(filepath=str(f), head=True, max_lines=10)
    assert "head -n 10" in r
    assert "run_command" in r


def test_read_file_tail_returns_command_prompt(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("line1\nline2\n")
    r = ReadFileTool().execute(filepath=str(f), tail=True, max_lines=20)
    assert "tail -n 20" in r
    assert "run_command" in r


def test_read_file_range_returns_sed_command(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("line1\nline2\n")
    r = ReadFileTool().execute(filepath=str(f), start_line=5, end_line=15)
    assert "sed -n '5,15p'" in r
    assert "run_command" in r


def test_read_file_max_lines_cap_at_500(tmp_path):
    """max_lines 应被钳制到 500 防止超大范围。"""
    f = tmp_path / "sample.txt"
    f.write_text("line1\n")
    r = ReadFileTool().execute(filepath=str(f), max_lines=99999)
    assert "sed -n '1,500p'" in r


def test_read_file_missing_filepath_returns_error():
    r = ReadFileTool().execute(filepath=None)
    assert "❌" in r and "filepath" in r


def test_read_file_nonexistent_returns_error():
    r = ReadFileTool().execute(filepath="/no/such/file_xyz.txt", head=True)
    assert "❌" in r


# ==================== WriteFileTool ====================

def test_write_file_overwrite_mode():
    r = WriteFileTool().execute(filepath="output.txt", content="hello")
    assert ">" in r and "run_command" in r
    # 覆盖模式应使用单 > 重定向
    assert "cat > " in r


def test_write_file_append_mode():
    r = WriteFileTool().execute(filepath="output.txt", content="more", mode="a")
    assert "cat >> " in r
    assert "run_command" in r


def test_write_file_heredoc_uses_eof_marker():
    r = WriteFileTool().execute(filepath="out.txt", content="data")
    assert "AIBOX_EOF" in r


def test_write_file_missing_content_returns_error():
    r = WriteFileTool().execute(filepath="out.txt", content=None)
    assert "❌" in r and "content" in r


def test_write_file_creates_parent_dir_command():
    """写深路径时应在命令中包含 mkdir -p。"""
    r = WriteFileTool().execute(filepath="sub/dir/out.txt", content="x")
    assert "mkdir -p" in r


# ==================== ListFilesTool ====================

def test_list_files_default_no_hidden_flag():
    r = ListFilesTool().execute(directory=".")
    assert "ls -l " in r
    assert "run_command" in r


def test_list_files_show_hidden_flag():
    r = ListFilesTool().execute(directory=".", show_hidden=True)
    assert "ls -la " in r


def test_list_files_nonexistent_dir_returns_error():
    r = ListFilesTool().execute(directory="/no/such/dir_xyz")
    assert "❌" in r


def test_list_files_path_is_file_returns_error(tmp_path):
    f = tmp_path / "afile.txt"
    f.write_text("x")
    r = ListFilesTool().execute(directory=str(f))
    assert "❌" in r and "不是目录" in r


# ==================== SimpleWebCrawlerTool ====================

def test_web_get_action_returns_curl_prompt():
    r = SimpleWebCrawlerTool().execute(action="get", url="https://example.com")
    assert "curl -sL" in r
    assert "run_command" in r
    assert "https://example.com" in r


def test_web_title_action_uses_grep():
    r = SimpleWebCrawlerTool().execute(action="title", url="https://example.com")
    assert "curl" in r and "<title>" in r


def test_web_links_action_uses_grep():
    r = SimpleWebCrawlerTool().execute(action="links", url="https://example.com")
    assert "href" in r and "curl" in r


def test_web_text_action_uses_sed():
    r = SimpleWebCrawlerTool().execute(action="text", url="https://example.com")
    assert "sed" in r and "curl" in r


def test_web_search_requires_keyword():
    r = SimpleWebCrawlerTool().execute(action="search", url="https://example.com")
    assert "❌" in r and "keyword" in r


def test_web_search_with_keyword_uses_grep_c():
    r = SimpleWebCrawlerTool().execute(
        action="search", url="https://example.com", keyword="hello"
    )
    assert "grep -c" in r and "hello" in r


def test_web_unknown_action_returns_error():
    r = SimpleWebCrawlerTool().execute(action="download", url="https://example.com")
    assert "❌" in r and "未知操作" in r


# ==================== FileToolBase ====================

# FileToolBase 是抽象基类（继承自 Tool 的抽象方法未实现），无法直接实例化。
# 用一个最小具体子类来测试基类提供的 normalize_path / check_path_safety 方法。
class _ConcreteFileTool(FileToolBase):
    def get_name(self): return "_test"
    def get_description(self): return "test"
    def get_parameters(self): return {"type": "object", "properties": {}}
    def execute(self, **kwargs): return ""


def test_filetoolbase_normalize_path_expands_user():
    base = _ConcreteFileTool()
    p = base.normalize_path("~/foo")
    assert "~" not in p
    assert os.path.isabs(p)


def test_filetoolbase_check_path_safety_existing_file(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("hi")
    base = _ConcreteFileTool()
    ok, abs_path, err = base.check_path_safety(str(f), check_exists=True)
    assert ok is True
    assert abs_path == str(f)
    assert err == ""


def test_filetoolbase_check_path_safety_nonexistent_with_check(tmp_path):
    base = _ConcreteFileTool()
    ok, _, err = base.check_path_safety(str(tmp_path / "missing.txt"), check_exists=True)
    assert ok is False
    assert "文件不存在" in err


def test_filetoolbase_check_path_safety_oversize_file(tmp_path):
    """超大文件应被拒绝。"""
    f = tmp_path / "big.bin"
    # 写入略超 MAX_FILE_SIZE 的内容
    from common import constants
    f.write_bytes(b"x" * (constants.MAX_FILE_SIZE + 1))
    base = _ConcreteFileTool()
    ok, _, err = base.check_path_safety(str(f))
    assert ok is False
    assert "文件太大" in err


def test_filetoolbase_is_subclass_of_tool():
    """FileToolBase 应是 Tool 的子类，且三个文件工具都继承它。"""
    from tools import Tool
    assert issubclass(FileToolBase, Tool)
    assert issubclass(ReadFileTool, FileToolBase)
    assert issubclass(WriteFileTool, FileToolBase)
    assert issubclass(ListFilesTool, FileToolBase)
