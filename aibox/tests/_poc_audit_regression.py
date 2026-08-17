#!/usr/bin/env python3
"""审计修复 PoC 回归验证（H1/H2/H3 + 1MB + cancel）。

验证 2026-08-17 审计五项修复的实际运行效果：
  PoC-1 (H2): 无输出命令 + timeout=3s → 应 ~3s 返回超时（修复前阻塞 15s+）
  PoC-2 (H3): 等待 stdin 的命令 + timeout=3s → 应超时返回而非永久挂起
  PoC-3      : 输出 >1MB → 应截断且总输出 ≈1MB
  PoC-4      : cancel() 应在宽限时间内终止长命令
  PoC-5 (H1) : CommandLineTool（工具层，非 executor）跑 >1MB 输出 →
               截断生效即证明已走 executor 委托路径
"""

import queue
import sys
import threading
import time
import types
from pathlib import Path

AIBOX_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AIBOX_DIR))
sys.modules.setdefault("websockets", types.ModuleType("websockets"))

from subsystems.executors import SubprocessCommandExecutor
from tools.command_tool import CommandLineTool

results = []


def check(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# ---------- PoC-1 (H2): 无输出命令超时 ----------
ex = SubprocessCommandExecutor()
t0 = time.time()
r = ex.execute("powershell -NoProfile -Command Start-Sleep 15", timeout=3)
elapsed = time.time() - t0
check("PoC-1 无输出命令超时(H2)", elapsed < 6 and "超时" in r,
      f"{elapsed:.1f}s 返回（修复前阻塞15s+），结果含超时: {'超时' in r}")

# ---------- PoC-2 (H3): 等待 stdin 的命令 ----------
# stdin=DEVNULL 下命令立即收到 EOF：要么快速退出，要么按 timeout 返回；
# 判定标准 = 不永久挂起（修复前 readline 无 EOF 永久阻塞）。
t0 = time.time()
r = ex.execute("powershell -NoProfile -Command Read-Host", timeout=3)
elapsed = time.time() - t0
check("PoC-2 stdin等待命令不挂起(H3)", elapsed < 6,
      f"{elapsed:.1f}s 返回（修复前永久挂起）")

# ---------- PoC-3: 1MB 输出截断 ----------
t0 = time.time()
r = ex.execute(
    'powershell -NoProfile -Command "$s=\'x\'*2048; 1..1024 | ForEach-Object { $s }"',
    timeout=60)
elapsed = time.time() - t0
size_mb = len(r.encode("utf-8", errors="replace")) / 1024 / 1024
check("PoC-3 1MB输出截断", "截断" in r and size_mb < 1.15,
      f"{elapsed:.1f}s，结果 {size_mb:.2f}MB，含截断标记: {'截断' in r}")

# ---------- PoC-4: cancel() 中断 ----------
out_q = queue.Queue()
done = threading.Event()


def _run():
    ex.execute_streaming(
        "powershell -NoProfile -Command Start-Sleep 60",
        out_q, timeout=120)
    done.set()


t0 = time.time()
th = threading.Thread(target=_run, daemon=True)
th.start()
time.sleep(2)
ex.cancel()
done.wait(timeout=10)
elapsed = time.time() - t0
check("PoC-4 cancel中断", done.is_set() and elapsed < 8,
      f"cancel 后 {elapsed:.1f}s 结束（修复前杀不到进程需等满120s）")

# ---------- PoC-5 (H1): 工具层委托 executor（截断保护贯通） ----------
tool = CommandLineTool(executor=ex)
t0 = time.time()
r = tool.execute(
    command='powershell -NoProfile -Command "$s=\'y\'*2048; 1..1024 | ForEach-Object { $s }"',
    timeout=60)
elapsed = time.time() - t0
size_mb = len(r.encode("utf-8", errors="replace")) / 1024 / 1024
check("PoC-5 工具层1MB贯通(H1)", "截断" in r and size_mb < 1.15,
      f"{elapsed:.1f}s，工具层结果 {size_mb:.2f}MB（修复前无限累积）")

# ---------- 汇总 ----------
fails = [n for n, ok, _ in results if not ok]
print("\n==== 汇总: %d/%d PASS ====" % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)
