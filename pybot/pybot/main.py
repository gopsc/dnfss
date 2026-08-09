"""CLI 入口 —— 移植自 bot4cj/src/main.cj

用法（与仓颉版管道方式兼容）：
    cat scripts/std.mt | python3 -m pybot          # stdin 管道
    python3 -m pybot scripts/std.mt                # 位置参数（增强）
    python3 -m pybot --dry-run scripts/std.mt      # Mock 总线，动作序列到 stdout
    python3 -m pybot --test roundtrip in.mt out.mt # 解析↔序列化往返

与仓颉版的差异（见方案 §4.7）：
    --test nn/cnn/cnn1 移除（NN 由 actpred 承担）；
    --test matrix_o/matrix_2 合并为 --test roundtrip（路径改为参数）；
    新增 --dry-run / --verbose；-i / -o 保留参数位（reserved，对接 actpred）。
日志规范（S02 根治）：全部诊断走 logging(stderr)，stdout 保持纯净。
"""

import argparse
import logging
import sys

from .hd import i2c, spi
from .parser import MatrixParser, MatrixSaver
from .robot import RobotStruct


def _build_argparser():
    p = argparse.ArgumentParser(
        prog="pybot",
        description="机器人 .mt 动作脚本解析器（bot4cj 的 Python 迁移版）")
    p.add_argument("script", nargs="?", default=None,
                   help=".mt 脚本路径（缺省从 stdin 读取）")
    p.add_argument("--test", nargs=3, metavar=("MODE", "IN", "OUT"),
                   help="自检：roundtrip <in.mt> <out.mt> 解析↔序列化往返")
    p.add_argument("--dry-run", action="store_true",
                   help="Mock 总线运行（不碰硬件），动作序列输出到 stdout")
    p.add_argument("--verbose", action="store_true",
                   help="DEBUG 级日志（stderr）")
    p.add_argument("-i", metavar="FIFO", default=None,
                   help="(reserved) 传感器数据输出管道，对接 actpred")
    p.add_argument("-o", metavar="FIFO", default=None,
                   help="(reserved) 动作指令输入管道，对接 actpred")
    return p


def _roundtrip(in_path, out_path):
    """解析 in.mt 再序列化到 out.mt（对应仓颉版 --test matrix_o/matrix_2）"""
    objects = []
    with open(in_path, encoding="utf-8") as f:
        parser = MatrixParser(f)
        while True:
            obj = parser.parse()
            if obj is None:
                break
            objects.append(obj)
    with open(out_path, "w", encoding="utf-8") as f:
        MatrixSaver(f).save(objects)
    logging.info("roundtrip: %d blocks, %s -> %s",
                 len(objects), in_path, out_path)
    return 0


def _dump_journal(*mocks):
    """--dry-run 结束后把 Mock 总线动作序列输出到 stdout（等价性验证工件）"""
    for mock in mocks:
        for entry in mock.journal:
            print(" ".join(str(x) for x in entry))


def main(argv=None):
    args = _build_argparser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")

    if args.i or args.o:
        logging.warning("-i/-o are reserved for actpred integration, ignored")

    if args.test:
        mode, in_path, out_path = args.test
        if mode != "roundtrip":
            logging.error("unknown --test mode: %s (supported: roundtrip)", mode)
            return 2
        return _roundtrip(in_path, out_path)

    mocks = ()
    if args.dry_run:
        mocks = (i2c.use_mock(), spi.use_mock())

    source = open(args.script, encoding="utf-8") if args.script else sys.stdin
    try:
        robot = RobotStruct(source, dry_run=args.dry_run)
        with robot:
            robot.run()
    finally:
        if args.script:
            source.close()

    if args.dry_run:
        _dump_journal(*mocks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
