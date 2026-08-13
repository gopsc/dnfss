#!/usr/bin/env python3
"""
tools.command_tool - 命令行执行工具

抽离自 aibox.py，使用抽象的 CommandExecutor 实现命令执行（流式 + 超时控制）。
工具本身不持有 subprocess 逻辑，而是委托给 SubprocessCommandExecutor。

依赖：common (ConfigManager, constants) + tools.base.Tool + subsystems.executors
不反向依赖 aibox 任何符号。
"""

import os
import queue
import subprocess
import time
from typing import Dict

from common import ConfigManager
from subsystems import CommandExecutor, SubprocessCommandExecutor
from .base import Tool


class CommandLineTool(Tool):
    """命令行执行工具 - 使用抽象的命令执行器（支持流式输出和超时控制）"""

    def __init__(self, executor: CommandExecutor = None, logger=None):
        super().__init__(logger)
        self.executor = executor or SubprocessCommandExecutor(logger)

    def get_name(self) -> str:
        return "run_command"

    def get_description(self) -> str:
        return """执行任意系统命令，支持实时输出显示和超时控制"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的命令"
                },
                "timeout": {
                    "type": "integer",
                    "default": 30,
                    "description": "命令执行超时时间（秒），超时后将终止命令执行。默认30秒"
                },
                "work_dir": {
                    "type": "string",
                    "default": ".",
                    "description": "命令执行的工作目录"
                },
                "env": {
                    "type": "object",
                    "description": "额外的环境变量"
                }
            },
            "required": ["command"]
        }

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行命令，支持超时控制"""
        command = kwargs.get("command")
        timeout = kwargs.get("timeout", 30)
        work_dir = kwargs.get("work_dir", ".")
        extra_env = kwargs.get("env", {})

        if not command:
            output_queue.put(("line", "❌ 错误：缺少命令参数\n"))
            output_queue.put(("complete", "❌ 错误：缺少命令参数"))
            return

        # 发送开始执行的消息
        output_queue.put(("line", f"\n🔧 执行命令: {command}\n"))
        output_queue.put(("line", f"⏱️ 超时设置: {timeout} 秒\n"))
        if work_dir != ".":
            output_queue.put(("line", f"📁 工作目录: {work_dir}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 准备环境变量
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        try:
            # 使用 subprocess.Popen 实现流式输出，支持超时
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=env,
                cwd=os.path.expanduser(work_dir)
            )

            start_time = time.time()
            output_lines = []
            timeout_occurred = False

            # 实时读取输出
            while True:
                # 检查超时
                elapsed = time.time() - start_time
                if timeout > 0 and elapsed > timeout:
                    process.terminate()
                    timeout_occurred = True
                    output_queue.put(("line", f"\n⏰ 命令执行超时（{timeout}秒），已终止\n"))
                    break

                # 使用非阻塞方式读取一行
                import select
                import sys

                # 检查是否有输出可读
                if sys.platform == 'win32':
                    # Windows 下使用 readline 带超时
                    try:
                        import msvcrt
                        if msvcrt.kbhit():
                            pass
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            line = line.rstrip('\n')
                            output_queue.put(("line", line + "\n"))
                            output_lines.append(line)
                        else:
                            time.sleep(0.05)
                    except:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            line = line.rstrip('\n')
                            output_queue.put(("line", line + "\n"))
                            output_lines.append(line)
                else:
                    # Unix 系统可以使用 select
                    import select
                    rlist, _, _ = select.select([process.stdout], [], [], 0.5)
                    if rlist:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            line = line.rstrip('\n')
                            output_queue.put(("line", line + "\n"))
                            output_lines.append(line)
                    else:
                        # 检查进程是否结束
                        if process.poll() is not None:
                            # 读取剩余输出
                            remaining = process.stdout.read()
                            if remaining:
                                remaining = remaining.rstrip('\n')
                                if remaining:
                                    output_queue.put(("line", remaining + "\n"))
                                    output_lines.append(remaining)
                            break

            # 如果超时，确保进程被终止
            if timeout_occurred and process.poll() is None:
                process.kill()
                process.wait(timeout=2)

            # 获取返回码
            return_code = process.returncode if process.returncode is not None else -1

            output_queue.put(("line", "-" * 50 + "\n"))

            if timeout_occurred:
                output_queue.put(("line", f"⏰ 命令执行超时（{timeout}秒），返回码: {return_code}\n"))
            else:
                output_queue.put(("line", f"✅ 命令执行完成，返回码: {return_code}\n"))

            # 准备完整结果
            full_output = "\n".join(output_lines)
            data = {
                "输出": full_output if full_output else "(无输出)",
                "返回码": return_code
            }

            if timeout_occurred:
                result = self.format_result(False, f"命令执行超时（{timeout}秒）", data)
            else:
                result = self.format_result(return_code == 0, "命令执行完成", data)

            output_queue.put(("complete", result))

        except subprocess.TimeoutExpired:
            error_msg = f"❌ 命令执行超时（{timeout}秒）"
            output_queue.put(("line", error_msg + "\n"))
            output_queue.put(("complete", error_msg))
        except FileNotFoundError as e:
            error_msg = f"❌ 命令执行错误: 命令未找到 - {str(e)}"
            output_queue.put(("line", error_msg + "\n"))
            output_queue.put(("complete", error_msg))
        except PermissionError as e:
            error_msg = f"❌ 命令执行错误: 权限不足 - {str(e)}"
            output_queue.put(("line", error_msg + "\n"))
            output_queue.put(("complete", error_msg))
        except Exception as e:
            error_msg = f"❌ 执行错误: {str(e)}"
            output_queue.put(("line", error_msg + "\n"))
            output_queue.put(("complete", error_msg))
        finally:
            # 确保进程被清理
            if 'process' in locals() and process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except:
                    process.kill()

    def execute(self, command: str, timeout: int = 30,
                work_dir: str = ".", env: Dict = None, **kwargs) -> str:
        """执行命令 - 仅支持流式执行（命令工具强制流式输出，无 stream 参数）"""
        if error := self.validate_params(["command"], command=command):
            return error

        # 如果正在流式输出，直接返回空（实际输出通过队列）
        if self.streaming_output and self.output_queue:
            return ""

        # 命令工具固定走流式执行：通过临时队列收集完整结果
        output_queue = queue.Queue()
        self.execute_streaming(
            output_queue,
            command=command,
            timeout=timeout,
            work_dir=work_dir,
            env=env
        )

        # 收集流式执行的最终结果（complete 消息）
        final_result = ""
        while not output_queue.empty():
            try:
                msg_type, content = output_queue.get_nowait()
                if msg_type == "complete":
                    final_result = content
            except queue.Empty:
                break
        return final_result
