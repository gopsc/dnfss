"""解析器测试：FSM / 列数校验(S01) / 占位符语义 / 序列化往返 / 全量脚本"""

import io

import pytest

from pybot.parser import (DoesNotSync, MatrixParser, MatrixSaver,
                          get_mt, parse_mt)
from tests.conftest import SCRIPTS_DIR


def parse_all(text):
    parser = MatrixParser(text)
    objects = []
    while True:
        obj = parser.parse()
        if obj is None:
            break
        objects.append(obj)
    return objects


# ---------------- 基本解析 ----------------

def test_parse_simple_block():
    objs = parse_all("@Foo\nname val;\na 1;\nb 2;\n@\n")
    assert len(objs) == 1
    m = objs[0]
    assert m.name == "Foo"
    assert m.size == 3 and m.col == 2
    assert m[0, 0] == "name" and m[2, 1] == "2"


def test_parse_multiple_blocks_and_comments():
    text = ("# 头部注释\n"
            "@A\nx y;\n1 2;\n@\n"
            "@B\nn;\nv;  # 行内注释\n@\n")
    objs = parse_all(text)
    assert [o.name for o in objs] == ["A", "B"]
    assert objs[1][1, 0] == "v"


def test_parse_std_mt():
    objs = parse_all((SCRIPTS_DIR / "std.mt").read_text(encoding="utf-8"))
    names = [o.name for o in objs]
    assert names == ["Alias", "I2C", "PCA9685", "MPU6050_BIND",
                     "Speaker", "Servo", "Action"]
    servo = objs[names.index("Servo")]
    assert servo.size == 11 and servo.col == 6      # 表头 + 10 舵机
    # 注释掉的 MPU6050_BIND 数据行不产生内容
    assert objs[names.index("MPU6050_BIND")].size == 1


@pytest.mark.parametrize("script", sorted(SCRIPTS_DIR.glob("*.mt")),
                         ids=lambda p: p.name)
def test_parse_all_scripts(script):
    """全量 scripts/*.mt 解析：块内每行列数一致且非空"""
    objs = parse_all(script.read_text(encoding="utf-8"))
    assert objs, f"{script.name}: no blocks parsed"
    for o in objs:
        assert o.name
        for row in o.values:
            assert len(row) == o.col


# ---------------- S01：列数校验 ----------------

def test_does_not_sync():
    with pytest.raises(DoesNotSync) as ei:
        parse_all("@Foo\na b c;\n1 2;\n@\n")
    assert ei.value.expected == 3 and ei.value.actual == 2


def test_missing_semicolon_merges_lines():
    """漏写分号导致跨行合并成一行（S04 回归场景），列数不齐即报错"""
    with pytest.raises(DoesNotSync):
        parse_all("@Foo\na b;\n1 2\n3 4;\n@\n")


# ---------------- 占位符语义 ----------------

def _matrix(text):
    return parse_all(text)[0]


def test_placeholder_recursion():
    m = _matrix("@T\nname v;\na 5;\nb -;\nc -;\n@\n")
    assert get_mt(m, 3, 1) == "5"           # 递归两级到首行
    assert parse_mt(m, 3, 1) == 5.0


def test_placeholder_first_row():
    """首行 '-'：get_mt 返回字面 '-'；parse_mt 解析为 typ("0")
    ⚠️ 与 actpred/mt_parser.py 不同（后者用 @Servo 初始角度填充），不得误统一"""
    m = _matrix("@T\nname v;\na -;\n@\n")
    assert get_mt(m, 1, 1) == "-"
    assert parse_mt(m, 1, 1) == 0.0
    assert parse_mt(m, 1, 1, int) == 0


# ---------------- 序列化往返 ----------------

def test_roundtrip():
    text = (SCRIPTS_DIR / "std.mt").read_text(encoding="utf-8")
    objs = parse_all(text)
    out = io.StringIO()
    MatrixSaver(out).save(objs)
    objs2 = parse_all(out.getvalue())
    assert len(objs2) == len(objs)
    for a, b in zip(objs, objs2):
        assert a.name == b.name
        assert a.values == b.values
