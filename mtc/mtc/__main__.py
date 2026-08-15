"""CLI 入口：python -m mtc script.py [-o out.mt]

诊断（编译告警/错误）走 stderr，stdout 保持纯净 .mt 文本
（对齐 pybot 的 S02 日志规范，支持 mtc walk.py | pybot --dry-run 管道）。
"""

import argparse
import logging
import sys

from .dsl import CompileError, compile_script


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="mtc",
        description="命令式动作脚本（Python DSL）→ .mt 编译器")
    p.add_argument("script", help="DSL 脚本路径（.py）")
    p.add_argument("-o", "--output", default=None,
                   help="输出 .mt 路径（缺省写 stdout）")
    p.add_argument("--verbose", action="store_true", help="DEBUG 日志")
    args = p.parse_args(argv)
    logging.basicConfig(stream=sys.stderr,
                        level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    try:
        with open(args.script, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        logging.error("无法读取脚本: %s", e)
        return 2

    try:
        text, warnings = compile_script(source, filename=args.script)
    except CompileError as e:
        logging.error("编译失败: %s", e)
        return 2

    for w in warnings:
        logging.warning("%s", w)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        logging.info("written: %s (%d lines)", args.output,
                     text.count("\n"))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
