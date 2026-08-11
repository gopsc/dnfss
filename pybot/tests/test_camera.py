"""Camera 测试：有界帧队列（满丢最旧）、守护线程生命周期、dry_run、.mt 接入

全部经注入 grabber 运行，不碰 picamera2 / 真实摄像头。
"""

import time

from pybot.hd.camera import Camera
from pybot.robot import RobotStruct


class FakeGrabber:
    """假帧源：产出递增序号帧"""

    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1
        return self.count


CAMERA_MT = """
@Camera
 name  width  height  framerate  capacity;
 cam0    320      240        15         2;
@

@Action
cam0.frame;
0;
@
"""


def test_queue_capacity_drops_oldest():
    cam = Camera(capacity=3, grabber=FakeGrabber())
    for _ in range(5):
        cam._grab_once()
    assert [f for _, f in cam.snapshot()] == [3, 4, 5]  # 容量 3，丢最旧
    assert cam.latest()[1] == 5
    assert cam.size == 3


def test_latest_empty():
    assert Camera(grabber=FakeGrabber()).latest() is None
    assert Camera(grabber=FakeGrabber()).snapshot() == []


def test_daemon_lifecycle():
    cam = Camera(framerate=200.0, capacity=10, grabber=FakeGrabber())
    cam.setup()                     # 注入 grabber 时不碰硬件
    cam.start_daemon()
    time.sleep(0.1)
    cam.stop_daemon()
    cam.wait_daemon()
    assert cam.size > 0
    n = cam.size
    time.sleep(0.05)
    assert cam.size == n            # 停止后队列不再增长


def test_grab_failure_keeps_daemon_alive(caplog):
    def bad_grabber():
        raise RuntimeError("boom")
    cam = Camera(framerate=200.0, capacity=2, grabber=bad_grabber)
    cam.start_daemon()
    time.sleep(0.05)
    cam.stop_daemon()
    cam.wait_daemon()
    assert cam.size == 0
    assert any("grab failed" in r.message for r in caplog.records)


def test_dry_run_no_hardware():
    cam = Camera(dry_run=True)
    cam.setup()
    cam.start_daemon()              # 空操作
    cam.stop_daemon()
    cam.wait_daemon()
    cam.close()
    assert cam.latest() is None


def test_camera_var_from_matrix():
    robot = RobotStruct(CAMERA_MT, dry_run=True)
    cam = robot.M["cam0"].camera
    assert (cam.width, cam.height) == (320, 240)
    assert cam.framerate == 15.0
    assert cam.capacity == 2
    robot.deactive()


def test_camera_full_lifecycle_in_robot():
    """dry_run 下 build/run/destroy 全流程不碰硬件"""
    robot = RobotStruct(CAMERA_MT, dry_run=True)
    robot.active()                  # build：dry_run 空操作
    assert robot.M["cam0"].play({}) is None
    robot.run()                     # @Action 引用 cam0.frame 不报错
    robot.deactive()
