#!/usr/bin/env python3
"""
subsystems.executors - 命令执行器

抽离自 aibox.py，提供命令执行的抽象基类与 subprocess 实现。
  - CommandExecutor           : 抽象基类，定义 execute / execute_streaming / cancel
  - SubprocessCommandExecutor : 基于 subprocess.Popen 的实现，支持超时/工作目录/
                                1MB 输出上限/线程安全取消

依赖：common (ConfigManager, constants, Logger) + 标准库
不反向依赖 aibox 任何符号。
"""

import os
import queue
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

from common import ConfigManager, constants, Logger


class CommandExecutor(ABC):
    """命令执行器抽象基类 - 负责执行系统命令"""

    def __init__(self, logger: Optional[Logger] = None):
        self.logger = logger or Logger("CommandExecutor")
        self.config = ConfigManager()
        self.process = None

    @abstractmethod
    def execute(self, command: str, timeout: int = 30, **kwargs) -> str:
        """执行命令并返回结果（非流式）"""
        pass

    @abstractmethod
    def execute_streaming(self, command: str, output_queue: queue.Queue, timeout: int = 30, **kwargs):
        """流式执行命令，通过队列输出结果"""
        pass

    def is_enabled(self) -> bool:
        """检查命令执行是否启用"""
        return constants.COMMANDS_ENABLED

    def format_result(self, success: bool, message: str, data: Dict = None) -> str:
        """统一结果格式化"""
        result = []
        emoji = "✅" if success else "❌"
        result.append(f"{emoji} {message}")

        if data:
            for key, value in data.items():
                if isinstance(value, str) and "\n" in value:
                    result.append(f"  {key}:")
                    for line in value.split("\n"):
                        result.append(f"    {line}")
                else:
                    result.append(f"  {key}: {value}")

        return "\n".join(result)


class SubprocessCommandExecutor(CommandExecutor):
    """基于subprocess的命令执行器 - 支持超时、工作目录、输出大小限制和取消"""

    # 命令输出最大字节数（1MB），超出截断
    MAX_OUTPUT_BYTES = 1 * 1024 * 1024

    def __init__(self, logger=None):
        super().__init__(logger)
        self._current_process = None
        self._current_pgid = None  # L9：Popen 后立即捕获，shell 死亡后 getpgid 会失败
        self._process_lock = threading.Lock()

    def _terminate_tree(self, proc, force: bool = False):
        """终止整个进程组（L9：POSIX 下 shell=True 只杀 shell 时，孙进程
        （`(x &)` 等）仍持管道写端——reader 阻塞无 EOF、fd/线程悬挂至孙
        进程退出，写满 64KB 管道缓冲后孙进程也卡死。start_new_session 使
        shell 成为会话首进程，按组信号可一次覆盖整棵进程树）。
        组 ID 在 Popen 后立即保存：shell 死亡后 os.getpgid(pid) 会抛
        ProcessLookupError，届时无法再从进程反查组。
        Windows 无进程组概念，退回单进程 terminate/kill。"""
        try:
            if os.name == "posix":
                sig = signal.SIGKILL if force else signal.SIGTERM
                with self._process_lock:
                    pgid = self._current_pgid
                if pgid is None:
                    pgid = os.getpgid(proc.pid)
                os.killpg(pgid, sig)
            elif force:
                proc.kill()
            else:
                proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def cancel(self):
        """取消当前正在运行的命令（kill子进程组）。线程安全。"""
        with self._process_lock:
            proc = self._current_process
        if proc is not None:
            try:
                if proc.poll() is None:
                    self._terminate_tree(proc, force=True)
                    self.logger.info("已终止正在运行的子进程组")
            except Exception:
                pass

    def _read_with_limit(self, process, timeout: int, start_time: float, output_queue=None) -> Tuple[str, bool, bool]:
        """从进程读取输出，限制总大小和超时。返回 (output_str, timeout_occurred, truncated)。

        H2 修复：阻塞 readline 在独立 reader 线程执行，主循环带超时轮询队列。
        此前超时检查只在两次 readline 之间执行，无输出命令会让 readline
        阻塞到进程自然退出，timeout 形同虚设（审计 PoC 实证）。
        """
        output_chunks = []
        total_bytes = 0
        timeout_occurred = False
        truncated = False

        line_queue: queue.Queue = queue.Queue()
        reader_done = threading.Event()

        def _reader():
            """后台 reader 线程：阻塞 readline，行入队；EOF/异常结束"""
            try:
                for line in iter(process.stdout.readline, ""):
                    line_queue.put(line)
            except Exception:
                pass
            finally:
                reader_done.set()

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        shell_dead_since = None  # shell 进程已死但 reader 未收到 EOF 的起始时刻
        while True:
            # 检查超时（readline 阻塞不再影响本判断）
            if timeout > 0 and time.time() - start_time > timeout:
                self._terminate_tree(process)
                timeout_occurred = True
                break

            # 检查输出大小
            if total_bytes >= self.MAX_OUTPUT_BYTES:
                truncated = True
                self._terminate_tree(process)
                break

            try:
                line = line_queue.get(timeout=0.1)
                shell_dead_since = None
            except queue.Empty:
                if reader_done.is_set():
                    break
                # shell 已被 kill/退出但 reader 仍阻塞（如 Windows shell=True
                # 下 cancel 只杀死包装进程，孙进程仍持有管道写端导致无 EOF）：
                # 宽限 1 秒排空残留输出后放弃等待
                if process.poll() is not None:
                    if shell_dead_since is None:
                        shell_dead_since = time.time()
                    elif time.time() - shell_dead_since > 1.0:
                        # L9：shell 已死但孙进程仍持管道写端（`(sleep 300 &) ;
                        # echo done` 形态）。只靠 daemon reader 兜底会悬挂线程
                        # 与管道 fd 至孙进程退出，写满 64KB 缓冲后孙进程自身
                        # 也卡死。此处强杀进程组收尾（nohup 重定向输出的
                        # 真正脱离场景不持管道，不会走到这里）。
                        self._terminate_tree(process, force=True)
                        break
                continue

            line_bytes = len(line.encode('utf-8', errors='replace'))
            if total_bytes + line_bytes > self.MAX_OUTPUT_BYTES and not truncated:
                allowed = self.MAX_OUTPUT_BYTES - total_bytes
                if allowed > 0:
                    # L5：decode 用 errors='ignore'——按字节截断可能落在多字节
                    # 字符中间，errors='replace' 会在行尾留下半个字符（�）
                    output_chunks.append(line.encode('utf-8', errors='replace')[:allowed].decode('utf-8', errors='ignore'))
                    total_bytes = self.MAX_OUTPUT_BYTES
                truncated = True
                self._terminate_tree(process)
                break

            output_chunks.append(line)
            total_bytes += line_bytes

            if output_queue:
                output_queue.put(("line", line.rstrip('\n') + "\n"))

        # N4 修复（审计 2026-08-18，二次修正）：reader 线程收尾。
        # 超时/截断/宽限到期 break 后，reader 可能仍阻塞在 readline
        # （Windows shell=True 下孙进程持管道写端，永远等不到 EOF）。
        # 注意：此时不能 close stdout——CPython 流对象内部有锁，
        # readline 持锁阻塞时 close() 会一同挂起直到 readline 返回
        # （实测命令超时被拖满整个 Start-Sleep 时长）。reader 为 daemon
        # 线程，孙进程退出收到 EOF 后自然结束，不阻塞主流程；
        # reader 已正常退出的场景才 close 显式释放管道 fd。
        if reader_done.is_set():
            try:
                process.stdout.close()
            except Exception:
                pass
        else:
            reader.join(timeout=2)
            if reader.is_alive():
                self.logger.warning(
                    "reader 线程仍阻塞在管道读取（孙进程持写端），已放弃等待，"
                    "线程由 daemon 兜底、随管道 EOF 自然退出")

        return ''.join(output_chunks), timeout_occurred, truncated

    def execute(self, command: str, timeout: int = 30, work_dir: str = None, env: Dict = None, **kwargs) -> str:
        """执行命令并返回完整结果（带输出大小限制）"""
        if not self.is_enabled():
            return "❌ 命令执行功能已禁用"

        # 默认工作目录为workspace，避免在启动目录散落文件
        if work_dir is None or work_dir == ".":
            work_dir = constants.WORKSPACE_DIR

        self.logger.info(f"执行命令: {command} (work_dir={work_dir})")

        # 准备环境变量
        exec_env = os.environ.copy()
        if env:
            exec_env.update(env)

        process = None
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,  # H3 修复：不挂 stdin，防等待输入命令永久挂起
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=exec_env,
                cwd=os.path.expanduser(work_dir),
                # L9：POSIX 下 shell 成为会话首进程，超时/取消可按进程组
                # 一次终止整棵进程树（含 & 后台孙进程），消除管道悬挂
                start_new_session=(os.name == "posix")
            )
            # 保存进程引用以便cancel()可以kill它
            with self._process_lock:
                self._current_process = process
                if os.name == "posix":
                    try:
                        self._current_pgid = os.getpgid(process.pid)
                    except Exception:
                        self._current_pgid = None

            start_time = time.time()
            output_str, timeout_occurred, truncated = self._read_with_limit(process, timeout, start_time)

            # 确保进程终止
            if process.poll() is None:
                self._terminate_tree(process, force=timeout_occurred or truncated)
                try:
                    process.wait(timeout=2)
                except:
                    self._terminate_tree(process, force=True)

            return_code = process.returncode if process.returncode is not None else -1

            data = {
                "输出": output_str.strip() if output_str.strip() else "(无输出)",
                "返回码": return_code
            }

            if timeout_occurred:
                return self.format_result(False, f"命令执行超时（{timeout}秒）", data)

            if truncated:
                data["警告"] = f"输出超过 {self.MAX_OUTPUT_BYTES/1024/1024:.0f}MB 限制，结果已截断"
                return self.format_result(False, "命令执行完成（输出已截断）", data)

            return self.format_result(return_code == 0, "命令执行完成", data)

        except subprocess.TimeoutExpired:
            return f"❌ 命令执行超时（{timeout}秒）"
        except FileNotFoundError as e:
            return f"❌ 命令执行错误: 命令未找到 - {str(e)}"
        except PermissionError as e:
            return f"❌ 命令执行错误: 权限不足 - {str(e)}"
        except Exception as e:
            return f"❌ 执行错误: {str(e)}"
        finally:
            if process and process.poll() is None:
                self._terminate_tree(process, force=True)
            # 清除当前进程引用
            with self._process_lock:
                self._current_process = None
                self._current_pgid = None

    def execute_streaming(self, command: str, output_queue: queue.Queue, timeout: int = 30,
                          work_dir: str = None, env: Dict = None, **kwargs):
        """流式执行命令，支持超时控制、工作目录和输出大小限制"""
        if not self.is_enabled():
            output_queue.put(("line", "❌ 命令执行功能已禁用\n"))
            output_queue.put(("complete", "❌ 命令执行功能已禁用"))
            return

        # 默认工作目录为workspace，避免在启动目录散落文件
        if work_dir is None or work_dir == ".":
            work_dir = constants.WORKSPACE_DIR

        self.logger.info(f"流式执行命令: {command} (work_dir={work_dir})")
        output_queue.put(("line", f"\n🔧 执行命令: {command}\n"))
        output_queue.put(("line", f"⏱️ 超时设置: {timeout} 秒\n"))
        output_queue.put(("line", f"📦 输出限制: {self.MAX_OUTPUT_BYTES/1024/1024:.0f}MB\n"))
        output_queue.put(("line", f"📁 工作目录: {work_dir}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 准备环境变量
        exec_env = os.environ.copy()
        if env:
            exec_env.update(env)

        process = None
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,  # H3 修复：不挂 stdin，防等待输入命令永久挂起
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=exec_env,
                cwd=os.path.expanduser(work_dir),
                # L9：POSIX 下 shell 成为会话首进程，超时/取消可按进程组
                # 一次终止整棵进程树（含 & 后台孙进程），消除管道悬挂
                start_new_session=(os.name == "posix")
            )
            self.process = process
            # 保存进程引用以便cancel()可以kill它
            with self._process_lock:
                self._current_process = process
                if os.name == "posix":
                    try:
                        self._current_pgid = os.getpgid(process.pid)
                    except Exception:
                        self._current_pgid = None

            start_time = time.time()
            # H2 修复：复用线程化读取（reader 线程 + 超时轮询），行经 output_queue 流式转发
            output_str, timeout_occurred, truncated = self._read_with_limit(
                process, timeout, start_time, output_queue=output_queue)

            if timeout_occurred:
                output_queue.put(("line", f"\n⏰ 命令执行超时（{timeout}秒）\n"))
            elif truncated:
                output_queue.put(("line", f"\n📦 输出超过 {self.MAX_OUTPUT_BYTES/1024/1024:.0f}MB 限制，结果已截断\n"))

            # 如果超时或截断，确保进程被终止
            if (timeout_occurred or truncated) and process.poll() is None:
                self._terminate_tree(process, force=True)
                try:
                    process.wait(timeout=2)
                except:
                    pass

            return_code = process.returncode if process.returncode is not None else -1

            output_queue.put(("line", "-" * 50 + "\n"))

            if timeout_occurred:
                output_queue.put(("line", f"⏰ 命令执行超时（{timeout}秒），返回码: {return_code}\n"))
            elif truncated:
                output_queue.put(("line", f"📦 输出已截断，返回码: {return_code}\n"))
            else:
                output_queue.put(("line", f"✅ 命令执行完成，返回码: {return_code}\n"))

            data = {
                "输出": output_str.strip() if output_str.strip() else "(无输出)",
                "返回码": return_code
            }

            if timeout_occurred:
                result = self.format_result(False, f"命令执行超时（{timeout}秒）", data)
            elif truncated:
                data["警告"] = f"输出超过 {self.MAX_OUTPUT_BYTES/1024/1024:.0f}MB 限制，结果已截断"
                result = self.format_result(False, "命令执行完成（输出已截断）", data)
            else:
                result = self.format_result(return_code == 0, "命令执行完成", data)

            output_queue.put(("complete", result))

        except Exception as e:
            output_queue.put(("line", f"❌ 执行错误: {str(e)}\n"))
            output_queue.put(("complete", f"❌ 执行错误: {str(e)}"))
        finally:
            if process and process.poll() is None:
                self._terminate_tree(process)
                try:
                    process.wait(timeout=2)
                except:
                    self._terminate_tree(process, force=True)
            self.process = None
            with self._process_lock:
                self._current_process = None
                self._current_pgid = None
