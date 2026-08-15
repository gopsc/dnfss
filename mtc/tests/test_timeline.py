"""时间轴 IR 测试：Segment 插值 / Channel 重叠截断 / 采样保持语义"""

import pytest

from mtc.timeline import Channel, Segment, total_ms


# ---------------- Segment ----------------

def test_segment_value_at_endpoints_and_midpoint():
    s = Segment(t0=50, dur=100, v0=10, v1=30)
    assert s.end == 150
    assert s.value_at(50) == 10        # 段起点取 v0
    assert s.value_at(100) == 20       # 中点线性
    assert s.value_at(150) == 30       # 段尾取 v1
    assert s.value_at(0) == 10         # 段前保持 v0
    assert s.value_at(999) == 30       # 段后保持 v1


# ---------------- Channel ----------------

def test_channel_target_defaults_to_initial():
    ch = Channel("s1.angle")
    assert ch.target == 0.0 and ch.busy_until == 0.0
    ch.set_initial(45)
    assert ch.target == 45.0


def test_channel_set_initial_after_segment_rejected():
    ch = Channel("s1.angle")
    ch.add(0, 100, 0, 10)
    with pytest.raises(ValueError):
        ch.set_initial(45)


def test_channel_overlap_truncates_prev_and_warns():
    """重叠：前段截断至新段 t0，基准取前段目标值（由调用方传 v0）"""
    ch = Channel("s1.angle")
    ch.add(0, 100, 0, 10)              # [0,100) 0→10
    v0 = ch.target                     # 10（前段目标值）
    ch.add(50, 100, v0, v0 + 10)       # [50,150) 10→20
    assert len(ch.segments) == 2
    assert ch.segments[0].dur == 50    # 前段截断至 t0=50
    assert ch.segments[0].end == 50
    assert ch.overlap_warns and "截断" in ch.overlap_warns[0]
    # 截断点连续：前段尾值 == 新段起点值
    assert ch.value_at(50) == 10
    assert ch.value_at(100) == 15


def test_channel_value_at_no_segment_uses_initial():
    ch = Channel("s1.angle")
    ch.set_initial(30)
    assert ch.value_at(10) == 30
    assert ch.value_at(9999) == 30


def test_channel_value_at_holds_last_target_after_all_segments():
    ch = Channel("s1.angle")
    ch.add(0, 100, 0, 10)
    ch.add(200, 100, 10, 5)
    assert ch.value_at(150) == 10      # 段间空隙保持前段目标值
    assert ch.value_at(100000) == 5    # 全部完结后保持最后目标值


def test_channel_rest_value_falls_back_in_gaps():
    """frequency 型通道（rest=0）：段前/段间空隙/段后统一回落静止值"""
    ch = Channel("spk.frequency", rest=0.0)
    ch.add(20, 30, 1000, 1000)         # [20,50) 阶跃段
    ch.add(70, 20, 2000, 2000)         # [70,90)
    assert ch.value_at(10) == 0        # 首段之前
    assert ch.value_at(30) == 1000     # 段内
    assert ch.value_at(50) == 1000     # 段尾端点仍属段内
    assert ch.value_at(60) == 0        # 段间空隙回落 rest
    assert ch.value_at(80) == 2000
    assert ch.value_at(999) == 0       # 全部段后回落 rest


def test_channel_rest_does_not_affect_in_segment_value():
    """rest 仅作用段外：舵机通道设 rest 时段内插值不受影响"""
    ch = Channel("s1.angle", rest=0.0)
    ch.add(0, 100, 10, 20)
    assert ch.value_at(50) == 15


def test_total_ms_takes_max_of_channels_and_cursor():
    a, b = Channel("a"), Channel("b")
    a.add(0, 100, 0, 1)
    b.add(50, 100, 0, 1)
    assert total_ms({"a": a, "b": b}, cursor=0) == 150
    assert total_ms({"a": a, "b": b}, cursor=500) == 500
