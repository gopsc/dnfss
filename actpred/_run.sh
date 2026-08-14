#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate

if [ "${1:-}" = "train" ]; then
    shift
    exec python3 train.py "$@"
elif [ "${1:-}" = "pretrain" ]; then
    shift
    exec python3 pretrain_mt.py "$@"
elif [ "${1:-}" = "export" ]; then
    shift
    exec python3 export_onnx.py "$@"
elif [ "${1:-}" = "predict" ]; then
    shift
    exec python3 predict.py "$@"
else
    echo "未知子命令: ${1:-}（可用: train / pretrain / export / predict）" >&2
    exit 1
fi
