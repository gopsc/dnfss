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
        self._process_lock = threading.Lock()

    def cancel(self):
        """取消当前正在运行的命令（kill子进程）。线程安全。"""
        with self._process_lock:
            proc = self._current_process
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
                    self.logger.info("已终止正在运行的子进程")
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
                process.terminate()
                timeout_occurred = True
                break

            # 检查输出大小
            if total_bytes >= self.MAX_OUTPUT_BYTES:
                truncated = True
                process.terminate()
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
                        break
                continue

            line_bytes = len(line.encode('utf-8', errors='replace'))
            if total_bytes + line_bytes > self.MAX_OUTPUT_BYTES and not truncated:
                allowed = self.MAX_OUTPUT_BYTES - total_bytes
                if allowed > 0:
                    output_chunks.append(line.encode('utf-8', errors='replace')[:allowed].decode('utf-8', errors='replace'))
                    total_bytes = self.MAX_OUTPUT_BYTES
                truncated = True
                process.terminate()
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
                cwd=os.path.expanduser(work_dir)
            )
            # 保存进程引用以便cancel()可以kill它
            with self._process_lock:
                self._current_process = process

            start_time = time.time()
            output_str, timeout_occurred, truncated = self._read_with_limit(process, timeout, start_time)

            # 确保进程终止
            if process.poll() is None:
                if timeout_occurred or truncated:
                    process.kill()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except:
                    process.kill()

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
                process.kill()
            # 清除当前进程引用
            with self._process_lock:
                self._current_process = None

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
                cwd=os.path.expanduser(work_dir)
            )
            self.process = process
            # 保存进程引用以便cancel()可以kill它
            with self._process_lock:
                self._current_process = process

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
                process.kill()
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
                process.terminate()
                try:
                    process.wait(timeout=2)
                except:
                    process.kill()
            self.process = None
            with self._process_lock:
                self._current_process = None
