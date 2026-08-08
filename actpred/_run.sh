#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate

if [ "${1:-}" = "train" ]; then
    shift
    exec python3 train.py "$@"
elif [ "${1:-}" = "export" ]; then
    shift
    exec python3 export_onnx.py "$@"
else
    exec python3 predict.py "$@"
fi
