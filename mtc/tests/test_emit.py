"""emit 测试：数值格式化 / .mt 文本结构 / 用户设计示例逐字比对"""

from mtc.dsl import compile_script
from mtc.emit import fmt_num

# 用户设计示例（修正后）：非阻塞时间轴，s2 第 6 行起步
EXAMPLE = '''
Servo("s1", 180, 0, 0, "pca9685-0", 0)
Servo("s2", 180, 0, 0, "pca9685-0", 1)
Speaker("spk", 2, 48000, 0.6, 5000, 20)
Action("s1", 10, 100)
Delay(50)
Action("s2", 20, 50)
'''

EXPECTED_MT = """\
@Servo
name max min offset controller channel;
s1 180 0 0 pca9685-0 0;
s2 180 0 0 pca9685-0 1;
@

@Speaker
name channels sample_rate volumn max_f min_f;
spk 2 48000 0.6 5000 20;
@

@Action
s1.angle s2.angle spk.frequency spk.delay;
1 0 0 10;
2 0 0 10;
3 0 0 10;
4 0 0 10;
5 0 0 10;
6 4 0 10;
7 8 0 10;
8 12 0 10;
9 16 0 10;
10 20 0 10;
@
"""


def test_example_matches_user_design_exactly():
    text, warnings = compile_script(EXAMPLE)
    assert warnings == []
    assert text == EXPECTED_MT


def test_fmt_num_integer_and_decimal():
    assert fmt_num(10.0) == "10"
    assert fmt_num(0) == "0"
    assert fmt_num(-3.0) == "-3"
    assert fmt_num(1.666666) == "1.667"
    assert fmt_num(2.500) == "2.5"


def test_non_integer_step_values():
    """10°/6 步：栅格值保留 3 位小数（pybot parse_mt float 可解析）"""
    text, _ = compile_script(
        'Servo("s1", 180, 0, 0, "pca9685-0", 0)\n'
        'Speaker("spk", 2, 48000, 0.6, 5000, 20)\n'
        'Action("s1", 10, 60)\n')
    rows = [l for l in text.splitlines() if l.endswith("10;")]
    assert len(rows) == 6
    assert rows[0].startswith("1.667 ")
    assert rows[-1].startswith("10 ")


def test_no_speaker_no_delay_column_and_warns():
    text, warnings = compile_script(
        'Servo("s1", 180, 0, 0, "pca9685-0", 0)\n'
        'Action("s1", 10, 20)\n')
    assert any("无 Speaker" in w for w in warnings)
    assert "s1.angle;" in text        # 表头无 frequency/delay 列
    assert "frequency" not in text


def test_beep_gap_falls_back_to_zero_silence():
    text, _ = compile_script(
        'Speaker("spk", 2, 48000, 0.6, 5000, 20)\n'
        'Beep(1000, 30)\n'
        'Delay(50)\n')               # T = max(段尾30, 游标50) = 50 → 5 行
    lines = text.splitlines()
    assert "spk.frequency spk.delay;" in lines
    rows = [l for l in lines if l.endswith(" 10;")]
    # 段内 1000；段后回落 0 = 静音（对齐 pybot speaker freq<=0 语义）
    assert [r.split()[0] for r in rows] == ["1000", "1000", "1000", "0", "0"]


def test_pure_delay_script_fills_freq_zero_column():
    """纯延时：自动补 spk.frequency 恒 0 列（SpeakerVar.play 两键契约）"""
    text, _ = compile_script(
        'Speaker("spk", 2, 48000, 0.6, 5000, 20)\n'
        'Delay(50)\n')
    assert "spk.frequency spk.delay;" in text
    assert text.count("0 10;") == 5
