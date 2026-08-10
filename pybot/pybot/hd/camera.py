"""摄像头帧队列 —— pybot 新增（无仓颉版对应）

后台守护线程按固定帧率抓帧，维护一个有界帧队列
（deque(maxlen=capacity)，满了丢最旧帧）——实时控制场景永远消费最新帧。
生命周期模式对齐 MPU6050 采样线程：
    setup() → start_daemon() → latest()/snapshot() → stop_daemon()/wait_daemon() → close()

帧来源 picamera2（树莓派 OS 预装）是可选依赖：仅在 setup() 内惰性导入，
不使用 @Camera 的脚本 / 单测 / --dry-run 都不需要它，零依赖承诺不变。
测试可注入 grabber（callable() -> frame）完全脱离硬件。

dry_run 模式：setup/start 均为空操作（不碰硬件），latest() 恒为 None。
"""

import logging
import threading
import time
from collections import deque

log = logging.getLogger("pybot.camera")


class Camera:
    """picamera2 摄像头 + 后台有界帧队列

    width/height: 帧尺寸；framerate: 抓帧帧率；capacity: 队列容量（满丢最旧）；
    grabber: 可注入的抓帧回调（测试用），None = 用 picamera2 抓帧。
    """

    def __init__(self, width=640, height=480, framerate=30.0, capacity=4,
                 grabber=None, dry_run=False):
        self.width = width
        self.height = height
        self.framerate = framerate
        self.capacity = capacity
        self.dry_run = dry_run
        self._grabber = grabber
        self._picam = None
        self._queue = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # ---------------- 初始化 ----------------

    def setup(self):
        """打开摄像头（dry_run / 已初始化 / 注入 grabber 时不碰硬件，幂等）"""
        if self.dry_run or self._grabber is not None:
            return
        try:
            from picamera2 import Picamera2     # 可选依赖，惰性导入
        except ImportError:
            raise RuntimeError(
                "@Camera 需要可选依赖 picamera2（树莓派 OS 预装，勿 pip 安装）；"
                "无硬件环境请用 --dry-run 或注入 grabber") from None
        self._picam = Picamera2()
        self._picam.configure(self._picam.create_video_configuration(
            main={"size": (self.width, self.height)}))
        self._picam.start()
        self._grabber = self._picam.capture_array
        time.sleep(0.1)     # 等传感器稳定（对齐 MPU6050 setup）
        log.debug("camera setup done (%dx%d @ %.1ffps)",
                  self.width, self.height, self.framerate)

    # ---------------- 帧访问（线程安全） ----------------

    def latest(self):
        """最新一帧 (monotonic 时间戳, frame)；队列空返回 None"""
        with self._lock:
            return self._queue[-1] if self._queue else None

    def snapshot(self):
        """队列快照 list（旧→新），供消费方批量取帧"""
        with self._lock:
            return list(self._queue)

    @property
    def size(self):
        """当前队列内帧数"""
        with self._lock:
            return len(self._queue)

    # ---------------- 抓帧线程 ----------------

    def start_daemon(self):
        if self.dry_run or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="camera-grabber", daemon=True)
        self._thread.start()
        log.debug("camera grabber started")

    def _grab_once(self):
        """抓一帧入队（满则挤掉最旧帧）；单独成方法便于无线程单测"""
        frame = self._grabber()
        with self._lock:
            self._queue.append((time.monotonic(), frame))

    def _loop(self):
        interval = 1.0 / self.framerate
        while not self._stop.wait(interval):
            try:
                self._grab_once()
            except Exception:
                # grabber 异常类型取决于帧源实现，守护线程不许死
                log.warning("camera grab failed", exc_info=True)

    def stop_daemon(self):
        self._stop.set()

    def wait_daemon(self):
        if self._thread is not None:
            self._thread.join()
            self._thread = None
            log.debug("camera grabber stopped")

    # ---------------- 释放 ----------------

    def close(self):
        """关闭摄像头；幂等"""
        if self._picam is not None:
            self._picam.stop()
            self._picam.close()
            self._picam = None
            log.debug("camera closed")
