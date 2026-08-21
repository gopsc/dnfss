#!/usr/bin/env python3
"""
common.atomic - 原子写文件工具（J5，审计 2026-08-19）

同目录 mkstemp 临时文件 + fsync + os.replace：进程中断/断电不留半截文件；
mkstemp 随机文件名避免多进程并发写同路径互踩（conversation_history 旧实现
的固定 .tmp 名在并发场景会互相覆盖）。仅依赖标准库，供 common/subsystems
各写路径复用。
"""

import os
import tempfile
import time


def atomic_write_text(path: str, content: str) -> None:
    """原子写文本文件。失败时清理临时文件并抛出原异常。"""
    path = os.path.expanduser(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".aibox_tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # T27（审计 2026-08-21）：Windows 下目标被其他进程打开（杀毒扫描/
        # 编辑器/并发读者）时 os.replace 抛 PermissionError。POSIX 无条件
        # 成功不受影响；Windows 做短退避重试，高频写路径（每条消息都原子
        # 写 current_conversation.json）一次冲突不再直接丢该条持久化。
        last_exc = None
        for attempt in range(3):
            try:
                os.replace(tmp_path, path)
                last_exc = None
                break
            except PermissionError as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

