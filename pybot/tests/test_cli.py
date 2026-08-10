"""CLI 端到端测试：--dry-run / --test roundtrip / stdin 输入"""

import io
import sys

from tests.conftest import SCRIPTS_DIR

from pybot.main import main


def test_stdin_input(monkeypatch):
    """stdin 输入（模拟 cat script | pybot）"""
    src = (SCRIPTS_DIR / "std.mt").read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(src))
    monkeypatch.setattr(sys, "argv", ["pybot", "--dry-run"])
    # 验证不抛异常且返回 0
    assert main(["--dry-run"]) == 0


def test_cli_help():
    """--help 返回 0"""
    try:
        main(["--help"])
    except SystemExit as e:
        assert e.code == 0


def test_test_roundtrip(tmp_path):
    """--test roundtrip in.mt out.mt 往返"""
    src = (SCRIPTS_DIR / "std.mt").read_text(encoding="utf-8")
    infile = tmp_path / "in.mt"
    infile.write_text(src, encoding="utf-8")
    outfile = tmp_path / "out.mt"
    assert main(["--test", "roundtrip", str(infile), str(outfile)]) == 0
    # 输出可被再次解析
    from pybot.parser import MatrixParser
    parser = MatrixParser(outfile.read_text(encoding="utf-8"))
    blocks = []
    while True:
        obj = parser.parse()
        if obj is None:
            break
        blocks.append(obj)
    assert len(blocks) == 7      # std.mt 含 7 个 @块


def test_test_unknown_mode():
    """未知 --test 模式返回 2"""
    assert main(["--test", "unknown", "/dev/null", "/dev/null"]) == 2
