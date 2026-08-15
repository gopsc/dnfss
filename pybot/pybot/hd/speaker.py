"""扬声器 —— 移植自 bot4cj/src/hd/snd.cj

与仓颉版的差异（审计修复 S03）：
    仓颉版 play 只是 sleep 占位、不出声；本版用标准库 wave 生成正弦波
    PCM 文件，经 `aplay -q` 播放（树莓派自带 alsa-utils）。
    aplay 不存在时降级为 sleep 占位（保持时序节拍不变）；
    dry_run 模式下仅记日志立即返回（不 sleep、不出声）。

时序约定：play() 阻塞总耗时对齐 delay_ms（与仓颉版 sleep(delay) 节拍一致），
播放提前结束则补足剩余时间。frequency <= 0 为静音节拍（不出声仅保节拍，
对齐仓颉版 sleep 占位语义），正频率仍按 [min_f, max_f] 钳制。
"""

import logging
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
import wave

log = logging.getLogger("pybot.speaker")

FADE_MS = 5     # 首尾淡入淡出时长，消除爆音


class Speaker:
    """正弦波蜂鸣扬声器

    volumn: 0.0~1.0 音量；max_f/min_f: 频率钳制范围（Hz）
    """

    def __init__(self, channels=1, sample_rate=44100, volumn=0.5,
                 max_f=20000.0, min_f=20.0, dry_run=False):
        self.channels = channels
        self.sample_rate = sample_rate
        self.volumn = volumn
        self.max_f = max_f
        self.min_f = min_f
        self.dry_run = dry_run
        self._aplay = shutil.which("aplay")
        if self._aplay is None and not dry_run:
            log.warning("aplay not found, speaker degrades to silent sleep")

    def play(self, frequency, delay_ms):
        """播放 frequency Hz 正弦波 delay_ms 毫秒（阻塞）

        frequency <= 0 为静音节拍：不出声、仅保持 delay 时序（对齐
        仓颉版 sleep 占位语义）。此前 0Hz 会被钳制到 min_f 发出低鸣，
        破坏了 .mt 首行 freq=0（jump.mt/std.mt）的静默意图。
        """
        if frequency <= 0:                      # 静音节拍
            if self.dry_run:
                log.info("speaker(dry-run): silence %dms", delay_ms)
                return
            time.sleep(delay_ms / 1000.0)
            return
        # 频率钳制（与仓颉版一致）
        if frequency > self.max_f:
            frequency = self.max_f
        if frequency < self.min_f:
            frequency = self.min_f
        if self.dry_run:
            log.info("speaker(dry-run): %.1fHz %dms", frequency, delay_ms)
            return
        start = time.monotonic()
        if self._aplay is not None:
            try:
                self._play_aplay(frequency, delay_ms)
            except (OSError, subprocess.SubprocessError) as e:
                log.warning("aplay failed: %s", e)
        # 补足剩余时间，保证总耗时对齐 delay_ms
        remain = delay_ms / 1000.0 - (time.monotonic() - start)
        if remain > 0:
            time.sleep(remain)

    # ---------------- 内部 ----------------

    def _synth(self, frequency, delay_ms):
        """生成带 5ms 淡入淡出的 16bit PCM 正弦波"""
        n = int(self.sample_rate * delay_ms / 1000.0)
        fade = min(int(self.sample_rate * FADE_MS / 1000.0), n // 2)
        amp = self.volumn * 32767.0
        w = 2.0 * math.pi * frequency / self.sample_rate
        frames = bytearray()
        for i in range(n):
            gain = 1.0
            if i < fade:
                gain = i / fade                 # 淡入
            elif i >= n - fade:
                gain = (n - i) / fade           # 淡出
            v = int(amp * gain * math.sin(w * i))
            frames += struct.pack("<h", v) * self.channels
        return bytes(frames)

    def _play_aplay(self, frequency, delay_ms):
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="pybot_beep_")
        os.close(fd)
        try:
            with wave.open(path, "wb") as f:
                f.setnchannels(self.channels)
                f.setsampwidth(2)               # 16bit
                f.setframerate(self.sample_rate)
                f.writeframes(self._synth(frequency, delay_ms))
            subprocess.run([self._aplay, "-q", path], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            os.unlink(path)
