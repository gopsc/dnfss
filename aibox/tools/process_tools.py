#!/usr/bin/env python3
"""
tools.process_tools - 后台进程管理工具

五个 worker 执行面工具，配合 subsystems.process_manager.ProcessManager：
  - BgRunTool     : bg_run    后台启动长驻进程，立即返回句柄
  - BgWriteTool   : bg_write  向后台进程 stdin 写入（watermark 写读对齐）
  - BgListTool    : bg_list   列出全部后台进程（运行中/已退出）
  - BgOutputTool  : bg_output 拉取输出（tail 尾部 / since 游标增量）
  - BgKillTool    : bg_kill   终止指定后台进程（关stdin→SIGTERM→SIGKILL）

设计语义（与用户约定的三决策）：
  - 用户中断（Ctrl+C/取消事件）不影响后台进程
  - aibox 进程退出时默认终止全部，bg_run detach=true 豁免
  - 注册表全局共享：任何子代理可管理其他子代理启动的进程

交互语义：
  - stdin 为管道：行协议程序（REPL/问答 CLI）可 bg_write 写入；
    watermark = [stdin] 回显行序号，bg_output(since=watermark) 即本条
    输入的响应（写读对齐）
  - bg_run log_file=true 时输出 tee 到 workspace/bg_<句柄>.log（无界
    全档），可被原生工具（run_command tail/cat）读取

依赖：common (ConfigManager, constants) + tools.base.Tool +
subsystems.process_manager（get_process_manager 单例）
不反向依赖 aibox 任何符号。
"""

from typing import Dict

from common import ConfigManager, constants
from subsystems.process_manager import get_process_manager, ProcessManager
from .base import Tool


def _enabled() -> bool:
    """后台进程功能开关：commands.enabled 与 background_processes.enabled
    双闸（后台执行本质仍是命令执行，前者关闭时一并拒绝）"""
    return constants.COMMANDS_ENABLED and constants.BG_PROCESSES_ENABLED


def _fmt_duration(seconds: float) -> str:
    """秒数 → 人读时长（如 3h05m / 12.5s）"""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "?"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def _coerce_int(value, default=None):
    """命令面输入规整（W 批惯例）：LLM 可能传字符串数字，统一安全转 int"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value, default: bool) -> bool:
    """命令面输入规整：LLM 可能传字符串 "false"/"0"，统一安全转 bool"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


class BgRunTool(Tool):
    """后台启动长驻进程"""

    def __init__(self, manager: ProcessManager = None, logger=None):
        super().__init__(logger)
        self.manager = manager or get_process_manager(logger)

    def get_name(self) -> str:
        return "bg_run"

    def get_description(self) -> str:
        return ("后台启动长驻进程（如 dev server、训练任务、watch 监控），立即返回句柄不阻塞。"
                "与 run_command 的区别：run_command 同步等待命令结束（超时即杀），适合短命令；"
                "bg_run 适合需要持续运行的进程，启动后用 bg_list 查看状态、bg_output 拉取输出、"
                "bg_kill 终止。stdin 为管道：行协议交互程序（REPL/问答式 CLI）可用 bg_write 写入。"
                "注意：用户中断不影响后台进程；aibox 退出时后台进程默认被终止，"
                "需要跨重启存活的进程用 detach=true（随后无法再经句柄管理）。")

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的命令（shell 命令行）",
                },
                "proc_name": {
                    "type": "string",
                    "description": "进程标签（可选，便于识别，≤64 字符，默认取命令前 40 字符）",
                },
                "work_dir": {
                    "type": "string",
                    "description": "工作目录（默认 ~/.aibox/workspace）",
                },
                "env": {
                    "type": "object",
                    "description": "额外的环境变量",
                },
                "detach": {
                    "type": "boolean",
                    "description": "aibox 退出时不终止该进程（默认 false；detach 后进程脱离句柄管理）",
                },
                "log_file": {
                    "type": "boolean",
                    "description": "同时镜像全程输出到 workspace/bg_<句柄>.log（无界全档，可被 run_command 的 tail/cat 读取；默认 false 仅环形缓冲）",
                },
            },
            "required": ["command"],
        }

    def execute(self, command: str = None, proc_name: str = None,
                work_dir: str = None, env: Dict = None,
                detach: bool = False, log_file: bool = False, **kwargs) -> str:
        if error := self.validate_params(["command"], command=command):
            return error
        if not _enabled():
            return "❌ 后台进程功能已禁用（commands.enabled 或 background_processes.enabled 为 false）"

        # T18：LLM 可能传字符串 "false"，裸 bool("false") is True——
        # detach 误开致进程脱离管理、log_file 误开，统一规整
        detach = _coerce_bool(detach, False)
        log_file = _coerce_bool(log_file, False)

        result = self.manager.start(command, name=proc_name, work_dir=work_dir,
                                    env=env, detach=detach, log_file=log_file)
        if not result.get("ok"):
            return self.format_result(False, "后台进程启动失败",
                                      {"错误": result.get("error", "未知错误")})
        data = {
            "句柄": result["handle"],
            "PID": result["pid"],
            "名称": result["name"],
            "detach": "是（aibox 退出不终止）" if result.get("detach") else "否（aibox 退出时终止）",
            "后续": f"bg_output 拉取输出 / bg_list 查看状态 / bg_kill 终止（句柄 #{result['handle']}）",
        }
        if result.get("log_file"):
            data["日志镜像"] = result["log_file"]
        return self.format_result(True, f"后台进程已启动（#{result['handle']}）", data)


class BgWriteTool(Tool):
    """向后台进程 stdin 写入（交互式问答）"""

    def __init__(self, manager: ProcessManager = None, logger=None):
        super().__init__(logger)
        self.manager = manager or get_process_manager(logger)

    def get_name(self) -> str:
        return "bg_write"

    def get_description(self) -> str:
        return ("向后台进程的标准输入写入一行（行协议交互：REPL / 问答式 CLI）。"
                "返回 watermark 水位：bg_output(handle=..., since=watermark) 得到的必然且仅是"
                "本条输入引发的进程响应（写读对齐，不含 [stdin] 回显行）。"
                "适用前提：进程按行读 stdin 并及时输出——建议启动时强制行缓冲"
                "（python -i -u、PYTHONUNBUFFERED=1、stdbuf -oL），否则响应可能滞留子进程缓冲。"
                "限制：无 TTY，全屏程序（vim/top 等）不可用；喂密码类交互（如 sudo -S）会留在"
                "对话记录中，敏感凭据请改用环境变量注入。")

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "handle": {
                    "type": "integer",
                    "description": "bg_run 返回的句柄编号",
                },
                "text": {
                    "type": "string",
                    "description": "要写入的文本（默认追加换行；多行输入会折叠为单行回显）",
                },
                "append_newline": {
                    "type": "boolean",
                    "description": "写入后是否自动补换行（默认 true；false 发送原始字节）",
                },
                "wait_lines": {
                    "type": "integer",
                    "description": "写完同步等待 N 行响应再返回（默认 0 立即返回，用 bg_output 轮询）",
                },
                "wait_timeout": {
                    "type": "number",
                    "description": "同步等待响应的超时秒数（默认 5，上限 60）",
                },
            },
            "required": ["handle", "text"],
        }

    def execute(self, handle=None, text: str = None, append_newline=True,
                wait_lines=None, wait_timeout=None, **kwargs) -> str:
        if error := self.validate_params(["handle", "text"], handle=handle, text=text):
            return error
        if not _enabled():
            return "❌ 后台进程功能已禁用"

        handle = _coerce_int(handle)
        if handle is None:
            return "❌ 错误：handle 必须是整数句柄"
        wait_i = _coerce_int(wait_lines, 0)
        wait_t = _coerce_int(wait_timeout, 5)

        result = self.manager.write(
            handle, text,
            append_newline=_coerce_bool(append_newline, True),
            wait_lines=max(0, wait_i),
            wait_timeout=wait_t,
        )
        if not result.get("ok"):
            return self.format_result(False, f"写入后台进程 #{handle} 失败",
                                      {"错误": result.get("error", "未知错误")})

        data = {
            "句柄": handle,
            "已写入": f"{result['sent_bytes']} 字节",
            "watermark": result["watermark"],
            "对齐读取": f"bg_output(handle={handle}, since={result['watermark']}) → 本条输入的响应",
        }
        if "wait" in result:
            w = result["wait"]
            data["等待输出"] = w.get("output") or "(超时未收到响应)"
            data["next_cursor"] = w.get("next_cursor")
            if w.get("timed_out"):
                data["警告"] = "等待响应超时（进程可能无输出或缓冲滞留），可用 bg_output 轮询"
        return self.format_result(True, f"已写入后台进程 #{handle}", data)


class BgListTool(Tool):
    """列出后台进程"""

    def __init__(self, manager: ProcessManager = None, logger=None):
        super().__init__(logger)
        self.manager = manager or get_process_manager(logger)

    def get_name(self) -> str:
        return "bg_list"

    def get_description(self) -> str:
        return ("列出全部后台进程（含已退出的，保留尾部输出供验尸）。运行中在前。"
                "输出包含句柄/名称/PID/状态/运行时长，用句柄配合 bg_output/bg_kill 管理。")

    def get_parameters(self) -> Dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        if not _enabled():
            return "❌ 后台进程功能已禁用"
        procs = self.manager.list_procs()
        if not procs:
            return "✅ 当前没有后台进程（bg_run 启动）"
        lines = []
        status_emoji = {"running": "🟢", "exited": "⚪", "killed": "🔴", "expired": "⏹️"}
        for p in procs:
            emoji = status_emoji.get(p["status"], "❔")
            extra = ""
            if p["status"] == "running":
                extra = f"运行 {_fmt_duration(p['runtime_sec'])}"
            else:
                extra = f"已结束（{_fmt_duration(p['runtime_sec'])}，返回码 {p['exit_code']}）"
            detach_tag = " [detach]" if p["detach"] else ""
            lines.append(f"{emoji} #{p['handle']} {p['name']}{detach_tag} "
                         f"(pid {p['pid']}, {extra})")
            lines.append(f"    命令: {p['command']}")
        running = sum(1 for p in procs if p["status"] == "running")
        header = f"共 {len(procs)} 个后台进程（运行中 {running}）"
        return "✅ " + header + "\n" + "\n".join(lines)


class BgOutputTool(Tool):
    """拉取后台进程输出"""

    def __init__(self, manager: ProcessManager = None, logger=None):
        super().__init__(logger)
        self.manager = manager or get_process_manager(logger)

    def get_name(self) -> str:
        return "bg_output"

    def get_description(self) -> str:
        return ("拉取后台进程输出。两种模式：tail=N 取最近 N 行（默认 50）；"
                "since=游标 增量拉取上次读取之后的新输出（游标用上次返回的 next_cursor）。"
                "长驻进程轮询建议用 since 模式避免重复传输旧输出。")

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "handle": {
                    "type": "integer",
                    "description": "bg_run 返回的句柄编号",
                },
                "tail": {
                    "type": "integer",
                    "description": "取最近 N 行（默认 50；与 since 二选一）",
                },
                "since": {
                    "type": "integer",
                    "description": "增量游标：上次返回的 next_cursor，取此序号之后的新行（与 tail 二选一）",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "本次拉取最大字节数（默认 64KB，上限 1MB）",
                },
            },
            "required": ["handle"],
        }

    def execute(self, handle=None, tail=None, since=None, max_bytes=None, **kwargs) -> str:
        if error := self.validate_params(["handle"], handle=handle):
            return error
        if not _enabled():
            return "❌ 后台进程功能已禁用"

        handle = _coerce_int(handle)
        if handle is None:
            return "❌ 错误：handle 必须是整数句柄"
        since_i = _coerce_int(since)
        tail_i = _coerce_int(tail)

        result = self.manager.get_output(handle, tail=tail_i, since=since_i,
                                         max_bytes=_coerce_int(max_bytes, 65536))
        if not result.get("ok"):
            return self.format_result(False, "拉取输出失败",
                                      {"错误": result.get("error", "未知错误")})

        data = {
            "状态": result["status"] + (f"（返回码 {result['exit_code']}）"
                                        if result["exit_code"] is not None else ""),
            "本次行数": result["lines"],
            "next_cursor": result["next_cursor"],
        }
        if result.get("truncated"):
            data["警告"] = "输出超过本次字节上限，已截断（提高 max_bytes 或用 since 分段拉取）"
        if result.get("evicted"):
            data["提示"] = (f"有 {result['evicted']} 行早期输出因环形缓冲淘汰不可读取"
                           "（需要完整日志请让进程重定向到文件）")
        output = result.get("output") or "(本次无新输出)"
        data["输出"] = output
        return self.format_result(True, f"后台进程 #{handle} 输出", data)


class BgKillTool(Tool):
    """终止后台进程"""

    def __init__(self, manager: ProcessManager = None, logger=None):
        super().__init__(logger)
        self.manager = manager or get_process_manager(logger)

    def get_name(self) -> str:
        return "bg_kill"

    def get_description(self) -> str:
        return ("终止后台进程（整个进程组，含子进程）。默认先 SIGTERM 优雅终止，"
                "宽限期内未退出再 SIGKILL；force=true 直接 SIGKILL。对已退出进程幂等。")

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "handle": {
                    "type": "integer",
                    "description": "bg_run 返回的句柄编号",
                },
                "force": {
                    "type": "boolean",
                    "description": "跳过优雅终止直接 SIGKILL（默认 false）",
                },
                "grace": {
                    "type": "number",
                    "description": "SIGTERM 后等待进程退出的宽限秒数（默认 3，上限 30）",
                },
            },
            "required": ["handle"],
        }

    def execute(self, handle=None, force: bool = False, grace: float = 3.0, **kwargs) -> str:
        if error := self.validate_params(["handle"], handle=handle):
            return error
        if not _enabled():
            return "❌ 后台进程功能已禁用"

        handle = _coerce_int(handle)
        if handle is None:
            return "❌ 错误：handle 必须是整数句柄"

        # T18：force 字符串 "false" 会被裸 bool 判为 True，跳过宽限直接 SIGKILL
        force = _coerce_bool(force, False)

        result = self.manager.kill(handle, force=force, grace=grace)
        if not result.get("ok"):
            return self.format_result(False, f"终止后台进程 #{handle} 失败",
                                      {"错误": result.get("error", "未知错误")})
        data = {
            "句柄": handle,
            "状态": result["status"],
        }
        if result.get("exit_code") is not None:
            data["返回码"] = result["exit_code"]
        if result.get("already_finished"):
            return self.format_result(True, f"后台进程 #{handle} 此前已结束", data)
        return self.format_result(True, f"后台进程 #{handle} 已终止", data)
