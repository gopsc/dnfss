"""端到端：mtc 编译产物 → pybot MatrixParser 解析断言

验证闭环：编译产物必须被 pybot 零修改解析（列数一致、Q05 表头
'设备.参数' 校验、栅格行数与数值逐一比对）。
"""

from mtc.dsl import compile_script
from tests.conftest import load_pybot_parser
from tests.test_emit import EXAMPLE


def _parse(mt_text):
    parser_mod = load_pybot_parser()
    parser = parser_mod.MatrixParser(mt_text)
    objects = []
    while True:
        obj = parser.parse()
        if obj is None:
            break
        objects.append(obj)
    return objects


def test_compiled_mt_parses_with_pybot():
    objs = _parse(compile_script(EXAMPLE)[0])
    names = [o.name for o in objs]
    assert names == ["Servo", "Speaker", "Action"]
    action = objs[names.index("Action")]
    assert action.size == 11           # 表头 + 10 栅格行
    assert action.col == 4             # s1/s2 + spk.frequency + spk.delay
    for row in action.values:          # 列数一致（DoesNotSync 不触发）
        assert len(row) == action.col


def test_compiled_action_grid_values():
    objs = _parse(compile_script(EXAMPLE)[0])
    action = [o for o in objs if o.name == "Action"][0]
    assert action.values[0] == ["s1.angle", "s2.angle",
                                "spk.frequency", "spk.delay"]
    grid = action.values[1:]
    assert [r[0] for r in grid] == [str(i) for i in range(1, 11)]
    assert [r[1] for r in grid] == ["0"] * 5 + ["4", "8", "12", "16", "20"]
    assert {r[2] for r in grid} == {"0"}       # 无 Beep：frequency 恒 0
    assert {r[3] for r in grid} == {"10"}


def test_compiled_mt_roundtrips_through_pybot():
    """编译产物经 pybot 解析→序列化→再解析等价（roundtrip 自检）"""
    import io
    parser_mod = load_pybot_parser()
    objs = _parse(compile_script(EXAMPLE)[0])
    out = io.StringIO()
    parser_mod.MatrixSaver(out).save(objs)
    objs2 = _parse(out.getvalue())
    assert [(a.name, a.values) for a in objs] == \
           [(b.name, b.values) for b in objs2]


def test_q05_header_contract_holds():
    """@Action 全部列名恰含一个 '.'（pybot robot._validate_action 契约）"""
    objs = _parse(compile_script(EXAMPLE)[0])
    action = [o for o in objs if o.name == "Action"][0]
    for col in action.values[0]:
        assert col.count(".") == 1
