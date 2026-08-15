"""DSL 收集测试：硬件声明 / 时间轴指令 / 校验规则"""

import pytest

from mtc.dsl import Collector, CompileError, compile_script, make_namespace


def run(source):
    """exec 脚本并返回 collector"""
    c = Collector()
    exec(compile(source, "<test>", "exec"), make_namespace(c))
    return c


# ---------------- 硬件声明 ----------------

def test_hw_blocks_collected_in_order():
    c = run('I2C("i2c-1", "/dev/i2c-1")\n'
            'Servo("s1", 90, 0, 0, "pca9685-0", 0)\n'
            'Servo("s2", 180, 0, 0, "pca9685-0", 1)\n')
    assert [b[0] for b in c.blocks] == ["I2C", "Servo", "Servo"]
    assert c.blocks[1][2] == ["s1", "90", "0", "0", "pca9685-0", "0"]
    assert c.device_names() == {"i2c-1", "s1", "s2"}


def test_hw_block_arity_error():
    with pytest.raises(CompileError, match="需要 6 个参数"):
        run('Servo("s1", 90, 0)\n')


def test_register_block_extension():
    c = run('register_block("ADS1115", ["name", "I2C", "addr"])\n'
            'ADS1115("ads0", "i2c-1", "0x48")\n')
    assert c.blocks[0][0] == "ADS1115"
    assert c.blocks[0][1] == ["name", "I2C", "addr"]


def test_register_block_requires_name_first():
    with pytest.raises(CompileError, match="首列必须是 name"):
        run('register_block("Bad", ["addr"])\n')


# ---------------- 时间轴指令 ----------------

def test_action_delay_wait_cursor():
    c = run('Servo("s1", 90, 0, 0, "pca9685-0", 0)\n'
            'Servo("s2", 90, 0, 0, "pca9685-0", 1)\n'
            'Action("s1", 10, 100)\n'
            'Delay(50)\n'
            'Action("s2", 20, 50)\n'
            'Wait()\n')
    assert c.cursor == 100.0           # Wait 推进到最晚完成时刻
    assert c.channels["s1.angle"].segments[0].v1 == 10
    assert c.channels["s2.angle"].segments[0].t0 == 50


def test_action_relative_accumulates():
    c = run('Servo("s1", 90, 0, 0, "pca9685-0", 0)\n'
            'Action("s1", 10, 100)\n'
            'Wait()\n'
            'Action("s1", 10, 100)\n')
    segs = c.channels["s1.angle"].segments
    assert (segs[1].v0, segs[1].v1) == (10, 20)   # 相对叠加在前段目标值上


def test_setto_absolute():
    c = run('Servo("s1", 90, 0, 0, "pca9685-0", 0)\n'
            'Pose("s1", 45)\n'
            'SetTo("s1", 90, 100)\n')
    seg = c.channels["s1.angle"].segments[0]
    assert (seg.v0, seg.v1) == (45, 90)


def test_beep_registers_frequency_channel():
    c = run('Speaker("spk", 2, 48000, 0.6, 5000, 20)\n'
            'Beep(1000, 50)\n')
    assert "spk.frequency" in c.channels
    assert c.channels["spk.frequency"].segments[0].v1 == 1000


# ---------------- 校验 ----------------

def test_action_undeclared_device_rejected():
    with pytest.raises(CompileError, match="未声明"):
        run('Action("s1", 10, 100)\n')


def test_dur_must_be_positive_multiple_of_10():
    with pytest.raises(CompileError, match="10 的倍数"):
        run('Servo("s1", 90, 0, 0, "pca9685-0", 0)\n'
            'Action("s1", 10, 15)\n')
    with pytest.raises(CompileError, match="10 的倍数"):
        run('Delay(5)\n')


def test_delay_column_is_reserved():
    with pytest.raises(CompileError, match="编译器管理"):
        run('Speaker("spk", 2, 48000, 0.6, 5000, 20)\n'
            'Action("spk", 10, 100, param="delay")\n')


def test_pose_after_action_rejected():
    with pytest.raises(CompileError, match="Action 之前"):
        run('Servo("s1", 90, 0, 0, "pca9685-0", 0)\n'
            'Action("s1", 10, 100)\n'
            'Pose("s1", 45)\n')


def test_empty_script_rejected():
    with pytest.raises(CompileError, match="没有任何动作"):
        compile_script('Servo("s1", 90, 0, 0, "pca9685-0", 0)\n')


def test_python_loop_and_math_work():
    """Python DSL 红利：for 循环与数学运算直接可用"""
    c = run('Servo("s1", 90, 0, 0, "pca9685-0", 0)\n'
            'for i in range(4):\n'
            '    Action("s1", 5 if i % 2 == 0 else -5, 20)\n'
            '    Wait()\n')
    segs = c.channels["s1.angle"].segments
    assert len(segs) == 4
    assert [s.v1 for s in segs] == [5, 0, 5, 0]


def test_syntax_error_wrapped():
    with pytest.raises(CompileError, match="SyntaxError"):
        compile_script("def broken(:\n")
