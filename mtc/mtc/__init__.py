"""mtc —— 命令式动作脚本（Python DSL）→ .mt 矩阵脚本编译器

管线：script.py --exec--> 事件收集 --> 时间轴 IR（相对→绝对，
重叠截断）--> 10ms 栅格化 --> out.mt --> pybot 解析/执行。

用法：
    python -m mtc walk.py              # 编译到 stdout（管道风格）
    python -m mtc walk.py -o walk.mt   # 写文件
    python -m pybot --dry-run walk.mt  # 交给 pybot 验证/执行
"""

__version__ = "0.1.0"

from .dsl import CompileError, compile_script  # noqa: F401
