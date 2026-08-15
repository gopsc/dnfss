"""Speaker 测试：静音语义 / 频率钳制 / SpeakerVar 参数透传

静音语义（2026-08-15 修复）：frequency <= 0 不出声、仅保持 delay 节拍
（对齐仓颉版 sleep 占位语义）；此前 0Hz 被钳到 min_f 发出 20Hz 低鸣，
破坏了 .mt 首行 freq=0（jump.mt/std.mt）的静默意图。
"""

import logging

import pytest

from pybot.hardvars import SpeakerVar
from pybot.hd.speaker import Speaker


class _FakeSpeaker:
    """记录 play 调用的桩（SpeakerVar 透传测试用）"""

    def __init__(self):
        self.calls = []

    def play(self, freq, delay_ms):
        self.calls.append((freq, delay_ms))


# ---------------- 静音语义 ----------------

def test_zero_frequency_is_silent_and_keeps_delay(monkeypatch):
    """freq=0：不合成波形、不播放，仅 sleep 保持节拍"""
    spk = Speaker(dry_run=False)
    slept, synths = [], []
    monkeypatch.setattr("pybot.hd.speaker.time.sleep", lambda s: slept.append(s))
    monkeypatch.setattr(spk, "_synth", lambda *a: synths.append(a) or b"")
    spk.play(0, 100)
    assert slept == [0.1]
    assert synths == []                # 未走波形合成路径


def test_negative_frequency_is_silent(monkeypatch):
    spk = Speaker(dry_run=False)
    slept = []
    monkeypatch.setattr("pybot.hd.speaker.time.sleep", lambda s: slept.append(s))
    spk.play(-5, 50)
    assert slept == [0.05]


def test_zero_frequency_dry_run_logs_silence(caplog):
    """dry_run：仅记日志立即返回（不 sleep），日志标识 silence"""
    spk = Speaker(dry_run=True)
    with caplog.at_level(logging.INFO, logger="pybot.speaker"):
        spk.play(0, 100)
    assert "silence 100ms" in caplog.text
    assert "Hz" not in caplog.text


# ---------------- 正频率行为不破坏 ----------------

def test_positive_frequency_still_clamped(caplog):
    """正频率钳制不变：5Hz → min_f(20)，25000Hz → max_f"""
    spk = Speaker(dry_run=True, min_f=20.0, max_f=5000.0)
    with caplog.at_level(logging.INFO, logger="pybot.speaker"):
        spk.play(5, 10)
        spk.play(25000, 10)
    assert "20.0Hz 10ms" in caplog.text
    assert "5000.0Hz 10ms" in caplog.text


# ---------------- SpeakerVar 透传 ----------------

def test_speakervar_passes_zero_frequency_through():
    """.mt 行 freq=0 经 SpeakerVar 原样透传到 Speaker.play（进入静音分支）"""
    fake = _FakeSpeaker()
    var = SpeakerVar("spk", fake)
    assert var.play({"frequency": "0", "delay": "100"}) == {"status": "OK"}
    assert fake.calls == [(0.0, 100)]


def test_speakervar_requires_frequency_key():
    """缺 frequency 键仍 KeyError（契约不变：delay 列必须与 frequency 成对）"""
    var = SpeakerVar("spk", _FakeSpeaker())
    with pytest.raises(KeyError):
        var.play({"delay": "100"})
