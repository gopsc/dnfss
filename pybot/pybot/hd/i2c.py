"""Linux I2C 封装 —— 移植自 bot4cj/src/hd/I2C.cj + src/cpp/I2C.c

纯标准库实现（fcntl.ioctl + os.read/write），零第三方依赖。

与仓颉版的差异（审计修复，语义不变）：
    S06: read_reg 返回 0..255 无符号 int（仓颉版返回有符号 Int8，
         16 位拼接时符号扩展破坏低字节）；
    Q03: 每 fd 锁用 dict[fd → threading.Lock]（仓颉版 20 槽数组 O(n) 扫描且容量受限）。

后端可切换：默认真实总线；use_mock() 切换为记录型 Mock（--dry-run 与单测共用），
Mock 的写序列 journal 是与仓颉版做逐字节 diff 的等价性验证工件（方案 §6.3）。
"""

import fcntl
import logging
import os
import threading
from contextlib import contextmanager

log = logging.getLogger("pybot.i2c")

I2C_SLAVE = 0x0703      # at least on arm64（与仓颉版一致）


class I2CError(OSError):
    """I2C 设备异常（对应仓颉版 I2cExcept 系列）"""


# ---------------- 后端（真实总线 / Mock） ----------------

class _RealBackend:
    """真实 I2C 总线：glibc open/ioctl/read/write 一层到底"""

    def open(self, path):
        return os.open(path, os.O_RDWR)

    def close(self, fd):
        os.close(fd)

    def set_addr(self, fd, addr):
        fcntl.ioctl(fd, I2C_SLAVE, addr)

    def write_reg(self, fd, reg, val):
        if os.write(fd, bytes([reg & 0xFF, val & 0xFF])) != 2:
            raise I2CError(f"failed to write i2c reg 0x{reg:02X} (fd={fd})")

    def read_reg(self, fd, reg):
        # 读取寄存器之前要先写寄存器地址
        if os.write(fd, bytes([reg & 0xFF])) != 1:
            raise I2CError(f"failed to select i2c reg 0x{reg:02X} (fd={fd})")
        data = os.read(fd, 1)
        if len(data) != 1:
            raise I2CError(f"failed to read i2c reg 0x{reg:02X} (fd={fd})")
        return data[0]                      # 0..255 无符号（修复 S06）

    def write_block(self, fd, reg, data):
        """向 reg 起连写多字节（ADS1115 等 16 位寄存器设备）"""
        buf = bytes([reg & 0xFF]) + bytes(data)
        if os.write(fd, buf) != len(buf):
            raise I2CError(f"failed to write i2c block at reg 0x{reg:02X} (fd={fd})")

    def read_block(self, fd, reg, n):
        """从 reg 起连读 n 字节（自动递增寄存器，MPU6050 一次读 14 字节）"""
        if os.write(fd, bytes([reg & 0xFF])) != 1:
            raise I2CError(f"failed to select i2c reg 0x{reg:02X} (fd={fd})")
        data = os.read(fd, n)
        if len(data) != n:
            raise I2CError(f"short read at i2c reg 0x{reg:02X}: "
                           f"{len(data)}/{n} bytes (fd={fd})")
        return data


class MockBackend:
    """记录型 Mock 总线：不碰硬件，记录全部操作序列供断言/diff"""

    def __init__(self):
        self.journal = []       # [(op, ...), ...] 全部操作按序记录
        self.reg_values = {}    # {(fd, reg): int} 单寄存器读注入
        self.block_values = {}  # {(fd, reg): bytes} 块读注入
        self._next_fd = 1000

    def open(self, path):
        fd = self._next_fd
        self._next_fd += 1
        self.journal.append(("open", path, fd))
        return fd

    def close(self, fd):
        self.journal.append(("close", fd))

    def set_addr(self, fd, addr):
        self.journal.append(("set_addr", fd, addr))

    def write_reg(self, fd, reg, val):
        self.journal.append(("write_reg", fd, reg & 0xFF, val & 0xFF))

    def read_reg(self, fd, reg):
        self.journal.append(("read_reg", fd, reg))
        return self.reg_values.get((fd, reg), 0) & 0xFF

    def write_block(self, fd, reg, data):
        self.journal.append(("write_block", fd, reg & 0xFF, bytes(data)))

    def read_block(self, fd, reg, n):
        self.journal.append(("read_block", fd, reg, n))
        return self.block_values.get((fd, reg), bytes(n))


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


# ---------------- I2C 锁具（每 fd 一把，修复 Q03） ----------------
# 同一 fd 上"设地址 + 读/写寄存器"必须是原子序列（多设备共享总线）

_MAIN_LOCK = threading.Lock()   # 主锁，操作锁具注册表时使用
_LOCKS = {}                     # {fd: threading.Lock}


def _register_lock(fd):
    with _MAIN_LOCK:
        _LOCKS[fd] = threading.Lock()


def _unregister_lock(fd):
    with _MAIN_LOCK:
        _LOCKS.pop(fd, None)


@contextmanager
def transaction(fd):
    """with transaction(fd): 持有该 fd 的总线锁，保证 I2C 操作序列原子"""
    with _MAIN_LOCK:
        lock = _LOCKS.get(fd)
    if lock is None:
        raise I2CError(f"no i2c lock for fd {fd} (device not opened by pybot?)")
    with lock:
        yield


# ---------------- 模块级函数（供插件 modules/*.py 直接调用） ----------------
# 对应仓颉版注册给 Lua 的 i2c_open/i2c_close/i2c_set_addr/i2c_write_reg/i2c_read_reg

def open_dev(path):
    fd = _backend.open(path)
    _register_lock(fd)
    log.debug("i2c open %s -> fd=%d", path, fd)
    return fd


def close_dev(fd):
    _unregister_lock(fd)
    _backend.close(fd)
    log.debug("i2c close fd=%d", fd)


def set_addr(fd, addr):
    _backend.set_addr(fd, addr)


def write_reg(fd, reg, val):
    _backend.write_reg(fd, reg, val)


def read_reg(fd, reg):
    return _backend.read_reg(fd, reg)


def write_block(fd, reg, data):
    _backend.write_block(fd, reg, data)


def read_block(fd, reg, n):
    return _backend.read_block(fd, reg, n)


# ---------------- I2C 总线对象（对应仓颉版 I2C_Orig） ----------------

class I2CBus:
    """持有一个 I2C 设备文件句柄；close() 幂等"""

    def __init__(self, path):
        self.path = path
        self.fd = open_dev(path)

    def close(self):
        if self.fd >= 0:
            close_dev(self.fd)
            self.fd = -1

    def set_addr(self, addr):
        set_addr(self.fd, addr)

    def write_reg(self, reg, val):
        write_reg(self.fd, reg, val)

    def read_reg(self, reg):
        return read_reg(self.fd, reg)

    def write_block(self, reg, data):
        write_block(self.fd, reg, data)

    def read_block(self, reg, n):
        return read_block(self.fd, reg, n)

    def transaction(self):
        return transaction(self.fd)
