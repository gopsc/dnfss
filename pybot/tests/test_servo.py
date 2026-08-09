"""Servo 校准算法测试：正装/反装/漂移/钳位（与 Servo.cj 语义逐项对齐）"""

from pybot.hd.servo import Servo


def make(max_a, min_a, offset):
    sent = []
    servo = Servo(max_a, min_a, offset, sent.append)
    return servo, sent


def test_normal_install():
    servo, sent = make(90.0, 0.0, 0.0)
    servo.set_angle(45.0)
    assert sent == [45.0]


def test_clamp_max():
    servo, sent = make(90.0, 0.0, 0.0)
    servo.set_angle(120.0)
    assert sent == [90.0]


def test_clamp_min():
    servo, sent = make(180.0, 30.0, 0.0)
    servo.set_angle(10.0)
    assert sent == [30.0]


def test_offset():
    servo, sent = make(180.0, 0.0, 5.0)
    servo.set_angle(40.0)
    assert sent == [45.0]


def test_inverted_install():
    """负 max 表示反装：a = 180 - a（std.mt 的 s3 max=-90，45° → 135°）"""
    servo, sent = make(-90.0, 0.0, 0.0)
    servo.set_angle(45.0)
    assert sent == [180.0 - 45.0]


def test_inverted_clamp_uses_abs():
    """反装钳位用 |max|：-90 装机 100° → 钳到 90 → 翻转 90"""
    servo, sent = make(-90.0, 0.0, 0.0)
    servo.set_angle(100.0)
    assert sent == [90.0]
