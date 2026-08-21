#!/usr/bin/env python3
"""
subsystems.process_manager - 后台进程管理器

让智能体能够启动并管理长驻后台进程（dev server / 训练任务 / watch 等），
弥补 run_command 同步阻塞、超时即杀的不足：
  - bg 语义：start() 立即返回句柄，进程独立于对话轮次存活
  - 有界环形输出缓冲：每进程上限 buffer_per_process（默认 256KB），
    解决 nohup+日志文件方案输出无界增长的问题
  - 注册表：自增句柄跨会话/跨子代理可见（全局单例）
  - 生命周期：惰性状态收割（reader 线程 EOF 即 poll 收割防僵尸）+
    TTL 惰性清理 + 退出统一收尾（非 detach 进程默认全杀）

安全边界（对齐 SubprocessCommandExecutor 既有惯例）：
  - 输出单行截断 16KB，防超长单行撑爆内存
  - 运行中进程数上限（默认 16）+ 总条目上限（默认 32，已退出条目
    保留尾部输出供验尸，LRU 淘汰）
  - POSIX start_new_session + 进程组信号（L9 语义：孙进程不悬挂）；
    Windows 无进程组概念，回退单进程 terminate/kill
  - 中断语义：用户 Ctrl+C / 取消事件不影响后台进程（后台的本意就是
    脱离本轮对话生命周期）；aibox 进程退出时经 atexit 统一收尾，
    detach=true 的进程豁免

依赖：common (ConfigManager, constants, Logger) + 标准库
不反向依赖 aibox 任何符号。
"""

import atexit
import collections
import os
import signal
import subprocess
import threading
import time
from typing import Dict, List, Optional

from common import ConfigManager, constants, Logger
from subsystems.executors import filter_env_overrides

# 单行输出最大字节数，超出截断（防超长单行撑爆环形缓冲内存预算）
MAX_LINE_BYTES = 16 * 1024

# 单次 bg_output 拉取的硬上限（对齐执行器 1MB 输出限制）
MAX_FETCH_BYTES = 1 * 1024 * 1024


class _ProcEntry:
    """单个后台进程的注册表条目（内部类）"""

    __slots__ = (
        "handle", "name", "command", "popen", "pgid", "started_at",
        "detach", "max_lifetime", "owner", "status", "exit_code", "end_time",
        "lines", "buffer_bytes", "max_buffer", "next_seq", "evicted",
        "lock", "reader_done", "reader", "stdin_lock", "log_fh",
    )

    # 状态取值：running / exited（自然退出）/ killed（被管理器终止）/ expired（TTL 到期）
    def __init__(self, handle: int, name: str, command: str, popen, pgid,
                 detach: bool, max_lifetime: float, buffer_bytes: int,
                 owner: Optional[str] = None):
        self.handle = handle
        self.name = name
        self.command = command
        self.popen = popen
        self.pgid = pgid
        self.started_at = time.time()
        self.detach = detach
        self.max_lifetime = max_lifetime
        self.owner = owner
        self.status = "running"
        self.exit_code = None
        self.end_time = None
        # 环形输出缓冲：deque of (seq, line, nbytes)，seq 单调递增不回收，
        # 淘汰最旧行仍保持 seq 连续性——bg_output 的 since 游标据此续读
        self.lines = collections.deque()
        self.buffer_bytes = 0
        self.max_buffer = max(1024, int(buffer_bytes))
        self.next_seq = 1
        self.evicted = 0
        self.lock = threading.RLock()  # T8：可重入，write_input 持锁期间可调用 _record_line
        self.reader_done = threading.Event()
        self.reader = None
        # 交互写入：单写锁保序（bg_write 串行化，防交错输入）
        self.stdin_lock = threading.Lock()
        # 可选全档镜像文件句柄（log_file=True 时由 reader 线程 tee 写入）
        self.log_fh = None


class ProcessManager:
    """后台进程管理器：启动 / 列举 / 增量拉取输出 / 终止 / 退出收尾"""

    def __init__(self, logger: Optional[Logger] = None,
                 max_processes: int = None, max_entries: int = None,
                 buffer_per_process: int = None, max_lifetime: float = None):
        self.logger = logger or Logger("ProcessManager")
        self.config = ConfigManager()
        self.max_processes = int(max_processes if max_processes is not None
                                 else constants.BG_MAX_PROCESSES)
        self.max_entries = int(max_entries if max_entries is not None
                               else constants.BG_MAX_ENTRIES)
        self.buffer_per_process = int(buffer_per_process if buffer_per_process is not None
                                      else constants.BG_BUFFER_PER_PROCESS)
        self.max_lifetime = float(max_lifetime if max_lifetime is not None
                                  else constants.BG_MAX_LIFETIME)
        self.max_write_bytes = int(constants.BG_MAX_WRITE_BYTES)
        self._procs: Dict[int, _ProcEntry] = {}
        self._next_id = 1
        self._lock = threading.Lock()
        self._shutdown_done = False

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------

    def start(self, command: str, name: str = None, work_dir: str = None,
              env: Dict = None, detach: bool = False,
              log_file: bool = False, owner: str = None) -> Dict:
        """后台启动命令，立即返回。成功返回 {ok, handle, pid, name, log_file}，
        失败返回 {ok: False, error}。

        stdin 为管道：后续可经 write() 交互写入（行协议 REPL 问答）。
        log_file=True 时输出同步 tee 到 workspace/bg_<句柄>.log（无界
        全档，可被原生工具 tail/cat 读取；环形缓冲仍为 bg_output 视图）。"""
        if not command or not isinstance(command, str) or not command.strip():
            return {"ok": False, "error": "缺少或空的 command"}

        # 名称规整（M5 惯例）：标签仅用于展示回显，拒绝控制字符防终端/前端注入
        if name is not None:
            name = str(name).strip()
            if not name.isprintable() or len(name) > 64:
                return {"ok": False, "error": "name 含控制字符或超过 64 字符"}
            if not name:
                name = None
        if name is None:
            name = command.strip()[:40]

        if work_dir is None or work_dir == ".":
            work_dir = constants.WORKSPACE_DIR

        with self._lock:
            if self._shutdown_done:
                return {"ok": False, "error": "管理器已关闭"}
            # TTL 惰性清理 + 状态收割
            self._sweep_locked()
            running = [e for e in self._procs.values() if e.status == "running"]
            if len(running) >= self.max_processes:
                return {"ok": False,
                        "error": f"运行中后台进程已达上限（{self.max_processes}），"
                                 f"请先用 bg_kill 清理"}
            # 总条目上限：淘汰最老的已退出条目（保留其信息的价值最低）
            if len(self._procs) >= self.max_entries:
                exited = sorted(
                    (e for e in self._procs.values() if e.status != "running"),
                    key=lambda e: e.started_at)
                while self._procs and len(self._procs) >= self.max_entries and exited:
                    victim = exited.pop(0)
                    self._procs.pop(victim.handle, None)
                if len(self._procs) >= self.max_entries:
                    return {"ok": False,
                            "error": f"后台进程条目已达上限（{self.max_entries}）且全部运行中"}
            handle = self._next_id
            self._next_id += 1

        exec_env = os.environ.copy()
        if env:
            # T21：黑名单键拒绝（对齐 executors，防 LD_PRELOAD/PYTHONPATH 劫持）
            safe_env, rejected_keys = filter_env_overrides(env)
            if rejected_keys:
                self.logger.warning(f"env 覆盖被黑名单拒绝（T21）: {rejected_keys}")
            exec_env.update(safe_env)

        try:
            popen = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # 交互式 I/O：stdin 挂管道供 write() 写入。与 H3 的差异：
                # 后台进程由管理器显式管理生命周期，不存在"等待输入的
                # 命令永久挂起无人知晓"问题——不读 stdin 的进程不受影响，
                # 读 stdin 的进程获得交互能力，kill 路径三级优雅终止兜底。
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding="utf-8",  # T17：显式 UTF-8，避免 Windows cp936 解码 UTF-8 输出致 reader 静默退出
                errors="replace",
                env=exec_env,
                cwd=os.path.expanduser(work_dir),
                # 对齐 L9：POSIX 下 shell 成为会话首进程，kill 按进程组
                # 一次覆盖整棵进程树（含 & 后台孙进程）
                start_new_session=(os.name == "posix"),
            )
        except FileNotFoundError as e:
            return {"ok": False, "error": f"命令未找到: {e}"}
        except PermissionError as e:
            return {"ok": False, "error": f"权限不足: {e}"}
        except Exception as e:
            return {"ok": False, "error": f"启动失败: {e}"}

        pgid = None
        if os.name == "posix":
            try:
                pgid = os.getpgid(popen.pid)
            except Exception:
                pgid = None

        entry = _ProcEntry(
            handle, name, command.strip(), popen, pgid,
            detach=bool(detach), max_lifetime=self.max_lifetime,
            buffer_bytes=self.buffer_per_process, owner=owner,
        )
        # 可选全档镜像（失败降级为仅环形缓冲，不阻断启动）
        log_path = None
        if log_file:
            try:
                log_dir = constants.WORKSPACE_DIR
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, f"bg_{handle}.log")
                entry.log_fh = open(log_path, "a", encoding="utf-8",
                                    errors="replace", buffering=1)
            except Exception as e:
                entry.log_fh = None
                log_path = None
                self.logger.warning(f"输出镜像文件开启失败，仅保留环形缓冲: {e}")
        with self._lock:
            # start() 竞态兜底：期间并发 shutdown 则立即回收刚拉起的进程
            if self._shutdown_done:
                self._close_stdin(entry)
                self._terminate_tree(entry, force=True)
                try:
                    popen.wait(timeout=2)
                except Exception:
                    pass
                return {"ok": False, "error": "管理器已关闭"}
            self._procs[handle] = entry

        entry.reader = threading.Thread(
            target=self._reader, args=(entry,), daemon=True,
            name=f"bg-proc-{handle}-reader")
        entry.reader.start()
        self.logger.info(f"后台进程已启动: #{handle} pid={popen.pid} "
                         f"detach={entry.detach} cmd={entry.command[:60]}")
        return {"ok": True, "handle": handle, "pid": popen.pid, "name": name,
                "detach": entry.detach, "log_file": log_path}

    # ------------------------------------------------------------------
    # 输出读取
    # ------------------------------------------------------------------

    def get_output(self, handle, tail: int = None, since: int = None,
                   max_bytes: int = 65536) -> Dict:
        """拉取后台进程输出。

        - tail=N：取最近 N 行（缺省 50）
        - since=S：取行序号 > S 的全部缓冲行（增量续读，游标为上次
          返回的 next_cursor；被环形淘汰的早期行以 evicted 提示）
        - max_bytes：本次拉取字节上限（缺省 64KB，硬顶 1MB）
        """
        entry = self._lookup(handle)
        if entry is None:
            return {"ok": False, "error": f"句柄 #{handle} 不存在"}

        try:
            max_bytes = int(max_bytes or 65536)
        except (TypeError, ValueError):
            max_bytes = 65536
        max_bytes = max(1024, min(max_bytes, MAX_FETCH_BYTES))

        with entry.lock:
            snapshot = list(entry.lines)
            evicted = entry.evicted
            next_seq = entry.next_seq

        selected = []
        evicted_note = 0
        if since is not None:
            try:
                since = int(since)
            except (TypeError, ValueError):
                return {"ok": False, "error": "since 游标必须是整数"}
            selected = [(s, l) for (s, l, _) in snapshot if s > since]
            # 请求范围早于环形缓冲起点：被淘汰部分如实告知
            oldest = snapshot[0][0] if snapshot else next_seq
            if since + 1 < oldest:
                evicted_note = oldest - since - 1
        else:
            try:
                tail = int(tail) if tail is not None else 50
            except (TypeError, ValueError):
                tail = 50
            tail = max(1, min(tail, 100000))
            selected = [(s, l) for (s, l, _) in snapshot[-tail:]]

        # 字节上限截断（保留最新行优先：从尾部回溯收集）
        kept = []
        total = 0
        truncated = False
        for s, line in reversed(selected):
            nb = len(line.encode("utf-8", errors="replace"))
            if total + nb > max_bytes:
                truncated = True
                break
            kept.append((s, line))
            total += nb
        kept.reverse()

        output = "".join(line for _, line in kept)
        result = {
            "ok": True,
            "handle": entry.handle,
            "name": entry.name,
            "status": entry.status,
            "exit_code": entry.exit_code,
            "output": output,
            "lines": len(kept),
            "next_cursor": kept[-1][0] if kept else max(since or 0, next_seq - 1),
            "truncated": truncated,
        }
        if evicted_note > 0 or (since is None and evicted > 0):
            result["evicted"] = evicted_note if since is not None else evicted
        return result

    # ------------------------------------------------------------------
    # 交互写入（stdin 管道 + watermark 对齐）
    # ------------------------------------------------------------------

    def write(self, handle, text: str, append_newline: bool = True,
              wait_lines: int = 0, wait_timeout: float = 5.0,
              write_timeout: float = 5.0, owner: str = None) -> Dict:
        """向后台进程 stdin 写入一行（交互式问答）。

        返回 {ok, handle, watermark, sent_bytes[, wait]}：
          - watermark：本条输入的 [stdin] 回显行序号；bg_output(since=
            watermark) 得到的必然且仅是本条输入引发的响应（写读对齐）
          - wait_lines>0 时同步等待 N 行响应（wait 子 dict 含 output/
            timed_out），expect 式一步问答
        失败返回 {ok: False, error}（进程已退出/写入超时/超长等）。

        T22 属主隔离：条目与调用方都携带 owner 且不一致时拒绝——他人
        stdin 不容注入。任一侧未标识（None，如测试/管理面直调）不隔离。"""
        entry = self._lookup(handle)
        if entry is None:
            return {"ok": False, "error": f"句柄 #{handle} 不存在"}
        denial = self._check_owner(entry, owner, "写入")
        if denial:
            return denial
        if not isinstance(text, str) or text == "":
            return {"ok": False, "error": "缺少或空的 text"}
        data = text if (not append_newline or text.endswith("\n")) else text + "\n"
        payload = data.encode("utf-8")
        if len(payload) > self.max_write_bytes:
            return {"ok": False,
                    "error": f"单次写入超过 {self.max_write_bytes} 字节上限（超长内容建议落文件后让进程从文件读）"}

        with self._lock:
            self._update_status_locked(entry)
        if entry.status != "running":
            return {"ok": False, "error": f"进程已结束（{entry.status}），无法写入"}

        # watermark：取号与回显行记账必须在同一临界区，保证 watermark 等于
        # 回显行真实序号，bg_output(since=watermark) 正确对齐本条响应（T8）
        echo = "[stdin] " + text.replace("\n", "⏎")
        with entry.lock:
            watermark = entry.next_seq
            # 回显行进环形缓冲 + log 镜像（多行输入折叠为单行展示）
            self._record_line(entry, echo)

        # 写入放 watchdog 线程：进程不读 stdin 且管道缓冲（~64KB）写满时
        # write() 会阻塞，不能拖死工具线程；超时后 daemon 线程由系统兜底
        error_box = {}
        done = threading.Event()

        def _do_write():
            try:
                with entry.stdin_lock:
                    stdin = entry.popen.stdin
                    if stdin is None or stdin.closed:
                        raise BrokenPipeError("stdin 已关闭")
                    stdin.write(data)
                    stdin.flush()
            except Exception as e:
                error_box["e"] = e
            finally:
                done.set()

        t = threading.Thread(target=_do_write, daemon=True,
                             name=f"bg-proc-{entry.handle}-writer")
        t.start()
        try:
            write_timeout = max(0.1, min(60.0, float(write_timeout)))
        except (TypeError, ValueError):
            write_timeout = 5.0
        if not done.wait(timeout=write_timeout):
            return {"ok": False,
                    "error": f"写入超时（>{write_timeout}s）：进程可能不读取 stdin，管道缓冲已满"}
        if error_box:
            with self._lock:
                self._update_status_locked(entry)
            return {"ok": False,
                    "error": f"写入失败: {error_box['e']}"}

        result = {"ok": True, "handle": entry.handle, "watermark": watermark,
                  "sent_bytes": len(payload)}
        if wait_lines and int(wait_lines) > 0:
            result["wait"] = self._wait_lines(entry, watermark,
                                              int(wait_lines), wait_timeout)
        return result

    def _wait_lines(self, entry: _ProcEntry, watermark: int, count: int,
                    timeout: float) -> Dict:
        """等待 watermark 之后出现 count 行新输出（进程退出且 reader 排空
        即提前返回，如实标记 timed_out）。"""
        try:
            timeout = max(0.1, min(60.0, float(timeout)))
        except (TypeError, ValueError):
            timeout = 5.0
        deadline = time.time() + timeout

        def _snap():
            with entry.lock:
                return [(s, l) for (s, l, _) in entry.lines if s > watermark]

        lines = _snap()
        timed_out = False
        while len(lines) < count:
            if entry.popen.poll() is not None and entry.reader_done.is_set():
                time.sleep(0.1)  # 排空 reader 尾部在途行后取终值
                lines = _snap()
                break
            if time.time() > deadline:
                timed_out = True
                break
            time.sleep(0.05)
            lines = _snap()

        # 字节上限（复用单次拉取硬顶）
        kept, total, truncated = [], 0, False
        for s, line in lines:
            nb = len(line.encode("utf-8", errors="replace"))
            if total + nb > MAX_FETCH_BYTES:
                truncated = True
                break
            kept.append((s, line))
            total += nb
        out = {
            "lines": len(kept),
            "output": "".join(l for _, l in kept),
            "timed_out": timed_out,
            "next_cursor": kept[-1][0] if kept else watermark,
        }
        if truncated:
            out["truncated"] = True
        return out

    def _close_stdin(self, entry: _ProcEntry):
        """关闭 stdin 管道（行协议进程见 EOF 常自行退出；幂等）"""
        try:
            with entry.stdin_lock:
                stdin = entry.popen.stdin
                if stdin is not None and not stdin.closed:
                    stdin.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 列举 / 终止 / 收尾
    # ------------------------------------------------------------------

    def list_procs(self) -> List[Dict]:
        """列出全部条目（运行中在前，其余按句柄序）"""
        with self._lock:
            self._sweep_locked()
            # 惰性收割：列举即刷新全部条目状态（此前仅 kill/get_output 路径更新）
            for entry in self._procs.values():
                self._update_status_locked(entry)
            entries = sorted(self._procs.values(),
                             key=lambda e: (e.status != "running", e.handle))
            return [self._entry_info(e) for e in entries]

    def kill(self, handle, force: bool = False, grace: float = 3.0,
             owner: str = None) -> Dict:
        """终止后台进程。SIGTERM → 宽限轮询 → SIGKILL；force=True 直接 SIGKILL。
        对已退出条目幂等，返回当前状态。

        T22 属主隔离：条目与调用方都携带 owner 且不一致时拒绝——防止
        提示注入的会话/任务跨属主杀掉他人的 dev server。shutdown/TTL
        清理属管理器特权，不走本校验。"""
        entry = self._lookup(handle)
        if entry is None:
            return {"ok": False, "error": f"句柄 #{handle} 不存在"}
        denial = self._check_owner(entry, owner, "终止")
        if denial:
            return denial

        with self._lock:
            self._update_status_locked(entry)
            if entry.status != "running":
                return {"ok": True, "handle": entry.handle, "status": entry.status,
                        "exit_code": entry.exit_code, "already_finished": True}

        try:
            grace = min(30.0, max(0.1, float(grace)))
        except (TypeError, ValueError):
            grace = 3.0

        if force:
            self._terminate_tree(entry, force=True)
        else:
            # 三级优雅终止：关 stdin（行协议进程见 EOF 常自行退出）→
            # SIGTERM 宽限 → SIGKILL 兜底
            self._close_stdin(entry)
            deadline = time.time() + grace * 0.4
            while time.time() < deadline and entry.popen.poll() is None:
                time.sleep(0.05)
            if entry.popen.poll() is None:
                self._terminate_tree(entry)
                deadline = time.time() + grace * 0.6
                while time.time() < deadline and entry.popen.poll() is None:
                    time.sleep(0.05)
        if entry.popen.poll() is None:
            self._terminate_tree(entry, force=True)
            try:
                entry.popen.wait(timeout=2)
            except Exception:
                pass

        with self._lock:
            self._update_status_locked(entry)
            if entry.status == "running":
                # 进程仍未死（极端：不可杀的 D 状态），如实报告
                return {"ok": False, "handle": entry.handle,
                        "error": "进程未能终止（可能处于不可中断状态）"}
            entry.status = "killed"
            return {"ok": True, "handle": entry.handle, "status": entry.status,
                    "exit_code": entry.exit_code}

    def shutdown(self) -> Dict:
        """退出收尾：终止全部非 detach 运行中进程（SIGTERM→SIGKILL），
        清空注册表；detach 进程保留运行、仅注销登记。幂等。"""
        with self._lock:
            if self._shutdown_done:
                return {"killed": 0, "detached": 0}
            self._shutdown_done = True
            entries = list(self._procs.values())
            self._procs.clear()

        killed = detached = 0
        for entry in entries:
            self._update_status(entry)
            if entry.status != "running":
                continue
            if entry.detach:
                detached += 1
                self.logger.info(f"后台进程 #{entry.handle}（detach）保留运行: "
                                 f"pid={entry.popen.pid}")
                continue
            # 优雅收尾：先关 stdin（行协议进程见 EOF 常自行退出）再走信号
            self._close_stdin(entry)
            try:
                entry.popen.wait(timeout=1)
            except Exception:
                pass
            if entry.popen.poll() is None:
                self._terminate_tree(entry)
                try:
                    entry.popen.wait(timeout=3)
                except Exception:
                    self._terminate_tree(entry, force=True)
                    try:
                        entry.popen.wait(timeout=2)
                    except Exception:
                        pass
            entry.status = "killed"
            killed += 1
        if killed or detached:
            self.logger.info(f"后台进程收尾: 终止 {killed} 个, detach 保留 {detached} 个")
        return {"killed": killed, "detached": detached}

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _check_owner(self, entry: _ProcEntry, caller_owner: Optional[str],
                     action: str) -> Optional[Dict]:
        """T22：属主校验——条目与调用方都携带 owner 且不一致时返回拒绝
        dict（写/杀权限隔离）；任一侧未标识（None，如测试/管理面直调）
        不隔离，保持向后兼容。观察面（list/get_output）不经过本校验。"""
        if (entry.owner is not None and caller_owner is not None
                and entry.owner != caller_owner):
            self.logger.warning(
                f"属主校验拒绝：owner={caller_owner} 尝试{action} "
                f"#{entry.handle}（属主 {entry.owner}）")
            return {"ok": False,
                    "error": f"进程 #{entry.handle} 属于其他会话（属主不匹配），无权{action}"}
        return None

    def _lookup(self, handle) -> Optional[_ProcEntry]:
        try:
            handle = int(handle)
        except (TypeError, ValueError):
            return None
        with self._lock:
            entry = self._procs.get(handle)
        if entry is not None:
            self._update_status(entry)
        return entry

    def _update_status(self, entry: _ProcEntry):
        with self._lock:
            self._update_status_locked(entry)

    def _update_status_locked(self, entry: _ProcEntry):
        """惰性收割：poll() 即 waitpid(WNOHANG)，自然退出时记录状态。"""
        if entry.status != "running":
            return
        rc = entry.popen.poll()
        if rc is not None:
            entry.exit_code = rc
            entry.end_time = time.time()
            entry.status = "exited"

    def _sweep_locked(self):
        """TTL 惰性清理：超期运行中进程标记 expired 并终止（max_lifetime<=0 不启用）。
        调用方须持有 self._lock。"""
        if self.max_lifetime <= 0:
            return
        now = time.time()
        for entry in list(self._procs.values()):
            if entry.status == "running" and now - entry.started_at > self.max_lifetime:
                self._terminate_tree(entry)
                entry.status = "expired"
                entry.end_time = now
                entry.exit_code = entry.popen.poll()
                self.logger.info(f"后台进程 #{entry.handle} TTL 到期已终止")

    def _terminate_tree(self, entry: _ProcEntry, force: bool = False):
        """按进程组终止整棵进程树（对齐 executors L9/T11 语义与 Windows 回退）"""
        proc = entry.popen
        try:
            if os.name == "posix":
                sig = signal.SIGKILL if force else signal.SIGTERM
                pgid = entry.pgid
                if pgid is None:
                    pgid = os.getpgid(proc.pid)
                os.killpg(pgid, sig)
            else:
                # T11（审计 2026-08-21）：Windows 无进程组，单进程 kill 收割
                # 不到 spawn 的孙进程（dev server/训练任务成孤儿继续占用端口
                # /文件锁）。taskkill /T /F 递归终止整棵树，失败兜底单进程 kill
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True, timeout=10)
                except Exception:
                    pass
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _reader(self, entry: _ProcEntry):
        """后台 reader 线程（对齐 executors H2 模式）：阻塞 readline 入环形
        缓冲（+ 可选 log 镜像）；EOF/异常即 poll() 收割防僵尸，并显式关闭
        管道与镜像文件句柄。"""
        try:
            for line in iter(entry.popen.stdout.readline, ""):
                self._record_line(entry, line)
        except Exception:
            pass
        finally:
            entry.reader_done.set()
            try:
                entry.popen.poll()  # 立即收割退出子进程，防僵尸悬挂
            except Exception:
                pass
            try:
                entry.popen.stdout.close()
            except Exception:
                pass
            if entry.log_fh is not None:
                try:
                    entry.log_fh.flush()
                    entry.log_fh.close()
                except Exception:
                    pass

    def _record_line(self, entry: _ProcEntry, line: str):
        """统一记录：环形缓冲 + 可选 log 全档镜像（stdin 回显同样入档）"""
        if entry.log_fh is not None:
            try:
                entry.log_fh.write(line)
            except Exception:
                pass
        self._append_line(entry, line)

    @staticmethod
    def _append_line(entry: _ProcEntry, line: str):
        # 单行截断：环形缓冲内存预算不被超长单行击穿
        nbytes = len(line.encode("utf-8", errors="replace"))
        if nbytes > MAX_LINE_BYTES:
            allowed = line.encode("utf-8", errors="replace")[:MAX_LINE_BYTES]
            line = allowed.decode("utf-8", errors="ignore") + "…[单行截断]\n"
            nbytes = len(line.encode("utf-8", errors="replace"))
        with entry.lock:
            entry.lines.append((entry.next_seq, line, nbytes))
            entry.next_seq += 1
            entry.buffer_bytes += nbytes
            while entry.buffer_bytes > entry.max_buffer and len(entry.lines) > 1:
                _, old, old_nb = entry.lines.popleft()
                entry.buffer_bytes -= old_nb
                entry.evicted += 1

    @staticmethod
    def _entry_info(entry: _ProcEntry) -> Dict:
        now = time.time()
        return {
            "handle": entry.handle,
            "name": entry.name,
            "pid": entry.popen.pid,
            "status": entry.status,
            "exit_code": entry.exit_code,
            "detach": entry.detach,
            "started_at": entry.started_at,
            "runtime_sec": round((entry.end_time or now) - entry.started_at, 1),
            "buffered_lines": len(entry.lines),
            "evicted_lines": entry.evicted,
            "command": entry.command,
            "owner": entry.owner,  # T22：属主标识（观察面可见，写/杀需匹配）
        }


# ----------------------------------------------------------------------
# 模块级单例（工具层共享；测试经 reset_process_manager() 重置）
# ----------------------------------------------------------------------

_manager: Optional[ProcessManager] = None
_manager_lock = threading.Lock()
_atexit_registered = False


def get_process_manager(logger: Optional[Logger] = None) -> ProcessManager:
    """获取全局 ProcessManager 单例（懒加载）。

    后台进程注册表必须跨会话/跨子代理共享——任何 worker 子代理启动的
    进程，后续任意子代理都能 bg_list/bg_output/bg_kill 管理。"""
    global _manager, _atexit_registered
    with _manager_lock:
        if _manager is None:
            _manager = ProcessManager(logger)
        if not _atexit_registered:
            # 退出语义：aibox 进程退出（含 --once sys.exit / Ctrl+C）时
            # 非 detach 后台进程统一终止；detach 进程保留运行仅注销登记。
            # SIGKILL/SIGTERM 硬杀无法覆盖（与既有线程池等资源同级）。
            atexit.register(_shutdown_atexit)
            _atexit_registered = True
        return _manager


def reset_process_manager(manager: Optional[ProcessManager] = None) -> None:
    """替换/清空全局单例（测试隔离用）。传入 None 仅解除引用，不触发收尾。"""
    global _manager
    with _manager_lock:
        _manager = manager


def _shutdown_atexit():
    m = _manager
    if m is not None:
        try:
            m.shutdown()
        except Exception:
            pass
