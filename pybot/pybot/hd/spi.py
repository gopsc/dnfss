"""Linux spidev 封装 —— 对应 bot4cj/modules/SPI.lua 所需的底层能力

纯标准库实现（fcntl.ioctl + ctypes 缓冲区），零第三方依赖。
与 hd.i2c 同构：后端可切换（use_mock()/use_real()），
Mock journal 供 --dry-run 与单测断言。

spi_ioc_transfer 结构（32 字节，struct.pack 'QQIIHBBBBH'）：
    __u64 tx_buf; __u64 rx_buf; __u32 len; __u32 speed_hz;
    __u16 delay_usecs; __u8 bits_per_word; __u8 cs_change;
    __u8 tx_nbits; __u8 rx_nbits; __u16 pad;
"""

import ctypes
import fcntl
import logging
import os
import struct

log = logging.getLogger("pybot.spi")

# spidev ioctl 常量（linux/spi/spidev.h，小端 arm64/x86_64 通用）
SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
SPI_IOC_MESSAGE_1 = 0x40206B00      # SPI_IOC_MESSAGE(1)


class SPIError(OSError):
    """SPI 设备异常"""


# ---------------- 后端（真实总线 / Mock） ----------------

class _RealBackend:

    def open(self, path):
        return os.open(path, os.O_RDWR)

    def close(self, fd):
        os.close(fd)

    def setup(self, fd, mode, bits, speed):
        fcntl.ioctl(fd, SPI_IOC_WR_MODE, struct.pack("B", mode))
        fcntl.ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", bits))
        fcntl.ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("I", speed))

    def transfer(self, fd, tx, speed, bits):
        """全双工传输：发送 tx 字节串，返回等长 rx 字节串"""
        n = len(tx)
        tx_buf = ctypes.create_string_buffer(bytes(tx), n)
        rx_buf = ctypes.create_string_buffer(n)
        msg = struct.pack("QQIIHBBBBH",
                          ctypes.addressof(tx_buf), ctypes.addressof(rx_buf),
                          n, speed, 0, bits, 0, 0, 0, 0)
        if fcntl.ioctl(fd, SPI_IOC_MESSAGE_1, msg) < 0:
            raise SPIError(f"spi transfer failed (fd={fd}, {n} bytes)")
        return rx_buf.raw


class MockBackend:
    """记录型 Mock：不碰硬件，记录全部操作序列供断言/diff"""

    def __init__(self):
        self.journal = []
        self.rx_values = {}     # {fd: bytes} transfer 返回值注入
        self._next_fd = 2000

    def open(self, path):
        fd = self._next_fd
        self._next_fd += 1
        self.journal.append(("open", path, fd))
        return fd

    def close(self, fd):
        self.journal.append(("close", fd))

    def setup(self, fd, mode, bits, speed):
        self.journal.append(("setup", fd, mode, bits, speed))

    def transfer(self, fd, tx, speed, bits):
        self.journal.append(("transfer", fd, bytes(tx), speed, bits))
        rx = self.rx_values.get(fd, b"")
        return rx.ljust(len(tx), b"\x00")[:len(tx)]


_backend = _RealBackend()


def use_mock():
    """切换到 Mock 总线（--dry-run / 单测），返回 MockBackend 以便读取 journal"""
    global _backend
    _backend = MockBackend()
    return _backend


def use_real():
    """切换回真实总线"""
    global _backend
    _backend = _RealBackend()


# ---------------- 模块级函数（供插件 modules/*.py 直接调用） ----------------

def open_dev(path):
    fd = _backend.open(path)
    log.debug("spi open %s -> fd=%d", path, fd)
    return fd


def close_dev(fd):
    _backend.close(fd)
    log.debug("spi close fd=%d", fd)


def setup(fd, mode=0, bits=8, speed=500000):
    _backend.setup(fd, mode, bits, speed)


def transfer(fd, tx, speed=500000, bits=8):
    return _backend.transfer(fd, tx, speed, bits)
