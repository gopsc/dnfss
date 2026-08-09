"""硬件变量 + RobotStruct 全流程测试（Mock I2C 总线，journal 断言）

关键期望值（与 bot4cj + PCA9685.lua 逐字节对齐）：
    45°  → 脉宽 0.5+2*45/180 = 1.0ms → offval = int(1.0*4096/20) = 204
    135° → 脉宽 2.0ms → offval = int(2.0*4096/20) = 409（s3 反装 45°→135°）
    0°   → 脉宽 0.5ms → offval = 102（首行 '-' 占位解析为 0）
"""

from pybot.robot import RobotStruct
from tests.conftest import SCRIPTS_DIR

PRESCALE = 121      # int(25e6/(4096*50) - 1 + 0.5)


def std_robot(mock_i2c):
    with open(SCRIPTS_DIR / "std.mt", encoding="utf-8") as f:
        return RobotStruct(f, dry_run=True)


def _writes(mock, fd):
    return [(e[2], e[3]) for e in mock.journal
            if e[0] == "write_reg" and e[1] == fd]


def test_create_hardvars(mock_i2c):
    robot = std_robot(mock_i2c)
    assert set(robot.M) == {"A", "F", "i2c-0", "i2c-1", "pca9685-0", "spk",
                            "s1", "s2", "s3", "s4", "s5",
                            "s6", "s7", "s8", "s9", "s10"}
    # 两条 I2C 总线在工厂阶段即打开
    opens = [e for e in mock_i2c.journal if e[0] == "open"]
    assert [e[1] for e in opens] == ["/dev/i2c-0", "/dev/i2c-1"]
    # 别名变量
    assert robot.M["A"].play({}) == "angle"
    assert robot.M["F"].play({}) == "frequency"
    robot.deactive()


def test_pca9685_build_sequence(mock_i2c):
    robot = std_robot(mock_i2c)
    fd = robot.M["i2c-1"].play({})["fd"]
    robot.active()
    # 初始化序列：复位 → SLEEP → prescale=121 → 唤醒（与 Lua 版一致）
    assert _writes(mock_i2c, fd) == [
        (0x00, 0x80), (0x00, 0x10), (0xFE, PRESCALE), (0x00, 0x20)]
    assert ("set_addr", fd, 0x40) in mock_i2c.journal
    robot.deactive()


def test_run_stand_action(mock_i2c):
    robot = std_robot(mock_i2c)
    fd = robot.M["i2c-1"].play({})["fd"]
    robot.active()
    mock_i2c.journal.clear()        # 只看 @Action 阶段
    robot.run()
    writes = _writes(mock_i2c, fd)
    reg = lambda ch: 0x06 + 4 * ch  # noqa: E731

    def servo_writes(ch, offval):
        return [(reg(ch) + 0, 0x00), (reg(ch) + 1, 0x00),
                (reg(ch) + 2, offval & 0xFF), (reg(ch) + 3, offval >> 8)]

    # s1: 45° 正装 → offval 204；s2: '-' 首行 → 0° → 102
    assert writes[0:4] == servo_writes(0, 204)
    assert writes[4:8] == servo_writes(1, 102)
    # s3: max=-90 反装，45° → 135° → offval 409（写 [153, 1]）
    assert writes[8:12] == servo_writes(2, 409)
    # s9/s10: 90° → offval = int(1.5*4096/20) = 307
    assert writes[32:36] == servo_writes(8, 307)
    assert writes[36:40] == servo_writes(9, 307)
    # 10 舵机 × 4 寄存器
    assert len(writes) == 40
    robot.deactive()


def test_deactive_reverse_and_idempotent(mock_i2c):
    robot = std_robot(mock_i2c)
    fd0 = robot.M["i2c-0"].play({})["fd"]
    fd1 = robot.M["i2c-1"].play({})["fd"]
    robot.active()
    robot.deactive()
    # 倒序：先 PCA9685 复位（fd1 写 0x80），再关 i2c-1，最后关 i2c-0
    closes = [e[1] for e in mock_i2c.journal if e[0] == "close"]
    assert closes == [fd1, fd0]
    reset_idx = mock_i2c.journal.index(("write_reg", fd1, 0x00, 0x80),
                                       mock_i2c.journal.index(("close", fd1))
                                       - 5)
    assert reset_idx < mock_i2c.journal.index(("close", fd1))
    # 幂等：重复调用不再产生操作
    n = len(mock_i2c.journal)
    robot.deactive()
    assert len(mock_i2c.journal) == n


def test_redefined_hardvar_destroys_old(mock_i2c, caplog):
    """重名硬件变量：destroy 旧值 + 告警（修复 r.cj:911 FIXME）"""
    text = ("@I2C\nname path;\nx /dev/i2c-0;\nx /dev/i2c-1;\n@\n"
            "@Action\nx.angle;\n0;\n@\n")
    robot = RobotStruct(text, dry_run=True)
    closes = [e for e in mock_i2c.journal if e[0] == "close"]
    assert len(closes) == 1         # 旧的 /dev/i2c-0 已被关闭
    assert any("redefined" in r.message for r in caplog.records)
    robot.deactive()


def test_action_validation(mock_i2c):
    """Q05：执行前整表校验列名格式与设备存在性"""
    import pytest
    text = ("@I2C\nname path;\ni /dev/i2c-0;\n@\n"
            "@Action\nbadcolumn;\n0;\n@\n")
    robot = RobotStruct(text, dry_run=True)
    with pytest.raises(ValueError, match="not valid"):
        robot.run()
    robot.deactive()

    text2 = ("@I2C\nname path;\ni /dev/i2c-0;\n@\n"
             "@Action\nghost.angle;\n0;\n@\n")
    robot2 = RobotStruct(text2, dry_run=True)
    with pytest.raises(ValueError, match="not defined"):
        robot2.run()
    robot2.deactive()
